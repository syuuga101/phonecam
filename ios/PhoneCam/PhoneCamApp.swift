import SwiftUI
import AVFoundation
import UIKit
import Foundation
import CoreMedia
import CoreVideo

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
    @Published var previewEnabled = false {
        didSet {
            stateLock.lock()
            previewFlag = previewEnabled
            stateLock.unlock()
            updateCameraRunning()
        }
    }
    @Published var streamActiveUI = true
    @Published var dimScreen = false
    @Published var errorText: String?
    @Published var rotationAngle = 0
    @Published var thermalText = "正常"
    @Published var batteryText = "-"
    @Published var screenBlack = false
    @Published var autoBlackSeconds = 0
    @Published var recActive = false
    @Published var recSeconds = 0.0
    @Published var recError = ""
    @Published var micText = "オフ"
    @Published var wifiAllowed = false
    @Published var pairingCode = ""

    let camera = CameraController()
    private let encoder = H264Encoder()
    private let security: SecurityManager
    private let server: StreamServer
    /// 低遅延の音声専用チャンネル (5556)。映像の滞留に巻き込まれない
    private let audioServer: StreamServer
    private let effects = EffectsProcessor()
    private let recorder = Recorder()
    private let scaler = FrameDownscaler(maxBuffers: 6)   // videoQueue 上でのみ使う
    /// エンコーダの破棄 (VT の完了待ちで止まりうる) をネットワークのキューから切り離す
    private let encoderStopQueue = DispatchQueue(label: "phonecam.encoderstop")

    init() {
        let sec = SecurityManager()
        security = sec
        server = StreamServer(security: sec)
        audioServer = StreamServer(port: StreamServer.audioPort, ignoreIncoming: true, audioCapMs: 60,
                                   role: .audio, security: sec)
        wifiAllowed = sec.wifiAllowed
        pairingCode = sec.pairingCode
    }

    // encoder のコールバックは任意のスレッドから来るため lock で保護する
    private let stateLock = NSLock()
    private var active = false            // クライアント接続中
    private var streamActive = true        // PC の "stream" (接続ごとに true から始まる)
    private var encoderLive = false        // videoQueue 上で encode を呼んだ (stop が必要)
    private var previewFlag = false
    private var lastCameraWant: Bool?
    private var waitingForKeyframe = true
    private var sentCount = 0
    private var sentFpsValue = 0.0
    private var encodeMsAvg = 0.0
    private var recHighRes = false
    private var screenBlackFlag = false
    private var autoBlackFlag = 0
    private var lastTouch = ProcessInfo.processInfo.systemUptime

    private var started = false
    private var timer: DispatchSourceTimer?
    private let timerQueue = DispatchQueue(label: "phonecam.timer", qos: .utility)
    private var lastSentCount = 0
    private var savedBrightness: CGFloat = 0.5
    private var batteryLevel: Double = -1
    private var batteryStateText = "unknown"
    private var codecName = "h264"

    func start() {
        guard !started else { return }
        started = true
        UIApplication.shared.isIdleTimerDisabled = true
        UIDevice.current.isBatteryMonitoringEnabled = true
        refreshDeviceInfo()
        NotificationCenter.default.addObserver(
            forName: ProcessInfo.thermalStateDidChangeNotification,
            object: nil,
            queue: nil
        ) { [weak self] _ in
            guard let self = self else { return }
            DispatchQueue.main.async { self.refreshDeviceInfo() }
            self.sendFullStatus()
        }

        // 前面に戻ったとき (写真の許可ダイアログのあとなど) にリスナーが死んでいれば作り直す
        NotificationCenter.default.addObserver(
            forName: UIApplication.didBecomeActiveNotification,
            object: nil,
            queue: nil
        ) { [weak self] _ in
            self?.server.ensureListening()
            self?.audioServer.ensureListening()
        }

        // カメラ
        camera.onFrame = { [weak self] pixelBuffer, pts, duration in
            guard let self = self else { return }
            // 録画はエフェクト前のきれいな映像を使う
            if self.recorder.isActive {
                self.recorder.appendVideo(pixelBuffer, pts: pts)
            }
            self.stateLock.lock()
            let isActive = self.active && self.streamActive
            let hiRes = self.recHighRes
            let live = self.encoderLive
            if !isActive { self.encoderLive = false }
            self.stateLock.unlock()
            if !isActive && live {
                // 配信オフ / 切断の直後に残ったエンコーダを破棄する
                self.encoder.stop()
            }
            if isActive {
                self.stateLock.lock()
                self.encoderLive = true
                self.stateLock.unlock()
                var source = pixelBuffer
                var skipStream = false
                if hiRes {
                    // 4K 録画中: ストリームは PC が要求した解像度へ縮小してからエフェクトへ渡す
                    let req = self.camera.streamRequestSize()
                    let bw = CVPixelBufferGetWidth(pixelBuffer)
                    let bh = CVPixelBufferGetHeight(pixelBuffer)
                    let portrait = bh > bw
                    let tw = portrait ? req.1 : req.0
                    let th = portrait ? req.0 : req.1
                    if bw > tw || bh > th {
                        if let scaled = self.scaler.scale(pixelBuffer, width: tw, height: th) {
                            source = scaled
                        } else {
                            // 縮小できないときは 4K のままエンコーダへ渡さない (エンコーダの作り直しで止まるため)
                            skipStream = true
                        }
                    }
                }
                if !skipStream {
                    let toEncode = self.effects.process(source)
                    self.encoder.encode(pixelBuffer: toEncode, pts: pts, duration: duration)
                }
            }
        }
        camera.onAudio = { [weak self] captureTime, sampleRate, pcm in
            guard let self = self else { return }
            self.stateLock.lock()
            let isActive = self.active && self.streamActive
            self.stateLock.unlock()
            if isActive {
                // 音声チャンネルのクライアントがいて、メインも接続中ならそちらだけに送る
                if self.audioServer.hasClient && self.server.hasClient {
                    self.audioServer.sendAudio(captureTime: captureTime, sampleRate: sampleRate, pcm: pcm)
                } else {
                    self.server.sendAudio(captureTime: captureTime, sampleRate: sampleRate, pcm: pcm)
                }
            }
        }
        camera.onAudioSample = { [weak self] sampleBuffer in
            guard let self = self else { return }
            if self.recorder.isActive {
                self.recorder.appendAudio(sampleBuffer)
            }
        }
        recorder.onChanged = { [weak self] in
            guard let self = self else { return }
            self.refreshRecordingUI()
            self.updateCameraRunning()
            self.timerQueue.async { self.sendFullStatus() }
        }
        recorder.onAutoStopped = { [weak self] in
            self?.restoreAfterRecording()
        }
        camera.onApplied = { [weak self] status in
            guard let self = self else { return }
            self.server.sendStatus(self.withExtras(status))
            if let mic = status["mic"] as? [String: Any] {
                let enabled = (mic["enabled"] as? Bool) ?? false
                let available = (mic["available"] as? Bool) ?? true
                let text = enabled ? (available ? "オン" : "オン (許可されていません)") : "オフ"
                DispatchQueue.main.async { self.micText = text }
            }
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
        camera.onRotation = { [weak self] angle in
            DispatchQueue.main.async {
                self?.rotationAngle = angle
            }
        }
        camera.onError = { [weak self] message in
            DispatchQueue.main.async {
                self?.errorText = message
            }
        }

        // エンコーダ
        encoder.onEncoded = { [weak self] data, isKey, isHevc, pts, encodeMs in
            self?.handleEncoded(data, isKey: isKey, hevc: isHevc, pts: pts, encodeMs: encodeMs)
        }

        // サーバ
        server.onClientConnected = { [weak self] kind in
            guard let self = self else { return }
            self.stateLock.lock()
            self.active = true
            self.streamActive = true   // 古い PC 受信側は "stream" を送らないので既定は true
            self.waitingForKeyframe = true
            self.sentCount = 0
            self.lastSentCount = 0
            self.encodeMsAvg = 0
            self.sentFpsValue = 0
            self.lastTouch = ProcessInfo.processInfo.systemUptime
            self.stateLock.unlock()
            self.camera.resetCounters()
            self.encoder.requestKeyframe()
            self.updateCameraRunning()
            self.sendFullStatus()
            self.camera.requestPublish()   // 最新のカメラ状態は sessionQueue で作って onApplied で届く
            DispatchQueue.main.async {
                self.streamActiveUI = true
                self.isConnected = true
                self.connectionKind = kind
                // 保存済みの「真っ黒」を再適用する (PC も接続時に再送する)
                if UserDefaults.standard.bool(forKey: "screenBlack") {
                    self.setScreenBlack(true, persist: false)
                }
            }
        }
        audioServer.onClientConnected = { [weak self] _ in
            self?.timerQueue.async { self?.sendFullStatus() }
        }
        audioServer.onClientDisconnected = { [weak self] in
            self?.timerQueue.async { self?.sendFullStatus() }
        }
        server.onClientDisconnected = { [weak self] in
            guard let self = self else { return }
            self.audioServer.disconnectClient()   // メイン切断時は音声チャンネルも閉じる
            self.stateLock.lock()
            self.active = false
            self.stateLock.unlock()
            self.stopEncoderAsync()
            self.updateCameraRunning()
            DispatchQueue.main.async {
                self.isConnected = false
                self.connectionKind = ""
                self.sentFps = 0
                // 切断したら省電力を解除する (保存値は残す)
                self.setScreenBlack(false, persist: false)
            }
        }
        server.onControl = { [weak self] json in
            guard let self = self else { return }
            if let key = json["keyframe"] as? NSNumber, key.boolValue {
                self.encoder.requestKeyframe()
            }
            if let st = json["stream"] as? [String: Any], let a = st["active"] as? NSNumber {
                self.setStreamActive(a.boolValue)
            }
            self.effects.apply(json)
            if let rs = json["recordSettings"] as? [String: Any] {
                self.recorder.applySettings(rs)
            }
            if let sc = json["screen"] as? [String: Any] {
                self.applyScreenControl(sc)
            }
            if let r = json["record"] as? String {
                DispatchQueue.main.async {
                    if r == "start" {
                        self.startRecording()
                    } else if r == "stop" {
                        self.stopRecording()
                    }
                }
            }
            if let c = json["codec"] as? String, (c == "h264" || c == "hevc") {
                self.stateLock.lock()
                self.codecName = c
                self.stateLock.unlock()
                self.encoder.setCodec(hevc: c == "hevc")
            }
            // 適用後に camera.onApplied 経由でステータスが返る
            self.camera.apply(json)
        }

        // 省電力の設定を復元する
        let defaults = UserDefaults.standard
        autoBlackSeconds = max(0, defaults.integer(forKey: "autoBlackSeconds"))
        stateLock.lock()
        autoBlackFlag = autoBlackSeconds
        stateLock.unlock()
        // 起動時は未接続なので黒画面にはしない (保存値は接続時に再適用する)

        server.start()
        audioServer.start()
        updateCameraRunning()   // camera.start() より前に要求を積む (最初は停止)
        camera.start()
        startTimer()
    }

    private func handleEncoded(_ data: Data, isKey: Bool, hevc: Bool, pts: CMTime, encodeMs: Double) {
        stateLock.lock()
        let waiting = waitingForKeyframe
        let streaming = active && streamActive
        encodeMsAvg = (encodeMsAvg == 0) ? encodeMs : (encodeMsAvg * 0.9 + encodeMs * 0.1)
        stateLock.unlock()
        if !streaming { return }
        if waiting && !isKey { return }

        let captureTime = CMTimeGetSeconds(pts)
        let sent = server.sendVideo(data, isKey: isKey, hevc: hevc,
                                    captureTime: captureTime.isFinite ? captureTime : nil)

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
            self.sentFpsValue = Double(max(0, delta))
            self.stateLock.unlock()

            self.checkAutoBlack()
            self.refreshRecordingUI()

            if isActive {
                self.sendFullStatus()
            }
            DispatchQueue.main.async { self.refreshDeviceInfo() }
            let wifiOn = self.security.wifiAllowed
            let ip = wifiOn ? AppModel.wifiIPv4() : nil
            DispatchQueue.main.async {
                self.sentFps = max(0, delta)
                self.wifiAddress = wifiOn ? (ip.map { "\($0):5555" } ?? "未接続") : "オフ"
            }
        }
        t.resume()
        timer = t
    }

    // MARK: 端末状態 / ステータス拡張

    private static func thermalName(_ s: ProcessInfo.ThermalState) -> String {
        switch s {
        case .nominal: return "nominal"
        case .fair: return "fair"
        case .serious: return "serious"
        case .critical: return "critical"
        @unknown default: return "nominal"
        }
    }

    private static func thermalLabel(_ s: ProcessInfo.ThermalState) -> String {
        switch s {
        case .nominal: return "正常"
        case .fair: return "やや高い"
        case .serious: return "高い"
        case .critical: return "危険"
        @unknown default: return "正常"
        }
    }

    /// メインスレッドで呼ぶ。バッテリー情報を読み、UI とステータス用の値を更新する。
    private func refreshDeviceInfo() {
        let dev = UIDevice.current
        let level = Double(dev.batteryLevel)
        let stateText: String
        switch dev.batteryState {
        case .charging: stateText = "charging"
        case .full: stateText = "full"
        case .unplugged: stateText = "unplugged"
        default: stateText = "unknown"
        }
        let lvl = (level >= 0 && level.isFinite) ? level : -1
        stateLock.lock()
        batteryLevel = lvl
        batteryStateText = stateText
        stateLock.unlock()

        thermalText = AppModel.thermalLabel(ProcessInfo.processInfo.thermalState)
        batteryText = lvl >= 0 ? "\(Int((lvl * 100).rounded()))%" : "-"
    }

    private func withExtras(_ base: [String: Any]) -> [String: Any] {
        var s = base
        stateLock.lock()
        let lvl = batteryLevel
        let bst = batteryStateText
        let codec = codecName
        let fpsOut = sentFpsValue
        let encMs = encodeMsAvg
        let black = screenBlackFlag
        let autoSec = autoBlackFlag
        let streamOn = streamActive
        stateLock.unlock()
        s["streamActive"] = streamOn
        s["wifiAllowed"] = security.wifiAllowed
        s["authOk"] = true   // 認証前の接続にはここまで届かない (StreamServer が authRequired だけ返す)
        s["audioChannel"] = audioServer.hasClient
        s["sentFps"] = fpsOut
        s["networkDroppedFrames"] = server.networkDroppedFrames()
        s["encodeMs"] = encMs
        s["screenBlack"] = black
        s["autoBlackSeconds"] = autoSec
        s["recording"] = recorder.statusDictionary()
        s["recordSettings"] = recorder.settingsDictionary()
        s["codec"] = codec
        s["thermalState"] = AppModel.thermalName(ProcessInfo.processInfo.thermalState)
        s["batteryLevel"] = lvl
        s["batteryState"] = bst
        s["lowPowerMode"] = ProcessInfo.processInfo.isLowPowerModeEnabled
        for (k, v) in effects.statusFields() { s[k] = v }
        return s
    }

    // MARK: カメラの起動 / 停止

    /// カメラを動かすべきか判定して CameraController へ伝える。どのスレッドからでも可。
    /// 条件: (接続中 && 配信オン) || 録画中 || プレビューオン
    func updateCameraRunning() {
        stateLock.lock()
        let want = (active && streamActive) || previewFlag || recorder.isActive
        if lastCameraWant != want {
            lastCameraWant = want
            camera.setRunning(want)   // sessionQueue へ積むだけ (順序は lock で保証)
        }
        stateLock.unlock()
    }

    /// PC の "stream": {"active": bool}
    private func setStreamActive(_ on: Bool) {
        stateLock.lock()
        let changed = (streamActive != on)
        streamActive = on
        if changed {
            waitingForKeyframe = true
            sentFpsValue = 0
        }
        stateLock.unlock()
        if on {
            encoder.requestKeyframe()
        } else if changed {
            stopEncoderAsync()   // ハードウェアエンコーダを解放 (TCP / ステータス / ping は継続)
        }
        updateCameraRunning()
        if changed {
            DispatchQueue.main.async { self.streamActiveUI = on }
        }
        timerQueue.async { [weak self] in self?.sendFullStatus() }
    }

    /// キャッシュ済みのカメラ状態で即送信し、次回用にキャッシュを非同期で更新する (sessionQueue を待たない)
    private func sendFullStatus() {
        camera.refreshStatus()
        server.sendStatus(withExtras(camera.status()))
    }

    private func stopEncoderAsync() {
        encoderStopQueue.async { [weak self] in
            self?.encoder.stop()
        }
    }

    /// 録画の開始 / 終了でカメラ構成が変わったあと、配信側を新しいキーフレームから再開させる
    private func resyncStream() {
        stateLock.lock()
        waitingForKeyframe = true
        stateLock.unlock()
        encoder.requestKeyframe()
    }

    // MARK: Wi-Fi の許可 / ペアリングコード (メインスレッドで呼ぶ)

    func setWifiAllowed(_ on: Bool) {
        security.setWifiAllowed(on)
        wifiAllowed = on
        if !on {
            wifiAddress = "オフ"
            server.disconnectNonLoopback()
            audioServer.disconnectNonLoopback()
        }
        timerQueue.async { [weak self] in self?.sendFullStatus() }
    }

    func regeneratePairingCode() {
        pairingCode = security.regenerateCode()
        // 古いコードで認証済みの Wi-Fi 接続は切る (USB はそのまま)
        server.disconnectNonLoopback()
        audioServer.disconnectNonLoopback()
    }

    // MARK: 画面を暗くする

    func setDim(_ on: Bool) {
        if on {
            if !dimScreen && !screenBlack {
                savedBrightness = UIScreen.main.brightness
            }
            UIScreen.main.brightness = screenBlack ? 0.0 : 0.02
        } else {
            UIScreen.main.brightness = screenBlack ? 0.0 : savedBrightness
        }
        dimScreen = on
    }

    // MARK: 省電力 (画面を真っ黒にする)

    /// 画面に触れた (自動で黒くするまでの時間を数え直す)。どのスレッドからでも可。
    func noteTouch() {
        stateLock.lock()
        lastTouch = ProcessInfo.processInfo.systemUptime
        stateLock.unlock()
    }

    /// メインスレッドで呼ぶ
    func setScreenBlack(_ on: Bool, persist: Bool = true) {
        stateLock.lock()
        let connectedNow = active
        stateLock.unlock()
        if on && !connectedNow { return }   // 黒画面は接続中のみ
        if on != screenBlack {
            if on {
                if !dimScreen {
                    savedBrightness = UIScreen.main.brightness
                }
                UIScreen.main.brightness = 0.0
            } else {
                UIScreen.main.brightness = dimScreen ? 0.02 : savedBrightness
            }
            screenBlack = on
            stateLock.lock()
            screenBlackFlag = on
            stateLock.unlock()
            if persist {
                UserDefaults.standard.set(on, forKey: "screenBlack")
            }
            timerQueue.async { [weak self] in self?.sendFullStatus() }
        }
        noteTouch()
    }

    private func applyScreenControl(_ sc: [String: Any]) {
        DispatchQueue.main.async {
            if let a = sc["autoBlackSeconds"] as? NSNumber {
                let v = min(max(a.intValue, 0), 3600)
                self.autoBlackSeconds = v
                self.stateLock.lock()
                self.autoBlackFlag = v
                self.stateLock.unlock()
                UserDefaults.standard.set(v, forKey: "autoBlackSeconds")
                self.noteTouch()
            }
            if let b = sc["black"] as? NSNumber {
                self.setScreenBlack(b.boolValue)
            } else {
                self.timerQueue.async { self.sendFullStatus() }
            }
        }
    }

    private func checkAutoBlack() {
        stateLock.lock()
        let black = screenBlackFlag
        let auto = autoBlackFlag
        let last = lastTouch
        let connectedNow = active
        stateLock.unlock()
        guard connectedNow, !black, auto > 0 else { return }
        if ProcessInfo.processInfo.systemUptime - last >= Double(auto) {
            DispatchQueue.main.async { self.setScreenBlack(true) }
        }
    }

    // MARK: 録画

    /// メインスレッドまたは任意のスレッドから呼べる
    func toggleRecording() {
        if recorder.isActive {
            stopRecording()
        } else {
            startRecording()
        }
    }

    func startRecording() {
        let s = recorder.currentSettings()
        let hiRes = (s.resolution == "4k")
        guard recorder.claim() else {
            refreshRecordingUI()
            return
        }
        stateLock.lock()
        recHighRes = hiRes
        stateLock.unlock()
        camera.setRecording(highRes: hiRes, audio: s.includeAudio) { [weak self] longSide, fps in
            self?.recorder.arm(expectedLongSide: hiRes ? longSide : 0, fps: fps)
            self?.resyncStream()
        }
        updateCameraRunning()
        refreshRecordingUI()
    }

    func stopRecording() {
        recorder.stop()
        restoreAfterRecording()
        refreshRecordingUI()
    }

    /// 録画が終わったあと、カメラのフォーマットと音声を元に戻す
    private func restoreAfterRecording() {
        updateCameraRunning()
        camera.setRecording(highRes: false, audio: false) { [weak self] _, _ in
            guard let self = self else { return }
            if !self.recorder.isActive {
                self.stateLock.lock()
                self.recHighRes = false
                self.stateLock.unlock()
            }
            self.resyncStream()
        }
    }

    private func refreshRecordingUI() {
        let st = recorder.statusDictionary()
        let act = (st["active"] as? Bool) ?? false
        let sec = (st["seconds"] as? Double) ?? 0
        let err = (st["error"] as? String) ?? ""
        DispatchQueue.main.async {
            self.recActive = act
            self.recSeconds = sec
            self.recError = err
        }
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

    /// 出力と同じ回転角 (0/90/180/270)。プレビューも出力映像と同じ向きで表示する。
    var angle: CGFloat = 0 {
        didSet { applyRotation() }
    }

    func applyRotation() {
        if #available(iOS 17.0, *) {
            if let conn = previewLayer.connection, conn.isVideoRotationAngleSupported(angle) {
                conn.videoRotationAngle = angle
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
    let angle: Int

    func makeUIView(context: Context) -> PreviewUIView {
        let view = PreviewUIView()
        view.backgroundColor = .black
        view.previewLayer.session = session
        view.previewLayer.videoGravity = .resizeAspect
        view.angle = CGFloat(angle)
        return view
    }

    func updateUIView(_ uiView: PreviewUIView, context: Context) {
        uiView.angle = CGFloat(angle)
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
                        Text(model.isConnected
                             ? (model.streamActiveUI ? "配信中 · \(model.connectionKind)" : "待機中（カメラ停止）· \(model.connectionKind)")
                             : "待機中")
                            .font(.system(size: 17, weight: .medium))
                            .foregroundColor(.white)
                    }

                    VStack(spacing: 0) {
                        InfoRow(title: model.wifiAllowed ? "Wi-Fi アドレス" : "Wi-Fi接続",
                                value: model.wifiAllowed ? model.wifiAddress : "オフ")
                        Divider().background(Color(white: 0.2))
                        InfoRow(title: "映像", value: model.formatText)
                        Divider().background(Color(white: 0.2))
                        InfoRow(title: "ビットレート", value: model.bitrateText)
                        Divider().background(Color(white: 0.2))
                        InfoRow(title: "送信fps", value: "\(model.sentFps)")
                        Divider().background(Color(white: 0.2))
                        InfoRow(title: "温度状態", value: model.thermalText)
                        Divider().background(Color(white: 0.2))
                        InfoRow(title: "バッテリー", value: model.batteryText)
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
                        Divider().background(Color(white: 0.2))
                        Toggle("画面を真っ黒にする", isOn: Binding(
                            get: { model.screenBlack },
                            set: { model.setScreenBlack($0) }
                        ))
                        .tint(.green)
                        .disabled(!model.isConnected)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 10)
                        if !model.isConnected {
                            Text("PCと接続中のみ使えます")
                                .font(.system(size: 13))
                                .foregroundColor(Color(white: 0.55))
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.horizontal, 16)
                                .padding(.bottom, 10)
                        }
                    }
                    .foregroundColor(.white)
                    .background(cardColor)
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))

                    VStack(spacing: 0) {
                        Toggle("Wi-Fiからの接続を許可", isOn: Binding(
                            get: { model.wifiAllowed },
                            set: { model.setWifiAllowed($0) }
                        ))
                        .tint(.green)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 10)
                        if model.wifiAllowed {
                            Divider().background(Color(white: 0.2))
                            VStack(spacing: 6) {
                                Text("ペアリングコード")
                                    .font(.system(size: 13))
                                    .foregroundColor(Color(white: 0.55))
                                Text(model.pairingCode)
                                    .font(.system(size: 44, weight: .semibold, design: .monospaced))
                                    .kerning(6)
                                    .lineLimit(1)
                                    .minimumScaleFactor(0.5)
                                    .foregroundColor(.white)
                                Button(action: { model.regeneratePairingCode() }) {
                                    Text("コードを変更")
                                        .font(.system(size: 15, weight: .medium))
                                        .foregroundColor(Color.blue)
                                }
                                .padding(.top, 2)
                                Text("PCの「ペアリングコード」欄に入力してください。USB接続では不要です。")
                                    .font(.system(size: 13))
                                    .foregroundColor(Color(white: 0.55))
                                    .multilineTextAlignment(.center)
                                    .padding(.top, 2)
                            }
                            .frame(maxWidth: .infinity)
                            .padding(.horizontal, 16)
                            .padding(.vertical, 14)
                        }
                    }
                    .foregroundColor(.white)
                    .background(cardColor)
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))

                    HStack(spacing: 10) {
                        Text("録画")
                            .font(.system(size: 17))
                            .foregroundColor(.white)
                        if model.recActive {
                            Circle()
                                .fill(Color.red)
                                .frame(width: 10, height: 10)
                            Text("REC " + ContentView.timeText(model.recSeconds))
                                .font(.system(size: 15, weight: .medium, design: .monospaced))
                                .foregroundColor(.red)
                        }
                        Spacer()
                        Button(action: { model.toggleRecording() }) {
                            Text(model.recActive ? "停止" : "開始")
                                .font(.system(size: 15, weight: .semibold))
                                .foregroundColor(.white)
                                .padding(.horizontal, 18)
                                .padding(.vertical, 7)
                                .background(model.recActive ? Color.red : Color(white: 0.28))
                                .clipShape(Capsule())
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)
                    .background(cardColor)
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))

                    Text("マイク: \(model.micText)")
                        .font(.system(size: 13))
                        .foregroundColor(Color(white: 0.55))

                    if !model.recError.isEmpty {
                        Text(model.recError)
                            .font(.system(size: 13))
                            .foregroundColor(.orange)
                    }

                    if model.previewEnabled {
                        let isPortraitOutput = (model.rotationAngle == 90 || model.rotationAngle == 270)
                        CameraPreview(session: model.camera.session, angle: model.rotationAngle)
                            .aspectRatio(isPortraitOutput ? 9.0 / 16.0 : 16.0 / 9.0, contentMode: .fit)
                            .frame(maxHeight: isPortraitOutput ? 420 : nil)
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

            if model.screenBlack {
                Color.black
                    .ignoresSafeArea()
                    .contentShape(Rectangle())
                    .onTapGesture { model.setScreenBlack(false) }
            }
        }
        .statusBarHidden(model.screenBlack)
        .simultaneousGesture(
            DragGesture(minimumDistance: 0).onChanged { _ in model.noteTouch() }
        )
    }

    static func timeText(_ seconds: Double) -> String {
        let total = max(0, Int(seconds))
        let h = total / 3600
        let m = (total % 3600) / 60
        let sec = total % 60
        if h > 0 {
            return String(format: "%d:%02d:%02d", h, m, sec)
        }
        return String(format: "%02d:%02d", m, sec)
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
