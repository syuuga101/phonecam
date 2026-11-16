import Foundation
import CryptoKit
import Security

// Wi-Fi の暗号化と相互認証 (プロトコル v2) の iPhone 側。
// 仕様は docs/PROTOCOL.md の「セキュリティ (v2)」。Windows 側 (windows/phonecam/core/secure.py) と同じ計算で、
// docs/test-vectors/handshake-v2.json で一致を確かめる (ios/Tests/HandshakeChecks.swift)。
// このファイルは Foundation / CryptoKit / Security だけに依存する (UI やネットワークは StreamServer 側)。

/// ハンドシェイクの失敗。reply は PC へ返すエラー名 (nil なら何も返さず切る)
enum HSFailure: Error {
    case bad          // 形式が違う・鍵が不正
    case unpaired     // PC の鍵を覚えていない
    case auth         // 確認 (HMAC) が合わない
    case version      // v1 の PC
    case declined

    var reply: String? {
        switch self {
        case .bad: return "bad"
        case .unpaired: return "unpaired"
        case .auth: return "auth"
        case .version: return "version"
        case .declined: return "declined"
        }
    }
}

// MARK: - 16 進・base32

enum SecureHex {
    static func encode(_ data: Data) -> String {
        let digits = Array("0123456789abcdef".utf8)
        var out = [UInt8]()
        out.reserveCapacity(data.count * 2)
        for byte in data {
            out.append(digits[Int(byte >> 4)])
            out.append(digits[Int(byte & 0x0F)])
        }
        return String(decoding: out, as: UTF8.self)
    }

    /// 偶数長の 16 進だけ受け付ける。それ以外は nil
    static func decode(_ text: String) -> Data? {
        let chars = Array(text.utf8)
        guard chars.count % 2 == 0 else { return nil }
        func nibble(_ c: UInt8) -> UInt8? {
            switch c {
            case 48...57: return c - 48
            case 97...102: return c - 87
            case 65...70: return c - 55
            default: return nil
            }
        }
        var out = Data()
        out.reserveCapacity(chars.count / 2)
        var index = 0
        while index < chars.count {
            guard let hi = nibble(chars[index]), let lo = nibble(chars[index + 1]) else { return nil }
            out.append(hi << 4 | lo)
            index += 2
        }
        return out
    }

    static func decode(_ text: String, bytes: Int) -> Data? {
        guard text.utf8.count == bytes * 2 else { return nil }
        return decode(text)
    }
}

// MARK: - 計算

enum SecureCrypto {
    static let infoPairSAS = Data("phonecam pair sas".utf8)
    static let infoPairKey = Data("phonecam pair key".utf8)
    static let infoSession = Data("phonecam v2 session".utf8)
    static let infoQR = Data("phonecam v2 qr".utf8)
    static let tagLength = 16
    static let handshakeSeconds: TimeInterval = 10
    static let version = 2

    static func sha256(_ parts: Data...) -> Data {
        var hasher = SHA256()
        for part in parts { hasher.update(data: part) }
        return Data(hasher.finalize())
    }

    static func hmac(key: Data, _ message: Data) -> Data {
        let code = HMAC<SHA256>.authenticationCode(for: message, using: SymmetricKey(data: key))
        return Data(code)
    }

    /// HKDF-SHA256 (RFC 5869)
    static func hkdf(ikm: Data, salt: Data, info: Data, length: Int) -> Data {
        let key = HKDF<SHA256>.deriveKey(inputKeyMaterial: SymmetricKey(data: ikm), salt: salt, info: info,
                                         outputByteCount: length)
        return key.withUnsafeBytes { Data($0) }
    }

    static func x25519Public(_ priv: Data) throws -> Data {
        let key = try Curve25519.KeyAgreement.PrivateKey(rawRepresentation: priv)
        return key.publicKey.rawRepresentation
    }

    /// DH。結果が全部 0 (位数の小さい点) は拒否する
    static func x25519(_ priv: Data, _ pub: Data) throws -> Data {
        guard priv.count == 32, pub.count == 32 else { throw HSFailure.bad }
        do {
            let key = try Curve25519.KeyAgreement.PrivateKey(rawRepresentation: priv)
            let peer = try Curve25519.KeyAgreement.PublicKey(rawRepresentation: pub)
            let shared = try key.sharedSecretFromKeyAgreement(with: peer)
            let bytes = shared.withUnsafeBytes { Data($0) }
            if bytes.allSatisfy({ $0 == 0 }) { throw HSFailure.bad }
            return bytes
        } catch {
            throw HSFailure.bad
        }
    }

