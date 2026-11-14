import SwiftUI
import AVFoundation
import UIKit

// MARK: - 状態のピル (プレビュー左上)

/// 「● 配信中」(赤い点) / 「待機中」 / 「PC 未接続」。半透明の黒い背景に白い文字。
struct StatusPill: View {
    @EnvironmentObject var model: AppModel

    private var text: String {
        if !model.isConnected { return String(localized: "PC 未接続") }
        return model.streamActiveUI ? String(localized: "配信中") : String(localized: "待機中")
    }

    private var live: Bool { model.isConnected && model.streamActiveUI }

    var body: some View {
        HStack(spacing: 6) {
            if live {
                Circle().fill(Color.red).frame(width: 8, height: 8)
            }
            Text(text)
                .font(.system(size: 13, weight: .semibold))
                .lineLimit(1)
            if model.micMuted {
                Image(systemName: "mic.slash.fill")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(.red)
                    .accessibilityLabel(Text("ミュート中"))
            }
        }
        .foregroundColor(.white)
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(Color.black.opacity(0.55))
        .clipShape(Capsule())
    }
}

// MARK: - アイコンボタン

/// 丸いアイコンボタン。overlay = プレビューの上 (黒い半透明)、bar = 操作バー / 横画面の操作列 (システムの塗り)。
struct RoundIconButton: View {
    enum Style { case overlay, bar }

    let symbol: String
    var active: Bool = false
    var enabled: Bool = true
    var size: CGFloat = 36
    var style: Style = .overlay
    let label: LocalizedStringKey
    var value: LocalizedStringKey = ""
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: size * 0.42, weight: .semibold))
                .foregroundColor(foreground)
                .frame(width: size, height: size)
                .background(background)
                .clipShape(Circle())
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .opacity(enabled ? 1 : 0.35)
        .accessibilityLabel(label)
        .accessibilityValue(value)
    }

    private var foreground: Color {
        if active { return .black }
        return style == .overlay ? .white : .primary
    }

    private var background: Color {
        if active { return Color.yellow }
        return style == .overlay ? Color.black.opacity(0.5) : Color(.tertiarySystemFill)
    }
}

// MARK: - レンズ切り替え (プレビュー下中央の小さなチップ)

struct LensChips: View {
    @EnvironmentObject var model: AppModel

    private var order: [String] {
        ["ultrawide", "wide", "front"].filter { model.lenses.contains($0) }
    }

    private func title(_ l: String) -> String {
        switch l {
        case "ultrawide": return ".5"
        default: return "1×"
        }
    }

    private func axLabel(_ l: String) -> LocalizedStringKey {
        switch l {
        case "ultrawide": return "超広角カメラ"
        case "front": return "インカメラ"
        default: return "広角カメラ"
        }
    }

    var body: some View {
        HStack(spacing: 4) {
            ForEach(order, id: \.self) { l in
                let selected = model.lens == l
                Button {
                    model.selectLens(l)
                } label: {
                    Group {
                        if l == "front" {
                            Image(systemName: "arrow.triangle.2.circlepath.camera")
                                .font(.system(size: 12, weight: .semibold))
                        } else {
                            Text(title(l))
                                .font(.system(size: 12, weight: .semibold))
                        }
                    }
                    .foregroundColor(selected ? Color.yellow : Color.white)
                    .frame(width: 30, height: 30)
                    .background(selected ? Color.black.opacity(0.55) : Color.clear)
                    .clipShape(Circle())
                    .contentShape(Circle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(axLabel(l))
                .accessibilityAddTraits(selected ? .isSelected : [])
            }
        }
        .padding(3)
        .background(Color.black.opacity(0.35))
        .clipShape(Capsule())
    }
}

// MARK: - 録画ボタン (iOS のカメラアプリと同じ形)

/// 白い輪の中に赤い丸。録画中は赤い角丸の四角に変わる。
struct RecordButton: View {
    let recording: Bool
    let action: () -> Void

    var body: some View {
        Button {
            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
            action()
        } label: {
            ZStack {
                Circle()
                    .strokeBorder(Color.primary, lineWidth: 4)
                    .frame(width: 72, height: 72)
                RoundedRectangle(cornerRadius: recording ? 7 : 30, style: .continuous)
                    .fill(Color.red)
                    .frame(width: recording ? 28 : 60, height: recording ? 28 : 60)
            }
            .frame(width: 76, height: 76)
            .contentShape(Circle())
        }
        .buttonStyle(RecordPressStyle())
        .animation(.easeInOut(duration: 0.18), value: recording)
        .accessibilityLabel(recording ? Text("録画を停止") : Text("録画を開始"))
    }
}

private struct RecordPressStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.92 : 1)
            .animation(.easeOut(duration: 0.1), value: configuration.isPressed)
    }
}

