import Foundation
import AVFoundation
import CoreMedia
import CoreVideo
import UIKit

/// AVFoundation を使ったカメラ制御。設定変更は sessionQueue で直列に処理する。
final class CameraController: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
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

    private let sessionQueue = DispatchQueue(label: "phonecam.session")
    private let videoQueue = DispatchQueue(label: "phonecam.video", qos: .userInteractive)
    private let output = AVCaptureVideoDataOutput()
    private var outputAdded = false

    private var device: AVCaptureDevice?
    private var input: AVCaptureDeviceInput?
    private var started = false

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
        NotificationCenter.default.addObserver(
            forName: .AVCaptureSessionRuntimeError,
            object: session,
            queue: nil
        ) { [weak self] _ in
            self?.sessionQueue.async {
                guard let self = self, self.started, !self.session.isRunning else { return }
                self.session.startRunning()
            }
        }
    }

    deinit {
        rotationObservation?.invalidate()
        rotationObservation = nil
        NotificationCenter.default.removeObserver(self)
    }

    // MARK: - 開始

    func start() {
        // UI は縦固定なので、向きの変化を RotationCoordinator に伝えるため明示的に有効化する
        DispatchQueue.main.async {
            UIDevice.current.beginGeneratingDeviceOrientationNotifications()
        }
        AVCaptureDevice.requestAccess(for: .video) { [weak self] granted in
            guard let self = self else { return }
            self.sessionQueue.async {
                guard granted else {
                    self.onError?("カメラへのアクセスが許可されていません。設定アプリでPhoneCamのカメラを許可してください。")
                    return
                }
                self.started = true
                self.reconfigure()
                if !self.session.isRunning {
                    self.session.startRunning()
                }
                self.publishState()
            }
        }
    }

    // MARK: - ステータス

    /// 現在のステータス。sessionQueue 上以外から呼ぶこと。
    func status() -> [String: Any] {
        return sessionQueue.sync { buildStatus() }
    }

    private func fin(_ v: Double) -> Double {
        return v.isFinite ? v : 0
    }

    private func buildStatus() -> [String: Any] {
        var s: [String: Any] = [:]
        var lenses = ["wide"]
        if AVCaptureDevice.default(.builtInUltraWideCamera, for: .video, position: .back) != nil {
            lenses.append("ultrawide")
        }
        s["lens"] = activeLens
        s["lenses"] = lenses
        s["width"] = activeWidth
        s["height"] = activeHeight
        s["fps"] = activeFps
        s["bitrate"] = bitrate
        s["orientation"] = reqOrientation
        s["rotationAngle"] = activeRotation
        s["exposureMode"] = (exposure["mode"] as? String) ?? "auto"
        s["wbMode"] = (wb["mode"] as? String) ?? "auto"
        s["focusMode"] = (focus["mode"] as? String) ?? "auto"

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

    private func publishState() {
        onStreamSettings?(activeWidth, activeHeight, activeFps, bitrate)
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

    private func applyOnQueue(_ json: [String: Any]) {
        var parts = Set<String>()
        var needReconfigure = false

        if let l = json["lens"] as? String, (l == "wide" || l == "ultrawide"), l != reqLens {
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

    // MARK: - デバイス / フォーマット

    private func pickDevice() -> AVCaptureDevice? {
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

        let attempts: [(Int, Int, Int)] = [
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
        guard let dev = pickDevice() else {
            onError?("使用できるカメラが見つかりません。")
            return
        }
        guard let choice = chooseFormat(dev) else {
            onError?("使用できるカメラフォーマットが見つかりません。")
            return
        }

        session.beginConfiguration()
        session.sessionPreset = .inputPriority

        if let old = input {
            session.removeInput(old)
            input = nil
        }
        do {
            let newInput = try AVCaptureDeviceInput(device: dev)
            if session.canAddInput(newInput) {
                session.addInput(newInput)
                input = newInput
            } else {
                onError?("カメラ入力を追加できませんでした。")
            }
        } catch {
            onError?("カメラを開けませんでした。他のアプリがカメラを使用していないか確認してください。")
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
            onError?("カメラの設定を変更できませんでした。")
        }

        if let conn = output.connection(with: .video) {
            if conn.isVideoStabilizationSupported {
                conn.preferredVideoStabilizationMode = .off
            }
        }
        setupRotationCoordinator(for: dev)
        applyRotationOnQueue()

        session.commitConfiguration()

        let dim = CMVideoFormatDescriptionGetDimensions(choice.format.formatDescription)
        activeWidth = Int(dim.width)
        activeHeight = Int(dim.height)
        activeFps = choice.fps
        activeLens = (dev.deviceType == .builtInUltraWideCamera) ? "ultrawide" : "wide"

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
}

// MARK: - デリゲート

extension CameraController {
    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let pts = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
        let dur = CMSampleBufferGetDuration(sampleBuffer)
        onFrame?(pixelBuffer, pts, dur)
    }
}
