import SwiftUI
import AVFoundation
import UIKit
import Foundation
import CoreMedia
import CoreVideo
import CoreMotion

/// PC の仮想カメラキャンバス (outputCanvas)。framing guide 用
struct OutputCanvas: Equatable {
    let width: Int
    let height: Int
    let crop: Bool   // fit == "crop"。false は "pad"
}

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
    // 手元操作 (すべてメインスレッドで更新する)
    @Published var lens = "wide"
    @Published var lenses: [String] = ["wide"]
    @Published var micEnabled = false
    @Published var micMuted = false
    @Published var outputCanvas: OutputCanvas?
    @Published var cameraSize = CGSize(width: 1920, height: 1080)   // カメラ素の向き (横長) のサイズ
    @Published var previewAngle = 90                                 // プレビュー接続の回転角 (0/90/180/270)
    @Published var previewDevice: AVCaptureDevice?
    @Published var levelDeg = 0.0
    @Published var levelFlat = false
    @Published var toast: String?
    @Published var vibrateOnConnect = (UserDefaults.standard.object(forKey: "vibrateOnConnect") as? Bool) ?? true {
        didSet { UserDefaults.standard.set(vibrateOnConnect, forKey: "vibrateOnConnect") }
    }

    let camera = CameraController()
    private let encoder = H264Encoder()
    private let security: SecurityManager
    private let server: StreamServer
    /// 低遅延の音声専用チャンネル (5556)。映像の滞留に巻き込まれない
    private let audioServer: StreamServer
    private let effects = EffectsProcessor()
    private let recorder = Recorder()
    private let levelMonitor = LevelMonitor()
    private var toastSeq = 0
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
            let lensNow = status["lens"] as? String
            let lensList = status["lenses"] as? [String]
            let mutedNow = status["micMuted"] as? Bool
            var micEnabledNow: Bool?
            var micTextNow: String?
            if let mic = status["mic"] as? [String: Any] {
                let enabled = (mic["enabled"] as? Bool) ?? false
                let available = (mic["available"] as? Bool) ?? true
                micEnabledNow = enabled
                micTextNow = enabled ? (available ? "オン" : "オン (許可されていません)") : "オフ"
            }
            DispatchQueue.main.async {
                if let t = micTextNow { self.micText = t }
                if let e = micEnabledNow, self.micEnabled != e { self.micEnabled = e }
                if let m = mutedNow, self.micMuted != m { self.micMuted = m }
                if let l = lensNow, self.lens != l { self.lens = l }
                if let list = lensList, self.lenses != list { self.lenses = list }
            }
        }
        camera.onDevice = { [weak self] dev in
            DispatchQueue.main.async { self?.previewDevice = dev }
        }
        levelMonitor.onUpdate = { [weak self] deg, flat in
            DispatchQueue.main.async {
                guard let self = self else { return }
                if self.levelDeg != deg { self.levelDeg = deg }
                if self.levelFlat != flat { self.levelFlat = flat }
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
                    let size = CGSize(width: width, height: height)
                    if self.cameraSize != size { self.cameraSize = size }
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
                self.showToast("PCとつながりました（\(kind)）", haptic: .success)
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
                self.outputCanvas = nil
                self.showToast("PCとの接続が切れました", haptic: .warning)
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
            if let oc = json["outputCanvas"] as? [String: Any] {
                let w = (oc["width"] as? NSNumber)?.intValue ?? 0
                let h = (oc["height"] as? NSNumber)?.intValue ?? 0
                let crop = ((oc["fit"] as? String) ?? "pad") == "crop"
                if w > 0 && h > 0 {
                    DispatchQueue.main.async {
                        let canvas = OutputCanvas(width: w, height: h, crop: crop)
                        if self.outputCanvas != canvas { self.outputCanvas = canvas }
                    }
                }
            }
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

    // MARK: iPhone 側の操作 (メインスレッドで呼ぶ)

    /// レンズを切り替える。camera.apply と同じ経路で適用され、phoneEdit.seq が進む。
    func selectLens(_ l: String) {
        guard lens != l else { return }
        lens = l   // 先に UI へ反映 (実際の値は onApplied のステータスで上書きされる)
        camera.phoneChange(lens: l, micMuted: nil)
    }

    func toggleMute() {
        let v = !micMuted
        micMuted = v
        camera.phoneChange(lens: nil, micMuted: v)
    }

    func updatePreviewAngle(_ a: Int) {
        if previewAngle != a { previewAngle = a }
    }

    /// 水平メーター用のモーション取得を開始 / 停止する (プレビュー表示中だけ動かして電池を節約)
    func setMotionActive(_ on: Bool) {
        if on {
            levelMonitor.start()
        } else {
            levelMonitor.stop()
        }
    }

    /// 上部のトースト (2 秒) と、設定がオンなら触覚フィードバック
    func showToast(_ text: String, haptic: UINotificationFeedbackGenerator.FeedbackType?) {
        if vibrateOnConnect, let h = haptic {
            let g = UINotificationFeedbackGenerator()
            g.prepare()
            g.notificationOccurred(h)
        }
        toast = text
        toastSeq += 1
        let seq = toastSeq
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) { [weak self] in
            guard let self = self, self.toastSeq == seq else { return }
            self.toast = nil
        }
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

// MARK: - ピル

struct MutePill: View {
    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "mic.slash.fill")
                .font(.system(size: 12, weight: .semibold))
            Text("ミュート中")
                .font(.system(size: 13, weight: .semibold))
        }
        .foregroundColor(.white)
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(Color.red)
        .clipShape(Capsule())
    }
}

