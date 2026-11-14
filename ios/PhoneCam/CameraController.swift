import Foundation
import AVFoundation
import CoreMedia
import CoreVideo
import UIKit

/// AVFoundation を使ったカメラ制御。設定変更は sessionQueue で直列に処理する。
final class CameraController: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate, AVCaptureAudioDataOutputSampleBufferDelegate {
    let session = AVCaptureSession()

    /// 映像フレーム (videoQueue 上で呼ばれる)
    var onFrame: ((CVPixelBuffer, CMTime, CMTime) -> Void)?
    /// 設定を適用した後に最新のステータスを渡す (sessionQueue 上で呼ばれる)
    var onApplied: (([String: Any]) -> Void)?
    /// 実際の (width, height, fps, bitrateMbps)
    var onStreamSettings: ((Int, Int, Int, Int) -> Void)?
    /// ユーザー向けエラーメッセージ
    var onError: ((String) -> Void)?
    /// 出力の回転角 (0/90/180/270) が変わったとき (sessionQueue 上で呼ばれる)
    var onRotation: ((Int) -> Void)?
    /// マイク音声 (captureTime 秒, サンプルレート, Int16 LE モノラル PCM)。マイクが有効なときだけ呼ばれる (audioQueue 上)
    var onAudio: ((Double, Int, Data) -> Void)?
    /// 録画用に元の音声 CMSampleBuffer を渡す (audioQueue 上)
    var onAudioSample: ((CMSampleBuffer) -> Void)?
    /// 使用中のカメラデバイスが変わったとき (sessionQueue 上・構成の確定後に呼ばれる)。プレビューの RotationCoordinator 用
    var onDevice: ((AVCaptureDevice) -> Void)?

    private let sessionQueue = DispatchQueue(label: "phonecam.session")
    private let videoQueue = DispatchQueue(label: "phonecam.video", qos: .userInteractive)
    private let output = AVCaptureVideoDataOutput()
    private var outputAdded = false

    // 音声 (追加/削除は sessionQueue 上)
    private let audioQueue = DispatchQueue(label: "phonecam.audio", qos: .userInteractive)
    private let audioOutput = AVCaptureAudioDataOutput()
    private var audioInput: AVCaptureDeviceInput?
    private var audioOutputAdded = false
    private var micWanted = false          // PC からの "mic"
    private var micMuted = false           // 実効ミュート = userMuted || pcMuted (true の間は音声パケットを一切送らない)。sessionQueue 上
    // iPhone 側で操作したミュート (UserDefaults に保存し、音声が始まる前から有効。PC の micMuted=false では解除されない)
    private var userMuted = UserDefaults.standard.bool(forKey: "micMuted")
    private var pcMuted = false            // PC 側からのミュート (保存しない)
    private var phoneEditSeq = 0           // iPhone 側で操作するたびに増やす (sessionQueue 上)
    private var recAudioWanted = false     // 録画に音声を入れる
    private var audioRequestPending = false
    // 低遅延マイク (AVAudioEngine)。sessionQueue 上で操作する
    private let lowLatencyMic = LowLatencyMic()
    private var llActive = false           // LowLatencyMic で配信中
    private var llFailed = false           // 起動失敗 → capture 経路にフォールバック (mic が OFF になるまで)
    private var recordHighRes = false      // 録画のため 4K に切り替え中

    // 複数スレッドから読む値 (flagLock で保護)
    private let flagLock = NSLock()
    private var micSendFlag = false
    private var audioRate = 0
    private var droppedCount = 0
    private var droppedLate = 0
    private var droppedOutOfBuffers = 0
    private var droppedDiscontinuity = 0
    private var streamReqW = 1920
    private var streamReqH = 1080

    // システム負荷 (sessionQueue 上)
    private var pressureObservation: NSKeyValueObservation?
    private weak var pressureDevice: AVCaptureDevice?
    private var pressureLevelName = "nominal"
    private var pressureFactorNames: [String] = []

    private var device: AVCaptureDevice?
    private var input: AVCaptureDeviceInput?
    // 最後に sessionQueue 上で作ったステータス (どのスレッドからでも読める。sessionQueue を待たない)
    private let statusLock = NSLock()
    private var cachedStatus: [String: Any] = [:]

    private var started = false
    private var runWanted = false          // AppModel が求めるカメラの起動状態 (sessionQueue 上)

    // 要求値
    private var reqLens = "wide"
    private var reqWidth = 1920
    private var reqHeight = 1080
    private var reqFps = 60
    private var bitrate = 20

    // 実際に適用された値
    private var activeLens = "wide"
    private var activeWidth = 0
    private var activeHeight = 0
    private var activeFps = 0

    // マージ済みの制御状態
    private var exposure: [String: Any] = ["mode": "auto", "bias": 0.0, "iso": 200.0, "shutter": 100.0]
    private var wb: [String: Any] = ["mode": "auto", "temperature": 5000.0, "tint": 0.0]
    private var focus: [String: Any] = ["mode": "auto", "position": 0.5]
    private var zoom: Double = 1.0
    private var torch = false

    // 向き (auto / landscape / portrait)
    private var reqOrientation = "auto"
    private var autoAngle = 0          // auto のときの角度 (RotationCoordinator 由来)
    private var activeRotation = 0     // 実際に接続へ適用した角度
    private var rotationCoordinator: AVCaptureDevice.RotationCoordinator?
    private var rotationObservation: NSKeyValueObservation?
    private weak var rotationDevice: AVCaptureDevice?

    private static let allParts: Set<String> = ["exposure", "wb", "focus", "zoom", "torch"]

