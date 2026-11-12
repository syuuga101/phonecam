import Foundation
import AVFoundation
import CoreMedia
import CoreVideo
import VideoToolbox
import Photos

/// 4K 入力をストリーム解像度へ縮小する (VTPixelTransferSession)。videoQueue 上でのみ使う。
final class FrameDownscaler {
    private var session: VTPixelTransferSession?
    private var pool: CVPixelBufferPool?
    private var poolWidth = 0
    private var poolHeight = 0

    deinit {
        if let s = session {
            VTPixelTransferSessionInvalidate(s)
        }
    }

    func scale(_ src: CVPixelBuffer, width: Int, height: Int) -> CVPixelBuffer? {
        guard width > 0, height > 0 else { return nil }
        if session == nil {
            var s: VTPixelTransferSession?
            let st = VTPixelTransferSessionCreate(allocator: nil, pixelTransferSessionOut: &s)
            guard st == noErr, let created = s else { return nil }
            session = created
        }
        if pool == nil || poolWidth != width || poolHeight != height {
            let attrs: [String: Any] = [
                kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,
                kCVPixelBufferWidthKey as String: width,
                kCVPixelBufferHeightKey as String: height,
                kCVPixelBufferIOSurfacePropertiesKey as String: [String: Any]()
            ]
            var newPool: CVPixelBufferPool?
            let st = CVPixelBufferPoolCreate(nil, nil, attrs as CFDictionary, &newPool)
            guard st == kCVReturnSuccess, let p = newPool else { return nil }
            pool = p
            poolWidth = width
            poolHeight = height
        }
        guard let p = pool, let s = session else { return nil }
        var out: CVPixelBuffer?
        let st = CVPixelBufferPoolCreatePixelBuffer(nil, p, &out)
        guard st == kCVReturnSuccess, let dst = out else { return nil }
        let r = VTPixelTransferSessionTransferImage(s, from: src, to: dst)
        if r != noErr { return nil }
        CVBufferPropagateAttachments(src, dst)
        return dst
    }
}

/// iPhone 側の高画質録画 (HEVC / AAC, .mov)。書き込みはすべて recordQueue 上で行う。
final class Recorder {
    struct Settings {
        var resolution = "same"     // same / 4k
        var bitrate = 50            // Mbps
        var includeAudio = true
        var saveToPhotos = true
    }

    /// 録画が自動で止まったとき (空き容量不足など。任意のスレッド)
    var onAutoStopped: (() -> Void)?
    /// 状態が変わったとき (ステータス送信用。任意のスレッド)
    var onChanged: (() -> Void)?

    private let recordQueue = DispatchQueue(label: "phonecam.record")
    private static let minFreeBytes: Int64 = 1_000_000_000
    private static let defaultsKey = "recordSettings"

    // lk で保護 (どのスレッドからも読める状態)
    private let lk = NSLock()
    private var settings = Settings()
    private var active = false
    private var armed = false
    private var errorText = ""
    private var fileName = ""
    private var startDate: Date?
    private var endDate: Date?
    private var finishedBytes: Int64 = 0
    private var currentURL: URL?
    private var pendingVideo = 0
    private var pendingAudio = 0
    private var lastAudioRate = 0
    private var sessionSettings = Settings()
    private var sessionFps = 30

    // recordQueue 上でのみ触る
    private var writer: AVAssetWriter?
    private var videoInput: AVAssetWriterInput?
    private var audioInput: AVAssetWriterInput?
    private var adaptor: AVAssetWriterInputPixelBufferAdaptor?
    private var sessionStart = CMTime.invalid
    private var curWidth = 0
    private var curHeight = 0
    private var segmentIndex = 1
    private var baseName = ""
    private var expectedLongSide = 0
    private var armedAt = Date()
    private var lastSpaceCheck = Date.distantPast

    init() {
        if let d = UserDefaults.standard.dictionary(forKey: Recorder.defaultsKey) {
            Recorder.merge(d, into: &settings)
        }
    }

    // MARK: - 設定

    private static func merge(_ json: [String: Any], into s: inout Settings) {
        if let r = json["resolution"] as? String, (r == "same" || r == "4k") {
            s.resolution = r
        }
        if let b = json["bitrate"] as? NSNumber {
            s.bitrate = min(max(b.intValue, 10), 100)
        }
        if let a = json["includeAudio"] as? NSNumber {
            s.includeAudio = a.boolValue
        }
        if let p = json["saveToPhotos"] as? NSNumber {
            s.saveToPhotos = p.boolValue
        }
    }

