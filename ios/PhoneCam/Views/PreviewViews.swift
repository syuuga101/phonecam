import SwiftUI
import AVFoundation
import UIKit
import CoreMotion

// MARK: - プレビュー

/// 画面に対して常に正立するプレビュー。回転角は RotationCoordinator (previewLayer 付き) に任せる。
/// 出力 (エンコーダ) の回転とは独立。
final class PreviewUIView: UIView {
    override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }

    var previewLayer: AVCaptureVideoPreviewLayer {
        return layer as! AVCaptureVideoPreviewLayer
    }

    /// スナップ済みの角度 (0/90/180/270) を通知する (必ずメインスレッドの次のターンで呼ぶ)
    var onAngle: ((Int) -> Void)?

    private var desiredDevice: AVCaptureDevice?
    private weak var boundDevice: AVCaptureDevice?
    private var coordinator: AVCaptureDevice.RotationCoordinator?
    private var observation: NSKeyValueObservation?
    private var sessionObserver: NSObjectProtocol?
    private var currentAngle: CGFloat = 90

    deinit {
        observation?.invalidate()
        if let o = sessionObserver { NotificationCenter.default.removeObserver(o) }
    }

    /// デバイス (レンズ) が変わったら作り直す。ウィンドウに載るまで実際の生成は待つ。
    func bind(device: AVCaptureDevice?) {
        desiredDevice = device
        guard window != nil else { return }
        if coordinator != nil && boundDevice === device { return }
        rebuildCoordinator()
    }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        if window != nil {
            rebuildCoordinator()
            if sessionObserver == nil {
                // 入力の差し替えなどで接続が作り直されても角度を入れ直す
                sessionObserver = NotificationCenter.default.addObserver(
                    forName: .AVCaptureSessionDidStartRunning,
                    object: nil,
                    queue: .main
                ) { [weak self] _ in
                    self?.reapplyAngle()
                }
            }
        } else {
            observation?.invalidate()
            observation = nil
            coordinator = nil
            if let o = sessionObserver {
                NotificationCenter.default.removeObserver(o)
                sessionObserver = nil
            }
        }
    }

    private func rebuildCoordinator() {
        observation?.invalidate()
        observation = nil
        coordinator = nil
        boundDevice = desiredDevice
        guard let dev = desiredDevice else { return }
        let c = AVCaptureDevice.RotationCoordinator(device: dev, previewLayer: previewLayer)
        coordinator = c
        applyAngle(c.videoRotationAngleForHorizonLevelPreview)
        observation = c.observe(\.videoRotationAngleForHorizonLevelPreview, options: [.new]) { [weak self] coord, _ in
            let a = coord.videoRotationAngleForHorizonLevelPreview
            DispatchQueue.main.async { self?.applyAngle(a) }
        }
    }

    private func applyAngle(_ a: CGFloat) {
        guard a.isFinite else { return }
        currentAngle = a
        reapplyAngle()
        let q = Int((Double(a) / 90.0).rounded())
        let snapped = ((q % 4) + 4) % 4 * 90
        DispatchQueue.main.async { [weak self] in
            self?.onAngle?(snapped)
        }
    }

    private func reapplyAngle() {
        if let conn = previewLayer.connection, conn.isVideoRotationAngleSupported(currentAngle),
           conn.videoRotationAngle != currentAngle {
            conn.videoRotationAngle = currentAngle
        }
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        reapplyAngle()
    }
}

struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession
    let device: AVCaptureDevice?
    let onAngle: (Int) -> Void

    func makeUIView(context: Context) -> PreviewUIView {
        let view = PreviewUIView()
        view.backgroundColor = .black
        view.previewLayer.session = session
        view.previewLayer.videoGravity = .resizeAspect
        view.onAngle = onAngle
        view.bind(device: device)
        return view
    }

    func updateUIView(_ uiView: PreviewUIView, context: Context) {
        uiView.onAngle = onAngle
        uiView.bind(device: device)
    }
}

// MARK: - 構図ガイド

