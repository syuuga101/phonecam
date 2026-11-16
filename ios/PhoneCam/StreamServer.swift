import Foundation
import Network
import CoreMedia

/// TCP サーバ。メイン (5555) と音声専用チャンネル (5556) の両方にこのクラスを使う。
/// プロトコル v2: どの接続も最初にハンドシェイク (Secure.swift) で相手の鍵を確かめる。Wi-Fi は成立したあと全メッセージを
/// AEAD で包む。USB (usbmux 経由のループバック) は認証だけして平文のまま。v1 の PC との Wi-Fi は受け付けない。
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
        if role == .audio {
            // 映像のセッションが変わったら、古いセッションの音声の接続は閉じる (新しいトークンでつなぎ直させる)
            security.addBindingObserver { [weak self] in
                self?.queue.async {
                    guard let self = self else { return }
                    for p in self.pendings { self.closeSession(p) }
                    self.dropClient(generation: nil)
                }
            }
        }
    }

    var onControl: (([String: Any]) -> Void)?
    var onClientConnected: ((String) -> Void)?
    var onClientDisconnected: (() -> Void)?
    /// PC のコード方式ペアリング (プロトコル v2): 6 桁の照合コードを画面に出す合図。
    /// respond に "shown" か "background" を渡すと PC へ「iPhone に出ている / アプリが前面にない」を知らせる。
    /// respond はどのスレッドからでも呼べる。
    var onPairShow: ((_ id: String, _ name: String, _ ip: String, _ code: String, _ respond: @escaping (String) -> Void) -> Void)?
    /// ポップアップを閉じる (要求した PC が切れた / 時間切れ / コードが合った / 拒否)
    var onPairDismiss: ((_ id: String) -> Void)?
    /// ペアリングが済んで PC の鍵を覚えた (PC 名)
    var onPaired: ((_ name: String) -> Void)?
    /// v1 の PC が USB でつないできた (平文のまま受け付ける。警告を出す)
    var onLegacyPeer: (() -> Void)?
    /// ペアリングの自己紹介に使う、この iPhone の ID・名前・機種名・アドレス
    var deviceInfo: (() -> (id: String, name: String, model: String, addresses: [String]))?

    private let queue = DispatchQueue(label: "phonecam.server")
    private var listener: NWListener?
    private var listenerStartedAt = Date.distantPast
    private var running = false
    private var healthTimer: DispatchSourceTimer?

    // 以下は lock で保護 (どのスレッドからも送信できるようにする)
    private let lock = NSLock()
    private var client: NWConnection?
    private var clientChannel: SecureChannel?   // Wi-Fi で認証済みのクライアントへ送るものを包む (seal と送信は lock 内で続けて行う)
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
        enum Phase {
            case first        // 最初のメッセージ待ち
            case pairing      // コード方式ペアリングの途中
            case connecting   // 通常接続のハンドシェイクの途中
            case established  // 認証済み
        }

        let conn: NWConnection
        let ip: String
        let loopback: Bool
        var authorized: Bool
        var ready = false
        var closed = false
        var isClient = false
        var pairID: String?      // コードのポップアップを出している間の ID
        var gen = 0
        var parser = PacketParser()
        var timeout: DispatchWorkItem?
        var legacyTimer: DispatchWorkItem?
        var expires = TimeInterval.infinity     // この時刻 (systemUptime) を過ぎた認証前の接続は閉じる
        var phase = Phase.first
        var channel: SecureChannel?             // Wi-Fi で認証が済んだあとの暗号化 (USB・v1 は nil)
        var pair: PairResponder?
        var pairStage = 0        // 1: m2 を送った / 2: コード表示中 (confirm 待ち) / 3: confirm 済み (自己紹介待ち)
        var connect: ConnectResponder?

        init(conn: NWConnection, ip: String, loopback: Bool, authorized: Bool) {
            self.conn = conn
            self.ip = ip
            self.loopback = loopback
            self.authorized = authorized
        }
    }

    // queue 上でのみ触る
    private var activeSession: Session?
    private var pendings: [Session] = []     // 認証前の接続 (クライアントにはまだならない。同じ送信元は 1 本・全部で 4 本まで)
    private var activePairSession: Session?  // コードのポップアップを出している接続 (常に 1 つだけ)
    private var pairGate = PairRequestGate()
    private static let maxPendings = 4

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
        sweepExpired()
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

    private static var uptime: TimeInterval { return ProcessInfo.processInfo.systemUptime }

    private func handleNewConnection(_ conn: NWConnection) {
        let info = StreamServer.remoteInfo(conn.endpoint)
        sweepExpired()

        if !info.loopback {
            // Wi-Fi: 許可が無い / ロックアウト中は即切断。既存のクライアントは置き換えない。
            if !security.wifiAllowed || security.isLockedOut(info.ip) {
                conn.cancel()
                return
            }
        }

        // 音声チャンネル: 映像のハンドシェイクで結び付けられた相手 (同じ IP) だけ。v1 の PC との USB だけはトークンなしで受ける
        var legacyAudio = false
        if role == .audio {
            guard let binding = security.audioBinding, binding.loopback == info.loopback,
                  info.loopback || (binding.keys != nil && binding.ip == info.ip) else {
                conn.cancel()
                return
            }
            legacyAudio = binding.keys == nil
        }

        let s = Session(conn: conn, ip: info.ip, loopback: info.loopback, authorized: legacyAudio)

        if !s.authorized {
            // 認証前の接続: 同じ送信元は 1 本だけ (古い方を閉じる)、全部で 4 本まで。ハンドシェイクは 10 秒で期限切れ
            for p in pendings where p.ip == info.ip { closeSession(p) }
            if pendings.count >= StreamServer.maxPendings {
                conn.cancel()
                return
            }
            s.parser.maxPayload = PairRequestRules.maxPreAuthBytes
            pendings.append(s)
            scheduleClose(s, after: SecureCrypto.handshakeSeconds)
        }

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
            s.phase = .established
            activate(s)
        }
        conn.start(queue: queue)
    }

    /// 認証前で期限切れ (または閉じ済み) の接続を片付ける
    private func sweepExpired() {
        let now = StreamServer.uptime
        for p in pendings where p.closed || now >= p.expires {
            closeSession(p)
        }
    }

    /// 認可済みの接続を現在のクライアントにする (古いクライアントは置き換える)
    private func activate(_ s: Session) {
        lock.lock()
        let old = client
        generation += 1
        s.gen = generation
        client = s.conn
        clientChannel = s.channel
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
        } else if role == .main && s.loopback && s.phase == .first {
            // v1 の PC は hs を送らない。USB で 1.5 秒何も来なければ、旧方式 (平文) として受け付ける
            let item = DispatchWorkItem { [weak self, weak s] in
                guard let self = self, let s = s, !s.closed, s.phase == .first else { return }
                self.becomeLegacy(s, first: nil)
            }
            s.legacyTimer = item
            queue.asyncAfter(deadline: .now() + 1.5, execute: item)
        }
    }

    /// 認証の失敗として数える。ロックアウトが始まったら、その IP の接続を (認証前も済みも) 全部切る
    private func registerFailure(_ s: Session) {
        if security.noteFailure(s.ip) {
            cutAll(ip: s.ip, except: s)
        }
    }

    private func authFailed(_ s: Session) {
        registerFailure(s)
        closeSession(s)
    }

    private func cutAll(ip: String, except keep: Session?) {
        for p in pendings where p.ip == ip && !p.loopback && p !== keep {
            closeSession(p)
        }
        if let a = activeSession, !a.loopback, a.ip == ip, a !== keep {
            dropClient(generation: nil)
        }
    }

    /// 認証が通った: クライアントにする。main なら音声チャンネルの結び付け情報も差し替える。
    private func authSucceeded(_ s: Session, keys: SessionKeys?) {
        security.noteSuccess(s.ip)
        s.authorized = true
        s.phase = .established
        s.parser.maxPayload = PacketParser.defaultMaxPayload + SecureChannel.overhead
        s.timeout?.cancel()
        s.timeout = nil
        s.legacyTimer?.cancel()
        s.legacyTimer = nil
        pendings.removeAll { $0 === s }
        // 認証が通ったので、ほかの要求のコードのポップアップは閉じる
        if let p = activePairSession, p !== s {
            closeSession(p)
        }
        activate(s)
        if role == .main {
            security.setAudioBinding(AudioBinding(ip: s.loopback ? "" : s.ip, loopback: s.loopback, keys: keys))
        }
    }

    /// v1 の PC (hs を知らない) との USB: 旧方式のまま受け付ける。Wi-Fi ではここに来ない
    private func becomeLegacy(_ s: Session, first: (type: UInt8, payload: Data)?) {
        guard s.loopback, role == .main else { closeSession(s); return }
        authSucceeded(s, keys: nil)
        onLegacyPeer?()
        if let first = first { handleMessage(s, first) }
    }

    /// 接続を閉じて後始末する (冪等)
    private func closeSession(_ s: Session) {
        if s.closed { return }
        s.closed = true
        s.timeout?.cancel()
        s.timeout = nil
        s.legacyTimer?.cancel()
        s.legacyTimer = nil
        pendings.removeAll { $0 === s }
        if activePairSession === s {
            activePairSession = nil
            if let id = s.pairID { onPairDismiss?(id) }
        }
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
        clientChannel = nil
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
            security.setAudioBinding(nil)
        }
        conn?.cancel()
        if wasConnected {
            onClientDisconnected?()
        }
    }

    private func receiveLoop(_ s: Session) {
        s.conn.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, isComplete, error in
            guard let self = self, !s.closed else { return }
            // 音声チャンネルは、認証 (トークン) が済んだあとの受信データは読み捨てる (接続の健全性のために読み続ける)
            if let data = data, !data.isEmpty, !(self.ignoreIncoming && s.authorized) {
                s.parser.append(data)
                while let msg = s.parser.next() {
                    self.handleFrame(s, msg)
                    if s.closed { break }
                }
                if !s.closed && s.parser.overflowed {
                    // 認証前の大きすぎるメッセージは失敗として数える。認証後は壊れたストリームなので切る
                    if s.authorized { self.closeSession(s) } else { self.authFailed(s) }
                    return
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

    /// 外側のメッセージ 1 つ。Wi-Fi で認証が済んだあとは暗号化されたものだけを受け、復号に失敗したら (改ざん・再送・順番違い) 切る。
    private func handleFrame(_ s: Session, _ msg: (type: UInt8, payload: Data)) {
        guard s.authorized else {
            handleHandshake(s, msg)
            return
        }
        if let channel = s.channel {
            guard msg.type == SecureChannel.messageType, let opened = try? channel.open(msg.payload) else {
                closeSession(s)
                return
            }
            handleMessage(s, (opened.type, opened.payload))
        } else {
            guard msg.type != SecureChannel.messageType else {   // 平文の接続に暗号化されたメッセージが来た
                closeSession(s)
                return
            }
            handleMessage(s, msg)
        }
    }

    /// 認証済みの接続のメッセージ
    private func handleMessage(_ s: Session, _ msg: (type: UInt8, payload: Data)) {
        guard msg.type == PacketType.control,
              let obj = try? JSONSerialization.jsonObject(with: msg.payload) as? [String: Any] else { return }
        if let ping = obj["ping"] as? NSNumber {
            sendPong(ping)
        }
        onControl?(obj)
    }

    // MARK: - ハンドシェイク (認証前。queue 上)

    private func handleHandshake(_ s: Session, _ msg: (type: UInt8, payload: Data)) {
        if role == .audio {
            handleAudioHello(s, msg)
            return
        }
        switch s.phase {
        case .first:
            s.legacyTimer?.cancel()
            s.legacyTimer = nil
            handleFirst(s, msg)
        case .pairing:
            handlePairing(s, msg)
        case .connecting:
            handleConnecting(s, msg)
        case .established:
            break
        }
    }

    private func handleFirst(_ s: Session, _ msg: (type: UInt8, payload: Data)) {
        guard msg.type == PacketType.control, let hs = SecureMessage.parse(msg.payload) else {
            // hs ではない: v1 の PC。USB だけ旧方式のまま受け、Wi-Fi は「最新版にしてください」と返して切る
            if s.loopback {
                becomeLegacy(s, first: msg)
            } else {
                sendHS(s, [("err", "version")], thenClose: true)
            }
            return
        }
        switch hs["mode"] as? String {
        case "pair": beginPair(s, msg.payload)
        case "connect": beginConnect(s, msg.payload)
        default: sendHS(s, [("err", "bad")], thenClose: true)
        }
    }

    // 通常接続 -----------------------------------------------------------------

    private func beginConnect(_ s: Session, _ payload: Data) {
        do {
            let responder = try ConnectResponder(identity: security.identity)
            let pinned = security.pinned
            let c2 = try responder.onC1(payload, lookup: { pinned.lookup($0) })
            s.connect = responder
            s.phase = .connecting
            sendControl(s, c2)
            scheduleClose(s, after: SecureCrypto.handshakeSeconds)
        } catch {
            sendHS(s, [("err", (error as? HSFailure)?.reply ?? "bad")], thenClose: true)
        }
    }

    private func handleConnecting(_ s: Session, _ msg: (type: UInt8, payload: Data)) {
        guard msg.type == PacketType.control, let hs = SecureMessage.parse(msg.payload), let responder = s.connect,
              let keys = responder.keys else {
            closeSession(s)
            return
        }
        do {
            try responder.checkConfirm(hs)
        } catch {
            sendHS(s, [("err", "auth")], thenClose: true)
            registerFailure(s)
            return
        }
        // こちらの確認は平文で返す (このあとから Wi-Fi は暗号化)。送信の順序は NWConnection が守る
        sendControl(s, responder.confirmMessage())
        s.channel = s.loopback ? nil : SecureChannel.session(keys)
        authSucceeded(s, keys: keys)
    }

    // コード方式ペアリング -----------------------------------------------------------

    private func beginPair(_ s: Session, _ payload: Data) {
        if !s.loopback {
            let decision = pairGate.evaluate(ip: s.ip, activeIP: activePairSession?.ip, now: StreamServer.uptime)
            guard decision == .accept else {
                sendHS(s, [("err", decision == .busy ? "busy" : "limited")], thenClose: true)
                return
            }
        }
        do {
            let responder = try PairResponder()
            let m2 = try responder.onM1(payload)
            s.pair = responder
            s.phase = .pairing
            s.pairStage = 1
            sendControl(s, m2)
            scheduleClose(s, after: SecureCrypto.handshakeSeconds)
        } catch {
            sendHS(s, [("err", "bad")], thenClose: true)
        }
    }

    private func handlePairing(_ s: Session, _ msg: (type: UInt8, payload: Data)) {
        guard let responder = s.pair else {
            closeSession(s)
            return
        }
        switch s.pairStage {
        case 1:
            // m3: PC の鍵と約束 (commit) を確かめて、コードを作る
            guard msg.type == PacketType.control else { closeSession(s); return }
            do {
                try responder.onM3(msg.payload)
            } catch {
                sendHS(s, [("err", (error as? HSFailure)?.reply ?? "bad")], thenClose: true)
                registerFailure(s)
                return
            }
            s.pairStage = 2
            if s.loopback {
                // USB: 接続そのものが「このコンピュータを信頼」で守られているので、コードの入力は無い
                scheduleClose(s, after: SecureCrypto.handshakeSeconds)
                return
            }
            showPairCode(s, responder)
        case 2:
            guard msg.type == PacketType.control, let hs = SecureMessage.parse(msg.payload) else {
                closeSession(s)
                return
            }
            if let abort = hs["abort"] as? String {
                // PC で入力したコードが違った: 失敗として数える。やめた (cancel) は数えない
                if abort == "mismatch" { registerFailure(s) }
                closeSession(s)
                return
            }
            do {
                try responder.checkConfirm(hs)
            } catch {
                sendHS(s, [("err", "auth")], thenClose: true)
                registerFailure(s)
                return
            }
            s.pairStage = 3
            if activePairSession === s {
                activePairSession = nil
                if let id = s.pairID { onPairDismiss?(id) }   // コードが合ったのでポップアップは閉じる
            }
            scheduleClose(s, after: SecureCrypto.handshakeSeconds)
        case 3:
            guard msg.type == SecureChannel.messageType else { closeSession(s); return }
            finishPair(s, responder, body: msg.payload)
        default:
            closeSession(s)
        }
    }

    private func showPairCode(_ s: Session, _ responder: PairResponder) {
        let id = UUID().uuidString.lowercased()
        s.pairID = id
        if let old = activePairSession, old !== s { closeSession(old) }   // 同じ PC の古い要求を置き換える
        activePairSession = s
        // ポップアップの時間が過ぎたら閉じる (失敗としては数えない)
        scheduleClose(s, after: PairRequestRules.popupSeconds)
        guard let handler = onPairShow else {
            sendHS(s, [("err", "unavailable")], thenClose: true)
            return
        }
        handler(id, PairRequestRules.sanitizeName(responder.pcName), s.ip, responder.code) { [weak self, weak s] state in
            self?.queue.async {
                guard let self = self, let s = s, !s.closed, s.pairID == id else { return }
                if state == "shown" || state == "background" {
                    self.sendHS(s, [("ui", state)], thenClose: false)
                } else {
                    self.sendHS(s, [("err", "unavailable")], thenClose: true)
                }
            }
        }
    }

    /// PC の自己紹介 (AEAD) を開いて鍵を覚え、こちらの自己紹介を返す
    private func finishPair(_ s: Session, _ responder: PairResponder, body: Data) {
        guard let info = deviceInfo?() else { closeSession(s); return }
        do {
            let peer = try responder.openPeer(body)
            let reply = try responder.replyFrame(identity: security.identity, id: info.id, name: info.name,
                                                 model: info.model, addresses: info.addresses)
            let name = PairRequestRules.sanitizeName(peer.name)
            security.pinned.add(pub: peer.pub, name: name, id: peer.id)
            security.noteSuccess(s.ip)
            scheduleClose(s, after: PairRequestRules.rejectLinger)
            s.conn.send(content: reply, completion: .contentProcessed({ [weak self, weak s] _ in
                self?.queue.async {
                    if let s = s { self?.closeSession(s) }
                }
            }))
            onPaired?(name)
        } catch {
            sendHS(s, [("err", "auth")], thenClose: true)
            registerFailure(s)
        }
    }

    /// 「拒否」: 要求した PC へ declined を返して、その接続を閉じる。どのスレッドからでも可。
    func declinePair(id: String) {
        queue.async { [weak self] in
            guard let self = self, let s = self.activePairSession, s.pairID == id, !s.closed else { return }
            self.sendHS(s, [("err", "declined")], thenClose: true)
        }
    }

    // 音声チャンネル -----------------------------------------------------------------

    /// 音声チャンネルの最初のメッセージ: 映像のセッションのトークン (Wi-Fi は音声用の鍵で包まれている)
    private func handleAudioHello(_ s: Session, _ msg: (type: UInt8, payload: Data)) {
        guard let binding = security.audioBinding, let keys = binding.keys, binding.loopback == s.loopback,
              s.loopback || binding.ip == s.ip else {
            closeSession(s)
            return
        }
        var type = msg.type
        var payload = msg.payload
        var channel: SecureChannel?
        if !s.loopback {
            let audioChannel = SecureChannel.session(keys, audio: true)
            guard type == SecureChannel.messageType, let opened = try? audioChannel.open(payload) else {
                closeSession(s)
                return
            }
            type = opened.type
            payload = opened.payload
            channel = audioChannel
        }
        guard type == PacketType.control, let hs = SecureMessage.parse(payload),
              let text = hs["audio_tok"] as? String, let token = SecureHex.decode(text, bytes: 16),
              SecureCrypto.constantTimeEqual(token, keys.audioTok) else {
            if !s.loopback { registerFailure(s) }
            closeSession(s)
            return
        }
        s.channel = channel
        s.authorized = true
        s.phase = .established
        s.parser.maxPayload = PacketParser.defaultMaxPayload + SecureChannel.overhead
        s.timeout?.cancel()
        s.timeout = nil
        pendings.removeAll { $0 === s }
        activate(s)
    }

    // 送信 (認証前) -------------------------------------------------------------------

    /// seconds 秒後に、認証の失敗としては数えずに接続を閉じる (前の予約は取り消す)
    private func scheduleClose(_ s: Session, after seconds: TimeInterval) {
        s.timeout?.cancel()
        s.expires = StreamServer.uptime + seconds
        let item = DispatchWorkItem { [weak self, weak s] in
            guard let self = self, let s = s else { return }
            self.closeSession(s)
        }
        s.timeout = item
        queue.asyncAfter(deadline: .now() + seconds, execute: item)
    }

    /// 認証前の接続へ、ハンドシェイクのメッセージ ({"hs": {...}}) を平文で送る。thenClose なら送ってから閉じる (送れなくても必ず閉じる)
    private func sendHS(_ s: Session, _ fields: [(String, Any)], thenClose: Bool) {
        if thenClose { scheduleClose(s, after: PairRequestRules.rejectLinger) }
        s.conn.send(content: Packet.encode(type: PacketType.control, payload: SecureMessage.encode(fields)),
                    completion: .contentProcessed({ [weak self, weak s] _ in
            guard thenClose else { return }
            self?.queue.async {
                if let s = s { self?.closeSession(s) }
            }
        }))
    }

    /// 認証前の接続へ、作り済みの本体 (ハンドシェイクの JSON) を control として送る
    private func sendControl(_ s: Session, _ payload: Data) {
        s.conn.send(content: Packet.encode(type: PacketType.control, payload: payload),
                    completion: .contentProcessed({ _ in }))
    }

    /// Wi-Fi 側の接続 (認証待ちも含む) をすべて切る。Wi-Fi 許可をオフにしたときやペアリングの解除のとき。
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
    static func remoteInfo(_ endpoint: NWEndpoint) -> (ip: String, loopback: Bool) {
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
            let loop = LoopbackAddress.isLoopback(b)
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
        return (text, LoopbackAddress.isLoopback(b))
    }

    // MARK: - 送信 (どのスレッドからでも可)

    /// lock を持って呼ぶ。Wi-Fi の認証済みクライアントには暗号化 (種別も本文も包む)、USB は平文のまま。
    /// 暗号化のカウンタは送る順序と一致させる必要があるので、呼んだら続けて同じ lock の中で send する。
    private func framed(_ type: UInt8, _ payload: Data) -> Data? {
        if let channel = clientChannel {
            return try? channel.seal(type: type, payload: payload)
        }
        return Packet.encode(type: type, payload: payload)
    }

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
            if let timing = framed(PacketType.timing, payload) {
                conn.send(content: timing, completion: .contentProcessed({ _ in }))
            }
        }
        guard let packet = framed(hevc ? PacketType.videoHevc : PacketType.video, data) else {
            lock.unlock()
            conn.cancel()    // 暗号化できない (起こらない想定): 切って作り直させる
            return false
        }
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
        defer { lock.unlock() }
        guard let conn = client, clientReady, let packet = framed(PacketType.pong, json) else { return }
        conn.send(content: packet, completion: .contentProcessed({ _ in }))
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
        guard let packet = framed(PacketType.audio, payload) else {
            pendingAudioBytes = max(0, pendingAudioBytes - size)
            lock.unlock()
            return
        }
        conn.send(content: packet, completion: .contentProcessed({ [weak self] _ in
            guard let self = self else { return }
            self.lock.lock()
            if self.generation == gen {
                self.pendingAudioBytes = max(0, self.pendingAudioBytes - size)
            }
            self.lock.unlock()
        }))
        lock.unlock()
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
        let ready = client != nil && clientReady
        lock.unlock()
        guard ready, JSONSerialization.isValidJSONObject(status),
              let json = try? JSONSerialization.data(withJSONObject: status) else { return }
        lock.lock()
        defer { lock.unlock() }
        guard let conn = client, clientReady, let packet = framed(PacketType.status, json) else { return }
        conn.send(content: packet, completion: .contentProcessed({ _ in }))
    }
}


