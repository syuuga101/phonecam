import Foundation
import Network

/// TCP サーバ (ポート 5555)。USB (usbmux 経由のループバック) と Wi-Fi の両方を受け付ける。
final class StreamServer {
    static let port: UInt16 = 5555

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

        guard let port = NWEndpoint.Port(rawValue: StreamServer.port),
              let newListener = try? NWListener(using: params, on: port) else {
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

            if let data = data, !data.isEmpty {
                self.parser.append(data)
                while let msg = self.parser.next() {
                    if msg.type == PacketType.control {
                        if let obj = try? JSONSerialization.jsonObject(with: msg.payload) as? [String: Any] {
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
    /// キーフレーム以外は、未送信フレームが2つ以上溜まっていると破棄する。
    func sendVideo(_ data: Data, isKey: Bool, hevc: Bool = false) -> Bool {
        lock.lock()
        guard let conn = client, clientReady else {
            lock.unlock()
            return false
        }
        if !isKey && pendingVideoFrames >= 2 {
            lock.unlock()
            return false
        }
        pendingVideoFrames += 1
        let gen = generation
        lock.unlock()

        let packet = Packet.encode(type: hevc ? PacketType.videoHevc : PacketType.video, payload: data)
        conn.send(content: packet, completion: .contentProcessed({ [weak self] _ in
            guard let self = self else { return }
            self.lock.lock()
            if self.generation == gen {
                self.pendingVideoFrames = max(0, self.pendingVideoFrames - 1)
            }
            self.lock.unlock()
        }))
        return true
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
