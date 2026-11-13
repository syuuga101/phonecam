import Foundation
import Network
import CoreMedia

/// TCP サーバ。メイン (5555) と音声専用チャンネル (5556) の両方にこのクラスを使う。
/// USB (usbmux 経由のループバック) は信頼する。Wi-Fi は SecurityManager の許可とペアリングコードが必要。
final class StreamServer {
    enum Role {
        case main
        case audio
    }

    static let port: UInt16 = 5555
    static let audioPort: UInt16 = 5556

    private let port: UInt16
    /// true の間は受信データを読み捨てる (音声チャンネル用。接続の健全性のため読み続ける)
    private let ignoreIncoming: Bool
    /// 未送信の音声をこの時間 (ms) 分までに制限する
    private let audioCapMs: Int
    private let role: Role
    private let security: SecurityManager
    private var bonjourService: NWListener.Service?

    init(port: UInt16 = StreamServer.port, ignoreIncoming: Bool = false, audioCapMs: Int = 200,
         role: Role = .main, security: SecurityManager, bonjourService: NWListener.Service? = nil) {
        self.port = port
        self.ignoreIncoming = ignoreIncoming
        self.audioCapMs = max(10, audioCapMs)
        self.role = role
        self.security = security
        self.bonjourService = bonjourService
    }

    var onControl: (([String: Any]) -> Void)?
    var onClientConnected: ((String) -> Void)?
    var onClientDisconnected: (() -> Void)?

    private let queue = DispatchQueue(label: "phonecam.server")
    private var listener: NWListener?
    private var listenerStartedAt = Date.distantPast
    private var running = false
    private var healthTimer: DispatchSourceTimer?

    // 以下は lock で保護 (どのスレッドからも送信できるようにする)
    private let lock = NSLock()
    private var client: NWConnection?
    private var clientReady = false
    private var generation = 0
    private var pendingVideoFrames = 0
    private var pendingAudioBytes = 0
    private var networkDropped = 0
    private var measureVideo = false
    private var limitVideoQueue = false
    private var videoSendTimes: [UInt64: Double] = [:]
    private var videoSendID: UInt64 = 0
    private var videoSample = VideoSendSample()

    /// 1 本の接続 (queue 上でのみ触る)
    private final class Session {
        let conn: NWConnection
        let ip: String
        let loopback: Bool
        var authorized: Bool
        var ready = false
        var closed = false
        var isClient = false
        var gen = 0
        var parser = PacketParser()
        var timeout: DispatchWorkItem?

        init(conn: NWConnection, ip: String, loopback: Bool, authorized: Bool) {
            self.conn = conn
            self.ip = ip
            self.loopback = loopback
            self.authorized = authorized
        }
    }

    // queue 上でのみ触る
    private var activeSession: Session?
    private var pendings: [Session] = []     // 認証待ちの Wi-Fi 接続 (クライアントにはまだならない)

    // MARK: - 開始 / 停止

    func start() {
        queue.async { [weak self] in
            guard let self = self else { return }
            self.running = true
            self.startListener()
            self.startHealthTimer()
        }
    }

    func stop() {
        queue.async { [weak self] in
            guard let self = self else { return }
            self.running = false
            self.healthTimer?.cancel()
            self.healthTimer = nil
            self.listener?.cancel()
            self.listener = nil
            let ps = self.pendings
            for p in ps { self.closeSession(p) }
            self.dropClient(generation: nil)
        }
    }

    /// 前面に戻ったときなどに、リスナーが死んでいれば作り直す。どのスレッドからでも可。
    func ensureListening() {
        queue.async { [weak self] in
            self?.checkListenerHealth()
        }
    }

    /// 広告内容 (名前など) を差し替えて再公開する
    func updateBonjourService(_ service: NWListener.Service?) {
        queue.async { [weak self] in
            guard let self else { return }
            self.bonjourService = service
            self.listener?.service = self.security.wifiAllowed ? service : nil
        }
    }

    func refreshAdvertisement() {
        queue.async { [weak self] in
            guard let self else { return }
            self.listener?.service = self.security.wifiAllowed ? self.bonjourService : nil
        }
    }

    private func startHealthTimer() {
        guard healthTimer == nil else { return }
        let t = DispatchSource.makeTimerSource(queue: queue)
        t.schedule(deadline: .now() + 5.0, repeating: 5.0, leeway: .seconds(1))
        t.setEventHandler { [weak self] in
            self?.checkListenerHealth()
        }
        t.resume()
        healthTimer = t
    }