    override init() {
        super.init()
        micMuted = userMuted
        NotificationCenter.default.addObserver(
            forName: .AVCaptureSessionRuntimeError,
            object: session,
            queue: nil
        ) { [weak self] _ in
            self?.sessionQueue.async {
                guard let self = self, self.started, self.runWanted, !self.session.isRunning else { return }
                self.session.startRunning()
            }
        }
    }

    deinit {
        pressureObservation?.invalidate()
        pressureObservation = nil
        rotationObservation?.invalidate()
        rotationObservation = nil
        NotificationCenter.default.removeObserver(self)
    }

    // MARK: - 開始

    func start() {
        // UI は縦・横どちらにも回る (設定「画面の向き」で縦 / 横に固定もできる)。端末の向きの変化を RotationCoordinator に伝えるため、明示的に有効化する
        DispatchQueue.main.async {
            UIDevice.current.beginGeneratingDeviceOrientationNotifications()
        }
        AVCaptureDevice.requestAccess(for: .video) { [weak self] granted in
            guard let self = self else { return }
            self.sessionQueue.async {
                guard granted else {
                    self.onError?(String(localized: "カメラへのアクセスが許可されていません。設定アプリでPhoneCamのカメラを許可してください。"))
                    return
                }
                self.started = true
                self.reconfigure()
                self.updateAudioOnQueue()
                self.applyRunningOnQueue()
                self.publishState()
            }
        }
    }

    // MARK: - 起動 / 停止

    /// カメラセッションを動かす / 止める。start()/stopRunning は必ず sessionQueue 上で行う。
    func setRunning(_ want: Bool, completion: (() -> Void)? = nil) {
        sessionQueue.async { [weak self] in
            guard let self = self else { return }
            self.runWanted = want
            self.applyRunningOnQueue()
            if self.started {
                self.publishState()
            }
            if let completion { DispatchQueue.main.async { completion() } }
        }
    }

    /// sessionQueue 上
    private func applyRunningOnQueue() {
        guard started else { return }
        if runWanted {
            if !session.isRunning { session.startRunning() }
        } else if session.isRunning {
            session.stopRunning()
        }
    }

    // MARK: - ステータス

    /// 最後に作ったステータス (キャッシュ)。どのスレッドからでも呼べ、sessionQueue を待たない。
    /// カメラ構成の変更中に呼び出し側 (ネットワーク / タイマー) が止まらないよう、sync は使わない。
    /// 最新化は refreshStatus() で非同期に行う。
    func status() -> [String: Any] {
        statusLock.lock()
        var s = cachedStatus
        statusLock.unlock()
        flagLock.lock()
        s["cameraDroppedFrames"] = droppedCount
        s["cameraDropReasons"] = [
            "late": droppedLate,
            "outOfBuffers": droppedOutOfBuffers,
            "discontinuity": droppedDiscontinuity
        ] as [String: Any]
        flagLock.unlock()
        return s
    }

    /// ステータスのキャッシュを sessionQueue 上で作り直す (非同期)
    func refreshStatus() {
        sessionQueue.async { [weak self] in
            _ = self?.buildStatus()
        }
    }

    /// sessionQueue 上でステータスを作って onApplied で配信する (非同期)
    func requestPublish() {
        sessionQueue.async { [weak self] in
            guard let self = self, self.started else { return }
            self.publishState()
        }
    }

    private func fin(_ v: Double) -> Double {
        return v.isFinite ? v : 0
    }

    /// sessionQueue 上: ステータスを作ってキャッシュも更新する
    private func buildStatus() -> [String: Any] {
        let s = buildStatusUncached()
        statusLock.lock()
        cachedStatus = s
        statusLock.unlock()
        return s
    }

    /// 搭載レンズの一覧。端末の構成は変わらないので 1 回だけ調べる (毎秒の状態作成で AVCaptureDevice を探し直さない)
    private lazy var availableLenses: [String] = {
        var lenses = ["wide"]
        if AVCaptureDevice.default(.builtInUltraWideCamera, for: .video, position: .back) != nil {
            lenses.append("ultrawide")
        }
        if self.frontDevice() != nil {
            lenses.append("front")
        }
        return lenses
    }()

