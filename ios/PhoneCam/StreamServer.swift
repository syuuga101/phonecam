import Foundation
import Network
import CoreMedia

/// TCP サーバ。メイン (5555) と音声専用チャンネル (5556) の両方にこのクラスを使う。
/// USB (usbmux 経由のループバック) と Wi-Fi の両方を受け付ける。
final class StreamServer {
    static let port: UInt16 = 5555
    static let audioPort: UInt16 = 5556

    private let port: UInt16
    /// true の間は受信データを読み捨てる (音声チャンネル用。接続の健全性のため読み続ける)
    private let ignoreIncoming: Bool
    /// 未送信の音声をこの時間 (ms) 分までに制限する
    private let audioCapMs: Int

    init(port: UInt16 = StreamServer.port, ignoreIncoming: Bool = false, audioCapMs: Int = 200) {
        self.port = port
        self.ignoreIncoming = ignoreIncoming
        self.audioCapMs = max(10, audioCapMs)
    }

    var onControl: (([String: Any]) -> Void)?
    var onClientConnected: ((String) -> Void)?
    var onClientDisconnected: (() -> Void)?

    private let queue = DispatchQueue(label: "phonecam.server")
    private var listener: NWListener?
    private var running = false

    // 以下は lock で保護 (どのスレッドからも送信できるようにする)
    private let lock = NSLock()
    private var client: NWConnection?
    private var clientReady = false
    private var generation = 0
    private var pendingVideoFrames = 0
    private var pendingAudioBytes = 0
    private var networkDropped = 0
    private var audioDropped = 0

    // 受信パーサは queue 上でのみ触る
    private var parser = PacketParser()

    // MARK: - 開始 / 停止

    func start() {
        queue.async { [weak self] in
            guard let self = self else { return }
            self.running = true
            self.startListener()
        }
    }

    func stop() {
        queue.async { [weak self] in
            guard let self = self else { return }
            self.running = false
            self.listener?.cancel()
            self.listener = nil
            self.dropClient(generation: nil)
        }
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
            case .failed(_):
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
        listener = newListener
        newListener.start(queue: queue)
    }

    private func scheduleRestart() {
        queue.asyncAfter(deadline: .now() + 1.0) { [weak self] in
            guard let self = self, self.running else { return }
            self.startListener()
        }
    }

    // MARK: - クライアント管理 (queue 上)

    private func handleNewConnection(_ conn: NWConnection) {
        // 新しい接続が古い接続を置き換える
        lock.lock()
        let old = client
        generation += 1
        let gen = generation
        client = conn
        clientReady = false
        pendingVideoFrames = 0
        pendingAudioBytes = 0
        networkDropped = 0
        audioDropped = 0
        lock.unlock()

        old?.cancel()
        parser.reset()

        let kind = StreamServer.describe(conn.endpoint)

        conn.stateUpdateHandler = { [weak self, weak conn] state in
            guard let self = self, let conn = conn else { return }
            switch state {
            case .ready:
                self.queue.async {
                    self.lock.lock()
                    let current = (self.generation == gen)
                    if current { self.clientReady = true }
                    self.lock.unlock()
                    guard current else { return }
                    self.receiveLoop(conn, generation: gen)
                    self.onClientConnected?(kind)
                }
            case .failed(_), .cancelled:
                self.queue.async {
                    self.dropClient(generation: gen)
                }
            default:
                break
            }
        }
        conn.start(queue: queue)
    }

    /// generation が nil なら無条件、それ以外は一致した場合のみ切断扱いにする
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
        lock.unlock()

        conn?.cancel()
        if wasConnected {
            onClientDisconnected?()
        }
    }

    private func receiveLoop(_ conn: NWConnection, generation gen: Int) {
        conn.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, isComplete, error in
            guard let self = self else { return }
            self.lock.lock()
            let current = (self.generation == gen)
            self.lock.unlock()
            guard current else { return }

            if let data = data, !data.isEmpty, !self.ignoreIncoming {
                self.parser.append(data)
                while let msg = self.parser.next() {
                    if msg.type == PacketType.control {
                        if let obj = try? JSONSerialization.jsonObject(with: msg.payload) as? [String: Any] {
                            if let ping = obj["ping"] as? NSNumber {
                                self.sendPong(ping)
                            }
                            self.onControl?(obj)
                        }
                    }
                }
            }
            if error != nil || isComplete {
                self.dropClient(generation: gen)
                return
            }
            self.receiveLoop(conn, generation: gen)
        }
    }

    private static func describe(_ endpoint: NWEndpoint) -> String {
        if case let .hostPort(host, _) = endpoint {
            let s = "\(host)"
            if s.hasPrefix("127.") || s == "::1" || s.hasPrefix("::ffff:127.") || s == "localhost" {
                return "USB"
            }
        }
        return "Wi-Fi"
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
        if !isKey && pendingVideoFrames >= 1 {
            networkDropped += 1
            lock.unlock()
            return false
        }
        pendingVideoFrames += 1
        let gen = generation

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
        conn.send(content: packet, completion: .contentProcessed({ [weak self] _ in
            guard let self = self else { return }
            self.lock.lock()
            if self.generation == gen {
                self.pendingVideoFrames = max(0, self.pendingVideoFrames - 1)
            }
            self.lock.unlock()
        }))
        lock.unlock()
        return true
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

    /// 音声チャンネルで破棄した音声パケット数
    func audioDroppedPackets() -> Int {
        lock.lock()
        defer { lock.unlock() }
        return audioDropped
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
            audioDropped += 1
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
        guard JSONSerialization.isValidJSONObject(status),
              let json = try? JSONSerialization.data(withJSONObject: status) else { return }
        lock.lock()
        guard let conn = client, clientReady else {
            lock.unlock()
            return
        }
        lock.unlock()
        let packet = Packet.encode(type: PacketType.status, payload: json)
        conn.send(content: packet, completion: .contentProcessed({ _ in }))
    }
}