    /// queue 上: リスナーが .ready でなければ作り直す
    private func checkListenerHealth() {
        guard running else { return }
        guard let l = listener else {
            startListener()
            return
        }
        if case .ready = l.state { return }
        // 作った直後は準備中なので少し待つ
        if Date().timeIntervalSince(listenerStartedAt) < 3.0 { return }
        l.cancel()
        listener = nil
        startListener()
    }

    private func startListener() {
        guard running, listener == nil else { return }

        let tcpOptions = NWProtocolTCP.Options()
        tcpOptions.noDelay = true
        tcpOptions.enableKeepalive = true
        tcpOptions.keepaliveIdle = 5
        let params = NWParameters(tls: nil, tcp: tcpOptions)
        params.allowLocalEndpointReuse = true

        guard let nwPort = NWEndpoint.Port(rawValue: self.port),
              let newListener = try? NWListener(using: params, on: nwPort) else {
            scheduleRestart()
            return
        }

        newListener.stateUpdateHandler = { [weak self, weak newListener] state in
            guard let self = self else { return }
            switch state {
            case .failed(_), .cancelled:
                newListener?.cancel()
                self.queue.async {
                    if self.listener === newListener {
                        self.listener = nil
                    }
                    self.scheduleRestart()
                }
            default:
                break
            }
        }
        newListener.newConnectionHandler = { [weak self] conn in
            self?.handleNewConnection(conn)
        }
        newListener.service = security.wifiAllowed ? bonjourService : nil
        listener = newListener
        listenerStartedAt = Date()
        newListener.start(queue: queue)
    }

    private func scheduleRestart() {
        queue.asyncAfter(deadline: .now() + 1.0) { [weak self] in
            guard let self = self, self.running else { return }
            self.startListener()
        }
    }

    // MARK: - 接続の受け入れ (queue 上)

    private func handleNewConnection(_ conn: NWConnection) {
        let info = StreamServer.remoteInfo(conn.endpoint)

        if !info.loopback {
            // Wi-Fi: 許可が無い / ロックアウト中 / 音声チャンネルで認証済み IP 以外 は即切断。
            // 既存のクライアントは置き換えない。
            if !security.wifiAllowed || security.isLockedOut(info.ip) {
                conn.cancel()
                return
            }
            if role == .audio && !security.isAuthorizedIP(info.ip) {
                conn.cancel()
                return
            }
        }

        let needsAuth = (role == .main && !info.loopback)
        let s = Session(conn: conn, ip: info.ip, loopback: info.loopback, authorized: !needsAuth)

        conn.stateUpdateHandler = { [weak self, weak s] state in
            guard let self = self, let s = s else { return }
            switch state {
            case .ready:
                self.queue.async { self.sessionReady(s) }
            case .failed(_), .cancelled:
                self.queue.async { self.closeSession(s) }
            default:
                break
            }
        }

        if s.authorized {
            activate(s)
        } else {
            pendings.append(s)
            if pendings.count > 4 {
                let oldest = pendings.removeFirst()
                closeSession(oldest)
            }
        }

        // 接続確立が終わらないまま止まった接続を捨てる
        queue.asyncAfter(deadline: .now() + 10.0) { [weak self, weak s] in
            guard let self = self, let s = s, !s.ready, !s.closed else { return }
            self.closeSession(s)
        }
        conn.start(queue: queue)
    }

    /// 認可済みの接続を現在のクライアントにする (古いクライアントは置き換える)
    private func activate(_ s: Session) {
        lock.lock()
        let old = client
        generation += 1
        s.gen = generation
        client = s.conn
        clientReady = false
        pendingVideoFrames = 0
        pendingAudioBytes = 0
        networkDropped = 0
        measureVideo = !s.loopback && role == .main
        limitVideoQueue = false
        videoSendTimes.removeAll()
        videoSample = VideoSendSample()
        lock.unlock()

        if let prev = activeSession {
            prev.closed = true
            prev.timeout?.cancel()
            prev.timeout = nil
        }
        activeSession = s
        s.isClient = true
        old?.cancel()

        if role == .main {
            security.setAuthorizedIP(s.loopback ? nil : s.ip)
        }
        if s.ready {
            markClientReady(s)
        }
    }

    private func markClientReady(_ s: Session) {
        lock.lock()
        let current = (generation == s.gen)
        if current { clientReady = true }
        lock.unlock()
        guard current else { return }
        onClientConnected?(s.loopback ? "USB" : "Wi-Fi")
    }

