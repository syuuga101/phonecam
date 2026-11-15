import Foundation
import Network
import UIKit

struct WirelessIdentity {
    let id: String

    /// PC の一覧に出す名前 (設定で変更可。未設定なら機種名)
    var name: String { DeviceName.current }
    var model: String { DeviceName.modelName }

    init() {
        let key = "wirelessPairingDeviceID"
        let defaults = UserDefaults.standard
        let uuid = defaults.string(forKey: key).flatMap { UUID(uuidString: $0) } ?? UUID()
        id = uuid.uuidString.lowercased()
        defaults.set(id, forKey: key)
    }

    var service: NWListener.Service {
        // TXT の値は 63 バイト以内 (UTF-8 の文字境界で切る)
        let fields = ["id=\(id)", "name=\(DeviceName.truncate(name, maxBytes: 63))",
                      "model=\(DeviceName.truncate(model, maxBytes: 63))", "v=2"]
        let txt = fields.reduce(into: Data()) { record, field in
            let bytes = Array(field.utf8.prefix(255))
            record.append(UInt8(bytes.count))
            record.append(contentsOf: bytes)
        }
        return NWListener.Service(name: "PhoneCam-\(id)", type: "_phonecam._tcp", txtRecord: txt)
    }

    static func addresses(wifiOnly: Bool = false) -> [String] {
        var addrs: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&addrs) == 0 else { return [] }
        defer { freeifaddrs(addrs) }
        var addresses: [String] = []
        var cursor = addrs
        while let ptr = cursor {
            let interface = ptr.pointee
            cursor = interface.ifa_next
            let flags = interface.ifa_flags
            guard flags & UInt32(IFF_UP) != 0, flags & UInt32(IFF_LOOPBACK) == 0,
                  !wifiOnly || String(cString: interface.ifa_name) == "en0",
                  let address = interface.ifa_addr, address.pointee.sa_family == UInt8(AF_INET) else { continue }
            var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            if getnameinfo(address, socklen_t(address.pointee.sa_len), &host, socklen_t(host.count),
                           nil, 0, NI_NUMERICHOST) == 0 {
                let ip = String(cString: host)
                if !addresses.contains(ip) { addresses.append(ip) }
            }
        }
        return addresses
    }
}

struct WirelessInvite: Identifiable {
    let name: String
    let addresses: [IPv4Address]
    let port: NWEndpoint.Port
    let token: String
    /// PC の静的公開鍵の指紋 (QR に入っている)。つないだ先の鍵がこれと違えば、何も渡さずに切る
    let fingerprint: String
    var id: String { token }

    static func isLocal(_ address: IPv4Address) -> Bool {
        let b = [UInt8](address.rawValue)
        guard b.count == 4 else { return false }
        return b[0] == 10 || (b[0] == 172 && (16...31).contains(b[1])) || (b[0] == 192 && b[1] == 168)
            || (b[0] == 169 && b[1] == 254) || (b[0] == 100 && (64...127).contains(b[1]))
    }

    /// 指紋: base32 (A-Z・2-7) の 26 文字
    static func isFingerprint(_ text: String) -> Bool {
        return text.utf8.count == 26 && text.utf8.allSatisfy { (65...90).contains($0) || (50...55).contains($0) }
    }

    init?(_ text: String) {
        guard text.utf8.count <= 4096, let url = URLComponents(string: text),
              url.scheme == "phonecam", url.host == "pair", url.path.isEmpty,
              let query = url.queryItems else { return nil }
        func value(_ key: String) -> String? { query.first(where: { $0.name == key })?.value }
        guard value("v") == "2", let token = value("token"), token.utf8.count == 32,
              token.utf8.allSatisfy({ (48...57).contains($0) || (65...90).contains($0)
                  || (97...122).contains($0) || $0 == 45 || $0 == 95 }),
              let fp = value("fp"), WirelessInvite.isFingerprint(fp),
              let rawPort = value("port").flatMap({ UInt16($0) }), rawPort > 0,
              let port = NWEndpoint.Port(rawValue: rawPort) else { return nil }
        // 悪意ある QR で情報を外部へ送らせないよう、LAN 内の IPv4 だけを許可する。
        let addresses = Array((value("ips") ?? "").split(separator: ",")
            .compactMap { IPv4Address(String($0)) }.filter(WirelessInvite.isLocal).prefix(16))
        guard !addresses.isEmpty else { return nil }
        self.name = String((value("name") ?? "").prefix(128))
        self.addresses = addresses
        self.port = port
        self.token = token
        self.fingerprint = fp
    }
}

/// QR でのペアリング (プロトコル v2)。QR の順にアドレスを試す。
/// つないだ先の PC の鍵の指紋が QR のものと合うときだけ、こちらの鍵とトークンを暗号化して渡す (PIN やコードは送らない)。
final class WirelessPairClient {
    private let queue = DispatchQueue(label: "phonecam.wireless.pair")
    private var connection: NWConnection?
    private var timeout: DispatchWorkItem?
    private var attempt = 0
    private var finished = false
    private var parser = PacketParser()
    private var initiator: QrInitiator?
    private var stage = 0

