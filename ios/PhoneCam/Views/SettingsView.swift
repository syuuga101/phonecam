import SwiftUI

// MARK: - 設定画面 (標準の inset grouped List)

struct SettingsView: View {
    @ObservedObject var model: AppModel
    @State private var showScanner = false
    @AppStorage("showGrid") private var showGrid = false
    @AppStorage("showLevel") private var showLevel = true

    var body: some View {
        List {
            generalSection
            screenSection
            connectionSection
            aboutSection
        }
        .listStyle(.insetGrouped)
        .navigationTitle("設定")
        .navigationBarTitleDisplayMode(.inline)
    }

    // MARK: 一般

    private var generalSection: some View {
        Section("一般") {
            NavigationLink {
                LanguageSettingsView()
            } label: {
                SettingsLabel("言語", symbol: "globe")
            }
            Link(destination: AppInfo.translateURL) {
                SettingsLabel("翻訳に参加する", symbol: "character.bubble")
            }
        }
    }

    // MARK: 画面

    private var screenSection: some View {
        Section("画面") {
            Toggle(isOn: Binding(
                get: { model.dimScreen },
                set: { model.setDim($0) }
            )) {
                SettingsLabel("画面を暗くする", symbol: "sun.min")
            }
            Toggle(isOn: Binding(
                get: { model.screenBlack },
                set: { model.setScreenBlack($0) }
            )) {
                SettingsLabel("画面を真っ黒にする", symbol: "moon")
            }
            .disabled(!model.isConnected)
            Picker(selection: Binding(
                get: { model.autoBlackSeconds },
                set: { v in model.setAutoBlackSeconds(v) }
            )) {
                Text("しない").tag(0)
                Text("10秒").tag(10)
                Text("30秒").tag(30)
                Text("60秒").tag(60)
            } label: {
                SettingsLabel("自動で黒くする", symbol: "timer")
            }
            Toggle(isOn: $model.vibrateOnConnect) {
                SettingsLabel("接続時に振動", symbol: "iphone.radiowaves.left.and.right")
            }
            Toggle(isOn: $showGrid) {
                SettingsLabel("グリッド", symbol: "grid")
            }
            Toggle(isOn: $showLevel) {
                SettingsLabel("水平メーター", symbol: "level")
            }
        }
    }

    // MARK: 接続

    private var connectionSection: some View {
        Section {
            Toggle(isOn: Binding(
                get: { model.wifiAllowed },
                set: { model.setWifiAllowed($0) }
            )) {
                SettingsLabel("Wi-Fiからの接続を許可", symbol: "wifi")
            }
            if model.wifiAllowed {
                Button {
                    showScanner = true
                } label: {
                    Label("wireless.scan.title", systemImage: "qrcode.viewfinder")
                }
                .disabled(model.recActive)
                .sheet(isPresented: $showScanner) { WirelessScanner(model: model) }
                VStack(spacing: 8) {
                    Text(model.pairingCode)
                        .font(.title.monospaced())
                        .lineLimit(1)
                        .minimumScaleFactor(0.5)
                        .textSelection(.enabled)
                    Button("コードを変更") {
                        model.regeneratePairingCode()
                    }
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 4)
            }
        } header: {
            Text("接続")
        } footer: {
            if model.wifiAllowed {
                Text("PCの『ペアリングコード』欄に入力します。USB接続では不要です。")
            }
        }
    }

    // MARK: アプリについて

    private var aboutSection: some View {
        Section("アプリについて") {
            LabeledContent {
                Text(AppInfo.author)
            } label: {
                SettingsLabel("作成者", symbol: "person")
            }
            Link(destination: AppInfo.repoURL) {
                LabeledContent {
                    Image(systemName: "arrow.up.right.square").foregroundStyle(.secondary)
                } label: {
                    SettingsLabel("配布場所", symbol: "arrow.down.circle")
                }
            }
            Link(destination: AppInfo.translateURL) {
                LabeledContent {
                    Image(systemName: "arrow.up.right.square").foregroundStyle(.secondary)
                } label: {
                    SettingsLabel("翻訳", symbol: "character.bubble")
                }
            }
            LabeledContent {
                Text(AppInfo.license)
            } label: {
                SettingsLabel("ライセンス", symbol: "doc.text")
            }
            LabeledContent {
                Text(AppInfo.version)
            } label: {
                SettingsLabel("バージョン", symbol: "info.circle")
            }
            Link(destination: AppInfo.guideURL) {
                LabeledContent {
                    Image(systemName: "arrow.up.right.square").foregroundStyle(.secondary)
                } label: {
                    SettingsLabel("使い方ガイド", symbol: "book")
                }
            }
        }
    }
}

// MARK: - 行ラベル (左に 28pt 幅のアイコン列)

private struct SettingsLabel: View {
    let title: LocalizedStringKey
    let symbol: String

    init(_ title: LocalizedStringKey, symbol: String) {
        self.title = title
        self.symbol = symbol
    }

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: symbol)
                .foregroundStyle(.secondary)
                .frame(width: 28)
            Text(title)
                .foregroundStyle(.primary)
        }
    }
}

// MARK: - 言語選択

private struct LanguageSettingsView: View {
    private enum Choice: String, CaseIterable, Identifiable {
        case system, ja, en
        var id: String { rawValue }
        var title: LocalizedStringKey {
            switch self {
            case .system: return "システムに合わせる"
            case .ja: return "日本語"
            case .en: return "English"
            }
        }
    }

    @State private var choice: Choice = LanguageSettingsView.current()

    private static func current() -> Choice {
        guard let arr = UserDefaults.standard.array(forKey: "AppleLanguages") as? [String],
              let first = arr.first else { return .system }
        if first.hasPrefix("ja") { return .ja }
        if first.hasPrefix("en") { return .en }
        return .system
    }

    var body: some View {
        List {
            Section {
                ForEach(Choice.allCases) { c in
                    Button {
                        choice = c
                        apply(c)
                    } label: {
                        HStack {
                            Text(c.title).foregroundStyle(.primary)
                            Spacer()
                            if choice == c {
                                Image(systemName: "checkmark").foregroundStyle(.tint)
                            }
                        }
                    }
                }
            } footer: {
                Text("アプリを再起動すると反映されます")
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("言語")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func apply(_ c: Choice) {
        switch c {
        case .system: UserDefaults.standard.removeObject(forKey: "AppleLanguages")
        case .ja: UserDefaults.standard.set(["ja"], forKey: "AppleLanguages")
        case .en: UserDefaults.standard.set(["en"], forKey: "AppleLanguages")
        }
    }
}