    private func sessionReady(_ s: Session) {
        guard !s.closed, !s.ready else { return }
        s.ready = true
        receiveLoop(s)
        if s.isClient {
            markClientReady(s)
        } else if !s.authorized {
            // 認証前: authRequired の status を 1 つだけ送り、5 秒以内の auth を待つ
            sendRaw(s, status: ["authRequired": true, "authOk": false])
            let item = DispatchWorkItem { [weak self, weak s] in
                guard let self = self, let s = s, !s.authorized, !s.closed else { return }
                self.authFailed(s)
            }
            s.timeout = item
            queue.asyncAfter(deadline: .now() + 5.0, execute: item)
        }
    }

    private func authFailed(_ s: Session) {
        security.noteFailure(s.ip)
        closeSession(s)
    }

    private func authSucceeded(_ s: Session) {
        security.noteSuccess(s.ip)
        s.authorized = true
        s.timeout?.cancel()
        s.timeout = nil
        pendings.removeAll { $0 === s }
        activate(s)
    }

    /// 接続を閉じて後始末する (冪等)
    private func closeSession(_ s: Session) {
        if s.closed { return }
        s.closed = true
        s.timeout?.cancel()
        s.timeout = nil
        pendings.removeAll { $0 === s }
        if s.isClient && activeSession === s {
            dropClient(generation: s.gen)
        } else {
            s.conn.cancel()
        }
    }

    /// generation が nil なら無条件、それ以外は一致した場合のみ切断扱いにする (queue 上)
    private func dropClient(generation gen: Int?) {
        lock.lock()
        if let g = gen, g != generation {
            lock.unlock()
            return
        }
        let conn = client
        let wasConnected = clientReady
        client = nil
        clientReady = false
        pendingVideoFrames = 0
        pendingAudioBytes = 0
        measureVideo = false
        limitVideoQueue = false
        videoSendTimes.removeAll()
        videoSample = VideoSendSample()
        lock.unlock()

        if let a = activeSession {
            a.closed = true
            a.timeout?.cancel()
            a.timeout = nil
            activeSession = nil
        }
        if role == .main {
            security.setAuthorizedIP(nil)
        }
        conn?.cancel()
        if wasConnected {
            onClientDisconnected?()
        }
    }

