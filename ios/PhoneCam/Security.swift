import Foundation

/// 接続の認可 (v1.5)。USB (ループバック) は信頼し、Wi-Fi は「許可」トグルとペアリングコードで守る。
/// どのスレッドからでも呼べる。
final class SecurityManager {
    private static let wifiKey = "wifiAllowed"
    private static let codeKey = "pairingCode"
    private static let maxFailures = 3
    private static let lockoutSeconds: TimeInterval = 60

    private let lock = NSLock()
    private var wifi: Bool
    private var code: String
    private var failures: [String: Int] = [:]
    private var lockedUntil: [String: Date] = [:]
    private var authorizedIPValue: String?

    init() {
        let d = UserDefaults.standard
        wifi = d.bool(forKey: SecurityManager.wifiKey)
        if let c = d.string(forKey: SecurityManager.codeKey), SecurityManager.isValid(c) {
            code = c
        } else {
            let fresh = SecurityManager.makeCode()
            code = fresh
            d.set(fresh, forKey: SecurityManager.codeKey)
        }
    }

    private static func isValid(_ c: String) -> Bool {
        return c.count == 6 && c.allSatisfy { $0 >= "0" && $0 <= "9" }
    }

    private static func makeCode() -> String {
        return String(format: "%06d", Int.random(in: 0...999_999))
    }

    // MARK: - Wi-Fi 許可

    var wifiAllowed: Bool {
        lock.lock()
        defer { lock.unlock() }
        return wifi
    }

    func setWifiAllowed(_ on: Bool) {
        lock.lock()
        wifi = on
        lock.unlock()
        UserDefaults.standard.set(on, forKey: SecurityManager.wifiKey)
    }

    // MARK: - ペアリングコード

    var pairingCode: String {
        lock.lock()
        defer { lock.unlock() }
        return code
    }

    /// 新しいコードを作って保存する。作ったコードを返す。
    @discardableResult
    func regenerateCode() -> String {
        let fresh = SecurityManager.makeCode()
        lock.lock()
        code = fresh
        failures.removeAll()
        lockedUntil.removeAll()
        lock.unlock()
        UserDefaults.standard.set(fresh, forKey: SecurityManager.codeKey)
        return fresh
    }

    /// コードの照合 (長さに依存しない比較)
    func verify(_ candidate: String) -> Bool {
        lock.lock()
        let expected = Array(code.utf8)
        lock.unlock()
        let given = Array(candidate.utf8)
        var diff = given.count ^ expected.count
        let n = min(given.count, expected.count)
        for i in 0..<n {
            diff |= Int(given[i] ^ expected[i])
        }
        return diff == 0
    }

    // MARK: - 失敗によるロックアウト

    func isLockedOut(_ ip: String) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        if let until = lockedUntil[ip] {
            if until > Date() {
                return true
            }
            lockedUntil[ip] = nil
            failures[ip] = nil
        }
        return false
    }

    func noteFailure(_ ip: String) {
        lock.lock()
        let n = (failures[ip] ?? 0) + 1
        if n >= SecurityManager.maxFailures {
            failures[ip] = nil
            lockedUntil[ip] = Date().addingTimeInterval(SecurityManager.lockoutSeconds)
        } else {
            failures[ip] = n
        }
        lock.unlock()
    }

    func noteSuccess(_ ip: String) {
        lock.lock()
        failures[ip] = nil
        lock.unlock()
    }

    // MARK: - 認証済みメインクライアントの IP (音声チャンネル用)

    func setAuthorizedIP(_ ip: String?) {
        lock.lock()
        authorizedIPValue = ip
        lock.unlock()
    }

    func isAuthorizedIP(_ ip: String) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return authorizedIPValue == ip
    }
}
