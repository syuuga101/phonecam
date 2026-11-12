import SwiftUI
import UIKit

struct ContentView: View {
    @EnvironmentObject var model: AppModel
    @StateObject private var updater = UpdateChecker()
    // 設定画面と共有するキー: "showGrid" / "showLevel" (どちらも Bool, @AppStorage)
    @AppStorage("showGrid") private var showGrid = false
    @AppStorage("showLevel") private var showLevel = true
    // プレビューの大きさ。previewHeight == 0 は「幅いっぱいの 16:9」(未保存)
    @AppStorage("previewHeight") private var previewHeight: Double = 0
    @AppStorage("previewWidthRatio") private var previewWidthRatio: Double = 0.6
    @State var rotationHint = false
    @State private var heightDragBase: Double?
    @State private var ratioDragBase: Double?

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
                    .onChange(of: landscape) { rotationHint = true }
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
        .onAppear { updater.checkIfNeeded() }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in
            updater.checkIfNeeded()
        }
        .onChange(of: model.previewEnabled) {
            if model.previewEnabled { rotationHint = false }
            model.setMotionActive(model.previewEnabled && showLevel)
        }
        .onChange(of: showLevel) {
            model.setMotionActive(model.previewEnabled && showLevel)
        }
        .onChange(of: model.rotationAngle) { rotationHint = true }
        .sheet(isPresented: $updater.showNotes) {
            UpdateNotesSheet(release: updater.available) { updater.showNotes = false }
        }
        .simultaneousGesture(
            DragGesture(minimumDistance: 0).onChanged { _ in model.noteTouch() }
        )
    }

    // MARK: レイアウト

    private func portraitLayout(_ size: CGSize) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            topBar
            statusHeader
                .padding(.horizontal, 20)
                .padding(.bottom, 6)
            controlList(previewListWidth: size.width - 32)
        }
    }

    private func landscapeLayout(_ size: CGSize) -> some View {
        let ratio = min(0.75, max(0.4, previewWidthRatio))
        let gripW: CGFloat = 20
        let previewW = max(1, size.width * CGFloat(ratio) - gripW)
        return HStack(spacing: 0) {
            landscapePreviewArea(CGSize(width: previewW, height: size.height))
            verticalGrabber(totalWidth: size.width)
                .frame(width: gripW, height: size.height)
            VStack(alignment: .leading, spacing: 0) {
                topBar
                statusHeader
                    .padding(.horizontal, 12)
                    .padding(.bottom, 6)
                controlList(previewListWidth: nil)
            }
            .frame(maxWidth: .infinity)
        }
    }

    private func landscapePreviewArea(_ box: CGSize) -> some View {
        Group {
            if model.previewEnabled {
                PreviewPanel(region: CGSize(width: max(1, box.width - 8), height: max(1, box.height - 16)),
                             showGrid: showGrid, showLevel: showLevel, cornerRadius: 12)
            } else {
                previewOffPlaceholder
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .padding(.vertical, 8)
                    .padding(.leading, 8)
            }
        }
        .frame(width: box.width, height: box.height)
    }

    // MARK: プレビューカード

    private var previewOffPlaceholder: some View {
        ZStack {
            Color.black
            VStack(spacing: 8) {
                Image(systemName: "eye.slash")
                    .font(.system(size: 28, weight: .light))
                Text("プレビューはオフです")
                    .font(.system(size: 15, weight: .medium))
            }
            .foregroundColor(Color(white: 0.6))
        }
    }

    private func clampedPreviewHeight(width: CGFloat) -> CGFloat {
        let base = previewHeight > 0 ? CGFloat(previewHeight) : width * 9.0 / 16.0
        return min(560, max(160, base))
    }

    /// 幅いっぱいのプレビュー + 下辺のつまみ (ドラッグで高さを変更し、保存する)
    private func previewCard(listWidth: CGFloat) -> some View {
        let h = clampedPreviewHeight(width: max(1, listWidth))
        return VStack(spacing: 0) {
            GeometryReader { g in
                Group {
                    if model.previewEnabled {
                        PreviewPanel(region: CGSize(width: max(1, g.size.width), height: h),
                                     showGrid: showGrid, showLevel: showLevel, cornerRadius: 0)
                    } else {
                        previewOffPlaceholder
                    }
                }
                .frame(width: g.size.width, height: h)
            }
            .frame(height: h)

            Capsule()
                .fill(Color.secondary.opacity(0.4))
                .frame(width: 36, height: 5)
                .frame(maxWidth: .infinity)
                .frame(height: 20)
                .contentShape(Rectangle())
                .highPriorityGesture(
                    DragGesture(minimumDistance: 1, coordinateSpace: .global)
                        .onChanged { v in
                            if heightDragBase == nil { heightDragBase = Double(h) }
                            let base = heightDragBase ?? Double(h)
                            previewHeight = min(560, max(160, base + Double(v.translation.height)))
                        }
                        .onEnded { _ in heightDragBase = nil }
                )
                .accessibilityLabel("プレビューの高さを変更")
        }
    }

    private func verticalGrabber(totalWidth: CGFloat) -> some View {
        Capsule()
            .fill(Color.secondary.opacity(0.4))
            .frame(width: 5, height: 36)
            .frame(maxHeight: .infinity)
            .frame(width: 20)
            .contentShape(Rectangle())
            .highPriorityGesture(
                DragGesture(minimumDistance: 1, coordinateSpace: .global)
                    .onChanged { v in
                        if ratioDragBase == nil { ratioDragBase = min(0.75, max(0.4, previewWidthRatio)) }
                        let base = ratioDragBase ?? 0.6
                        let delta = Double(v.translation.width) / Double(max(1, totalWidth))
                        previewWidthRatio = min(0.75, max(0.4, base + delta))
                    }
                    .onEnded { _ in ratioDragBase = nil }
            )
            .accessibilityLabel("プレビューの幅を変更")
    }

    // MARK: リスト

    private func controlList(previewListWidth: CGFloat?) -> some View {
        List {
            if let rel = updater.available {
                Section {
                    UpdateBanner(checker: updater, release: rel)
                        .listRowBackground(Color.clear)
                        .listRowInsets(EdgeInsets(top: 4, leading: 0, bottom: 4, trailing: 0))
                }
            }

            if let w = previewListWidth {
                Section {
                    previewCard(listWidth: w)
                        .listRowInsets(EdgeInsets())
                }
            }

            if model.lenses.count > 1 {
                Section {
                    lensPicker
                } header: {
                    Text("レンズ")
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
                recordRow
            } header: {
                Text("音声と録画")
            }

            infoSection

            Section {
                HStack(spacing: 12) {
                    RowIcon(symbol: "eye")
                    Toggle("プレビュー", isOn: $model.previewEnabled)
                }
            } header: {
                Text("表示")
            } footer: {
                if !model.previewEnabled && rotationHint {
                    Text("プレビューをオンにすると映る範囲を確認できます")
                }
            }

            Section {
                footnotes
                    .listRowBackground(Color.clear)
                    .listRowSeparator(.hidden)
            }
        }
        .listStyle(.insetGrouped)
        .scrollContentBackground(.hidden)
    }

    // MARK: 部品

    private var topBar: some View {
        HStack(spacing: 0) {
            Image(systemName: "camera")
                .font(.system(size: 26, weight: .regular))
                .foregroundStyle(.primary)
                .padding(.leading, 20)
                .accessibilityHidden(true)
            Spacer()
            NavigationLink {
                SettingsView(model: model)
            } label: {
                Image(systemName: "gearshape")
                    .font(.system(size: 20, weight: .regular))
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .padding(.trailing, 8)
            .accessibilityLabel("設定")
        }
        .frame(height: 44)
    }

    var statusText: String {
        if !model.isConnected { return String(localized: "待機中") }
        let kind = model.connectionKind
        if model.streamActiveUI { return String(localized: "配信中 · \(kind)") }
        return String(localized: "待機中（カメラ停止）· \(kind)")
    }

    private var statusHeader: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Circle()
                    .fill(model.isConnected ? Color.green : Color(.systemGray3))
                    .frame(width: 10, height: 10)
                Text(statusText)
                    .font(.system(size: 19, weight: .semibold))
                    .foregroundStyle(.primary)
            }
            if model.micEnabled && model.micMuted {
                MutePill()
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    static func lensName(_ l: String) -> String {
        switch l {
        case "ultrawide": return "0.5×"
        case "front": return String(localized: "前面")
        default: return "1×"
        }
    }

    private var lensPicker: some View {
        let order = ["ultrawide", "wide", "front"].filter { model.lenses.contains($0) }
        return Picker("レンズ", selection: Binding(
            get: { model.lens },
            set: { model.selectLens($0) }
        )) {
            ForEach(order, id: \.self) { l in
                Text(ContentView.lensName(l)).tag(l)
            }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
    }
}