    /// 成功したら PC の鍵を pinned に覚えてから completion(true)
    func send(_ invite: WirelessInvite, identity: WirelessIdentity, secure: DeviceIdentity, pinned: PinnedPCStore,
              completion: @escaping (Bool) -> Void) {
        queue.async { self.tryAddress(0, invite: invite, identity: identity, secure: secure, pinned: pinned, completion: completion) }
    }

    func cancel() {
        queue.async { self.finished = true; self.clearAttempt() }
    }

    private func clearAttempt() {
        timeout?.cancel()
        timeout = nil
        connection?.stateUpdateHandler = nil
        connection?.cancel()
        connection = nil
    }

    private func tryAddress(_ index: Int, invite: WirelessInvite, identity: WirelessIdentity, secure: DeviceIdentity,
                            pinned: PinnedPCStore, completion: @escaping (Bool) -> Void) {
        guard !finished else { return }
        clearAttempt()
        guard index < invite.addresses.count else {
            finished = true
            DispatchQueue.main.async { completion(false) }
            return
        }
        attempt += 1
        let generation = attempt
        let next = { [weak self] in
            guard let self, self.attempt == generation, !self.finished else { return }
            self.tryAddress(index + 1, invite: invite, identity: identity, secure: secure, pinned: pinned,
                            completion: completion)
        }
        guard let initiator = try? QrInitiator(identity: secure, expectedFingerprint: invite.fingerprint,
                                               token: invite.token) else {
            finished = true
            DispatchQueue.main.async { completion(false) }
            return
        }
        self.initiator = initiator
        parser = PacketParser()
        parser.maxPayload = PairRequestRules.maxPreAuthBytes
        stage = 0
        let conn = NWConnection(host: .ipv4(invite.addresses[index]), port: invite.port, using: .tcp)
        connection = conn
        let deadline = DispatchWorkItem(block: next)
        timeout = deadline
        queue.asyncAfter(deadline: .now() + 2, execute: deadline)
        conn.stateUpdateHandler = { [weak self, weak conn] state in
            guard let self, let conn, self.connection === conn else { return }
            switch state {
            case .ready:
                // つながったら、ハンドシェイク全体の期限 (8 秒) に切り替える
                self.timeout?.cancel()
                let whole = DispatchWorkItem(block: next)
                self.timeout = whole
                self.queue.asyncAfter(deadline: .now() + 8, execute: whole)
                conn.send(content: Packet.encode(type: PacketType.control, payload: initiator.start()),
                          completion: .contentProcessed { error in
                    if error != nil { next() }
                })
                self.receive(conn, initiator: initiator, identity: identity, pinned: pinned, next: next,
                             completion: completion)
            case .failed, .cancelled:
                next()
            default:
                break
            }
        }
        conn.start(queue: queue)
    }

    private func receive(_ conn: NWConnection, initiator: QrInitiator, identity: WirelessIdentity, pinned: PinnedPCStore,
                         next: @escaping () -> Void, completion: @escaping (Bool) -> Void) {
        conn.receive(minimumIncompleteLength: 1, maximumLength: 4096) { [weak self, weak conn] data, _, complete, error in
            guard let self, let conn, self.connection === conn else { return }
            if let data = data, !data.isEmpty {
                self.parser.append(data)
                while let msg = self.parser.next() {
                    if !self.handle(msg, conn: conn, initiator: initiator, identity: identity, pinned: pinned,
                                    completion: completion) {
                        next()
                        return
                    }
                    if self.finished { return }
                }
                if self.parser.overflowed {
                    next()
                    return
                }
            }
            if complete || error != nil {
                next()
            } else {
                self.receive(conn, initiator: initiator, identity: identity, pinned: pinned, next: next,
                             completion: completion)
            }
        }
    }

    /// 1 メッセージを進める。false なら、このアドレスでの試行は失敗 (次のアドレスへ)
    private func handle(_ msg: (type: UInt8, payload: Data), conn: NWConnection, initiator: QrInitiator,
                        identity: WirelessIdentity, pinned: PinnedPCStore, completion: @escaping (Bool) -> Void) -> Bool {
        switch stage {
        case 0:
            // q2: PC の鍵。指紋が QR と違えば、ここで終わり (何も渡さない)
            guard msg.type == PacketType.control, (try? initiator.onQ2(msg.payload)) != nil else { return false }
            stage = 1
            return true
        case 1:
            // q3: PC の確認。合えば、こちらの鍵とトークンを暗号化して渡す
            guard msg.type == PacketType.control, let hs = SecureMessage.parse(msg.payload),
                  (try? initiator.checkConfirm(hs)) != nil,
                  let frame = try? initiator.identityFrame(id: identity.id, name: identity.name, model: identity.model,
                                                           addresses: WirelessIdentity.addresses()) else { return false }
            conn.send(content: frame, completion: .contentProcessed { _ in })
            stage = 2
            return true
        case 2:
            guard msg.type == SecureChannel.messageType, let reply = try? initiator.openReply(msg.payload),
                  let pub = initiator.pcPub else { return false }
            pinned.add(pub: pub, name: PairRequestRules.sanitizeName(reply.name), id: reply.id)
            finished = true
            clearAttempt()
            DispatchQueue.main.async { completion(true) }
            return true
        default:
            return false
        }
    }
}