    static func randomBytes(_ count: Int) -> Data {
        var bytes = [UInt8](repeating: 0, count: count)
        let status = SecRandomCopyBytes(kSecRandomDefault, count, &bytes)
        if status != errSecSuccess {
            for index in 0..<count { bytes[index] = UInt8.random(in: 0...255) }
        }
        return Data(bytes)
    }

    /// RFC 4648 の base32 (パディングなし)
    static func base32(_ data: Data) -> String {
        let alphabet = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567".utf8)
        var out = [UInt8]()
        var buffer: UInt32 = 0
        var bits = 0
        for byte in data {
            buffer = (buffer << 8) | UInt32(byte)
            bits += 8
            while bits >= 5 {
                out.append(alphabet[Int((buffer >> UInt32(bits - 5)) & 0x1F)])
                bits -= 5
            }
            buffer &= (1 << UInt32(bits)) - 1
        }
        if bits > 0 {
            out.append(alphabet[Int((buffer << UInt32(5 - bits)) & 0x1F)])
        }
        return String(decoding: out, as: UTF8.self)
    }

    /// 静的公開鍵の指紋: SHA256 の先頭 16 バイトを base32 (26 文字)
    static func fingerprint(_ pub: Data) -> String {
        return base32(sha256(pub).prefix(16))
    }

    static func constantTimeEqual(_ a: Data, _ b: Data) -> Bool {
        let x = Array(a), y = Array(b)
        var diff = x.count ^ y.count
        for index in 0..<min(x.count, y.count) { diff |= Int(x[index] ^ y[index]) }
        return diff == 0
    }

    static func fingerprintEqual(_ a: String, _ b: String) -> Bool {
        return constantTimeEqual(Data(a.uppercased().utf8), Data(b.uppercased().utf8))
    }

    /// 照合コード: HKDF(ss, salt=th, info="phonecam pair sas") の先頭 4 バイト (ビッグエンディアン) を 1,000,000 で割った余り
    static func sasCode(ss: Data, th: Data) -> String {
        let b = [UInt8](hkdf(ikm: ss, salt: th, info: infoPairSAS, length: 4))
        let n = (UInt32(b[0]) << 24) | (UInt32(b[1]) << 16) | (UInt32(b[2]) << 8) | UInt32(b[3])
        return String(format: "%06d", Int(n % 1_000_000))
    }

    static func pairKey(ss: Data, th: Data) -> Data {
        return hkdf(ikm: ss, salt: th, info: infoPairKey, length: 32)
    }

    /// role は "pc" か "ph"
    static func confirmMac(key: Data, role: String, th: Data) -> Data {
        return hmac(key: key, Data(role.utf8) + th)
    }
}

/// 通常接続・QR の鍵 (HKDF の 192 バイトを切り分ける)
struct SessionKeys {
    let kPcToPh: Data
    let kPhToPc: Data
    let audioTok: Data
    let ivPcToPh: Data
    let ivPhToPc: Data
    let kConfirm: Data
    let kaPcToPh: Data
    let kaPhToPc: Data
    let ivaPcToPh: Data
    let ivaPhToPc: Data

    init(ikm: Data, th: Data, info: Data) {
        let block = [UInt8](SecureCrypto.hkdf(ikm: ikm, salt: th, info: info, length: 192))
        func part(_ a: Int, _ b: Int) -> Data { return Data(block[a..<b]) }
        kPcToPh = part(0, 32)
        kPhToPc = part(32, 64)
        audioTok = part(64, 80)
        ivPcToPh = part(80, 84)
        ivPhToPc = part(84, 88)
        kConfirm = part(88, 120)
        kaPcToPh = part(120, 152)
        kaPhToPc = part(152, 184)
        ivaPcToPh = part(184, 188)
        ivaPhToPc = part(188, 192)
    }
}

// MARK: - メッセージ (種別 10 の JSON。{"hs": {...}})

enum SecureMessage {
    /// 順序を保った {"hs":{...}}。値は String / Int / Bool / [String]。
    static func encode(_ fields: [(String, Any)]) -> Data {
        var parts = [String]()
        for (key, value) in fields {
            parts.append(quote(key) + ":" + render(value))
        }
        return Data(("{\"hs\":{" + parts.joined(separator: ",") + "}}").utf8)
    }

