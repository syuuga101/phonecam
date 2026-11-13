import Foundation
import Network
import UIKit

struct WirelessIdentity {
    let id: String
    let name: String

    init() {
        let key = "wirelessPairingDeviceID"
        let defaults = UserDefaults.standard
        let uuid = defaults.string(forKey: key).flatMap { UUID(uuidString: $0) } ?? UUID()
        id = uuid.uuidString.lowercased()
        defaults.set(id, forKey: key)
        name = UIDevice.current.name
    }

    var service: NWListener.Service {
        let fields = ["id=\(id)", "name=\(name)", "v=1"]
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
        let addresses = (value("ips") ?? "").split(separator: ",").compactMap { IPv4Address(String($0)) }
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
                                      "name": identity.name, "code": code,
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
