import SwiftUI
import AVFoundation
import UIKit
import Foundation

@main
struct PhoneCamApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
                .preferredColorScheme(.dark)
                .onAppear { model.start() }
        }
    }
}

// MARK: - AppModel (カメラ・エンコーダ・サーバをつなぐ)

final class AppModel: ObservableObject {
    @Published var isConnected = false
    @Published var connectionKind = ""
    @Published var wifiAddress = "未接続"
    @Published var formatText = "-"
    @Published var bitrateText = "-"
    @Published var sentFps = 0
    @Published var previewEnabled = false
    @Published var dimScreen = false
    @Published var errorText: String?

    let camera = CameraController()
    private let encoder = H264Encoder()
    private let server = StreamServer()

    // encoder のコールバックは任意のスレッドから来るため lock で保護する
    private let stateLock = NSLock()
    private var active = false
    private var waitingForKeyframe = true
    private var sentCount = 0

    private var started = false
    private var timer: DispatchSourceTimer?
    private let timerQueue = DispatchQueue(label: "phonecam.timer", qos: .utility)
    private var lastSentCount = 0
    private var savedBrightness: CGFloat = 0.5

    func start() {
        guard !started else { return }
        started = true
        UIApplication.shared.isIdleTimerDisabled = true

        // カメラ
        camera.onFrame = { [weak self] pixelBuffer, pts, duration in
            guard let self = self else { return }
            self.stateLock.lock()
            let isActive = self.active
            self.stateLock.unlock()
            if isActive {
                self.encoder.encode(pixelBuffer: pixelBuffer, pts: pts, duration: duration)
            }
        }
        camera.onApplied = { [weak self] status in
            self?.server.sendStatus(status)
        }
        camera.onStreamSettings = { [weak self] width, height, fps, bitrate in
            guard let self = self else { return }
            if fps > 0 {
                self.encoder.configure(fps: fps, bitrateMbps: bitrate)
            }
            DispatchQueue.main.async {
                if width > 0 && height > 0 {
                    self.formatText = "\(width)×\(height) \(fps)fps"
                }
                self.bitrateText = "\(bitrate) Mbps"
            }
        }
        camera.onError = { [weak self] message in
            DispatchQueue.main.async {
                self?.errorText = message
            }
        }

        // エンコーダ
        encoder.onEncoded = { [weak self] data, isKey in
            self?.handleEncoded(data, isKey: isKey)
        }

        // サーバ
        server.onClientConnected = { [weak self] kind in
            guard let self = self else { return }
            self.stateLock.lock()
            self.active = true
            self.waitingForKeyframe = true
            self.sentCount = 0
            self.lastSentCount = 0
            self.stateLock.unlock()
            self.encoder.requestKeyframe()
            self.server.sendStatus(self.camera.status())
            DispatchQueue.main.async {
                self.isConnected = true
                self.connectionKind = kind
            }
        }
        server.onClientDisconnected = { [weak self] in
            guard let self = self else { return }
            self.stateLock.lock()
            self.active = false
            self.stateLock.unlock()
            self.encoder.stop()
            DispatchQueue.main.async {
                self.isConnected = false
                self.connectionKind = ""
                self.sentFps = 0
            }
        }
        server.onControl = { [weak self] json in
            guard let self = self else { return }
            if let key = json["keyframe"] as? NSNumber, key.boolValue {
                self.encoder.requestKeyframe()
            }
            // 適用後に camera.onApplied 経由でステータスが返る
            self.camera.apply(json)
        }

        server.start()
        camera.start()
        startTimer()
    }

    private func handleEncoded(_ data: Data, isKey: Bool) {
        stateLock.lock()
        let waiting = waitingForKeyframe
        stateLock.unlock()
        if waiting && !isKey { return }

        let sent = server.sendVideo(data, isKey: isKey)

        stateLock.lock()
        if sent {
            if isKey { waitingForKeyframe = false }
            sentCount += 1
        } else if !isKey {
            waitingForKeyframe = true
        }
        stateLock.unlock()

        if !sent && !isKey {
            encoder.requestKeyframe()
        }
    }

    private func startTimer() {
        let t = DispatchSource.makeTimerSource(queue: timerQueue)
        t.schedule(deadline: .now() + 1.0, repeating: 1.0)
        t.setEventHandler { [weak self] in
            guard let self = self else { return }
            self.stateLock.lock()
            let count = self.sentCount
            let isActive = self.active
            let delta = count - self.lastSentCount
            self.lastSentCount = count
            self.stateLock.unlock()

            if isActive {
                self.server.sendStatus(self.camera.status())
            }
            let ip = AppModel.wifiIPv4()
            DispatchQueue.main.async {
                self.sentFps = max(0, delta)
                self.wifiAddress = ip.map { "\($0):5555" } ?? "未接続"
            }
        }
        t.resume()
        timer = t
    }