    private static func render(_ value: Any) -> String {
        if let text = value as? String { return quote(text) }
        if let flag = value as? Bool { return flag ? "true" : "false" }
        if let number = value as? Int { return String(number) }
        if let list = value as? [String] { return "[" + list.map(quote).joined(separator: ",") + "]" }
        return "null"
    }

    private static func quote(_ text: String) -> String {
        var out = "\""
        for scalar in text.unicodeScalars {
            switch scalar {
            case "\"": out += "\\\""
            case "\\": out += "\\\\"
            case "\n": out += "\\n"
            case "\r": out += "\\r"
            case "\t": out += "\\t"
            default:
                if scalar.value < 0x20 {
                    out += String(format: "\\u%04x", scalar.value)
                } else {
                    out.unicodeScalars.append(scalar)
                }
            }
        }
        return out + "\""
    }

    /// control JSON の本体から "hs" の辞書を取り出す。無い・不正なら nil
    static func parse(_ payload: Data) -> [String: Any]? {
        guard let object = try? JSONSerialization.jsonObject(with: payload) as? [String: Any],
              let hs = object["hs"] as? [String: Any] else { return nil }
        return hs
    }

    static func hex(_ hs: [String: Any], _ key: String, bytes: Int) throws -> Data {
        guard let text = hs[key] as? String, let data = SecureHex.decode(text, bytes: bytes) else { throw HSFailure.bad }
        return data
    }
}

// MARK: - 通信路 (AEAD)

/// 認証済みの通信路。メッセージ 1 つ = [種別 20][長さ][AEAD(種別 1 バイト + 本文)]。
/// nonce は「固定 4 バイト + 方向別 64bit カウンタ」、AAD は外側の 5 バイトの見出し。
/// 受信側は自分のカウンタでしか復号しないので、改ざん・入れ替え・欠落・再送はすべて失敗する。
final class SecureChannel {
    static let messageType: UInt8 = 20
    static let overhead = 17

    private let sendKey: SymmetricKey
    private let recvKey: SymmetricKey
    private let sendIV: Data
    private let recvIV: Data
    private var sendCount: UInt64 = 0
    private var recvCount: UInt64 = 0
    private let sendLock = NSLock()
    private let recvLock = NSLock()

    init(sendKey: Data, sendIV: Data, recvKey: Data, recvIV: Data) {
        self.sendKey = SymmetricKey(data: sendKey)
        self.recvKey = SymmetricKey(data: recvKey)
        self.sendIV = sendIV
        self.recvIV = recvIV
    }

    private static func nonce(_ iv: Data, _ counter: UInt64) throws -> ChaChaPoly.Nonce {
        var bytes = [UInt8](iv)
        for shift in stride(from: 56, through: 0, by: -8) {
            bytes.append(UInt8((counter >> UInt64(shift)) & 0xFF))
        }
        return try ChaChaPoly.Nonce(data: Data(bytes))
    }

    private static func header(length: Int) -> Data {
        let n = UInt32(length)
        return Data([messageType, UInt8((n >> 24) & 0xFF), UInt8((n >> 16) & 0xFF), UInt8((n >> 8) & 0xFF),
                     UInt8(n & 0xFF)])
    }

    /// 送るバイト列 (外側の見出し + 暗号文 + タグ)。呼び出し側は seal と送信を同じ順序で行う。
    func seal(type: UInt8, payload: Data) throws -> Data {
        sendLock.lock()
        defer { sendLock.unlock() }
        var inner = Data([type])
        inner.append(payload)
        let header = SecureChannel.header(length: inner.count + SecureCrypto.tagLength)
        let nonce = try SecureChannel.nonce(sendIV, sendCount)
        let box = try ChaChaPoly.seal(inner, using: sendKey, nonce: nonce, authenticating: header)
        sendCount += 1
        var out = header
        out.append(box.ciphertext)
        out.append(box.tag)
        return out
    }