    private func buildStatusUncached() -> [String: Any] {
        var s: [String: Any] = [:]
        s["appVersion"] = (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? ""
        s["lens"] = activeLens
        s["lenses"] = availableLenses
        let size = reportedSize()
        s["width"] = size.0
        s["height"] = size.1
        s["fps"] = activeFps
        s["bitrate"] = bitrate
        s["orientation"] = reqOrientation
        s["rotationAngle"] = activeRotation
        s["exposureMode"] = (exposure["mode"] as? String) ?? "auto"
        s["wbMode"] = (wb["mode"] as? String) ?? "auto"
        s["focusMode"] = (focus["mode"] as? String) ?? "auto"

        s["cameraRunning"] = session.isRunning
        s["pressureLevel"] = pressureLevelName
        s["pressureFactors"] = pressureFactorNames
        flagLock.lock()
        let dropped = droppedCount
        let rate = audioRate
        let dLate = droppedLate
        let dBuf = droppedOutOfBuffers
        let dDisc = droppedDiscontinuity
        flagLock.unlock()
        s["cameraDroppedFrames"] = dropped
        s["cameraDropReasons"] = [
            "late": dLate,
            "outOfBuffers": dBuf,
            "discontinuity": dDisc
        ] as [String: Any]
        let auth = AVCaptureDevice.authorizationStatus(for: .audio)
        let micAvailable = (auth != .denied && auth != .restricted)
            && AVCaptureDevice.default(for: .audio) != nil
        s["mic"] = [
            "enabled": micWanted,
            "available": micAvailable,
            "sampleRate": rate
        ] as [String: Any]
        s["micPath"] = llActive ? "lowLatency" : "capture"
        s["micMuted"] = userMuted   // iPhone で設定したミュートだけを返す (PC からの pcMuted は含めない)
        s["phoneEdit"] = [
            "seq": phoneEditSeq,
            "lens": activeLens,
            "micMuted": userMuted,
            "zoom": fin(zoom),
            "torch": torch
        ] as [String: Any]

        guard let dev = device else {
            return s
        }
        let fmt = dev.activeFormat
        let frameDur = CMTimeGetSeconds(dev.activeVideoMinFrameDuration)
        let maxExp = min(CMTimeGetSeconds(fmt.maxExposureDuration), frameDur > 0 ? frameDur : CMTimeGetSeconds(fmt.maxExposureDuration))

        s["isoMin"] = fin(Double(fmt.minISO))
        s["isoMax"] = fin(Double(fmt.maxISO))
        s["iso"] = fin(Double(dev.iso))
        s["exposureSeconds"] = fin(CMTimeGetSeconds(dev.exposureDuration))
        s["exposureMinSeconds"] = fin(CMTimeGetSeconds(fmt.minExposureDuration))
        s["exposureMaxSeconds"] = fin(maxExp)
        s["biasMin"] = fin(Double(dev.minExposureTargetBias))
        s["biasMax"] = fin(Double(dev.maxExposureTargetBias))

        let gains = clampGains(dev.deviceWhiteBalanceGains, dev)
        let tt = dev.temperatureAndTintValues(for: gains)
        s["temperature"] = fin(Double(tt.temperature))
        s["tint"] = fin(Double(tt.tint))

        s["focusSupported"] = dev.isFocusModeSupported(.locked) && dev.isLockingFocusWithCustomLensPositionSupported
        s["lensPosition"] = fin(Double(dev.lensPosition))
        s["zoom"] = fin(Double(dev.videoZoomFactor))
        s["zoomMax"] = fin(min(Double(dev.maxAvailableVideoZoomFactor), 10.0))
        s["torchAvailable"] = dev.hasTorch && dev.isTorchAvailable
        s["torch"] = (dev.torchMode == .on)
        return s
    }

    /// ステータス / UI に出す映像サイズ。4K 録画中はストリーム側 (PC の要求) のサイズを返す。
    private func reportedSize() -> (Int, Int) {
        if recordHighRes && activeWidth > reqWidth {
            return (reqWidth, reqHeight)
        }
        return (activeWidth, activeHeight)
    }

    private func publishState() {
        let size = reportedSize()
        onStreamSettings?(size.0, size.1, activeFps, bitrate)
        onApplied?(buildStatus())
    }

    // MARK: - 制御の受け取り

    func apply(_ json: [String: Any]) {
        sessionQueue.async { [weak self] in
            self?.applyOnQueue(json)
        }
    }

    private func intValue(_ any: Any?) -> Int? {
        if let n = any as? NSNumber { return n.intValue }
        return nil
    }

    private func doubleValue(_ any: Any?, _ def: Double) -> Double {
        if let n = any as? NSNumber { return n.doubleValue }
        return def
    }

    /// fromPhone = iPhone 側の操作 (phoneChange)。false は PC からの制御。
    private func applyOnQueue(_ json: [String: Any], fromPhone: Bool = false) {
        var parts = Set<String>()
        var needReconfigure = false

        if let l = json["lens"] as? String, (l == "wide" || l == "ultrawide" || l == "front"), l != reqLens {
            reqLens = l
            needReconfigure = true
        }
        if let w = intValue(json["width"]), w > 0, w != reqWidth {
            reqWidth = w
            needReconfigure = true
        }
        if let h = intValue(json["height"]), h > 0, h != reqHeight {
            reqHeight = h
            needReconfigure = true
        }
        if let f = intValue(json["fps"]), f > 0, f != reqFps {
            reqFps = f
            needReconfigure = true
        }
        flagLock.lock()
        streamReqW = reqWidth
        streamReqH = reqHeight
        flagLock.unlock()
        var orientationChanged = false
        if let o = json["orientation"] as? String,
           (o == "auto" || o == "landscape" || o == "portrait"), o != reqOrientation {
            reqOrientation = o
            orientationChanged = true
        }
        if let b = intValue(json["bitrate"]), b > 0 {
            bitrate = min(max(b, 1), 100)
        }
        if let e = json["exposure"] as? [String: Any] {
            for (k, v) in e { exposure[k] = v }
            parts.insert("exposure")
        }
        if let w = json["wb"] as? [String: Any] {
            for (k, v) in w { wb[k] = v }
            parts.insert("wb")
        }
        if let f = json["focus"] as? [String: Any] {
            for (k, v) in f { focus[k] = v }
            parts.insert("focus")
        }
        if let z = json["zoom"] as? NSNumber {
            zoom = z.doubleValue
            parts.insert("zoom")
        }
        if let t = json["torch"] as? NSNumber {
            torch = t.boolValue
            parts.insert("torch")
        }

        // PC / iPhone からのミュート (seq は applyOnQueue の呼び出し側 = phoneChange だけが増やす)
        // iPhone の操作: ミュート / 解除とも両方の状態を揃える。PC からの制御: PC 側のミュートだけを更新し、
        // iPhone で設定済みのミュート (userMuted) は PC の micMuted=false では解除しない。
        if let mm = json["micMuted"] as? NSNumber {
            if fromPhone {
                userMuted = mm.boolValue
                pcMuted = false
                UserDefaults.standard.set(userMuted, forKey: "micMuted")
            } else {
                pcMuted = mm.boolValue
            }
            micMuted = userMuted || pcMuted
            updateSendFlag()
        }

        if let m = json["mic"] as? [String: Any], let en = m["enabled"] as? NSNumber {
            micWanted = en.boolValue
            if !micWanted { llFailed = false }
            updateSendFlag()
            if started {
                updateAudioOnQueue()
            }
        }

        if started {
            if needReconfigure {
                reconfigure()   // 内部で全設定を再適用する
            } else {
                if orientationChanged {
                    applyRotationOnQueue()
                }
                if !parts.isEmpty {
                    applyDeviceSettings(parts)
                }
            }
        }
        publishState()
    }

    /// 音声を送ってよいか (マイクがオン かつ ミュートでない) を flagLock 側へ反映する (sessionQueue 上)
    private func updateSendFlag() {
        flagLock.lock()
        micSendFlag = micWanted && !micMuted
        flagLock.unlock()
    }

    /// iPhone 側で操作されたレンズ / ミュートを適用し、phoneEdit.seq を 1 つ進める。どのスレッドからでも可。
    /// 変化がなければ何もしない (seq も進めない)。
    /// zoom / torch も iPhone 側の操作として同じ経路 (applyOnQueue) で適用する。
    /// ピンチ中の途中経過は zoomFinal = false で呼ぶ (seq を進めず、PC への通知は最後の 1 回だけにする)。
    /// レンズを切り替えるときはライトを消し、ズームを 1× に戻す。
    func phoneChange(lens: String? = nil, micMuted newMuted: Bool? = nil, zoom newZoom: Double? = nil,
                     torch newTorch: Bool? = nil, zoomFinal: Bool = true) {
        sessionQueue.async { [weak self] in
            guard let self = self else { return }
            var json: [String: Any] = [:]
            var bump = false
            if let l = lens, (l == "wide" || l == "ultrawide" || l == "front"), l != self.reqLens {
                json["lens"] = l
                json["torch"] = NSNumber(value: false)
                json["zoom"] = NSNumber(value: 1.0)
                bump = true
            }
            if let m = newMuted, m != self.userMuted {
                json["micMuted"] = NSNumber(value: m)
                bump = true
            }
            if json["torch"] == nil, let t = newTorch, t != self.torch {
                json["torch"] = NSNumber(value: t)
                bump = true
            }
            if json["zoom"] == nil, let z = newZoom, z.isFinite, abs(z - self.zoom) > 0.0005 {
                json["zoom"] = NSNumber(value: z)
                if zoomFinal { bump = true }
            }
            guard !json.isEmpty else { return }
            if bump { self.phoneEditSeq += 1 }
            self.applyOnQueue(json, fromPhone: true)
        }
    }

    /// 接続直後、iPhone 側でミュート中なら PC に知らせる (phoneEdit.seq を 1 つ進めて publish)。
    /// PC は接続後の最初の status の seq を基準にし、その後に増えたときだけ取り込むため、少し待ってから進める。
    func announceMuteIfNeeded() {
        sessionQueue.asyncAfter(deadline: .now() + 0.8) { [weak self] in
            guard let self = self, self.userMuted else { return }
            self.phoneEditSeq += 1
            self.publishState()
        }
    }

    // MARK: - デバイス / フォーマット

    /// インカメ (広角 → TrueDepth の順で探す)
    private func frontDevice() -> AVCaptureDevice? {
        if let d = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .front) {
            return d
        }
        return AVCaptureDevice.default(.builtInTrueDepthCamera, for: .video, position: .front)
    }

