import Foundation

/// Secure.swift (iPhone 側の暗号) が、Windows 側 (secure.py) と同じ計算になることを、共有のテストベクタで確かめる。
/// ベクタ: docs/test-vectors/handshake-v2.json (windows/tests/make_vectors.py が作る)。実行はリポジトリの ios/ から。
@main
struct HandshakeChecks {
    static var failures = 0

    static func check(_ condition: @autoclosure () throws -> Bool, _ message: String) rethrows {
        if !(try condition()) {
            failures += 1
            print("FAIL: \(message)")
        }
    }

    static func hex(_ text: String) -> Data {
        guard let data = SecureHex.decode(text) else {
            print("bad hex in vectors")
            exit(2)
        }
        return data
    }

    static func text(_ data: Data) -> String { return String(decoding: data, as: UTF8.self) }

    /// 外側の見出し (5 バイト) を除いた本体
    static func body(_ frameHex: String) -> Data { return Data(hex(frameHex).dropFirst(5)) }

    static func dict(_ value: Any?) -> [String: Any] { return (value as? [String: Any]) ?? [:] }
    static func str(_ d: [String: Any], _ key: String) -> String { return (d[key] as? String) ?? "" }

    static func main() throws {
        let path = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "../docs/test-vectors/handshake-v2.json"
        let raw = try Data(contentsOf: URL(fileURLWithPath: path))
        let root = dict(try JSONSerialization.jsonObject(with: raw))
        let inputs = dict(root["inputs"]), pub = dict(root["public"])
        let pair = dict(root["pair"]), connect = dict(root["connect"]), qr = dict(root["qr"])
        let sPCPriv = hex(str(inputs, "s_pc_priv")), sPHPriv = hex(str(inputs, "s_ph_priv"))
        let ePCPriv = hex(str(inputs, "e_pc_priv")), ePHPriv = hex(str(inputs, "e_ph_priv"))
        let nPC = hex(str(inputs, "n_pc")), nPH = hex(str(inputs, "n_ph"))
        let sPCPub = hex(str(pub, "s_pc_pub")), sPHPub = hex(str(pub, "s_ph_pub"))
        let ePCPub = hex(str(pub, "e_pc_pub")), ePHPub = hex(str(pub, "e_ph_pub"))
        let ping1 = Data("{\"ping\":1}".utf8), ping2 = Data("{\"ping\":2}".utf8)
        let fps = Data("{\"fps\":30}".utf8)

        // --- 公開鍵と指紋 ---
        try check(try SecureCrypto.x25519Public(sPCPriv) == sPCPub, "s_pc public key")
        try check(try SecureCrypto.x25519Public(sPHPriv) == sPHPub, "s_ph public key")
        try check(try SecureCrypto.x25519Public(ePCPriv) == ePCPub, "e_pc public key")
        try check(try SecureCrypto.x25519Public(ePHPriv) == ePHPub, "e_ph public key")
        check(SecureCrypto.fingerprint(sPCPub) == str(pub, "s_pc_fp"), "PC fingerprint")
        check(SecureCrypto.fingerprint(sPHPub) == str(pub, "s_ph_fp"), "iPhone fingerprint")
        check(SecureCrypto.fingerprint(sPCPub).count == 26, "fingerprint is 26 characters")
        check(SecureCrypto.fingerprintEqual(str(pub, "s_pc_fp").lowercased(), str(pub, "s_pc_fp")), "fingerprint compare ignores case")

        // --- 低位数の点は拒否 ---
        var rejected = false
        do { _ = try SecureCrypto.x25519(ePHPriv, Data(repeating: 0, count: 32)) } catch { rejected = true }
        check(rejected, "all-zero public key is rejected")

        // --- コード方式ペアリング ---
        do {
            let m1 = Data(str(pair, "m1").utf8), m3 = Data(str(pair, "m3").utf8)
            check(SecureCrypto.sha256(ePCPub, nPC) == hex(str(pair, "commit")), "pair commit")
            let responder = try PairResponder(ephPriv: ePHPriv, nonce: nPH)
            let m2 = try responder.onM1(m1)
            check(text(m2) == str(pair, "m2"), "pair m2 is byte-identical")
            try responder.onM3(m3)
            check(responder.code == str(pair, "code"), "pair code \(responder.code) vs \(str(pair, "code"))")
            let ss = hex(str(pair, "ss"))
            let th = SecureCrypto.sha256(m1, m2, m3)
            check(th == hex(str(pair, "th")), "pair transcript hash")
            check(SecureCrypto.sasCode(ss: ss, th: th) == str(pair, "code"), "pair sas code")
            let key = SecureCrypto.pairKey(ss: ss, th: th)
            check(key == hex(str(pair, "key")), "pair key")
            check(SecureCrypto.confirmMac(key: key, role: "pc", th: th) == hex(str(pair, "confirm_pc")), "pair confirm")
            try responder.checkConfirm(["confirm": str(pair, "confirm_pc")])
            var bad = false
            do { try responder.checkConfirm(["confirm": String(repeating: "00", count: 32)]) } catch { bad = true }
            check(bad, "wrong pair confirm is rejected")
            // 同じ鍵で AEAD (PC → iPhone を開く / iPhone → PC を作る)
            let channel = SecureChannel.pairing(key: key)
            let opened = try channel.open(body(str(pair, "frame_pc2ph_0")))
            check(opened.type == 10 && opened.payload == ping1, "pair frame pc2ph")
            try check(try channel.seal(type: 3, payload: fps) == hex(str(pair, "frame_ph2pc_0")), "pair frame ph2pc")
            // 約束 (commit) が違う m3 は拒否
            let other = try PairResponder(ephPriv: ePHPriv, nonce: nPH)
            _ = try other.onM1(Data(str(pair, "m1").replacingOccurrences(of: str(pair, "commit"),
                                                                           with: String(repeating: "ab", count: 32)).utf8))
            var commitRejected = false
            do { try other.onM3(m3) } catch { commitRejected = true }
            check(commitRejected, "m3 that does not match the commit is rejected")
        }

        // --- 通常接続 ---
        do {
            let c1 = Data(str(connect, "c1").utf8)
            let identity = try DeviceIdentity(priv: sPHPriv)
            let responder = try ConnectResponder(identity: identity, ephPriv: ePHPriv, nonce: nPH)
            let pcFP = str(pub, "s_pc_fp")
            let c2 = try responder.onC1(c1, lookup: { SecureCrypto.fingerprintEqual($0, pcFP) ? sPCPub : nil })
            check(text(c2) == str(connect, "c2"), "connect c2 is byte-identical")
            let th = SecureCrypto.sha256(c1, c2)
            check(th == hex(str(connect, "th")), "connect transcript hash")
            let ikm = try SecureCrypto.x25519(ePHPriv, ePCPub) + SecureCrypto.x25519(ePHPriv, sPCPub)
                + SecureCrypto.x25519(sPHPriv, ePCPub)
            check(ikm == hex(str(connect, "ikm")), "connect ikm")
            guard let keys = responder.keys else { print("no keys"); exit(2) }
            let expected = dict(connect["keys"])
            check(keys.kPcToPh == hex(str(expected, "k_pc2ph")), "k_pc2ph")
            check(keys.kPhToPc == hex(str(expected, "k_ph2pc")), "k_ph2pc")
            check(keys.audioTok == hex(str(expected, "audio_tok")), "audio_tok")
            check(keys.ivPcToPh == hex(str(expected, "iv_pc2ph")), "iv_pc2ph")
            check(keys.ivPhToPc == hex(str(expected, "iv_ph2pc")), "iv_ph2pc")
            check(keys.kConfirm == hex(str(expected, "k_confirm")), "k_confirm")
            check(keys.kaPcToPh == hex(str(expected, "ka_pc2ph")), "ka_pc2ph")
            check(keys.kaPhToPc == hex(str(expected, "ka_ph2pc")), "ka_ph2pc")
            check(keys.ivaPcToPh == hex(str(expected, "iva_pc2ph")), "iva_pc2ph")
            check(keys.ivaPhToPc == hex(str(expected, "iva_ph2pc")), "iva_ph2pc")
            try responder.checkConfirm(["confirm": str(connect, "confirm_pc")])
            let mine = SecureMessage.parse(responder.confirmMessage())
            check((mine?["confirm"] as? String) == str(connect, "confirm_ph"), "connect iPhone confirm")
            var badConfirm = false
            do { try responder.checkConfirm(["confirm": String(repeating: "11", count: 32)]) } catch { badConfirm = true }
            check(badConfirm, "wrong connect confirm is rejected")

            // 通信路: 順番どおりに開け、再送・改ざん・入れ替えは失敗する
            let channel = SecureChannel.session(keys)
            let first = try channel.open(body(str(connect, "frame_pc2ph_0")))
            check(first.type == 10 && first.payload == ping1, "connect frame 0")
            var replay = false
            do { _ = try channel.open(body(str(connect, "frame_pc2ph_0"))) } catch { replay = true }
            check(replay, "replayed frame is rejected")
            let second = try channel.open(body(str(connect, "frame_pc2ph_1")))
            check(second.type == 10 && second.payload == ping2, "connect frame 1 (counter advanced)")
            try check(try channel.seal(type: 3, payload: fps) == hex(str(connect, "frame_ph2pc_0")), "connect frame ph2pc")

            let fresh = SecureChannel.session(keys)
            var tampered = body(str(connect, "frame_pc2ph_0"))
            tampered[tampered.startIndex + 3] ^= 1
            var tamperRejected = false
            do { _ = try fresh.open(tampered) } catch { tamperRejected = true }
            check(tamperRejected, "tampered ciphertext is rejected")
            let reordered = SecureChannel.session(keys)
            var reorderRejected = false
            do { _ = try reordered.open(body(str(connect, "frame_pc2ph_1"))) } catch { reorderRejected = true }
            check(reorderRejected, "out-of-order frame is rejected")

            // 音声チャンネル (別の鍵)
            let audio = SecureChannel.session(keys, audio: true)
            let hello = try audio.open(body(str(connect, "audio_frame_pc2ph_0")))
            let helloHS = SecureMessage.parse(hello.payload)
            check(hello.type == 10 && (helloHS?["audio_tok"] as? String) == str(expected, "audio_tok"), "audio hello")
            try check(try audio.seal(type: 6, payload: Data((0..<32).map { UInt8($0) })) == hex(str(connect, "audio_frame_ph2pc_0")), "audio frame ph2pc")

            // PC の鍵を覚えていなければ unpaired
            let stranger = try ConnectResponder(identity: identity, ephPriv: ePHPriv, nonce: nPH)
            var unpaired = false
            do { _ = try stranger.onC1(c1, lookup: { _ in nil }) } catch HSFailure.unpaired { unpaired = true } catch {}
            check(unpaired, "unknown PC fingerprint is unpaired")
        }

        // --- QR ---
        do {
            let identity = try DeviceIdentity(priv: sPHPriv)
            let pcFP = str(pub, "s_pc_fp")
            let initiator = try QrInitiator(identity: identity, expectedFingerprint: pcFP,
                                            token: str(inputs, "qr_token"), ephPriv: ePHPriv, nonce: nPH)
            let q1 = initiator.start()
            check(text(q1) == str(qr, "q1"), "qr q1 is byte-identical")
            let q2 = Data(str(qr, "q2").utf8)
            try initiator.onQ2(q2)
            try initiator.checkConfirm(["confirm": str(qr, "confirm_pc")])
            check(initiator.pcPub == sPCPub, "qr pins the PC key")
            let th = SecureCrypto.sha256(q1, q2)
            check(th == hex(str(qr, "th")), "qr transcript hash")
            let ikm = try SecureCrypto.x25519(ePHPriv, ePCPub) + SecureCrypto.x25519(ePHPriv, sPCPub)
            let keys = SessionKeys(ikm: ikm, th: th, info: SecureCrypto.infoQR)
            let expected = dict(qr["keys"])
            check(keys.kPcToPh == hex(str(expected, "k_pc2ph")) && keys.kPhToPc == hex(str(expected, "k_ph2pc")), "qr keys")
            check(keys.kConfirm == hex(str(expected, "k_confirm")), "qr k_confirm")
            let channel = SecureChannel.session(keys)
            try check(try channel.seal(type: 10, payload: Data("{\"hs\":{\"tok\":\"x\"}}".utf8)) == hex(str(qr, "frame_ph2pc_0")),
                  "qr frame ph2pc")
            let opened = try channel.open(body(str(qr, "frame_pc2ph_0")))
            check(opened.type == 10 && opened.payload == ping1, "qr frame pc2ph")

            // 指紋が QR と違う PC には何も渡さない
            let wrong = try QrInitiator(identity: identity, expectedFingerprint: str(pub, "s_ph_fp"),
                                        token: str(inputs, "qr_token"), ephPriv: ePHPriv, nonce: nPH)
            _ = wrong.start()
            var mismatch = false
            do { try wrong.onQ2(q2) } catch { mismatch = true }
            check(mismatch, "QR fingerprint mismatch is refused")
            var noFrame = false
            do { _ = try wrong.identityFrame(id: "x", name: "x", model: "x", addresses: []) } catch { noFrame = true }
            check(noFrame, "nothing is sealed after a fingerprint mismatch")
        }

        // --- 補助 ---
        check(text(SecureMessage.encode([("v", 2), ("mode", "qr")])) == "{\"hs\":{\"v\":2,\"mode\":\"qr\"}}", "message encoding keeps order")
        check(SecureHex.decode("0g") == nil && SecureHex.decode("abc") == nil && SecureHex.decode("0aFf") == Data([0x0a, 0xff]), "hex")
        check(SecureCrypto.base32(Data([0xff, 0x00])) == "74AA", "base32")
        check(SecureCrypto.constantTimeEqual(Data([1, 2]), Data([1, 2])) && !SecureCrypto.constantTimeEqual(Data([1, 2]), Data([1])),
              "constant time compare")

        // --- 長期鍵とピン留め (メモリ上の保存先) ---
        do {
            let storage = MemoryStorage()
            let first = DeviceIdentity.load(storage)
            let again = DeviceIdentity.load(storage)
            check(first.pub == again.pub && first.fp == again.fp, "identity is stable across loads")
            let store = PinnedPCStore(storage: storage)
            check(store.lookup(str(pub, "s_pc_fp")) == nil, "nothing is pinned at first")
            store.add(pub: sPCPub, name: "PC-A", id: "11111111-2222-4333-8444-555555555555")
            check(store.lookup(str(pub, "s_pc_fp")) == sPCPub, "pinned key is found by fingerprint")
            let reloaded = PinnedPCStore(storage: storage)
            check(reloaded.count == 1 && reloaded.lookup(str(pub, "s_pc_fp")) == sPCPub, "pins persist")
            store.add(pub: sPHPub, name: "PC-A", id: "11111111-2222-4333-8444-555555555555")
            check(store.count == 1 && store.lookup(str(pub, "s_pc_fp")) == nil, "the same PC id replaces its old key")
            store.removeAll()
            check(PinnedPCStore(storage: storage).count == 0, "pins can be reset")
        }

        if failures > 0 {
            print("HandshakeChecks: \(failures) failure(s)")
            exit(1)
        }
        print("HandshakeChecks passed")
    }
}
