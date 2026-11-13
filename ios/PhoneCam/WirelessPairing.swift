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
                      "model=\(DeviceName.truncate(model, maxBytes: 63))", "v=1"]
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
    var id: String { token }

    static func isLocal(_ address: IPv4Address) -> Bool {
        let b = [UInt8](address.rawValue)
        guard b.count == 4 else { return false }
        return b[0] == 10 || (b[0] == 172 && (16...31).contains(b[1])) || (b[0] == 192 && b[1] == 168)
            || (b[0] == 169 && b[1] == 254) || (b[0] == 100 && (64...127).contains(b[1]))
    }

    init?(_ text: String) {
        guard text.utf8.count <= 4096, let url = URLComponents(string: text),
              url.scheme == "phonecam", url.host == "pair", url.path.isEmpty,
              let query = url.queryItems else { return nil }
        func value(_ key: String) -> String? { query.first(where: { $0.name == key })?.value }
        guard value("v") == "1", let token = value("token"), token.utf8.count == 32,
              token.utf8.allSatisfy({ (48...57).contains($0) || (65...90).contains($0)
                  || (97...122).contains($0) || $0 == 45 || $0 == 95 }),
              let rawPort = value("port").flatMap({ UInt16($0) }), rawPort > 0,
              let port = NWEndpoint.Port(rawValue: rawPort) else { return nil }
        // 悪意ある QR でペアリングコードを外部へ送らせないよう、LAN 内の IPv4 だけを許可する。
        let addresses = Array((value("ips") ?? "").split(separator: ",")
            .compactMap { IPv4Address(String($0)) }.filter(WirelessInvite.isLocal).prefix(16))
        guard !addresses.isEmpty else { return nil }
        self.name = String((value("name") ?? "").prefix(128))
        self.addresses = addresses
        self.port = port
        self.token = token
    }
}

/// Tries the addresses in QR order. The response acknowledges a consumed token.
final class WirelessPairClient {
    private let queue = DispatchQueue(label: "phonecam.wireless.pair")
    private var connection: NWConnection?
    private var timeout: DispatchWorkItem?
    private var attempt = 0
    private var finished = false

    func send(_ invite: WirelessInvite, identity: WirelessIdentity, code: String,
              completion: @escaping (Bool) -> Void) {
        let payload: [String: Any] = ["v": 1, "token": invite.token, "id": identity.id,
                                      "name": identity.name, "model": identity.model, "code": code,
                                      "addresses": WirelessIdentity.addresses()]
        guard var data = try? JSONSerialization.data(withJSONObject: payload) else {
            completion(false)
            return
        }
        data.append(10)
        queue.async { self.tryAddress(0, invite: invite, data: data, completion: completion) }
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

    private func tryAddress(_ index: Int, invite: WirelessInvite, data: Data,
                            completion: @escaping (Bool) -> Void) {
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
            self.tryAddress(index + 1, invite: invite, data: data, completion: completion)
        }
        let conn = NWConnection(host: .ipv4(invite.addresses[index]), port: invite.port, using: .tcp)
        connection = conn
        let deadline = DispatchWorkItem(block: next)
        timeout = deadline
        queue.asyncAfter(deadline: .now() + 2, execute: deadline)
        conn.stateUpdateHandler = { [weak self, weak conn] state in
            guard let self, let conn, self.connection === conn else { return }
            switch state {
            case .ready:
                conn.send(content: data, completion: .contentProcessed { error in
                    if error != nil { next() }
                })
                self.receiveAck(conn, collected: Data(), next: next, completion: completion)
            case .failed, .cancelled:
                next()
            default:
                break
            }
        }
        conn.start(queue: queue)
    }

    private func receiveAck(_ conn: NWConnection, collected: Data, next: @escaping () -> Void,
                            completion: @escaping (Bool) -> Void) {
        conn.receive(minimumIncompleteLength: 1, maximumLength: 128) { [weak self, weak conn] data, _, complete, error in
            guard let self, let conn, self.connection === conn else { return }
            let buffer = collected + (data ?? Data())
            if let newline = buffer.firstIndex(of: 10),
               let reply = try? JSONSerialization.jsonObject(with: Data(buffer[..<newline])) as? [String: Bool],
               reply["ok"] == true {
                self.finished = true
                self.clearAttempt()
                DispatchQueue.main.async { completion(true) }
            } else if complete || error != nil || buffer.count >= 128 {
                next()
            } else {
                self.receiveAck(conn, collected: buffer, next: next, completion: completion)
            }
        }
    }
}
