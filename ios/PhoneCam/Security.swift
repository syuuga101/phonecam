import Foundation

/// 接続の認可 (プロトコル v2)。USB (ループバック) は「このコンピュータを信頼」で守られ、Wi-Fi は「許可」トグルと
/// ペアリングで覚えた PC の鍵 (Secure.swift) で守る。PIN やコードを保存・送信する仕組みは無い。
/// どのスレッドからでも呼べる。
final class SecurityManager {
    private static let wifiKey = "wifiAllowed"
    private static let maxFailures = 3
    private static let lockoutSeconds: TimeInterval = 60

    let identity: DeviceIdentity
    let pinned: PinnedPCStore

    private let lock = NSLock()
    private var wifi: Bool
    private var failures: [String: Int] = [:]
    private var lockedUntil: [String: Date] = [:]
    private var binding: AudioBinding?
    private var bindingObservers: [() -> Void] = []

    init(storage: SecretStorage = KeychainStorage()) {
        let d = UserDefaults.standard
        wifi = d.bool(forKey: SecurityManager.wifiKey)
        identity = DeviceIdentity.load(storage)
        pinned = PinnedPCStore(storage: storage)
        // v1 の 6 桁コードは使わない (残っていれば消す)
        d.removeObject(forKey: "pairingCode")
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

    // MARK: - 失敗によるロックアウト (同じ IP からの確認の失敗・コードの入力違い)

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

    /// 失敗を数える。ロックアウトが始まったら true (呼び出し側はその IP の接続を全部切る)
    @discardableResult
    func noteFailure(_ ip: String) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        let n = (failures[ip] ?? 0) + 1
        if n >= SecurityManager.maxFailures {
            failures[ip] = nil
            lockedUntil[ip] = Date().addingTimeInterval(SecurityManager.lockoutSeconds)
            // 期限切れの記録は掃除する (メモリが増え続けない)
            let now = Date()
            lockedUntil = lockedUntil.filter { $0.value > now }
            return true
        }
        failures[ip] = n
        if failures.count > 256 { failures.removeAll() }
        return false
    }

    func noteSuccess(_ ip: String) {
        lock.lock()
        failures[ip] = nil
        lock.unlock()
    }

    // MARK: - 音声チャンネルの結び付け

    /// 映像 (5555) のハンドシェイクが済んだら呼ぶ (切れたら nil)。音声 (5556) はこの情報のトークンで結び付く。
    /// 変わるたびに observer を呼ぶ: 音声側は古いセッションの接続を閉じる。
    func setAudioBinding(_ value: AudioBinding?) {
        lock.lock()
        binding = value
        let observers = bindingObservers
        lock.unlock()
        for observer in observers { observer() }
    }

    var audioBinding: AudioBinding? {
        lock.lock()
        defer { lock.unlock() }
        return binding
    }

    func addBindingObserver(_ observer: @escaping () -> Void) {
        lock.lock()
        bindingObservers.append(observer)
        lock.unlock()
    }
}