/// 録画中に録画ボタンの上へ出す、赤い背景の経過時間。録画していないときは場所だけ確保する。
struct RecordTimerPill: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var metrics: LiveMetrics

    var body: some View {
        Text(ContentView.timeText(metrics.recSeconds))
            .font(.system(size: 13, weight: .semibold).monospacedDigit())
            .foregroundColor(.white)
            .padding(.horizontal, 10)
            .padding(.vertical, 3)
            .background(Color.red)
            .clipShape(Capsule())
            .opacity(model.recActive ? 1 : 0)
            .accessibilityHidden(!model.recActive)
    }
}

// MARK: - プレビューのステージ (プレビュー + 重ねる UI + ピンチ)

struct PreviewStage: View {
    @EnvironmentObject var model: AppModel
    let size: CGSize
    let showGrid: Bool
    let showLevel: Bool
    /// 縦画面では右上に目 (プレビューの表示 / 非表示) のボタンだけを重ねる。ライトは操作バー (縦) / 操作列 (横) にある。横画面は右側の操作列にあるので出さない。
    let cornerButtons: Bool

    @State private var pinchBase: Double?
    @State private var zoomPillVisible = false
    @State private var hideWork: DispatchWorkItem?

    var body: some View {
        ZStack {
            if model.previewEnabled {
                PreviewPanel(region: size, showGrid: showGrid, showLevel: showLevel, cornerRadius: 0)
            } else {
                Color.black
                Button {
                    model.previewEnabled = true
                } label: {
                    Label("プレビューを表示", systemImage: "eye")
                        .font(.system(size: 15, weight: .medium))
                        .foregroundColor(.white)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 9)
                        .background(Color.white.opacity(0.18))
                        .clipShape(Capsule())
                }
                .buttonStyle(.plain)
            }

            VStack(spacing: 0) {
                HStack(alignment: .top) {
                    StatusPill()
                    Spacer(minLength: 8)
                    if cornerButtons {
                        PreviewToggleButton(style: .overlay)
                    }
                }
                Spacer(minLength: 0)
                LensChips()
            }
            .padding(8)

            if zoomPillVisible {
                Text(String(format: "%.1f×", model.zoom))
                    .font(.system(size: 15, weight: .semibold).monospacedDigit())
                    .foregroundColor(.yellow)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 6)
                    .background(Color.black.opacity(0.6))
                    .clipShape(Capsule())
                    .allowsHitTesting(false)
                    .transition(.opacity)
            }
        }
        .frame(width: size.width, height: size.height)
        .background(Color.black)
        .clipped()
        .contentShape(Rectangle())
        .gesture(pinch)
        .onTapGesture(count: 2) {
            model.setZoom(1.0, final: true)
            flashZoomPill()
        }
        .animation(.easeOut(duration: 0.15), value: zoomPillVisible)
    }

    private var pinch: some Gesture {
        MagnifyGesture()
            .onChanged { v in
                if pinchBase == nil {
                    pinchBase = model.zoom
                    model.zoomGestureActive = true
                }
                hideWork?.cancel()
                zoomPillVisible = true
                model.setZoom((pinchBase ?? 1.0) * Double(v.magnification), final: false)
            }
            .onEnded { v in
                model.setZoom((pinchBase ?? model.zoom) * Double(v.magnification), final: true)
                pinchBase = nil
                model.zoomGestureActive = false
                scheduleHide()
            }
    }

    private func flashZoomPill() {
        hideWork?.cancel()
        zoomPillVisible = true
        scheduleHide()
    }

    private func scheduleHide() {
        let w = DispatchWorkItem { zoomPillVisible = false }
        hideWork = w
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.8, execute: w)
    }
}

// MARK: - ライト / プレビュー切り替えボタン

struct TorchButton: View {
    @EnvironmentObject var model: AppModel
    var style: RoundIconButton.Style
    var size: CGFloat = 36

    var body: some View {
        RoundIconButton(symbol: model.torchOn ? "flashlight.on.fill" : "flashlight.off.fill",
                        active: model.torchOn,
                        enabled: model.torchAvailable,
                        size: size,
                        style: style,
                        label: "ライト",
                        value: model.torchOn ? "オン" : "オフ") {
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            model.toggleTorch()
        }
    }
}

/// マイクのミュート。音声がつながっていなくても常に操作できる (ミュート中は赤)。
struct MuteButton: View {
    @EnvironmentObject var model: AppModel
    var size: CGFloat = 48

    var body: some View {
        Button {
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            model.toggleMute()
        } label: {
            Image(systemName: model.micMuted ? "mic.slash.fill" : "mic.fill")
                .font(.system(size: size * 0.42, weight: .semibold))
                .foregroundColor(model.micMuted ? .white : .primary)
                .frame(width: size, height: size)
                .background(model.micMuted ? Color.red : Color(.tertiarySystemFill))
                .clipShape(Circle())
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(Text("マイク（ミュート）"))
        .accessibilityValue(model.micMuted ? Text("ミュート中") : Text(""))
    }
}

struct PreviewToggleButton: View {
    @EnvironmentObject var model: AppModel
    var style: RoundIconButton.Style
    var size: CGFloat = 36