    private func receiveLoop(_ s: Session) {
        s.conn.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, isComplete, error in
            guard let self = self, !s.closed else { return }
            if let data = data, !data.isEmpty, !self.ignoreIncoming {
                s.parser.append(data)
                while let msg = s.parser.next() {
                    self.handleMessage(s, msg)
                    if s.closed { break }
                }
            }
            if s.closed { return }
            if error != nil || isComplete {
                self.closeSession(s)
                return
            }
            self.receiveLoop(s)
        }
    }

    private func handleMessage(_ s: Session, _ msg: (type: UInt8, payload: Data)) {
        guard msg.type == PacketType.control,
              let obj = try? JSONSerialization.jsonObject(with: msg.payload) as? [String: Any] else { return }

        if !s.authorized {
            // 認証前は auth 以外を無視する (pong も返さない)
            guard let a = obj["auth"] as? [String: Any] else { return }
            let code = (a["code"] as? String) ?? ""
            if security.verify(code) {
                authSucceeded(s)
            } else {
                authFailed(s)
            }
            return
        }
        // 認可済みクライアントが送る auth は無視する (他のキーは通常どおり処理)
        if let ping = obj["ping"] as? NSNumber {
            sendPong(ping)
        }
        onControl?(obj)
    }

    /// 認証前の接続へ status を直接送る
    private func sendRaw(_ s: Session, status: [String: Any]) {
        guard JSONSerialization.isValidJSONObject(status),
              let json = try? JSONSerialization.data(withJSONObject: status) else { return }
        s.conn.send(content: Packet.encode(type: PacketType.status, payload: json),
                    completion: .contentProcessed({ _ in }))
    }

    /// Wi-Fi 側の接続 (認証待ちも含む) をすべて切る。Wi-Fi 許可をオフにしたときやコード変更時。
    func disconnectNonLoopback() {
        queue.async { [weak self] in
            guard let self = self else { return }
            let ps = self.pendings
            for p in ps where !p.loopback {
                self.closeSession(p)
            }
            if let a = self.activeSession, !a.loopback {
                self.dropClient(generation: nil)
            }
        }
    }

    /// 接続元のアドレス文字列とループバックかどうか。ループバック以外 (不明を含む) は Wi-Fi 扱い。
    private static func remoteInfo(_ endpoint: NWEndpoint) -> (ip: String, loopback: Bool) {
        guard case let .hostPort(host, _) = endpoint else {
            return ("unknown", false)
        }
        switch host {
        case .ipv4(let addr):
            return ipv4Info([UInt8](addr.rawValue))
        case .ipv6(let addr):
            let b = [UInt8](addr.rawValue)
            guard b.count == 16 else { return ("\(addr)", false) }
            let mapped = b[0..<10].allSatisfy { $0 == 0 } && b[10] == 0xFF && b[11] == 0xFF
            if mapped {
                // ::ffff:a.b.c.d (IPv4 射影アドレス)
                return ipv4Info(Array(b[12..<16]))
            }
            let loop = b[0..<15].allSatisfy { $0 == 0 } && b[15] == 1
            let text = b.map { String(format: "%02x", $0) }.joined()
            return (text, loop)
        case .name(let name, _):
            return (name, name == "localhost")
        @unknown default:
            return ("\(host)", false)
        }
    }

    private static func ipv4Info(_ b: [UInt8]) -> (ip: String, loopback: Bool) {
        guard b.count == 4 else { return ("unknown", false) }
        let text = "\(b[0]).\(b[1]).\(b[2]).\(b[3])"
        return (text, b[0] == 127)
    }

    // MARK: - 送信 (どのスレッドからでも可)

    /// 映像フレームを送信する。破棄した場合は false。
    /// キーフレーム以外は、前のフレームが未送信 (送信完了前) だと破棄する。キーフレームは破棄しない。
    /// captureTime (ホスト時計の秒) を渡すと、送信する場合に限り直前に type 5 のタイミングパケットを送る。
    func sendVideo(_ data: Data, isKey: Bool, hevc: Bool = false, captureTime: Double? = nil) -> Bool {
        lock.lock()
        guard let conn = client, clientReady else {
            lock.unlock()
            return false
        }
        if (!isKey && pendingVideoFrames >= 1) || (limitVideoQueue && pendingVideoFrames >= 2) {
            networkDropped += 1
            if measureVideo { videoSample.droppedFrames += 1 }
            lock.unlock()
            return false
        }
        pendingVideoFrames += 1
        let gen = generation
        let measure = measureVideo
        let started = measure ? ProcessInfo.processInfo.systemUptime : 0
        let bytes = data.count
        if measure { videoSendID &+= 1 }
        let sendID = videoSendID
        if measure {
            videoSendTimes[sendID] = started
            videoSample.pendingBytes += bytes
        }

        // タイミングと映像の順序を保つため、lock を持ったまま続けて送信キューへ積む
        if let ct = captureTime, ct.isFinite {
            var payload = Data()
            payload.reserveCapacity(16)
            StreamServer.appendDouble(&payload, ct)
            StreamServer.appendDouble(&payload, StreamServer.hostSeconds())
            conn.send(content: Packet.encode(type: PacketType.timing, payload: payload),
                      completion: .contentProcessed({ _ in }))
        }
        let packet = Packet.encode(type: hevc ? PacketType.videoHevc : PacketType.video, payload: data)
        conn.send(content: packet, completion: .contentProcessed({ [weak self] error in
            guard let self = self else { return }
            self.lock.lock()
            if self.generation == gen {
                self.pendingVideoFrames = max(0, self.pendingVideoFrames - 1)
                if measure, self.videoSendTimes.removeValue(forKey: sendID) != nil {
                    self.videoSample.pendingBytes -= bytes
                    self.videoSample.slowestSend = max(self.videoSample.slowestSend,
                                                      ProcessInfo.processInfo.systemUptime - started)
                    if error == nil { self.videoSample.sentBytes += bytes }
                }
            }
            self.lock.unlock()
        }))
        lock.unlock()
        return true
    }

    func setAdaptiveVideoQueue(_ enabled: Bool) {
        lock.lock()
        limitVideoQueue = enabled && measureVideo
        lock.unlock()
    }

    func takeVideoSendSample(now: Double) -> VideoSendSample {
        lock.lock()
        defer { lock.unlock() }
        var sample = videoSample
        if let oldest = videoSendTimes.values.min() {
            sample.pendingSeconds = max(0, now - oldest)
        }
        videoSample = VideoSendSample(pendingBytes: sample.pendingBytes)
        return sample
    }

    /// 接続してから送信側で破棄した映像フレーム数
    func networkDroppedFrames() -> Int {
        lock.lock()
        defer { lock.unlock() }
        return networkDropped
    }

    /// ping への応答 (type 4)。破棄しない。
    func sendPong(_ value: NSNumber) {
        let obj: [String: Any] = ["pong": value, "t": StreamServer.hostSeconds()]
        guard JSONSerialization.isValidJSONObject(obj),
              let json = try? JSONSerialization.data(withJSONObject: obj) else { return }
        lock.lock()
        guard let conn = client, clientReady else {
            lock.unlock()
            return
        }
        lock.unlock()
        conn.send(content: Packet.encode(type: PacketType.pong, payload: json),
                  completion: .contentProcessed({ _ in }))
    }

    /// クライアントが接続済みか
    var hasClient: Bool {
        lock.lock()
        defer { lock.unlock() }
        return client != nil && clientReady
    }

    /// クライアントを切断する (リスナーは維持)。どのスレッドからでも可。
    func disconnectClient() {
        queue.async { [weak self] in
            self?.dropClient(generation: nil)
        }
    }

    /// 音声 (type 6)。未送信が audioCapMs 分を超えているときは (新しい方を) 破棄する。
    func sendAudio(captureTime: Double, sampleRate: Int, pcm: Data) {
        guard captureTime.isFinite, sampleRate > 0, !pcm.isEmpty else { return }
        let limit = max(1000, sampleRate * 2 * audioCapMs / 1000)   // audioCapMs 分の Int16 モノラル
        lock.lock()
        guard let conn = client, clientReady else {
            lock.unlock()
            return
        }
        if pendingAudioBytes > limit {
            lock.unlock()
            return
        }
        pendingAudioBytes += pcm.count
        let gen = generation
        lock.unlock()

        var payload = Data()
        payload.reserveCapacity(13 + pcm.count)
        StreamServer.appendDouble(&payload, captureTime)
        let sr = UInt32(truncatingIfNeeded: sampleRate)
        payload.append(UInt8((sr >> 24) & 0xFF))
        payload.append(UInt8((sr >> 16) & 0xFF))
        payload.append(UInt8((sr >> 8) & 0xFF))
        payload.append(UInt8(sr & 0xFF))
        payload.append(1)
        payload.append(pcm)
        let size = pcm.count
        conn.send(content: Packet.encode(type: PacketType.audio, payload: payload),
                  completion: .contentProcessed({ [weak self] _ in
            guard let self = self else { return }
            self.lock.lock()
            if self.generation == gen {
                self.pendingAudioBytes = max(0, self.pendingAudioBytes - size)
            }
            self.lock.unlock()
        }))
    }

    static func hostSeconds() -> Double {
        return CMTimeGetSeconds(CMClockGetTime(CMClockGetHostTimeClock()))
    }

    private static func appendDouble(_ data: inout Data, _ v: Double) {
        let bits = v.bitPattern
        for shift in stride(from: 56, through: 0, by: -8) {
            data.append(UInt8((bits >> UInt64(shift)) & 0xFF))
        }
    }

    /// ステータス JSON を送信する (破棄しない)
    func sendStatus(_ status: [String: Any]) {
        // 送り先が無いときは JSON を作らない (大きい辞書の直列化を省く)
        lock.lock()
        guard let conn = client, clientReady else {
            lock.unlock()
            return
        }
        lock.unlock()
        guard JSONSerialization.isValidJSONObject(status),
              let json = try? JSONSerialization.data(withJSONObject: status) else { return }
        let packet = Packet.encode(type: PacketType.status, payload: json)
        conn.send(content: packet, completion: .contentProcessed({ _ in }))
    }
}


/// Read-only identification over USB. Loopback only, with no effect on the
/// main/audio listeners or their current clients.
final class USBIdentityListener {
    private let queue = DispatchQueue(label: "phonecam.usb.identity")
    private var listener: NWListener?
    private let payload: Data

    init(id: String) {
        payload = Data((id + "\n").utf8)
        let params = NWParameters.tcp
        params.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: 5557)
        guard let listener = try? NWListener(using: params) else { return }
        self.listener = listener
        listener.newConnectionHandler = { [weak self] conn in
            guard let self else { conn.cancel(); return }
            conn.stateUpdateHandler = { [weak conn, payload = self.payload] state in
                guard let conn, case .ready = state else { return }
                conn.send(content: payload, completion: .contentProcessed { _ in conn.cancel() })
            }
            conn.start(queue: self.queue)
            self.queue.asyncAfter(deadline: .now() + 2) { conn.cancel() }
        }
        listener.start(queue: queue)
    }

    deinit { listener?.cancel() }
}