    private func pickDevice() -> AVCaptureDevice? {
        if reqLens == "front", let d = frontDevice() {
            return d
        }
        let type: AVCaptureDevice.DeviceType = (reqLens == "ultrawide") ? .builtInUltraWideCamera : .builtInWideAngleCamera
        if let d = AVCaptureDevice.default(type, for: .video, position: .back) {
            return d
        }
        return AVCaptureDevice.default(for: .video)
    }

    private struct FormatChoice {
        let format: AVCaptureDevice.Format
        let fps: Int
    }

    private func chooseFormat(_ dev: AVCaptureDevice) -> FormatChoice? {
        let wantSubtype = OSType(kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange)

        func find(width: Int, height: Int, fps: Int) -> AVCaptureDevice.Format? {
            let matches = dev.formats.filter { f in
                let dim = CMVideoFormatDescriptionGetDimensions(f.formatDescription)
                guard Int(dim.width) == width, Int(dim.height) == height else { return false }
                return f.videoSupportedFrameRateRanges.contains { $0.maxFrameRate >= Double(fps) }
            }
            if let preferred = matches.first(where: { CMFormatDescriptionGetMediaSubType($0.formatDescription) == wantSubtype }) {
                return preferred
            }
            return matches.first
        }

        var attempts: [(Int, Int, Int)] = []
        if recordHighRes {
            // 録画のため 4K (現在の fps、無ければ 30)
            attempts.append((3840, 2160, reqFps))
            attempts.append((3840, 2160, 30))
        }
        attempts += [
            (reqWidth, reqHeight, reqFps),
            (reqWidth, reqHeight, 30),
            (1920, 1080, min(reqFps, 60)),
            (1920, 1080, 30),
            (1280, 720, 30)
        ]
        for (w, h, f) in attempts {
            if let fmt = find(width: w, height: h, fps: f) {
                return FormatChoice(format: fmt, fps: f)
            }
        }
        if let first = dev.formats.first {
            return FormatChoice(format: first, fps: 30)
        }
        return nil
    }