/// Read-only identification over USB (port 5557). Answers loopback peers only (usbmux arrives on lo0), with no effect on
/// the main/audio listeners or their current clients.
///
/// The listener is bound to the port only (no requiredLocalEndpoint): pinning it to 127.0.0.1 can miss usbmux
/// connections, which made the PC's reachability check fail and kept it on Wi-Fi. Non-loopback peers are dropped in
/// the handler instead. It restarts itself if the listener fails.
final class USBIdentityListener {
    static let port: UInt16 = 5557
    private let queue = DispatchQueue(label: "phonecam.usb.identity")
    private var listener: NWListener?
    private let payload: Data
    private var stopped = false

    init(id: String) {
        payload = Data((id + "\n").utf8)
        queue.async { [weak self] in self?.start() }
    }

    private func start() {
        guard !stopped, listener == nil else { return }
        let params = NWParameters.tcp
        params.allowLocalEndpointReuse = true
        guard let port = NWEndpoint.Port(rawValue: USBIdentityListener.port),
              let listener = try? NWListener(using: params, on: port) else {
            restartSoon()
            return
        }
        self.listener = listener
        listener.stateUpdateHandler = { [weak self, weak listener] state in
            switch state {
            case .failed, .cancelled:
                listener?.cancel()
                self?.queue.async {
                    guard let self, self.listener === listener else { return }
                    self.listener = nil
                    self.restartSoon()
                }
            default:
                break
            }
        }
        listener.newConnectionHandler = { [weak self] conn in
            guard let self, StreamServer.remoteInfo(conn.endpoint).loopback else { conn.cancel(); return }
            conn.stateUpdateHandler = { [weak conn, payload = self.payload] state in
                guard let conn, case .ready = state else { return }
                conn.send(content: payload, completion: .contentProcessed { _ in conn.cancel() })
            }
            conn.start(queue: self.queue)
            self.queue.asyncAfter(deadline: .now() + 2) { conn.cancel() }
        }
        listener.start(queue: queue)
    }

    private func restartSoon() {
        queue.asyncAfter(deadline: .now() + 1.0) { [weak self] in self?.start() }
    }

    deinit {
        stopped = true
        listener?.cancel()
    }
}
