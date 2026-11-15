@main
struct RecordingSessionChecks {
    static func main() {
        var gate = RecordingSessionGate()
        let first = gate.begin()
        precondition(gate.shouldApplyToDisplay(resultSession: first), "今の録画の結果は反映する")

        // 停止直後に次の録画を始めた: 旧録画の完了結果は反映しない
        let second = gate.begin()
        precondition(second != first, "録画ごとに別の ID")
        precondition(!gate.shouldApplyToDisplay(resultSession: first), "旧録画の結果は表示用の状態に混ぜない")
        precondition(gate.shouldApplyToDisplay(resultSession: second), "新しい録画の結果は反映する")

        // 何度繰り返しても、最新以外は反映されない
        var ids = [first, second]
        for _ in 0..<100 { ids.append(gate.begin()) }
        let latest = ids.last!
        for id in ids.dropLast() {
            precondition(!gate.shouldApplyToDisplay(resultSession: id))
        }
        precondition(gate.shouldApplyToDisplay(resultSession: latest))
        precondition(Set(ids).count == ids.count, "ID は重複しない")

        // 録画を始める前 (ID 0) は何も反映しない
        var g2 = RecordingSessionGate()
        precondition(!g2.shouldApplyToDisplay(resultSession: 99))
        _ = g2.begin()
    }
}