    // MARK: 画面を暗くする

    func setDim(_ on: Bool) {
        if on {
            if !dimScreen {
                savedBrightness = UIScreen.main.brightness
            }
            UIScreen.main.brightness = 0.02
        } else {
            UIScreen.main.brightness = savedBrightness
        }
        dimScreen = on
    }

    // MARK: Wi-Fi の IPv4 アドレス (en0)

    static func wifiIPv4() -> String? {
        var addrs: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&addrs) == 0, let first = addrs else { return nil }
        defer { freeifaddrs(addrs) }

        var cursor: UnsafeMutablePointer<ifaddrs>? = first
        while let p = cursor {
            let ifa = p.pointee
            if let sa = ifa.ifa_addr,
               sa.pointee.sa_family == UInt8(AF_INET),
               String(cString: ifa.ifa_name) == "en0" {
                var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
                let r = getnameinfo(sa, socklen_t(sa.pointee.sa_len), &host, socklen_t(host.count), nil, 0, NI_NUMERICHOST)
                if r == 0 {
                    return String(cString: host)
                }
            }
            cursor = ifa.ifa_next
        }
        return nil
    }
}

// MARK: - プレビュー

final class PreviewUIView: UIView {
    override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }

    var previewLayer: AVCaptureVideoPreviewLayer {
        return layer as! AVCaptureVideoPreviewLayer
    }

    func applyRotation() {
        if #available(iOS 17.0, *) {
            if let conn = previewLayer.connection, conn.isVideoRotationAngleSupported(0) {
                conn.videoRotationAngle = 0
            }
        }
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        applyRotation()
    }
}

struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession

    func makeUIView(context: Context) -> PreviewUIView {
        let view = PreviewUIView()
        view.backgroundColor = .black
        view.previewLayer.session = session
        view.previewLayer.videoGravity = .resizeAspect
        view.applyRotation()
        return view
    }

    func updateUIView(_ uiView: PreviewUIView, context: Context) {
        uiView.applyRotation()
    }
}

// MARK: - ContentView

struct ContentView: View {
    @EnvironmentObject var model: AppModel

    private let cardColor = Color(white: 0.11)

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Text("PhoneCam")
                        .font(.system(size: 34, weight: .bold))
                        .foregroundColor(.white)

                    HStack(spacing: 10) {
                        Circle()
                            .fill(model.isConnected ? Color.green : Color(white: 0.4))
                            .frame(width: 10, height: 10)
                        Text(model.isConnected ? "接続中: \(model.connectionKind)" : "待機中")
                            .font(.system(size: 17, weight: .medium))
                            .foregroundColor(.white)
                    }

                    VStack(spacing: 0) {
                        InfoRow(title: "Wi-Fi アドレス", value: model.wifiAddress)
                        Divider().background(Color(white: 0.2))
                        InfoRow(title: "映像", value: model.formatText)
                        Divider().background(Color(white: 0.2))
                        InfoRow(title: "ビットレート", value: model.bitrateText)
                        Divider().background(Color(white: 0.2))
                        InfoRow(title: "送信fps", value: "\(model.sentFps)")
                    }
                    .background(cardColor)
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))

                    VStack(spacing: 0) {
                        Toggle("プレビュー", isOn: $model.previewEnabled)
                            .tint(.green)
                            .padding(.horizontal, 16)
                            .padding(.vertical, 10)
                        Divider().background(Color(white: 0.2))
                        Toggle("画面を暗くする", isOn: Binding(
                            get: { model.dimScreen },
                            set: { model.setDim($0) }
                        ))
                        .tint(.green)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 10)
                    }
                    .foregroundColor(.white)
                    .background(cardColor)
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))

                    if model.previewEnabled {
                        CameraPreview(session: model.camera.session)
                            .aspectRatio(16.0 / 9.0, contentMode: .fit)
                            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                    }

                    if let err = model.errorText {
                        Text(err)
                            .font(.system(size: 13))
                            .foregroundColor(.orange)
                    }

                    Text("アプリは前面に表示したままにしてください")
                        .font(.system(size: 13))
                        .foregroundColor(Color(white: 0.55))
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 24)
            }
        }
    }
}

struct InfoRow: View {
    let title: String
    let value: String

    var body: some View {
        HStack {
            Text(title)
                .font(.system(size: 15))
                .foregroundColor(Color(white: 0.6))
            Spacer()
            Text(value)
                .font(.system(size: 15, weight: .medium, design: .monospaced))
                .foregroundColor(.white)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }
}