/// 真っ黒の省電力画面の上に出す、小さく暗い状態表示 (カメラが動いていることが分かるように)
struct LiveStatusPill: View {
    let text: String
    let live: Bool
    let muted: Bool

    var body: some View {
        HStack(spacing: 5) {
            Circle()
                .fill(live ? Color.green : Color(white: 0.5))
                .frame(width: 5, height: 5)
            Text(text)
                .font(.system(size: 11, weight: .medium))
            if muted {
                Image(systemName: "mic.slash.fill")
                    .font(.system(size: 10))
                    .foregroundColor(Color.red)
            }
        }
        .foregroundColor(Color(white: 0.65))
        .padding(.horizontal, 9)
        .padding(.vertical, 4)
        .background(Color(white: 0.1))
        .clipShape(Capsule())
        .opacity(0.55)
    }
}

// MARK: - ContentView

struct ContentView: View {
    @EnvironmentObject var model: AppModel
    @StateObject private var updater = UpdateChecker()
    @AppStorage("showGrid") private var showGrid = false
    @AppStorage("showLevel") private var showLevel = true
    @State private var rotationHint = false

    private let cardColor = Color(white: 0.11)

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

            if model.screenBlack {
                ZStack(alignment: .top) {
                    Color.black
                        .ignoresSafeArea()
                        .contentShape(Rectangle())
                        .onTapGesture { model.setScreenBlack(false) }
                    LiveStatusPill(text: blackPillText,
                                   live: model.isConnected || model.recActive,
                                   muted: model.micEnabled && model.micMuted)
                        .padding(.top, 8)
                        .allowsHitTesting(false)
                }
            }
        }
        .overlay(alignment: .top) {
            if let t = model.toast {
                Text(t)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(.white)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 9)
                    .background(Color(white: 0.2))
                    .clipShape(Capsule())
                    .padding(.top, 10)
                    .transition(.move(edge: .top).combined(with: .opacity))
                    .allowsHitTesting(false)
            }
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

    private var blackPillText: String {
        if model.isConnected && model.streamActiveUI { return "配信中" }
        if model.recActive { return "録画中" }
        return "待機中"
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

    private var infoCard: some View {
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
    }

    private var togglesCard: some View {
        VStack(spacing: 0) {
            Toggle("プレビュー", isOn: $model.previewEnabled)
                .tint(.green)
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
            if !model.previewEnabled && rotationHint {
                Text("プレビューをオンにすると映る範囲を確認できます")
                    .font(.system(size: 13))
                    .foregroundColor(Color(white: 0.55))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 16)
                    .padding(.bottom, 10)
            }
            Divider().background(Color(white: 0.2))
            Toggle("接続時に振動", isOn: $model.vibrateOnConnect)
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
    }

    private var wifiCard: some View {
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
    }

    private var recordRow: some View {
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
    }

    private var footnotes: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("マイク: \(model.micText)")
                .font(.system(size: 13))
                .foregroundColor(Color(white: 0.55))

            if !model.recError.isEmpty {
                Text(model.recError)
                    .font(.system(size: 13))
                    .foregroundColor(.orange)
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
