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
    /// 更新内容シート用 (バナーが消えても、シートの中身は変わらないよう控えておく)
    @Published var notesLatest: Release?
    @Published var notesReleases: [Release] = []
    @Published var notesLoading = false
    @Published var notesFailed = false

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
        guard let rel = available ?? notesLatest else { return }
        let fallback = rel.htmlURL
        guard let alt = URL(string: "altstore://") else { return }
        UIApplication.shared.open(alt, options: [:]) { ok in
            if !ok, let u = URL(string: fallback) {
                UIApplication.shared.open(u, options: [:], completionHandler: nil)
            }
        }
    }

    var releasesPageURL: URL? {
        if let u = notesLatest?.htmlURL, let url = URL(string: u), !u.isEmpty { return url }
        return URL(string: "https://github.com/\(repo)/releases")
    }

    /// 更新内容を開く。保存済みの本文をすぐ出しつつ、現在のバージョンより新しいリリースを全部取得し直す。
    func openNotes() {
        guard let latest = available ?? notesLatest else { return }
        notesLatest = latest
        notesReleases = latest.body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? [] : [latest]
        notesFailed = false
        notesLoading = true
        showNotes = true
        let repo = self.repo
        Task { [weak self] in
            let list = await UpdateChecker.fetchAll(repo: repo)
            await MainActor.run {
                guard let self = self else { return }
                self.notesLoading = false
                guard let list = list else {
                    self.notesFailed = self.notesReleases.isEmpty
                    return
                }
                let current = UpdateChecker.currentVersion
                var newer = list.filter { UpdateChecker.isNewer($0.version, than: current) }
                if newer.isEmpty { newer = list.filter { $0.tag == latest.tag } }
                newer.sort { UpdateChecker.isNewer($0.version, than: $1.version) }
                if newer.isEmpty { self.notesFailed = self.notesReleases.isEmpty } else { self.notesReleases = newer }
            }
        }
    }

    private static func fetchAll(repo: String) async -> [Release]? {
        guard let url = URL(string: "https://api.github.com/repos/\(repo)/releases?per_page=30") else { return nil }
        var req = URLRequest(url: url)
        req.timeoutInterval = 10
        req.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        req.setValue("PhoneCam-iOS", forHTTPHeaderField: "User-Agent")
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            guard let http = resp as? HTTPURLResponse, http.statusCode == 200,
                  let arr = try JSONSerialization.jsonObject(with: data) as? [[String: Any]] else { return nil }
            return arr.compactMap { o in
                guard (o["draft"] as? Bool) != true, (o["prerelease"] as? Bool) != true,
                      let tag = o["tag_name"] as? String, !tag.isEmpty else { return nil }
                return Release(tag: tag, htmlURL: (o["html_url"] as? String) ?? "", body: (o["body"] as? String) ?? "")
            }
        } catch {
            return nil
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
                Button(action: { checker.openNotes() }) {
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

/// リリースノートの Markdown を、見出し・箇条書き・段落に分けたもの
struct NotesSection: Identifiable {
    enum Block: Identifiable {
        case bullet(Int, String)
        case paragraph(Int, String)
        var id: Int { switch self { case .bullet(let i, _), .paragraph(let i, _): return i } }
    }
    let id: Int
    let title: String?
    var blocks: [Block]
    var isWindows: Bool { (title ?? "").lowercased().contains("windows") }

    /// 「---」以降 (インストール案内) と、自動生成の "What's Changed" 以降は省く
    static func parse(_ md: String) -> [NotesSection] {
        var sections: [NotesSection] = []
        var title: String?
        var blocks: [Block] = []
        var n = 0
        func flush() {
            if title != nil || !blocks.isEmpty { sections.append(NotesSection(id: sections.count, title: title, blocks: blocks)) }
            blocks = []
        }
        let lines = md.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n")
        for raw in lines {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.isEmpty { continue }
            if line.hasPrefix("---") || line.lowercased().hasPrefix("## what's changed") || line.hasPrefix("**Full Changelog") { break }
            n += 1
            if line.hasPrefix("#") {
                let t = line.drop(while: { $0 == "#" }).trimmingCharacters(in: .whitespaces)
                if line.hasPrefix("###") { flush(); title = t }
                else if sections.isEmpty && blocks.isEmpty && title == nil { continue } // 先頭の「## vX の変更点」は省く
                else { blocks.append(.paragraph(n, t)) }
            } else if line.hasPrefix("- ") || line.hasPrefix("* ") {
                blocks.append(.bullet(n, String(line.dropFirst(2))))
            } else {
                blocks.append(.paragraph(n, line))
            }
        }
        flush()
        // iPhone に関係する節を先に、Windows の節を後ろに回す
        return sections.filter { !$0.isWindows } + sections.filter { $0.isWindows }
    }
}

private func inlineMarkdown(_ s: String) -> AttributedString {
    let opt = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
    return (try? AttributedString(markdown: s, options: opt)) ?? AttributedString(s)
}

private struct NotesBlockView: View {
    let block: NotesSection.Block
    var body: some View {
        switch block {
        case .bullet(_, let t):
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("•").foregroundColor(Color(white: 0.55))
                Text(inlineMarkdown(t)).fixedSize(horizontal: false, vertical: true)
            }
            .font(.system(size: 15)).foregroundColor(.white)
            .frame(maxWidth: .infinity, alignment: .leading)
        case .paragraph(_, let t):
            Text(inlineMarkdown(t))
                .font(.system(size: 15)).foregroundColor(.white)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

private struct NotesReleaseView: View {
    let release: UpdateChecker.Release
    let showVersion: Bool
    @State private var windowsOpen = false

    var body: some View {
        let sections = NotesSection.parse(release.body)
        VStack(alignment: .leading, spacing: 14) {
            if showVersion {
                Text("v\(release.version)")
                    .font(.system(size: 20, weight: .bold)).foregroundColor(.white)
            }
            if sections.isEmpty {
                Text("更新内容の記載はありません。")
                    .font(.system(size: 15)).foregroundColor(Color(white: 0.55))
            }
            ForEach(sections) { sec in
                if sec.isWindows {
                    DisclosureGroup(isExpanded: $windowsOpen) {
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(sec.blocks) { NotesBlockView(block: $0) }
                        }
                        .padding(.top, 8)
                    } label: {
                        Text("Windows 向けの変更")
                            .font(.system(size: 15, weight: .semibold)).foregroundColor(Color(white: 0.7))
                    }
                    .tint(Color(white: 0.7))
                } else {
                    VStack(alignment: .leading, spacing: 8) {
                        if let t = sec.title {
                            Text(t).font(.system(size: 17, weight: .semibold)).foregroundColor(.white)
                        }
                        ForEach(sec.blocks) { NotesBlockView(block: $0) }
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// 更新内容 (リリースノート) のシート
struct UpdateNotesSheet: View {
    @ObservedObject var checker: UpdateChecker
    let onClose: () -> Void

    var body: some View {
        NavigationStack {
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Color.black.ignoresSafeArea())
                .navigationTitle("更新内容")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .confirmationAction) {
                        Button("閉じる", action: onClose)
                    }
                }
                .safeAreaInset(edge: .bottom, spacing: 0) {
                    Button(action: { checker.openAltStore() }) {
                        Text("AltStoreで更新")
                            .font(.system(size: 17, weight: .semibold))
                            .foregroundColor(.white)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 14)
                            .background(Color.blue)
                            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                    }
                    .padding(.horizontal, 20).padding(.vertical, 12)
                    .background(Color.black)
                }
        }
        .preferredColorScheme(.dark)
    }

    @ViewBuilder private var content: some View {
        if !checker.notesReleases.isEmpty {
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    ForEach(checker.notesReleases, id: \.tag) { r in
                        NotesReleaseView(release: r, showVersion: true)
                    }
                    if checker.notesLoading {
                        ProgressView().frame(maxWidth: .infinity)
                    }
                }
                .padding(20)
            }
        } else if checker.notesLoading {
            VStack(spacing: 12) {
                ProgressView()
                Text("読み込んでいます…").font(.system(size: 15)).foregroundColor(Color(white: 0.6))
            }
        } else {
            VStack(spacing: 14) {
                Text(checker.notesFailed ? "更新内容を取得できませんでした" : "更新内容の記載はありません。")
                    .font(.system(size: 16)).foregroundColor(Color(white: 0.7))
                if let url = checker.releasesPageURL {
                    Link("リリースページを開く", destination: url).font(.system(size: 16, weight: .medium))
                }
            }
            .padding(20)
        }
    }
}
