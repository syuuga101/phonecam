import SwiftUI
import UIKit

struct ContentView: View {
    @EnvironmentObject var model: AppModel
    @StateObject private var updater = UpdateChecker()
    @AppStorage("showGrid") private var showGrid = false
    @AppStorage("showLevel") private var showLevel = true
    @State var rotationHint = false

    let cardColor = Color(white: 0.11)

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

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
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                if let rel = updater.available {
                    UpdateBanner(checker: updater, release: rel)
                }
                Text("PhoneCam")
                    .font(.system(size: 34, weight: .bold))
                    .foregroundColor(.white)

                statusHeader

                if model.previewEnabled {
                    let w = max(1, size.width - 40)
                    let tall = (model.previewAngle == 90 || model.previewAngle == 270)
                    let h = tall ? min(size.height * 0.58, w * 16.0 / 9.0) : w * 0.75
                    PreviewPanel(region: CGSize(width: w, height: h), showGrid: showGrid, showLevel: showLevel)
                }

                quickControls
                infoCard
                togglesCard
                wifiCard
                recordRow
                footnotes
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 24)
        }
    }

    private func landscapeLayout(_ size: CGSize) -> some View {
        HStack(spacing: 0) {
            landscapePreviewArea(CGSize(width: size.width * 0.7, height: size.height))
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if let rel = updater.available {
                        UpdateBanner(checker: updater, release: rel)
                    }
                    Text("PhoneCam")
                        .font(.system(size: 22, weight: .bold))
                        .foregroundColor(.white)
                    statusHeader
                    quickControls
                    recordRow
                    togglesCard
                    infoCard
                    wifiCard
                    footnotes
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 12)
            }
            .frame(width: size.width * 0.3)
        }
    }

    private func landscapePreviewArea(_ box: CGSize) -> some View {
        Group {
            if model.previewEnabled {
                PreviewPanel(region: CGSize(width: max(1, box.width - 12), height: max(1, box.height - 12)),
                             showGrid: showGrid, showLevel: showLevel)
            } else {
                VStack(spacing: 10) {
                    Image(systemName: "eye.slash")
                        .font(.system(size: 34, weight: .light))
                    Text("プレビューはオフです")
                        .font(.system(size: 17, weight: .medium))
                    Text("プレビューをオンにすると映る範囲を確認できます")
                        .font(.system(size: 13))
                        .multilineTextAlignment(.center)
                }
                .foregroundColor(Color(white: 0.55))
                .padding(.horizontal, 24)
            }
        }
        .frame(width: box.width, height: box.height)
    }

    // MARK: 部品

    private var statusHeader: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Circle()
                    .fill(model.isConnected ? Color.green : Color(white: 0.4))
                    .frame(width: 10, height: 10)
                Text(model.isConnected
                     ? (model.streamActiveUI ? "配信中 · \(model.connectionKind)" : "待機中（カメラ停止）· \(model.connectionKind)")
                     : "待機中")
                    .font(.system(size: 17, weight: .medium))
                    .foregroundColor(.white)
            }
            if model.micEnabled && model.micMuted {
                MutePill()
            }
        }
    }

    static func lensName(_ l: String) -> String {
        switch l {
        case "ultrawide": return "超広角"
        case "front": return "インカメ"
        default: return "広角"
        }
    }

    private var lensPicker: some View {
        HStack(spacing: 4) {
            ForEach(model.lenses, id: \.self) { l in
                let selected = (model.lens == l)
                Button(action: { model.selectLens(l) }) {
                    Text(ContentView.lensName(l))
                        .font(.system(size: 14, weight: .semibold))
                        .lineLimit(1)
                        .minimumScaleFactor(0.7)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                        .foregroundColor(selected ? Color.black : Color.white)
                        .background(selected ? Color.white : Color.clear)
                        .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
                }
                .buttonStyle(.plain)
            }
        }
        .padding(3)
        .background(Color(white: 0.16))
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }

    private var quickControls: some View {
        VStack(spacing: 12) {
            Button(action: { model.previewEnabled.toggle() }) {
                HStack(spacing: 8) {
                    Image(systemName: model.previewEnabled ? "eye.slash" : "eye")
                    Text(model.previewEnabled ? "プレビューを止める" : "プレビュー")
                }
                .font(.system(size: 17, weight: .semibold))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
                .foregroundColor(model.previewEnabled ? Color.white : Color.black)
                .background(model.previewEnabled ? Color(white: 0.2) : Color.white)
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            }
            .buttonStyle(.plain)

            if model.lenses.count > 1 {
                lensPicker
            }

            HStack(spacing: 10) {
                if model.micEnabled {
                    Button(action: { model.toggleMute() }) {
                        HStack(spacing: 6) {
                            Image(systemName: model.micMuted ? "mic.slash.fill" : "mic.slash")
                            Text(model.micMuted ? "ミュート解除" : "ミュート")
                                .lineLimit(1)
                                .minimumScaleFactor(0.7)
                        }
                        .font(.system(size: 15, weight: .semibold))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                        .foregroundColor(.white)
                        .background(model.micMuted ? Color.red : Color(white: 0.2))
                        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                    }
                    .buttonStyle(.plain)
                }
            }
            if model.previewEnabled {
                HStack(spacing: 10) {
                    chip(title: "グリッド", symbol: "grid", on: showGrid) { showGrid.toggle() }
                    chip(title: "水平メーター", symbol: "level", on: showLevel) { showLevel.toggle() }
                }
            }
        }
    }

    private func chip(title: String, symbol: String, on: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Image(systemName: symbol)
                Text(title)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
            }
            .font(.system(size: 15, weight: .semibold))
            .frame(maxWidth: .infinity)
            .padding(.vertical, 12)
            .foregroundColor(on ? Color.black : Color.white)
            .background(on ? Color.white : Color(white: 0.2))
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        }
        .buttonStyle(.plain)
    }
}
