import SwiftUI
import UIKit

struct ContentView: View {
    @EnvironmentObject var model: AppModel
    @StateObject private var updater = UpdateChecker()
    @StateObject private var recordings = RecordingsStore()
    // 設定画面と共有するキー: "showGrid" / "showLevel" (どちらも Bool, @AppStorage)
    @AppStorage("showGrid") private var showGrid = false
    @AppStorage("showLevel") private var showLevel = true
    @State private var showRecordings = false
    @State private var showLandscapeSettings = false

    /// 横画面の右側の操作列の幅
    private let controlColumnWidth: CGFloat = 96

    var body: some View {
        ZStack {
            NavigationStack {
                GeometryReader { geo in
                    let landscape = geo.size.width > geo.size.height
                    Group {
                        if landscape {
                            landscapeLayout(geo.size)
                        } else {
                            portraitLayout(geo.size)
                        }
                    }
                    .frame(width: geo.size.width, height: geo.size.height)
                }
                .background(Color(.systemGroupedBackground).ignoresSafeArea())
                .toolbar(.hidden, for: .navigationBar)
            }

            blackOverlay
        }
        .overlay(alignment: .top) {
            toastOverlay
        }
        .animation(.easeOut(duration: 0.2), value: model.toast)
        .statusBarHidden(model.screenBlack)
        .onAppear {
            updater.checkIfNeeded()
            recordings.refresh()
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in
            updater.checkIfNeeded()
        }
        .onChange(of: model.previewEnabled) {
            model.setMotionActive(model.previewEnabled && showLevel)
        }
        .onChange(of: showLevel) {
            model.setMotionActive(model.previewEnabled && showLevel)
        }
        .onChange(of: model.recActive) {
            // 録画が終わったら、ファイルが書き終わるのを待ってからサムネイルを更新する
            if !model.recActive {
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { recordings.refresh() }
            }
        }
        .sheet(isPresented: $updater.showNotes) {
            UpdateNotesSheet(release: updater.available) { updater.showNotes = false }
        }
        .sheet(isPresented: $showRecordings) {
            NavigationStack {
                RecordingsView()
                    .toolbar {
                        ToolbarItem(placement: .confirmationAction) {
                            Button("完了") { showRecordings = false }
                        }
                    }
            }
        }
        .sheet(isPresented: $showLandscapeSettings) {
            NavigationStack {
                SettingsView(model: model, showStreamInfo: true) { showLandscapeSettings = false }
            }
            .environmentObject(model)
        }
        .simultaneousGesture(
            DragGesture(minimumDistance: 0).onChanged { _ in model.noteTouch() }
        )
    }

    // MARK: レイアウト

    /// 縦画面: プレビュー → 操作バー → 設定 → 配信情報 (一番下)
    private func portraitLayout(_ size: CGSize) -> some View {
        let w = max(1, size.width - 32)
        let stage = CGSize(width: w, height: (w * 9.0 / 16.0).rounded())
        return VStack(spacing: 0) {
            PreviewStage(size: stage, showGrid: showGrid, showLevel: showLevel, cornerButtons: true)
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                .padding(.top, 8)
            controlBar
                .padding(.top, 10)
                .padding(.horizontal, 16)
            if !model.recError.isEmpty {
                Text(model.recError)
                    .font(.system(size: 13))
                    .foregroundColor(.orange)
                    .padding(.horizontal, 16)
                    .padding(.top, 4)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            controlList
        }
    }

    /// 横画面: 左にプレビュー (縦横比を保って最大)、右に幅固定のアイコンだけの操作列
    private func landscapeLayout(_ size: CGSize) -> some View {
        let area = CGSize(width: max(1, size.width - controlColumnWidth - 8),
                          height: max(1, size.height - 16))
        let stage = ContentView.fit16x9(in: area)
        return HStack(spacing: 0) {
            PreviewStage(size: stage, showGrid: showGrid, showLevel: showLevel, cornerButtons: false)
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                .frame(width: area.width, height: size.height)
                .padding(.leading, 8)
            landscapeColumn
                .frame(width: controlColumnWidth, height: size.height)
        }
    }

    static func fit16x9(in area: CGSize) -> CGSize {
        let aspect: CGFloat = 16.0 / 9.0
        if area.width / area.height > aspect {
            return CGSize(width: (area.height * aspect).rounded(), height: area.height)
        }
        return CGSize(width: area.width, height: (area.width / aspect).rounded())
    }

    // MARK: 操作バー / 操作列

    /// 縦画面の操作バー (プレビューのすぐ下、1 行): 左にサムネイル / 中央に録画 / 右にライト
    private var controlBar: some View {
        VStack(spacing: 6) {
            RecordTimerPill()
            HStack(spacing: 0) {
                LastRecordingButton(store: recordings) { showRecordings = true }
                    .frame(width: 64, alignment: .leading)
                Spacer(minLength: 0)
                RecordButton(recording: model.recActive) { model.toggleRecording() }
                Spacer(minLength: 0)
                TorchButton(style: .bar, size: 52)
                    .frame(width: 64, alignment: .trailing)
            }
        }
    }

    /// 横画面の操作列 (アイコンだけ): 上からライト、録画 (中央)、プレビュー、設定
    private var landscapeColumn: some View {
        VStack(spacing: 0) {
            TorchButton(style: .bar, size: 48)
            Spacer(minLength: 8)
            VStack(spacing: 6) {
                RecordTimerPill()
                RecordButton(recording: model.recActive) { model.toggleRecording() }
            }
            Spacer(minLength: 8)
            PreviewToggleButton(style: .bar, size: 48)
            RoundIconButton(symbol: "gearshape", size: 48, style: .bar, label: "設定") {
                showLandscapeSettings = true
            }
            .padding(.top, 10)
        }
        .padding(.vertical, 12)
    }

    // MARK: リスト (設定 → 配信情報)

    private var controlList: some View {
        List {
            if let rel = updater.available {
                Section {
                    UpdateBanner(checker: updater, release: rel)
                        .listRowBackground(Color.clear)
                        .listRowInsets(EdgeInsets(top: 4, leading: 0, bottom: 4, trailing: 0))
                }
            }

            Section {
                if model.micEnabled {
                    HStack(spacing: 12) {
                        RowIcon(symbol: model.micMuted ? "mic.slash" : "mic")
                        Toggle("マイク（ミュート）", isOn: Binding(
                            get: { model.micMuted },
                            set: { _ in model.toggleMute() }
                        ))
                    }
                }
                NavigationLink {
                    SettingsView(model: model)
                } label: {
                    HStack(spacing: 12) {
                        RowIcon(symbol: "gearshape")
                        Text("設定")
                    }
                }
            }

            infoSection

            Section {
                footnotes
                    .listRowBackground(Color.clear)
                    .listRowSeparator(.hidden)
            }
        }
        .listStyle(.insetGrouped)
        .scrollContentBackground(.hidden)
    }
}