    /// 外側の本体 (暗号文 + タグ) を復号して (種別, 本文)。失敗したら throw (呼び出し側は切断する)。
    func open(_ body: Data) throws -> (type: UInt8, payload: Data) {
        recvLock.lock()
        defer { recvLock.unlock() }
        guard body.count >= SecureCrypto.tagLength + 1 else { throw HSFailure.bad }
        let header = SecureChannel.header(length: body.count)
        let split = body.index(body.endIndex, offsetBy: -SecureCrypto.tagLength)
        let nonce = try SecureChannel.nonce(recvIV, recvCount)
        let box = try ChaChaPoly.SealedBox(nonce: nonce, ciphertext: body[body.startIndex..<split], tag: body[split...])
        let plain = try ChaChaPoly.open(box, using: recvKey, authenticating: header)
        recvCount += 1
        guard let first = plain.first else { throw HSFailure.bad }
        return (first, Data(plain.dropFirst()))
    }

    /// コード方式ペアリング: 鍵は 1 本で、nonce の固定 4 バイトで方向を分ける (iPhone 側)
    static func pairing(key: Data) -> SecureChannel {
        return SecureChannel(sendKey: key, sendIV: Data("p2c\0".utf8), recvKey: key, recvIV: Data("c2p\0".utf8))
    }

    /// 通常接続・QR の通信路 (iPhone 側)。audio なら音声チャンネル用の鍵
    static func session(_ keys: SessionKeys, audio: Bool = false) -> SecureChannel {
        if audio {
            return SecureChannel(sendKey: keys.kaPhToPc, sendIV: keys.ivaPhToPc, recvKey: keys.kaPcToPh, recvIV: keys.ivaPcToPh)
        }
        return SecureChannel(sendKey: keys.kPhToPc, sendIV: keys.ivPhToPc, recvKey: keys.kPcToPh, recvIV: keys.ivPcToPh)
    }
}

// MARK: - 長期鍵・ピン留め

protocol SecretStorage: AnyObject {
    func read(_ account: String) -> Data?
    @discardableResult func write(_ account: String, _ data: Data) -> Bool
    func delete(_ account: String)
}

/// Keychain (kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly: 初回ロック解除後に読め、バックアップや他の端末へは移らない)
final class KeychainStorage: SecretStorage {
    private let service: String

    init(service: String = "phonecam.secure.v2") {
        self.service = service
    }

    private func query(_ account: String) -> [String: Any] {
        return [kSecClass as String: kSecClassGenericPassword,
                kSecAttrService as String: service,
                kSecAttrAccount as String: account]
    }

    func read(_ account: String) -> Data? {
        var q = query(account)
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &result) == errSecSuccess else { return nil }
        return result as? Data
    }

    @discardableResult
    func write(_ account: String, _ data: Data) -> Bool {
        SecItemDelete(query(account) as CFDictionary)
        var q = query(account)
        q[kSecValueData as String] = data
        q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        return SecItemAdd(q as CFDictionary, nil) == errSecSuccess
    }

    func delete(_ account: String) {
        SecItemDelete(query(account) as CFDictionary)
    }
}

/// 保存先が使えないとき・テスト用
final class MemoryStorage: SecretStorage {
    private var items: [String: Data] = [:]
    private let lock = NSLock()

    func read(_ account: String) -> Data? {
        lock.lock()
        defer { lock.unlock() }
        return items[account]
    }

    @discardableResult
    func write(_ account: String, _ data: Data) -> Bool {
        lock.lock()
        items[account] = data
        lock.unlock()
        return true
    }

    func delete(_ account: String) {
        lock.lock()
        items[account] = nil
        lock.unlock()
    }
}

/// この iPhone の長期鍵 (X25519 の静的鍵)
struct DeviceIdentity {
    let priv: Data
    let pub: Data
    let fp: String

    init(priv: Data) throws {
        self.priv = priv
        self.pub = try SecureCrypto.x25519Public(priv)
        self.fp = SecureCrypto.fingerprint(pub)
    }

    private static let account = "identity.x25519"

    /// 保存済みの鍵を読む。無い・壊れているときは作って保存する (保存できなくても、起動中はその鍵を使う)
    static func load(_ storage: SecretStorage) -> DeviceIdentity {
        if let stored = storage.read(account), stored.count == 32, let identity = try? DeviceIdentity(priv: stored) {
            return identity
        }
        while true {
            let priv = SecureCrypto.randomBytes(32)
            if let identity = try? DeviceIdentity(priv: priv) {
                storage.write(account, priv)
                return identity
            }
        }
    }
}

/// ペアリングで覚えた PC (公開鍵を指紋で引く)
struct PinnedPC {
    let pub: Data
    let name: String
    let id: String
    var fp: String { return SecureCrypto.fingerprint(pub) }
}

