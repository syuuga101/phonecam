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

    /// 自動で黒くするまでの秒数 (0 = しない)。メインスレッドで呼ぶ。保存し、PC へステータスも送る。
    func setAutoBlackSeconds(_ value: Int) {
        let v = min(max(value, 0), 3600)
        autoBlackSeconds = v
        stateLock.lock()
        autoBlackFlag = v
        stateLock.unlock()
        UserDefaults.standard.set(v, forKey: "autoBlackSeconds")
        noteTouch()
        timerQueue.async { [weak self] in self?.sendFullStatus() }
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