/// プレビュー領域の中での「カメラ映像の矩形」と「PC の出力キャンバスの矩形」
struct FramingLayout {
    var image: CGRect
    var canvas: CGRect?   // キャンバス不明のとき nil

    /// region: 描画できる領域 / imageAspect: 画面上のカメラ映像の縦横比 / canvasAspect: 画面上のキャンバスの縦横比
    /// crop: 映像がキャンバスを覆う (はみ出す部分は切れる)。pad: 映像がキャンバスの中に収まる (余白ができる)
    static func make(region: CGSize, imageAspect: CGFloat, canvasAspect: CGFloat?, crop: Bool) -> FramingLayout {
        guard region.width > 0, region.height > 0, imageAspect > 0, imageAspect.isFinite else {
            return FramingLayout(image: .zero, canvas: nil)
        }
        func fit(_ aspect: CGFloat, in size: CGSize) -> CGSize {
            if size.width / size.height > aspect {
                return CGSize(width: size.height * aspect, height: size.height)
            }
            return CGSize(width: size.width, height: size.width / aspect)
        }
        func centered(_ s: CGSize) -> CGRect {
            return CGRect(x: (region.width - s.width) / 2, y: (region.height - s.height) / 2,
                          width: s.width, height: s.height)
        }
        guard let ac = canvasAspect, ac > 0, ac.isFinite else {
            return FramingLayout(image: centered(fit(imageAspect, in: region)), canvas: nil)
        }
        let imgSize: CGSize
        let canSize: CGSize
        if crop {
            imgSize = fit(imageAspect, in: region)
            if ac > imageAspect {
                canSize = CGSize(width: imgSize.width, height: imgSize.width / ac)
            } else {
                canSize = CGSize(width: imgSize.height * ac, height: imgSize.height)
            }
        } else {
            canSize = fit(ac, in: region)
            if imageAspect > ac {
                imgSize = CGSize(width: canSize.width, height: canSize.width / imageAspect)
            } else {
                imgSize = CGSize(width: canSize.height * imageAspect, height: canSize.height)
            }
        }
        return FramingLayout(image: centered(imgSize), canvas: centered(canSize))
    }
}

struct FramingOverlay: View {
    let layout: FramingLayout
    let crop: Bool
    let showGrid: Bool

    var body: some View {
        Canvas { ctx, _ in
            let img = layout.image
            if let can = layout.canvas {
                if crop {
                    // 切れる部分を暗くする
                    var dim = Path()
                    dim.addRect(img)
                    dim.addRect(can)
                    ctx.fill(dim, with: .color(Color.black.opacity(0.6)), style: FillStyle(eoFill: true))
                } else {
                    // 余白 (レターボックス) をうすく塗る
                    var bars = Path()
                    bars.addRect(can)
                    bars.addRect(img)
                    ctx.fill(bars, with: .color(Color.white.opacity(0.12)), style: FillStyle(eoFill: true))
                }
                ctx.stroke(Path(can), with: .color(Color.white.opacity(0.9)), lineWidth: 1.5)
            } else {
                ctx.stroke(Path(img), with: .color(Color.white.opacity(0.6)),
                           style: StrokeStyle(lineWidth: 1, dash: [6, 4]))
            }
            if showGrid {
                let g = layout.canvas ?? img
                var grid = Path()
                for i in 1...2 {
                    let f = CGFloat(i) / 3.0
                    let x = g.minX + g.width * f
                    let y = g.minY + g.height * f
                    grid.move(to: CGPoint(x: x, y: g.minY))
                    grid.addLine(to: CGPoint(x: x, y: g.maxY))
                    grid.move(to: CGPoint(x: g.minX, y: y))
                    grid.addLine(to: CGPoint(x: g.maxX, y: y))
                }
                ctx.stroke(grid, with: .color(Color.white.opacity(0.35)), lineWidth: 0.5)
            }
        }
    }
}

struct PreviewPanel: View {
    @EnvironmentObject var model: AppModel
    let region: CGSize
    let showGrid: Bool
    let showLevel: Bool