    func applySettings(_ json: [String: Any]) {
        lk.lock()
        Recorder.merge(json, into: &settings)
        let s = settings
        lk.unlock()
        let dict: [String: Any] = [
            "resolution": s.resolution,
            "bitrate": s.bitrate,
            "includeAudio": s.includeAudio,
            "saveToPhotos": s.saveToPhotos
        ]
        UserDefaults.standard.set(dict, forKey: Recorder.defaultsKey)
    }

    func currentSettings() -> Settings {
        lk.lock()
        defer { lk.unlock() }
        return settings
    }

    func settingsDictionary() -> [String: Any] {
        let s = currentSettings()
        return [
            "resolution": s.resolution,
            "bitrate": s.bitrate,
            "includeAudio": s.includeAudio,
            "saveToPhotos": s.saveToPhotos
        ]
    }

    // MARK: - 状態

    var isActive: Bool {
        lk.lock()
        defer { lk.unlock() }
        return active
    }

    /// 現在の録画セッションが 4K 指定か
    var isHighRes: Bool {
        lk.lock()
        defer { lk.unlock() }
        return active && sessionSettings.resolution == "4k"
    }

    static func freeBytes() -> Int64 {
        let url = Recorder.documentsURL()
        if let v = try? url.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]),
           let cap = v.volumeAvailableCapacityForImportantUsage {
            return Int64(cap)
        }
        return 0
    }

    static func documentsURL() -> URL {
        if let u = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first {
            return u
        }
        return URL(fileURLWithPath: NSTemporaryDirectory())
    }

    func statusDictionary() -> [String: Any] {
        lk.lock()
        let isActive = active
        let err = errorText
        let name = fileName
        let start = startDate
        let end = endDate
        let finished = finishedBytes
        let url = currentURL
        let res = settings.resolution
        let sessionRes = sessionSettings.resolution
        lk.unlock()

        var seconds = 0.0
        if let st = start {
            seconds = (isActive ? Date() : (end ?? Date())).timeIntervalSince(st)
        }
        var bytes = finished
        if let u = url,
           let attrs = try? FileManager.default.attributesOfItem(atPath: u.path),
           let n = attrs[.size] as? NSNumber {
            bytes += n.int64Value
        }
        return [
            "active": isActive,
            "seconds": max(0, seconds),
            "fileName": name,
            "bytes": Int(bytes),
            "freeBytes": Int(Recorder.freeBytes()),
            "resolution": isActive ? sessionRes : res,
            "error": err
        ]
    }

    // MARK: - 開始 / 停止

    /// 録画の予約。成功すると active になるが、arm するまでフレームは受け付けない。
    func claim() -> Bool {
        lk.lock()
        if active {
            lk.unlock()
            return false
        }
        if Recorder.freeBytes() < Recorder.minFreeBytes {
            errorText = "空き容量が足りないため録画を停止しました"
            lk.unlock()
            onChanged?()
            return false
        }
        active = true
        armed = false
        errorText = ""
        fileName = ""
        startDate = Date()
        endDate = nil
        finishedBytes = 0
        currentURL = nil
        sessionSettings = settings
        lk.unlock()

        recordQueue.async { [weak self] in
            guard let self = self else { return }
            self.writer = nil
            self.videoInput = nil
            self.audioInput = nil
            self.adaptor = nil
            self.segmentIndex = 1
            let f = DateFormatter()
            f.locale = Locale(identifier: "en_US_POSIX")
            f.dateFormat = "yyyyMMdd_HHmmss"
            self.baseName = "PhoneCam_" + f.string(from: Date())
        }
        onChanged?()
        return true
    }

    /// カメラの準備ができたのでフレームの受け付けを始める
    func arm(expectedLongSide: Int, fps: Int) {
        recordQueue.async { [weak self] in
            guard let self = self else { return }
            self.expectedLongSide = expectedLongSide
            self.armedAt = Date()
            self.lk.lock()
            self.sessionFps = max(1, fps)
            if self.active { self.armed = true }
            self.lk.unlock()
        }
    }

    func stop() {
        lk.lock()
        guard active else {
            lk.unlock()
            return
        }
        active = false
        armed = false
        endDate = Date()
        lk.unlock()
        recordQueue.async { [weak self] in
            self?.finishCurrent()
        }
        onChanged?()
    }

    /// recordQueue 上: エラーで録画を止める
    private func failAndStop(_ message: String) {
        lk.lock()
        let wasActive = active
        active = false
        armed = false
        endDate = Date()
        errorText = message
        lk.unlock()
        finishCurrent()
        if wasActive {
            onAutoStopped?()
        }
        onChanged?()
    }

    // MARK: - フレーム入力

    func appendVideo(_ pixelBuffer: CVPixelBuffer, pts: CMTime) {
        lk.lock()
        guard active && armed, pendingVideo < 5 else {
            lk.unlock()
            return
        }
        pendingVideo += 1
        lk.unlock()
        recordQueue.async { [weak self] in
            guard let self = self else { return }
            self.handleVideo(pixelBuffer, pts: pts)
            self.lk.lock()
            self.pendingVideo = max(0, self.pendingVideo - 1)
            self.lk.unlock()
        }
    }

    func appendAudio(_ sampleBuffer: CMSampleBuffer) {
        if let fd = CMSampleBufferGetFormatDescription(sampleBuffer),
           let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(fd) {
            let rate = Int(asbd.pointee.mSampleRate)
            lk.lock()
            lastAudioRate = rate
            lk.unlock()
        }
        lk.lock()
        guard active && armed, sessionSettings.includeAudio, pendingAudio < 60 else {
            lk.unlock()
            return
        }
        pendingAudio += 1
        lk.unlock()
        recordQueue.async { [weak self] in
            guard let self = self else { return }
            self.handleAudio(sampleBuffer)
            self.lk.lock()
            self.pendingAudio = max(0, self.pendingAudio - 1)
            self.lk.unlock()
        }
    }

    // MARK: - 書き込み (recordQueue 上)

    private func handleVideo(_ pb: CVPixelBuffer, pts: CMTime) {
        lk.lock()
        let stillActive = active
        lk.unlock()
        guard stillActive else { return }

        let w = CVPixelBufferGetWidth(pb)
        let h = CVPixelBufferGetHeight(pb)

        // 空き容量は 1 秒ごとに確認する
        if Date().timeIntervalSince(lastSpaceCheck) >= 1.0 {
            lastSpaceCheck = Date()
            if Recorder.freeBytes() < Recorder.minFreeBytes {
                failAndStop("空き容量が足りないため録画を停止しました")
                return
            }
        }

        if writer == nil {
            // 4K へ切り替えた直後の古いサイズのフレームは、最大 2 秒だけ読み飛ばす
            if expectedLongSide > 0, max(w, h) != expectedLongSide,
               Date().timeIntervalSince(armedAt) < 2.0 {
                return
            }
            if !startWriter(width: w, height: h, pts: pts) { return }
        } else if w != curWidth || h != curHeight {
            // 回転などでサイズが変わった: 新しいファイル (セグメント) へ
            finishCurrent()
            segmentIndex += 1
            if !startWriter(width: w, height: h, pts: pts) { return }
        }

        guard let wr = writer, let vi = videoInput, let ad = adaptor else { return }
        if wr.status == .failed {
            let msg = "録画に失敗しました"
            failAndStop(msg)
            return
        }
        guard wr.status == .writing else { return }
        guard CMTimeCompare(pts, sessionStart) >= 0 else { return }
        if vi.isReadyForMoreMediaData {
            _ = ad.append(pb, withPresentationTime: pts)
        }
    }

    private func handleAudio(_ sb: CMSampleBuffer) {
        guard let wr = writer, wr.status == .writing, let ai = audioInput,
              sessionStart.isValid else { return }
        let pts = CMSampleBufferGetPresentationTimeStamp(sb)
        guard CMTimeCompare(pts, sessionStart) >= 0 else { return }
        if ai.isReadyForMoreMediaData {
            _ = ai.append(sb)
        }
    }

    private func nextURL() -> URL {
        let dir = Recorder.documentsURL()
        var seg = segmentIndex
        while true {
            let name = seg <= 1 ? "\(baseName).mov" : "\(baseName)_\(seg).mov"
            let url = dir.appendingPathComponent(name)
            if !FileManager.default.fileExists(atPath: url.path) {
                segmentIndex = seg
                return url
            }
            seg += 1
        }
    }

    private func startWriter(width: Int, height: Int, pts: CMTime) -> Bool {
        lk.lock()
        let s = sessionSettings
        let fps = sessionFps
        let rate = lastAudioRate > 0 ? lastAudioRate : 48000
        lk.unlock()

        let url = nextURL()
        do {
            let wr = try AVAssetWriter(outputURL: url, fileType: .mov)
            let videoSettings: [String: Any] = [
                AVVideoCodecKey: AVVideoCodecType.hevc,
                AVVideoWidthKey: width,
                AVVideoHeightKey: height,
                AVVideoCompressionPropertiesKey: [
                    AVVideoAverageBitRateKey: s.bitrate * 1_000_000,
                    AVVideoExpectedSourceFrameRateKey: fps
                ] as [String: Any]
            ]
            let vi = AVAssetWriterInput(mediaType: .video, outputSettings: videoSettings)
            vi.expectsMediaDataInRealTime = true
            let ad = AVAssetWriterInputPixelBufferAdaptor(
                assetWriterInput: vi,
                sourcePixelBufferAttributes: [
                    kCVPixelBufferPixelFormatTypeKey as String: Int(kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange),
                    kCVPixelBufferWidthKey as String: width,
                    kCVPixelBufferHeightKey as String: height
                ]
            )
            guard wr.canAdd(vi) else {
                failAndStop("録画を開始できませんでした")
                return false
            }
            wr.add(vi)

            var ai: AVAssetWriterInput?
            if s.includeAudio {
                let audioSettings: [String: Any] = [
                    AVFormatIDKey: kAudioFormatMPEG4AAC,
                    AVSampleRateKey: rate,
                    AVNumberOfChannelsKey: 1,
                    AVEncoderBitRateKey: 128_000
                ]
                let input = AVAssetWriterInput(mediaType: .audio, outputSettings: audioSettings)
                input.expectsMediaDataInRealTime = true
                if wr.canAdd(input) {
                    wr.add(input)
                    ai = input
                }
            }

            guard wr.startWriting() else {
                failAndStop("録画を開始できませんでした")
                return false
            }
            wr.startSession(atSourceTime: pts)

            writer = wr
            videoInput = vi
            audioInput = ai
            adaptor = ad
            sessionStart = pts
            curWidth = width
            curHeight = height

            lk.lock()
            fileName = url.lastPathComponent
            currentURL = url
            lk.unlock()
            onChanged?()
            return true
        } catch {
            failAndStop("録画を開始できませんでした")
            return false
        }
    }

    /// 現在のファイルを閉じる (recordQueue 上)。写真への保存は完了後に非同期で行う。
    private func finishCurrent() {
        guard let wr = writer else { return }
        let vi = videoInput
        let ai = audioInput
        let url = currentFileURL()
        writer = nil
        videoInput = nil
        audioInput = nil
        adaptor = nil
        sessionStart = CMTime.invalid
        curWidth = 0
        curHeight = 0

        lk.lock()
        let save = sessionSettings.saveToPhotos
        lk.unlock()

        vi?.markAsFinished()
        ai?.markAsFinished()
        wr.finishWriting { [weak self] in
            guard let self = self else { return }
            if wr.status == .completed {
                if let u = url {
                    self.addFinishedBytes(u)
                    if save {
                        self.saveToPhotoLibrary(u)
                    }
                }
            } else {
                self.setError("録画ファイルの保存に失敗しました")
            }
            self.onChanged?()
        }
    }

    private func currentFileURL() -> URL? {
        lk.lock()
        defer { lk.unlock() }
        return currentURL
    }

    private func addFinishedBytes(_ url: URL) {
        var size: Int64 = 0
        if let attrs = try? FileManager.default.attributesOfItem(atPath: url.path),
           let n = attrs[.size] as? NSNumber {
            size = n.int64Value
        }
        lk.lock()
        finishedBytes += size
        if currentURL == url { currentURL = nil }
        lk.unlock()
    }

    private func setError(_ message: String) {
        lk.lock()
        errorText = message
        lk.unlock()
    }

    private func saveToPhotoLibrary(_ url: URL) {
        PHPhotoLibrary.requestAuthorization(for: .addOnly) { [weak self] status in
            guard status == .authorized || status == .limited else {
                self?.setError("写真への保存が許可されていません")
                self?.onChanged?()
                return
            }
            PHPhotoLibrary.shared().performChanges({
                let request = PHAssetCreationRequest.forAsset()
                let options = PHAssetResourceCreationOptions()
                options.shouldMoveFile = false
                request.addResource(with: .video, fileURL: url, options: options)
            }, completionHandler: { success, _ in
                if !success {
                    self?.setError("写真への保存に失敗しました")
                    self?.onChanged?()
                }
            })
        }
    }
}