    /// レンズ / 解像度 / fps が変わったときの再構成。終わったら全設定を再適用する。
    private func reconfigure() {
        guard var dev = pickDevice() else {
            onError?(String(localized: "使用できるカメラが見つかりません。"))
            return
        }
        guard var choice = chooseFormat(dev) else {
            onError?(String(localized: "使用できるカメラフォーマットが見つかりません。"))
            return
        }

        session.beginConfiguration()
        session.sessionPreset = .inputPriority

        let oldInput = input
        if let old = oldInput {
            // 切り替え前のカメラのライトが点いたままにならないよう消す
            let oldDev = old.device
            if oldDev !== dev {
                // 切り替え先のカメラでライトが勝手に点かないよう、要求値も落とす
                torch = false
                if oldDev.hasTorch, oldDev.torchMode != .off, (try? oldDev.lockForConfiguration()) != nil {
                    oldDev.torchMode = .off
                    oldDev.unlockForConfiguration()
                }
            }
            session.removeInput(old)
            input = nil
        }
        var added = false
        do {
            let newInput = try AVCaptureDeviceInput(device: dev)
            if session.canAddInput(newInput) {
                session.addInput(newInput)
                input = newInput
                added = true
            } else {
                onError?(String(localized: "カメラ入力を追加できませんでした。"))
            }
        } catch {
            onError?(String(localized: "カメラを開けませんでした。他のアプリがカメラを使用していないか確認してください。"))
        }
        if !added, let old = oldInput, session.canAddInput(old) {
            // 追加に失敗したときは元の入力へ戻す (フォーマットも元のカメラ用に選び直す)
            session.addInput(old)
            input = old
            dev = old.device
            if let c = chooseFormat(dev) {
                choice = c
            }
        }

        if !outputAdded {
            output.videoSettings = [
                kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange
            ]
            output.alwaysDiscardsLateVideoFrames = true
            output.setSampleBufferDelegate(self, queue: videoQueue)
            if session.canAddOutput(output) {
                session.addOutput(output)
                outputAdded = true
            }
        }

        device = dev
        observePressure(for: dev)

        do {
            try dev.lockForConfiguration()
            dev.activeFormat = choice.format
            let duration = CMTime(value: 1, timescale: CMTimeScale(choice.fps))
            dev.activeVideoMinFrameDuration = duration
            dev.activeVideoMaxFrameDuration = duration
            if choice.format.isVideoHDRSupported {
                dev.automaticallyAdjustsVideoHDREnabled = false
                dev.isVideoHDREnabled = false
            }
            if dev.isSmoothAutoFocusSupported {
                dev.isSmoothAutoFocusEnabled = true
            }
            dev.unlockForConfiguration()
        } catch {
            onError?(String(localized: "カメラの設定を変更できませんでした。"))
        }

        if let conn = output.connection(with: .video) {
            if conn.isVideoStabilizationSupported {
                conn.preferredVideoStabilizationMode = .off
            }
            // インカメでも反転しない映像を送る (左右反転は PC 側で選ぶ)
            if conn.isVideoMirroringSupported {
                conn.automaticallyAdjustsVideoMirroring = false
                conn.isVideoMirrored = false
            }
        }
        setupRotationCoordinator(for: dev)
        applyRotationOnQueue()

        session.commitConfiguration()
        onDevice?(dev)

        let dim = CMVideoFormatDescriptionGetDimensions(choice.format.formatDescription)
        activeWidth = Int(dim.width)
        activeHeight = Int(dim.height)
        activeFps = choice.fps
        if dev.position == .front {
            activeLens = "front"
        } else {
            activeLens = (dev.deviceType == .builtInUltraWideCamera) ? "ultrawide" : "wide"
        }

        applyDeviceSettings(CameraController.allParts)
    }

    // MARK: - 向き

    /// 角度を 0/90/180/270 のいちばん近い値にそろえる
    private func snapAngle(_ a: Double) -> Int {
        guard a.isFinite else { return 0 }
        let q = Int((a / 90.0).rounded())
        return ((q % 4) + 4) % 4 * 90
    }

    /// デバイスが変わったとき RotationCoordinator と KVO を作り直す (sessionQueue 上)
    private func setupRotationCoordinator(for dev: AVCaptureDevice) {
        if rotationCoordinator != nil && rotationDevice === dev {
            return
        }
        rotationObservation?.invalidate()
        rotationObservation = nil
        rotationDevice = dev

        let coordinator = AVCaptureDevice.RotationCoordinator(device: dev, previewLayer: nil)
        rotationCoordinator = coordinator
        autoAngle = snapAngle(Double(coordinator.videoRotationAngleForHorizonLevelCapture))
        rotationObservation = coordinator.observe(
            \.videoRotationAngleForHorizonLevelCapture,
            options: [.initial, .new]
        ) { [weak self] coord, _ in
            let angle = Double(coord.videoRotationAngleForHorizonLevelCapture)
            self?.sessionQueue.async {
                guard let self = self else { return }
                self.autoAngle = self.snapAngle(angle)
                if self.started && self.reqOrientation == "auto" {
                    self.applyRotationOnQueue()
                }
            }
        }
    }

    /// 要求された向きを出力接続へ適用する (sessionQueue 上)
    private func applyRotationOnQueue() {
        let target: Int
        switch reqOrientation {
        case "landscape": target = 0
        case "portrait": target = 90
        default: target = autoAngle
        }
        guard let conn = output.connection(with: .video) else { return }
        let angle = CGFloat(target)
        if conn.isVideoRotationAngleSupported(angle) && conn.videoRotationAngle != angle {
            conn.videoRotationAngle = angle
        }
        let applied = snapAngle(Double(conn.videoRotationAngle))
        if applied != activeRotation {
            activeRotation = applied
            onRotation?(applied)
            onApplied?(buildStatus())
        }
    }