    private static func isQuarter(_ a: Int) -> Bool {
        let n = ((a % 360) + 360) % 360
        return n == 90 || n == 270
    }

    private var layout: FramingLayout {
        let w = max(1, model.cameraSize.width)
        let h = max(1, model.cameraSize.height)
        // プレビュー接続の角度が 90/270 なら縦長に表示される
        let imageAspect: CGFloat = PreviewPanel.isQuarter(model.previewAngle) ? (h / w) : (w / h)
        var canvasAspect: CGFloat?
        if let c = model.outputCanvas, c.width > 0, c.height > 0 {
            var a = CGFloat(c.width) / CGFloat(c.height)
            // 出力がプレビューに対して 90/270 度回っているときは、画面上でのキャンバスの縦横は入れ替わる
            if PreviewPanel.isQuarter(model.rotationAngle - model.previewAngle) { a = 1 / a }
            canvasAspect = a
        }
        return FramingLayout.make(region: region, imageAspect: imageAspect,
                                  canvasAspect: canvasAspect, crop: model.outputCanvas?.crop ?? false)
    }

    private var badgeText: String {
        let landscape = (model.rotationAngle == 0 || model.rotationAngle == 180)
        let live = model.isConnected && model.streamActiveUI
        let dir = landscape ? "横向き" : "縦向き"
        return live ? "\(dir)で配信中" : "\(dir) · 待機中"
    }

    var body: some View {
        let lay = layout
        let landscape = (model.rotationAngle == 0 || model.rotationAngle == 180)
        ZStack {
            Color.black
            CameraPreview(session: model.camera.session,
                          device: model.previewDevice,
                          onAngle: { a in model.updatePreviewAngle(a) })
                .frame(width: max(1, lay.image.width), height: max(1, lay.image.height))
                .position(x: lay.image.midX, y: lay.image.midY)
            FramingOverlay(layout: lay, crop: model.outputCanvas?.crop ?? false, showGrid: showGrid)
                .allowsHitTesting(false)
            if showLevel {
                LevelIndicator(deg: model.levelDeg, flat: model.levelFlat)
                    .position(x: lay.image.midX, y: lay.image.midY)
                    .allowsHitTesting(false)
            }
            VStack {
                HStack {
                    HStack(spacing: 6) {
                        Image(systemName: landscape ? "rectangle" : "rectangle.portrait")
                            .font(.system(size: 12, weight: .semibold))
                        Text(badgeText)
                            .font(.system(size: 13, weight: .semibold))
                    }
                    .foregroundColor(.white)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(Color.black.opacity(0.6))
                    .clipShape(Capsule())
                    Spacer()
                }
                Spacer()
            }
            .padding(10)
            .allowsHitTesting(false)
        }
        .frame(width: region.width, height: region.height)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}

/// 水平メーター。細い線がデバイスの傾きと逆に回り、画像の水平を示す。±1° 以内で緑。
struct LevelIndicator: View {
    let deg: Double
    let flat: Bool

    var body: some View {
        if flat {
            Text("上向きに置かれています")
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(.white)
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(Color.black.opacity(0.55))
                .clipShape(Capsule())
        } else {
            let level = abs(deg) <= 1.0
            let tint = level ? Color.green : Color.white.opacity(0.85)
            ZStack {
                HStack {
                    Rectangle().fill(Color.white.opacity(0.35)).frame(width: 14, height: 1)
                    Spacer()
                    Rectangle().fill(Color.white.opacity(0.35)).frame(width: 14, height: 1)
                }
                .frame(width: 190)
                Rectangle()
                    .fill(tint)
                    .frame(width: 120, height: 1.5)
                    .rotationEffect(.degrees(-deg))
                Text(level ? "水平 · " + String(format: "%.1f°", deg) : String(format: "%.1f°", deg))
                    .font(.system(size: 11, weight: .medium, design: .monospaced))
                    .foregroundColor(tint)
                    .offset(y: 16)
            }
            .frame(width: 190, height: 40)
        }
    }
}