final class PinnedPCStore {
    static let limit = 32
    private static let account = "pinned.pcs"
    private let storage: SecretStorage
    private let lock = NSLock()
    private var items: [PinnedPC] = []

    init(storage: SecretStorage) {
        self.storage = storage
        if let data = storage.read(PinnedPCStore.account),
           let list = (try? JSONSerialization.jsonObject(with: data)) as? [[String: String]] {
            for entry in list {
                if let hex = entry["pub"], let pub = SecureHex.decode(hex, bytes: 32) {
                    items.append(PinnedPC(pub: pub, name: entry["name"] ?? "", id: entry["id"] ?? ""))
                }
            }
        }
    }

    var count: Int {
        lock.lock()
        defer { lock.unlock() }
        return items.count
    }

    var all: [PinnedPC] {
        lock.lock()
        defer { lock.unlock() }
        return items
    }

    /// 指紋に合う PC の公開鍵 (無ければ nil)
    func lookup(_ fp: String) -> Data? {
        lock.lock()
        defer { lock.unlock() }
        return items.first(where: { SecureCrypto.fingerprintEqual($0.fp, fp) })?.pub
    }

    /// 覚える。同じ鍵・同じ PC の ID は置き換える (PC を入れ直して鍵が変わったとき)
    func add(pub: Data, name: String, id: String) {
        lock.lock()
        let entry = PinnedPC(pub: pub, name: name, id: id)
        items.removeAll { $0.pub == pub || (!id.isEmpty && $0.id == id) }
        items.append(entry)
        if items.count > PinnedPCStore.limit { items.removeFirst(items.count - PinnedPCStore.limit) }
        let snapshot = items
        lock.unlock()
        persist(snapshot)
    }

    func removeAll() {
        lock.lock()
        items.removeAll()
        lock.unlock()
        storage.delete(PinnedPCStore.account)
    }

    private func persist(_ list: [PinnedPC]) {
        let rows = list.map { ["pub": SecureHex.encode($0.pub), "name": $0.name, "id": $0.id] }
        if let data = try? JSONSerialization.data(withJSONObject: rows) {
            storage.write(PinnedPCStore.account, data)
        }
    }
}

/// 相手 (PC) の自己紹介
struct SecurePeer {
    let pub: Data
    let name: String
    let id: String

    static func parse(_ hs: [String: Any]) throws -> SecurePeer {
        let pub = try SecureMessage.hex(hs, "s", bytes: 32)
        guard let rawID = hs["id"] as? String, let uuid = UUID(uuidString: rawID) else { throw HSFailure.bad }
        let name = String(((hs["name"] as? String) ?? "").prefix(128))
        return SecurePeer(pub: pub, name: name, id: uuid.uuidString.lowercased())
    }
}

// MARK: - iPhone 側のハンドシェイクの計算

/// コード方式ペアリング (PC が始める)。m1 → m2 → m3 → (コードを PC に入力してもらう) → confirm → AEAD の自己紹介の交換。
final class PairResponder {
    private let ePriv: Data
    private let ePub: Data
    private let nonce: Data
    private var commit = Data()
    private var m1 = Data(), m2 = Data(), m3 = Data()
    private var th = Data()
    private var key = Data()
    private(set) var pcName = ""
    private(set) var code = ""
    private var channel: SecureChannel?

    init(ephPriv: Data? = nil, nonce: Data? = nil) throws {
        let priv = ephPriv ?? SecureCrypto.randomBytes(32)
        self.ePriv = priv
        self.ePub = try SecureCrypto.x25519Public(priv)
        self.nonce = nonce ?? SecureCrypto.randomBytes(16)
    }

    /// m1 を受けて m2 を返す
    func onM1(_ payload: Data) throws -> Data {
        guard let hs = SecureMessage.parse(payload), (hs["mode"] as? String) == "pair",
              (hs["v"] as? Int) == SecureCrypto.version else { throw HSFailure.bad }
        commit = try SecureMessage.hex(hs, "commit", bytes: 32)
        pcName = (hs["name"] as? String) ?? ""
        m1 = payload
        m2 = SecureMessage.encode([("e", SecureHex.encode(ePub)), ("n", SecureHex.encode(nonce))])
        return m2
    }