    // MARK: - 露出 / WB / フォーカス / ズーム / ライト

    private func clamp(_ v: Double, _ lo: Double, _ hi: Double) -> Double {
        if hi < lo { return lo }
        return min(max(v, lo), hi)
    }

    private func clampGains(_ g: AVCaptureDevice.WhiteBalanceGains, _ dev: AVCaptureDevice) -> AVCaptureDevice.WhiteBalanceGains {
        var out = g
        let maxGain = dev.maxWhiteBalanceGain
        out.redGain = min(max(1.0, g.redGain), maxGain)
        out.greenGain = min(max(1.0, g.greenGain), maxGain)
        out.blueGain = min(max(1.0, g.blueGain), maxGain)
        return out
    }

    private func applyDeviceSettings(_ parts: Set<String>) {
        guard let dev = device else { return }
        do {
            try dev.lockForConfiguration()
        } catch {
            return
        }
        defer { dev.unlockForConfiguration() }

        if parts.contains("zoom") {
            let lo = Double(dev.minAvailableVideoZoomFactor)
            let hi = min(Double(dev.maxAvailableVideoZoomFactor), 10.0)
            dev.videoZoomFactor = CGFloat(clamp(zoom, lo, hi))
        }

        if parts.contains("focus") {
            let mode = (focus["mode"] as? String) ?? "auto"
            switch mode {
            case "manual":
                if dev.isLockingFocusWithCustomLensPositionSupported {
                    let pos = clamp(doubleValue(focus["position"], 0.5), 0.0, 1.0)
                    dev.setFocusModeLocked(lensPosition: Float(pos), completionHandler: nil)
                }
            case "locked":
                if dev.isFocusModeSupported(.locked) {
                    dev.focusMode = .locked
                }
            default:
                if dev.isFocusModeSupported(.continuousAutoFocus) {
                    dev.focusMode = .continuousAutoFocus
                }
            }
        }

        if parts.contains("wb") {
            let mode = (wb["mode"] as? String) ?? "auto"
            switch mode {
            case "manual":
                if dev.isLockingWhiteBalanceWithCustomDeviceGainsSupported {
                    let temp = clamp(doubleValue(wb["temperature"], 5000), 2000, 10000)
                    let tint = clamp(doubleValue(wb["tint"], 0), -150, 150)
                    let tt = AVCaptureDevice.WhiteBalanceTemperatureAndTintValues(temperature: Float(temp), tint: Float(tint))
                    let gains = clampGains(dev.deviceWhiteBalanceGains(for: tt), dev)
                    dev.setWhiteBalanceModeLocked(with: gains, completionHandler: nil)
                }
            case "locked":
                if dev.isWhiteBalanceModeSupported(.locked) {
                    dev.whiteBalanceMode = .locked
                }
            default:
                if dev.isWhiteBalanceModeSupported(.continuousAutoWhiteBalance) {
                    dev.whiteBalanceMode = .continuousAutoWhiteBalance
                }
            }
        }

        if parts.contains("exposure") {
            let mode = (exposure["mode"] as? String) ?? "auto"
            switch mode {
            case "manual":
                if dev.isExposureModeSupported(.custom) {
                    let fmt = dev.activeFormat
                    let shutter = max(1.0, doubleValue(exposure["shutter"], 100))
                    let frameDur = CMTimeGetSeconds(dev.activeVideoMinFrameDuration)
                    let minD = CMTimeGetSeconds(fmt.minExposureDuration)
                    var maxD = CMTimeGetSeconds(fmt.maxExposureDuration)
                    if frameDur > 0 { maxD = min(maxD, frameDur) }
                    let sec = clamp(1.0 / shutter, minD, maxD)
                    let iso = clamp(doubleValue(exposure["iso"], 200), Double(fmt.minISO), Double(fmt.maxISO))
                    let duration = CMTimeMakeWithSeconds(sec, preferredTimescale: 1_000_000_000)
                    dev.setExposureModeCustom(duration: duration, iso: Float(iso), completionHandler: nil)
                }
            case "locked":
                if dev.isExposureModeSupported(.locked) {
                    dev.exposureMode = .locked
                }
            default:
                if dev.isExposureModeSupported(.continuousAutoExposure) {
                    dev.exposureMode = .continuousAutoExposure
                }
                let bias = clamp(doubleValue(exposure["bias"], 0),
                                 Double(dev.minExposureTargetBias),
                                 Double(dev.maxExposureTargetBias))
                dev.setExposureTargetBias(Float(bias), completionHandler: nil)
            }
        }

        if parts.contains("torch") && dev.hasTorch {
            if torch {
                if dev.isTorchAvailable && dev.isTorchModeSupported(.on) {
                    dev.torchMode = .on
                }
            } else if dev.torchMode != .off && dev.isTorchModeSupported(.off) {
                dev.torchMode = .off
            }
        }
    }

    // MARK: - 録画連携 / カウンタ

    /// PC が要求したストリームの解像度 (向き補正前: 横長の width, height)
    func streamRequestSize() -> (Int, Int) {
        flagLock.lock()
        defer { flagLock.unlock() }
        return (streamReqW, streamReqH)
    }

    /// カメラ側でフレームを落とした数を 0 に戻す (クライアント接続時)
    func resetCounters() {
        flagLock.lock()
        droppedCount = 0
        droppedLate = 0
        droppedOutOfBuffers = 0
        droppedDiscontinuity = 0
        flagLock.unlock()
    }

