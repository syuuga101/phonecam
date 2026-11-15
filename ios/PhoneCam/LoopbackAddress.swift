import Foundation

/// usbmux 経由の接続は iPhone の中では ループバック (lo0) から来る。接続元がループバックかどうかの判定 (純粋な関数)。
/// raw は IPv4 なら 4 バイト、IPv6 なら 16 バイト。::ffff:127.x.x.x (IPv4 射影) もループバックとして扱う。
enum LoopbackAddress {
    static func isLoopback(_ raw: [UInt8]) -> Bool {
        switch raw.count {
        case 4:
            return raw[0] == 127
        case 16:
            if raw[0..<15].allSatisfy({ $0 == 0 }) && raw[15] == 1 { return true }   // ::1
            let mapped = raw[0..<10].allSatisfy { $0 == 0 } && raw[10] == 0xFF && raw[11] == 0xFF
            return mapped && raw[12] == 127
        default:
            return false
        }
    }
}