    /// m3 を受けて、約束 (commit) を確かめ、コードと鍵を作る
    func onM3(_ payload: Data) throws {
        guard let hs = SecureMessage.parse(payload) else { throw HSFailure.bad }
        let ePC = try SecureMessage.hex(hs, "e", bytes: 32)
        let nPC = try SecureMessage.hex(hs, "n", bytes: 16)
        guard SecureCrypto.constantTimeEqual(SecureCrypto.sha256(ePC, nPC), commit) else { throw HSFailure.auth }
        m3 = payload
        th = SecureCrypto.sha256(m1, m2, m3)
        let ss = try SecureCrypto.x25519(ePriv, ePC)
        code = SecureCrypto.sasCode(ss: ss, th: th)
        key = SecureCrypto.pairKey(ss: ss, th: th)
        channel = SecureChannel.pairing(key: key)
    }

    /// PC の confirm (コードが合ったときだけ送られてくる)。合わなければ throw
    func checkConfirm(_ hs: [String: Any]) throws {
        let mac = try SecureMessage.hex(hs, "confirm", bytes: 32)
        guard !key.isEmpty,
              SecureCrypto.constantTimeEqual(mac, SecureCrypto.confirmMac(key: key, role: "pc", th: th)) else {
            throw HSFailure.auth
        }
    }

    /// AEAD の自己紹介 (PC 側) を開く
    func openPeer(_ body: Data) throws -> SecurePeer {
        guard let channel = channel else { throw HSFailure.bad }
        let (type, plain) = try channel.open(body)
        guard type == 10, let hs = SecureMessage.parse(plain) else { throw HSFailure.bad }
        return try SecurePeer.parse(hs)
    }

    /// こちらの自己紹介 (AEAD)
    func replyFrame(identity: DeviceIdentity, id: String, name: String, model: String, addresses: [String]) throws -> Data {
        guard let channel = channel else { throw HSFailure.bad }
        let body = SecureMessage.encode([("s", SecureHex.encode(identity.pub)), ("name", name), ("id", id),
                                         ("model", model), ("addresses", addresses)])
        return try channel.seal(type: 10, payload: body)
    }
}

/// 通常接続 (両者がピン留め済み)。c1 → c2 → c3 (PC の confirm) → こちらの confirm。
final class ConnectResponder {
    private let identity: DeviceIdentity
    private let ePriv: Data
    private let ePub: Data
    private let nonce: Data
    private var th = Data()
    private(set) var keys: SessionKeys?
    private(set) var peerPub: Data?

    init(identity: DeviceIdentity, ephPriv: Data? = nil, nonce: Data? = nil) throws {
        self.identity = identity
        let priv = ephPriv ?? SecureCrypto.randomBytes(32)
        self.ePriv = priv
        self.ePub = try SecureCrypto.x25519Public(priv)
        self.nonce = nonce ?? SecureCrypto.randomBytes(16)
    }

    /// c1 を受けて c2 を返す。PC の指紋 (sf) に合う鍵を覚えていなければ unpaired
    func onC1(_ payload: Data, lookup: (String) -> Data?) throws -> Data {
        guard let hs = SecureMessage.parse(payload), (hs["mode"] as? String) == "connect",
              (hs["v"] as? Int) == SecureCrypto.version,
              let sf = hs["sf"] as? String, sf.utf8.count <= 64 else { throw HSFailure.bad }
        let ePC = try SecureMessage.hex(hs, "e", bytes: 32)
        _ = try SecureMessage.hex(hs, "n", bytes: 16)
        guard let sPC = lookup(sf) else { throw HSFailure.unpaired }
        let c2 = SecureMessage.encode([("e", SecureHex.encode(ePub)), ("n", SecureHex.encode(nonce)),
                                       ("tf", identity.fp)])
        th = SecureCrypto.sha256(payload, c2)
        // ikm = DH(e_pc, e_ph) || DH(s_pc, e_ph) || DH(e_pc, s_ph)
        let ikm = try SecureCrypto.x25519(ePriv, ePC) + SecureCrypto.x25519(ePriv, sPC)
            + SecureCrypto.x25519(identity.priv, ePC)
        keys = SessionKeys(ikm: ikm, th: th, info: SecureCrypto.infoSession)
        peerPub = sPC
        return c2
    }

    func checkConfirm(_ hs: [String: Any]) throws {
        guard let keys = keys else { throw HSFailure.bad }
        let mac = try SecureMessage.hex(hs, "confirm", bytes: 32)
        guard SecureCrypto.constantTimeEqual(mac, SecureCrypto.confirmMac(key: keys.kConfirm, role: "pc", th: th)) else {
            throw HSFailure.auth
        }
    }