    /// 録画の開始/終了に合わせてカメラ構成を変える。構成が終わったら completion (最大辺, fps) を呼ぶ。
    func setRecording(highRes: Bool, audio: Bool, completion: ((Int, Int) -> Void)?) {
        sessionQueue.async { [weak self] in
            guard let self = self else { return }
            let changed = (self.recordHighRes != highRes)
            self.recordHighRes = highRes
            self.recAudioWanted = audio
            if self.started {
                if changed {
                    self.reconfigure()
                }
                self.updateAudioOnQueue()
            }
            self.publishState()
            completion?(max(self.activeWidth, self.activeHeight), self.activeFps)
        }
    }

    // MARK: - マイク (sessionQueue 上)

    /// マイクが必要か (PC のマイク or 録画の音声) に合わせて経路を切り替える。
    /// - 録画の音声あり: capture セッション経路 (配信も同じ経路)
    /// - 配信のみ: LowLatencyMic (AVAudioEngine)。失敗したら capture 経路
    private func updateAudioOnQueue() {
        let want = micWanted || recAudioWanted
        if !want {
            stopLowLatencyOnQueue()
            if audioInput != nil { removeAudio() }
            return
        }

        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            break
        case .notDetermined:
            if audioRequestPending { return }
            audioRequestPending = true
            AVCaptureDevice.requestAccess(for: .audio) { [weak self] _ in
                self?.sessionQueue.async {
                    guard let self = self else { return }
                    self.audioRequestPending = false
                    if self.started && (self.micWanted || self.recAudioWanted) {
                        self.updateAudioOnQueue()
                    }
                    self.publishState()
                }
            }
            return
        default:
            return
        }

        let wantLL = micWanted && !recAudioWanted && !llFailed
        if wantLL {
            if !llActive {
                // capture 側の音声を先に外し、アプリのオーディオセッションを LowLatencyMic に任せる
                if audioInput != nil { removeAudio() }
                session.automaticallyConfiguresApplicationAudioSession = false
                lowLatencyMic.onSamples = { [weak self] data, rate, t in
                    self?.handleLowLatencySamples(data, rate: rate, captureTime: t)
                }
                if lowLatencyMic.start() {
                    llActive = true
                    flagLock.lock()
                    audioRate = lowLatencyMic.sampleRate
                    flagLock.unlock()
                    publishState()
                    return
                }
                lowLatencyMic.stop()
                llFailed = true
                publishState()
            } else {
                return
            }
        } else {
            stopLowLatencyOnQueue()
        }

        // capture セッション経路
        if audioInput == nil {
            session.automaticallyConfiguresApplicationAudioSession = true
            addAudio()
        }
    }

    private func stopLowLatencyOnQueue() {
        guard llActive else { return }
        lowLatencyMic.stop()
        lowLatencyMic.onSamples = nil
        llActive = false
        flagLock.lock()
        audioRate = 0
        flagLock.unlock()
        session.automaticallyConfiguresApplicationAudioSession = true
        publishState()
    }

    /// LowLatencyMic からのサンプル (LowLatencyMic の送信キュー上)
    private func handleLowLatencySamples(_ data: Data, rate: Int, captureTime: Double) {
        flagLock.lock()
        audioRate = rate
        let send = micSendFlag
        flagLock.unlock()
        guard send, rate > 0, let callback = onAudio else { return }
        callback(captureTime, rate, data)
    }

    private func addAudio() {
        guard let adev = AVCaptureDevice.default(for: .audio),
              let ai = try? AVCaptureDeviceInput(device: adev) else { return }
        session.beginConfiguration()
        if session.canAddInput(ai) {
            session.addInput(ai)
            audioInput = ai
        }
        if audioInput != nil && !audioOutputAdded {
            audioOutput.setSampleBufferDelegate(self, queue: audioQueue)
            if session.canAddOutput(audioOutput) {
                session.addOutput(audioOutput)
                audioOutputAdded = true
            }
        }
        session.commitConfiguration()
        if audioInput != nil {
            // 低遅延化: 音声 I/O バッファを短くする (カテゴリは変更しない。失敗しても無視)
            try? AVAudioSession.sharedInstance().setPreferredIOBufferDuration(0.005)
        }
    }

    private func removeAudio() {
        session.beginConfiguration()
        if let ai = audioInput {
            session.removeInput(ai)
            audioInput = nil
        }
        if audioOutputAdded {
            session.removeOutput(audioOutput)
            audioOutputAdded = false
        }
        session.commitConfiguration()
        flagLock.lock()
        audioRate = 0
        flagLock.unlock()
    }

    // MARK: - システム負荷 (sessionQueue 上)

    private func observePressure(for dev: AVCaptureDevice) {
        if pressureObservation != nil && pressureDevice === dev {
            return
        }
        pressureObservation?.invalidate()
        pressureObservation = nil
        pressureDevice = dev
        handlePressure(dev.systemPressureState)
        pressureObservation = dev.observe(\.systemPressureState, options: [.new]) { [weak self] d, _ in
            let state = d.systemPressureState
            self?.sessionQueue.async {
                self?.handlePressure(state)
            }
        }
    }

    private func handlePressure(_ st: AVCaptureDevice.SystemPressureState) {
        let level: String
        if st.level == .fair {
            level = "fair"
        } else if st.level == .serious {
            level = "serious"
        } else if st.level == .critical {
            level = "critical"
        } else if st.level == .shutdown {
            level = "shutdown"
        } else {
            level = "nominal"
        }
        var factors: [String] = []
        if st.factors.contains(.systemTemperature) { factors.append("systemTemperature") }
        if st.factors.contains(.peakPower) { factors.append("peakPower") }
        if st.factors.contains(.depthModuleTemperature) { factors.append("depthModuleTemperature") }
        if #available(iOS 17.0, *) {
            if st.factors.contains(.cameraTemperature) { factors.append("cameraTemperature") }
        }
        let changed = (level != pressureLevelName) || (factors != pressureFactorNames)
        pressureLevelName = level
        pressureFactorNames = factors
        if changed && started {
            onApplied?(buildStatus())
        }
    }
}