    var body: some View {
        RoundIconButton(symbol: model.previewEnabled ? "eye" : "eye.slash",
                        size: size,
                        style: style,
                        label: model.previewEnabled ? "プレビューを隠す" : "プレビューを表示") {
            model.previewEnabled.toggle()
        }
    }
}

// MARK: - 録画ファイルの一覧とサムネイル

struct RecordingItem: Identifiable {
    let url: URL
    let date: Date
    let size: Int64
    var id: URL { url }
}

final class RecordingsStore: ObservableObject {
    @Published var items: [RecordingItem] = []
    @Published var latestThumbnail: UIImage?
    private var thumbURL: URL?

    static func thumbnail(for url: URL, maxSide: CGFloat = 240) async -> UIImage? {
        let gen = AVAssetImageGenerator(asset: AVURLAsset(url: url))
        gen.appliesPreferredTrackTransform = true
        gen.maximumSize = CGSize(width: maxSide, height: maxSide)
        do {
            let (cg, _) = try await gen.image(at: CMTime(seconds: 0.2, preferredTimescale: 600))
            return UIImage(cgImage: cg)
        } catch {
            if let (cg, _) = try? await gen.image(at: .zero) { return UIImage(cgImage: cg) }
            return nil
        }
    }

    /// Documents の録画ファイルを新しい順に読み直す (録画中は呼ばない)
    func refresh() {
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let dir = Recorder.documentsURL()
            let keys: [URLResourceKey] = [.contentModificationDateKey, .fileSizeKey]
            let urls = (try? FileManager.default.contentsOfDirectory(
                at: dir, includingPropertiesForKeys: keys, options: [.skipsHiddenFiles])) ?? []
            var list: [RecordingItem] = []
            for u in urls where ["mp4", "mov", "m4v"].contains(u.pathExtension.lowercased()) {
                let v = try? u.resourceValues(forKeys: Set(keys))
                list.append(RecordingItem(url: u,
                                          date: v?.contentModificationDate ?? .distantPast,
                                          size: Int64(v?.fileSize ?? 0)))
            }
            list.sort { $0.date > $1.date }
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.items = list
                guard let first = list.first else {
                    self.latestThumbnail = nil
                    self.thumbURL = nil
                    return
                }
                if self.thumbURL == first.url && self.latestThumbnail != nil { return }
                self.thumbURL = first.url
                Task {
                    let img = await RecordingsStore.thumbnail(for: first.url)
                    await MainActor.run {
                        if self.thumbURL == first.url { self.latestThumbnail = img }
                    }
                }
            }
        }
    }
}

/// 操作バー左の、最後の録画のサムネイル。無いときは場所だけ空ける。
struct LastRecordingButton: View {
    @ObservedObject var store: RecordingsStore
    let action: () -> Void

    var body: some View {
        Group {
            if let img = store.latestThumbnail {
                Button(action: action) {
                    Image(uiImage: img)
                        .resizable()
                        .scaledToFill()
                        .frame(width: 48, height: 48)
                        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                        .overlay(
                            RoundedRectangle(cornerRadius: 8, style: .continuous)
                                .strokeBorder(Color.primary.opacity(0.35), lineWidth: 1)
                        )
                }
                .buttonStyle(.plain)
                .accessibilityLabel("録画ファイルを開く")
            } else {
                Color.clear.frame(width: 48, height: 48)
            }
        }
    }
}

private struct RecordingRow: View {
    let item: RecordingItem
    @State private var image: UIImage?

    var body: some View {
        HStack(spacing: 12) {
            Group {
                if let image = image {
                    Image(uiImage: image).resizable().scaledToFill()
                } else {
                    Color(.tertiarySystemFill)
                }
            }
            .frame(width: 64, height: 40)
            .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
            VStack(alignment: .leading, spacing: 2) {
                Text(item.url.lastPathComponent)
                    .font(.system(size: 15))
                    .lineLimit(1)
                    .truncationMode(.middle)
                Text(item.date.formatted(date: .abbreviated, time: .shortened)
                     + " · " + ByteCountFormatter.string(fromByteCount: item.size, countStyle: .file))
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            ShareLink(item: item.url) {
                Image(systemName: "square.and.arrow.up")
            }
            .accessibilityLabel("共有")
        }
        .task {
            if image == nil { image = await RecordingsStore.thumbnail(for: item.url, maxSide: 160) }
        }
    }
}

/// 録画ファイルの一覧 (共有できる)
struct RecordingsView: View {
    @StateObject private var store = RecordingsStore()

    var body: some View {
        List {
            if store.items.isEmpty {
                Text("録画はまだありません")
                    .foregroundStyle(.secondary)
            }
            ForEach(store.items) { item in
                RecordingRow(item: item)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("録画ファイル")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { store.refresh() }
    }
}
