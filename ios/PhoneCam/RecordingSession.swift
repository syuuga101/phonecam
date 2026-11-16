import Foundation

/// 録画セッションの世代管理 (純粋な型)。
/// 録画を始めるたびに新しい ID を振り、書き込み完了の結果が「今の録画」のものかどうかを判定する。
/// 古い録画の結果 (サイズ・エラー) で、今の録画の表示用カウンターを汚さないために使う。
struct RecordingSessionGate {
    private(set) var current: UInt64 = 0

    /// 新しい録画を始める。以前の ID はすべて「古い録画」になる。
    mutating func begin() -> UInt64 {
        current &+= 1
        return current
    }

    /// 完了結果を表示用の状態 (書き込み済みサイズ・エラー) に反映してよいか。
    /// 保存処理 (写真への保存など) はこの判定にかかわらず行う。
    func shouldApplyToDisplay(resultSession id: UInt64) -> Bool {
        return id == current
    }
}