// MARK: - デリゲート

extension CameraController {
    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        if output === audioOutput {
            handleAudioSample(sampleBuffer)
            return
        }
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let pts = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
        let dur = CMSampleBufferGetDuration(sampleBuffer)
        onFrame?(pixelBuffer, pts, dur)
    }

    func captureOutput(_ output: AVCaptureOutput, didDrop sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        if output === audioOutput { return }
        // 落とした理由 (kCMSampleBufferAttachmentKey_DroppedFrameReason の値は文字列)
        var reasonText = ""
        if let raw = CMGetAttachment(sampleBuffer,
                                     key: kCMSampleBufferAttachmentKey_DroppedFrameReason,
                                     attachmentModeOut: nil) {
            reasonText = String(describing: raw)
        }
        flagLock.lock()
        droppedCount += 1
        if reasonText.contains("OutOfBuffers") {
            droppedOutOfBuffers += 1
        } else if reasonText.contains("Discontinuity") {
            droppedDiscontinuity += 1
        } else {
            // FrameWasLate (理由が取れないときも late 扱い)
            droppedLate += 1
        }
        flagLock.unlock()
    }

    /// 音声バッファを Int16 LE モノラルへ変換して渡す (audioQueue 上)
    private func handleAudioSample(_ sb: CMSampleBuffer) {
        onAudioSample?(sb)

        guard let fd = CMSampleBufferGetFormatDescription(sb),
              let asbdPtr = CMAudioFormatDescriptionGetStreamBasicDescription(fd) else { return }
        let asbd = asbdPtr.pointee
        let rate = Int(asbd.mSampleRate)
        flagLock.lock()
        audioRate = rate
        let send = micSendFlag
        flagLock.unlock()
        guard send, let callback = onAudio, rate > 0 else { return }

        let channels = max(1, Int(asbd.mChannelsPerFrame))
        let isFloat = (asbd.mFormatFlags & kAudioFormatFlagIsFloat) != 0
        let nonInterleaved = (asbd.mFormatFlags & kAudioFormatFlagIsNonInterleaved) != 0
        let bits = Int(asbd.mBitsPerChannel)
        guard (isFloat && bits == 32) || (!isFloat && bits == 16) else { return }
        let bytesPerSample = bits / 8

        var sizeNeeded = 0
        var st = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sb,
            bufferListSizeNeededOut: &sizeNeeded,
            bufferListOut: nil,
            bufferListSize: 0,
            blockBufferAllocator: nil,
            blockBufferMemoryAllocator: nil,
            flags: 0,
            blockBufferOut: nil
        )
        guard st == noErr, sizeNeeded > 0 else { return }

        let raw = UnsafeMutableRawPointer.allocate(
            byteCount: sizeNeeded,
            alignment: MemoryLayout<AudioBufferList>.alignment
        )
        defer { raw.deallocate() }
        let ablPtr = raw.bindMemory(to: AudioBufferList.self, capacity: 1)
        var blockBuffer: CMBlockBuffer?
        st = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sb,
            bufferListSizeNeededOut: nil,
            bufferListOut: ablPtr,
            bufferListSize: sizeNeeded,
            blockBufferAllocator: nil,
            blockBufferMemoryAllocator: nil,
            flags: UInt32(kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment),
            blockBufferOut: &blockBuffer
        )
        guard st == noErr else { return }

        let list = UnsafeMutableAudioBufferListPointer(ablPtr)
        guard list.count > 0 else { return }

        // 1 フレームあたりのサンプルを (buffer, オフセット) で引く
        let frames: Int
        if nonInterleaved {
            frames = Int(list[0].mDataByteSize) / bytesPerSample
        } else {
            frames = Int(list[0].mDataByteSize) / (bytesPerSample * channels)
        }
        guard frames > 0 else { return }

        var out = Data(count: frames * 2)
        out.withUnsafeMutableBytes { (dst: UnsafeMutableRawBufferPointer) in
            guard let dstBase = dst.baseAddress else { return }
            let d = dstBase.bindMemory(to: Int16.self, capacity: frames)
            for i in 0..<frames {
                var sum: Float = 0
                var n: Float = 0
                for c in 0..<channels {
                    var base: UnsafeRawPointer?
                    var index = i
                    if nonInterleaved {
                        guard c < list.count, let p = list[c].mData else { continue }
                        if i >= Int(list[c].mDataByteSize) / bytesPerSample { continue }
                        base = UnsafeRawPointer(p)
                    } else {
                        guard let p = list[0].mData else { continue }
                        base = UnsafeRawPointer(p)
                        index = i * channels + c
                    }
                    guard let b = base else { continue }
                    if isFloat {
                        sum += b.load(fromByteOffset: index * 4, as: Float.self)
                    } else {
                        sum += Float(b.load(fromByteOffset: index * 2, as: Int16.self)) / 32768.0
                    }
                    n += 1
                }
                var v = n > 0 ? sum / n : 0
                if !v.isFinite { v = 0 }
                v = min(max(v, -1.0), 1.0)
                d[i] = Int16(v * 32767.0).littleEndian
            }
        }
        withExtendedLifetime(blockBuffer) {}

        let t = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sb))
        callback(t, rate, out)
    }
}
