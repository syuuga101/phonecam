import SwiftUI
import UIKit
import Foundation

/// GitHub Releases の最新版を確認して、新しいバージョンがあれば知らせる。エラーは無視する。
final class UpdateChecker: ObservableObject {
    struct Release: Equatable {
        let tag: String
        let htmlURL: String
        let body: String
        var version: String { UpdateChecker.stripV(tag) }
    }

    /// 表示中の更新 (nil なら何も出さない)
    @Published var available: Release?
    @Published var showNotes = false

    private let defaults = UserDefaults.standard
    private let interval: TimeInterval = 6 * 3600
    private var checking = false

    private enum Key {
        static let lastCheck = "update.lastCheck"
        static let tag = "update.tag"
        static let url = "update.url"
        static let body = "update.body"
        static let skipped = "update.skippedVersion"
        static let dismissedTag = "update.dismissedTag"
        static let dismissedAt = "update.dismissedAt"
    }

    static var currentVersion: String {
        (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "0"
    }

    private var repo: String {
        let r = (Bundle.main.object(forInfoDictionaryKey: "PhoneCamRepo") as? String) ?? ""
        return r.contains("/") && !r.contains("$") ? r : "syuuga101/phonecam"
    }

    static func stripV(_ s: String) -> String {
        var t = s.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.hasPrefix("v") || t.hasPrefix("V") { t.removeFirst() }
        return t
    }

    /// "1.4.1" > "1.4" のように数値で比較する (-beta などの接尾辞は無視)
    static func isNewer(_ a: String, than b: String) -> Bool {
        func parts(_ s: String) -> [Int] {
            let core = stripV(s).split(whereSeparator: { $0 == "-" || $0 == "+" }).first.map(String.init) ?? ""
            return core.split(separator: ".").map { Int($0.prefix(while: { $0.isNumber })) ?? 0 }
        }
        let x = parts(a), y = parts(b)
        for i in 0..<max(x.count, y.count) {
            let p = i < x.count ? x[i] : 0
            let q = i < y.count ? y[i] : 0
            if p != q { return p > q }
        }
        return false
    }

    /// 起動時・前面復帰時に呼ぶ。まず保存済みの結果を出し、6時間以上経っていれば取得し直す。
    func checkIfNeeded() {
        applyCached()
        let last = defaults.double(forKey: Key.lastCheck)
        if last > 0 && Date().timeIntervalSince1970 - last < interval { return }
        guard !checking else { return }
        checking = true
        let repo = self.repo
        Task { [weak self] in
            let result = await UpdateChecker.fetch(repo: repo)
            await MainActor.run {
                guard let self = self else { return }
                self.checking = false
                guard let rel = result else { return }
                self.defaults.set(Date().timeIntervalSince1970, forKey: Key.lastCheck)
                self.defaults.set(rel.tag, forKey: Key.tag)
                self.defaults.set(rel.htmlURL, forKey: Key.url)
                self.defaults.set(rel.body, forKey: Key.body)
                self.applyCached()
            }
        }
    }

    private func applyCached() {
        guard let tag = defaults.string(forKey: Key.tag), !tag.isEmpty else { available = nil; return }
        let rel = Release(tag: tag,
                          htmlURL: defaults.string(forKey: Key.url) ?? "",
                          body: defaults.string(forKey: Key.body) ?? "")
        guard UpdateChecker.isNewer(rel.version, than: UpdateChecker.currentVersion) else { available = nil; return }
        if defaults.string(forKey: Key.skipped) == rel.version { available = nil; return }
        if defaults.string(forKey: Key.dismissedTag) == rel.version {
            let at = defaults.double(forKey: Key.dismissedAt)
            if Date().timeIntervalSince1970 - at < interval { available = nil; return }
        }
        if available != rel { available = rel }
    }

    private static func fetch(repo: String) async -> Release? {
        guard let url = URL(string: "https://api.github.com/repos/\(repo)/releases/latest") else { return nil }
        var req = URLRequest(url: url)
        req.timeoutInterval = 8
        req.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        req.setValue("PhoneCam-iOS", forHTTPHeaderField: "User-Agent")
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            guard let http = resp as? HTTPURLResponse, http.statusCode == 200,
                  let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let tag = obj["tag_name"] as? String, !tag.isEmpty else { return nil }
            return Release(tag: tag,
                           htmlURL: (obj["html_url"] as? String) ?? "",
                           body: (obj["body"] as? String) ?? "")
        } catch {
            return nil
        }
    }

