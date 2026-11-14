import Foundation

/// 速く変わる表示用の値 (水平メーター 最大 20Hz / 毎秒更新される配信情報 / 録画の経過時間)。
/// AppModel に置くと、それを観測する ContentView 全体 (List を含む) が更新のたびに描き直されるため、別オブジェクトに分けている。
/// これらを表示する小さな View だけが観測する。代入は、値が変わったときだけ行うこと (同じ値でも @Published は通知する)。
final class LiveMetrics: ObservableObject {
    @Published var levelDeg = 0.0
    @Published var levelFlat = false
    @Published var wifiAddress = String(localized: "未接続")
    @Published var formatText = "-"
    @Published var bitrateText = "-"
    @Published var sentFps = 0
    @Published var thermalText = String(localized: "正常")
    @Published var batteryText = "-"
    @Published var recSeconds = 0.0
}