    func confirmMessage() -> Data {
        guard let keys = keys else { return Data() }
        return SecureMessage.encode([("confirm", SecureHex.encode(SecureCrypto.confirmMac(key: keys.kConfirm, role: "ph", th: th)))])
    }
}

/// QR 方式 (iPhone が始める)。PC の静的鍵の指紋が QR の指紋と合うときだけ、こちらの鍵とトークンを暗号化して渡す。
final class QrInitiator {
    private let identity: DeviceIdentity
    private let expectedFingerprint: String
    private let token: String
    private let ePriv: Data
    private let ePub: Data
    private let nonce: Data
    private var q1 = Data()
    private var th = Data()
    private var keys: SessionKeys?
    private var channel: SecureChannel?
    private(set) var pcPub: Data?

    init(identity: DeviceIdentity, expectedFingerprint: String, token: String, ephPriv: Data? = nil,
         nonce: Data? = nil) throws {
        self.identity = identity
        self.expectedFingerprint = expectedFingerprint
        self.token = token
        let priv = ephPriv ?? SecureCrypto.randomBytes(32)
        self.ePriv = priv
        self.ePub = try SecureCrypto.x25519Public(priv)
        self.nonce = nonce ?? SecureCrypto.randomBytes(16)
    }

    func start() -> Data {
        q1 = SecureMessage.encode([("v", SecureCrypto.version), ("mode", "qr"), ("e", SecureHex.encode(ePub)),
                                   ("n", SecureHex.encode(nonce))])
        return q1
    }

    /// q2 を受ける。PC の鍵の指紋が QR のものと違えば、何も渡さず throw
    func onQ2(_ payload: Data) throws {
        guard let hs = SecureMessage.parse(payload) else { throw HSFailure.bad }
        let ePC = try SecureMessage.hex(hs, "e", bytes: 32)
        _ = try SecureMessage.hex(hs, "n", bytes: 16)
        let sPC = try SecureMessage.hex(hs, "s", bytes: 32)
        guard SecureCrypto.fingerprintEqual(SecureCrypto.fingerprint(sPC), expectedFingerprint) else { throw HSFailure.auth }
        th = SecureCrypto.sha256(q1, payload)
        let ikm = try SecureCrypto.x25519(ePriv, ePC) + SecureCrypto.x25519(ePriv, sPC)
        let derived = SessionKeys(ikm: ikm, th: th, info: SecureCrypto.infoQR)
        keys = derived
        channel = SecureChannel.session(derived)
        pcPub = sPC
    }

    func checkConfirm(_ hs: [String: Any]) throws {
        guard let keys = keys else { throw HSFailure.bad }
        let mac = try SecureMessage.hex(hs, "confirm", bytes: 32)
        guard SecureCrypto.constantTimeEqual(mac, SecureCrypto.confirmMac(key: keys.kConfirm, role: "pc", th: th)) else {
            throw HSFailure.auth
        }
    }

    /// こちらの鍵・名前・ID とトークン (AEAD)
    func identityFrame(id: String, name: String, model: String, addresses: [String]) throws -> Data {
        guard let channel = channel else { throw HSFailure.bad }
        let body = SecureMessage.encode([("s", SecureHex.encode(identity.pub)), ("tok", token), ("name", name),
                                         ("id", id), ("model", model), ("addresses", addresses)])
        return try channel.seal(type: 10, payload: body)
    }

    /// PC の返事 (AEAD) -> {"ok": true, "name", "id"}
    func openReply(_ body: Data) throws -> (name: String, id: String) {
        guard let channel = channel else { throw HSFailure.bad }
        let (type, plain) = try channel.open(body)
        guard type == 10, let hs = SecureMessage.parse(plain), (hs["ok"] as? Bool) == true,
              let rawID = hs["id"] as? String, let uuid = UUID(uuidString: rawID) else { throw HSFailure.bad }
        return (String(((hs["name"] as? String) ?? "").prefix(128)), uuid.uuidString.lowercased())
    }
}

/// 音声チャンネルを映像のセッションに結び付けるための情報 (映像のハンドシェイクが済むたびに差し替わる)
struct AudioBinding {
    let ip: String
    let loopback: Bool
    /// nil は v1 の PC との USB (トークンなし)
    let keys: SessionKeys?
}