    // MARK: 操作

    func openAltStore() {
        guard let rel = available else { return }
        let fallback = rel.htmlURL
        guard let alt = URL(string: "altstore://") else { return }
        UIApplication.shared.open(alt, options: [:]) { ok in
            if !ok, let u = URL(string: fallback) {
                UIApplication.shared.open(u, options: [:], completionHandler: nil)
            }
        }
    }

    func dismiss() {
        guard let rel = available else { return }
        defaults.set(rel.version, forKey: Key.dismissedTag)
        defaults.set(Date().timeIntervalSince1970, forKey: Key.dismissedAt)
        available = nil
    }

    func skipThisVersion() {
        guard let rel = available else { return }
        defaults.set(rel.version, forKey: Key.skipped)
        available = nil
    }

    /// リリースノートの Markdown を素のテキストにする
    static func plainText(_ md: String) -> String {
        var s = md.replacingOccurrences(of: "\r\n", with: "\n")
        s = s.replacingOccurrences(of: "\\[([^\\]]*)\\]\\([^)]*\\)", with: "$1", options: .regularExpression)
        s = s.replacingOccurrences(of: "(?m)^\\s{0,3}#{1,6}\\s*", with: "", options: .regularExpression)
        s = s.replacingOccurrences(of: "\\*\\*", with: "", options: .regularExpression)
        s = s.replacingOccurrences(of: "__", with: "")
        return s.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

/// メイン画面上部に出すバナー
struct UpdateBanner: View {
    @ObservedObject var checker: UpdateChecker
    let release: UpdateChecker.Release

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 10) {
                Image(systemName: "arrow.down.circle.fill")
                    .font(.system(size: 22))
                    .foregroundColor(.blue)
                Text("新しいバージョン v\(release.version) があります")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundColor(.white)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
                Button(action: { checker.dismiss() }) {
                    Text("閉じる")
                        .font(.system(size: 14))
                        .foregroundColor(Color(white: 0.6))
                }
            }
            HStack(spacing: 10) {
                Button(action: { checker.openAltStore() }) {
                    Text("AltStoreで更新")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundColor(.white)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 9)
                        .background(Color.blue)
                        .clipShape(Capsule())
                }
                Button(action: { checker.showNotes = true }) {
                    Text("更新内容")
                        .font(.system(size: 15, weight: .medium))
                        .foregroundColor(.blue)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 9)
                }
            }
            Button(action: { checker.skipThisVersion() }) {
                Text("このバージョンを通知しない")
                    .font(.system(size: 13))
                    .foregroundColor(Color(white: 0.5))
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(white: 0.11))
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}

/// 更新内容 (リリースノート) のシート
struct UpdateNotesSheet: View {
    let release: UpdateChecker.Release?
    let onClose: () -> Void

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            VStack(alignment: .leading, spacing: 16) {
                HStack {
                    Text("更新内容")
                        .font(.system(size: 24, weight: .bold))
                        .foregroundColor(.white)
                    Spacer()
                    Button(action: onClose) {
                        Text("閉じる")
                            .font(.system(size: 16, weight: .medium))
                            .foregroundColor(.blue)
                    }
                }
                if let r = release {
                    Text("v\(r.version)")
                        .font(.system(size: 15))
                        .foregroundColor(Color(white: 0.55))
                    ScrollView {
                        let text = UpdateChecker.plainText(r.body)
                        Text(text.isEmpty ? "更新内容の記載はありません。" : text)
                            .font(.system(size: 15))
                            .foregroundColor(.white)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
            .padding(20)
        }
        .preferredColorScheme(.dark)
    }
}
