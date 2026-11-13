import Foundation

/// アプリ情報の定数 (設定画面で使う)
enum AppInfo {
    static let author = "syuuga101"
    static let repoURL = URL(string: "https://github.com/syuuga101/phonecam")!
    static let guideURL = URL(string: "https://github.com/syuuga101/phonecam/wiki")!
    /// 仮の URL (Crowdin プロジェクト作成後に差し替える)
    static let translateURL = URL(string: "https://crowdin.com/project/phonecam")!
    static var license: String { String(localized: "GPL-2.0以降") }

    static var version: String {
        let v = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
        return v ?? "-"
    }
}
