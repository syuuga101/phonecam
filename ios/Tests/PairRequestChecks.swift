import Foundation

@main
struct PairRequestChecks {
    static func main() {
        // --- 要求の数の制限 (IP ごとに 10 秒に 1 回) ---
        var gate = PairRequestGate()
        precondition(gate.evaluate(ip: "192.168.1.5", activeIP: nil, now: 100) == .accept)
        precondition(gate.evaluate(ip: "192.168.1.5", activeIP: nil, now: 105) == .rateLimited, "Same IP within 10s")
        precondition(gate.evaluate(ip: "192.168.1.5", activeIP: nil, now: 109.9) == .rateLimited)
        precondition(gate.evaluate(ip: "192.168.1.6", activeIP: nil, now: 105) == .accept, "Other IPs are independent")
        precondition(gate.evaluate(ip: "192.168.1.5", activeIP: nil, now: 110) == .accept, "Allowed again after 10s")

        // --- ポップアップは 1 つだけ ---
        var popup = PairRequestGate()
        precondition(popup.evaluate(ip: "10.0.0.2", activeIP: "10.0.0.1", now: 1) == .busy, "Another PC's popup is showing")
        precondition(popup.evaluate(ip: "10.0.0.1", activeIP: "10.0.0.1", now: 1) == .accept, "Same PC replaces its own popup")
        precondition(popup.evaluate(ip: "10.0.0.1", activeIP: "10.0.0.1", now: 2) == .rateLimited, "...but not faster than the limit")
        precondition(popup.evaluate(ip: "10.0.0.2", activeIP: nil, now: 2) == .accept, "A busy answer is not counted")

        // 古い記録は捨てられて、メモリが増え続けない
        var many = PairRequestGate()
        for i in 0..<500 { _ = many.evaluate(ip: "10.1.\(i / 250).\(i % 250)", activeIP: nil, now: Double(i)) }
        precondition(many.evaluate(ip: "10.1.0.0", activeIP: nil, now: 1000) == .accept)

        // --- PC 名の表示用の整形 ---
        precondition(PairRequestRules.sanitizeName("Desk-PC") == "Desk-PC")
        precondition(PairRequestRules.sanitizeName("  padded  ") == "padded")
        precondition(PairRequestRules.sanitizeName("a\u{0}b\u{1B}[31mc\n") == "ab[31mc", "Control characters are removed")
        precondition(PairRequestRules.sanitizeName("name\u{202E}gpj") == "namegpj", "Bidi override is removed")
        precondition(PairRequestRules.sanitizeName("x\u{2028}y\u{2029}z") == "xyz")
        precondition(PairRequestRules.sanitizeName(String(repeating: "あ", count: 100)).unicodeScalars.count == PairRequestRules.nameLimit)
        precondition(PairRequestRules.sanitizeName("") == "PC")
        precondition(PairRequestRules.sanitizeName("\n\t \u{200B}") == "PC")
        precondition(PairRequestRules.sanitizeName(String(repeating: "a\u{0301}", count: 5000)).unicodeScalars.count <= PairRequestRules.nameLimit)

        // --- 要求 ID ---
        precondition(PairRequestRules.isValidID("abcd1234abcd1234"))
        precondition(PairRequestRules.isValidID("A-b_c-1234"))
        precondition(!PairRequestRules.isValidID("short"))
        precondition(!PairRequestRules.isValidID(String(repeating: "a", count: 65)))
        precondition(!PairRequestRules.isValidID("abcd1234 abcd"))
        precondition(!PairRequestRules.isValidID("abcd1234\u{0}"))
        precondition(!PairRequestRules.isValidID("あいうえおかきくけこ"))

        // --- 表示用のコード ---
        precondition(PairRequestRules.displayCode("123456") == "123 456")
        precondition(PairRequestRules.displayCode("12345") == "12345")
        precondition(PairRequestRules.displayCode("12a456") == "12a456")

        // --- 認証前の小さなメッセージ上限 (パーサ) ---
        var pre = PacketParser()
        pre.maxPayload = PairRequestRules.maxPreAuthBytes
        let small = Packet.encode(type: PacketType.control, payload: Data(repeating: 0x20, count: 100))
        pre.append(small)
        precondition(pre.next() != nil && !pre.overflowed)
        pre.append(Packet.encode(type: PacketType.control, payload: Data(repeating: 0x20, count: 70_000)))
        precondition(pre.next() == nil && pre.overflowed, "Oversized pre-auth message is flagged")
        var after = PacketParser()
        after.maxPayload = PairRequestRules.maxPreAuthBytes
        after.maxPayload = PacketParser.defaultMaxPayload   // 認証が通ったあと
        after.append(Packet.encode(type: PacketType.control, payload: Data(repeating: 0x20, count: 2000)))
        precondition(after.next() != nil && !after.overflowed, "Full-size messages are accepted after auth")

        print("PairRequestChecks passed")
    }
}
