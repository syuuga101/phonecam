import Foundation
import VideoToolbox
import CoreMedia
import CoreVideo

/// VideoToolbox を使った低遅延 H.264 / HEVC エンコーダ。出力は Annex B 形式。
final class H264Encoder {
    /// エンコード済みアクセスユニット (Annex B)、キーフレームかどうか、HEVC かどうか、
    /// 入力の PTS、エンコード所要時間 (ms)
    var onEncoded: ((Data, Bool, Bool, CMTime, Double) -> Void)?

    private var session: VTCompressionSession?
    private var sessionWidth: Int32 = 0
    private var sessionHeight: Int32 = 0
    private var fps: Int = 60
    private var bitrateBps: Int = 20_000_000
    private var useHevc = false

    private let sessionLock = NSLock()
    private let flagLock = NSLock()
    private var keyframeRequested = false

    // 以下は flagLock で保護 (出力コールバックのスレッドからも触るため)
    private var consecutiveFailures = 0
    private var usingLowLatency = false
    private var needsRecreate = false
    private var lowLatencyBlockedKey: Int64 = -1
    private var sessionWidthSnapshot: Int32 = 0
    private var sessionHeightSnapshot: Int32 = 0

    private let startCode: [UInt8] = [0, 0, 0, 1]

    deinit {
        sessionLock.lock()
        destroySessionLocked()
        sessionLock.unlock()
    }

    // MARK: - 設定

    /// fps / ビットレート(Mbps) を設定する。fps が変わった場合はセッションを作り直す。
    func configure(fps newFps: Int, bitrateMbps: Int) {
        sessionLock.lock()
        defer { sessionLock.unlock() }
        let safeFps = max(1, newFps)
        if safeFps != fps {
            fps = safeFps
            destroySessionLocked()
        }
        let bps = max(1, bitrateMbps) * 1_000_000
        if bps != bitrateBps {
            bitrateBps = bps
            if let s = session {
                updateBitrateLocked(s)
            }
        }
    }

    /// コーデックを切り替える。変更時はセッションを作り直し、キーフレームを強制する。
    func setCodec(hevc: Bool) {
        sessionLock.lock()
        defer { sessionLock.unlock() }
        if hevc != useHevc {
            useHevc = hevc
            destroySessionLocked()
            requestKeyframe()
        }
    }

    /// ビットレートを動的に変更する (Mbps)
    func setBitrate(mbps: Int) {
        sessionLock.lock()
        defer { sessionLock.unlock() }
        let bps = max(1, mbps) * 1_000_000
        guard bps != bitrateBps else { return }
        bitrateBps = bps
        if let s = session {
            updateBitrateLocked(s)
        }
    }

    /// 次のフレームをキーフレームにする (スレッドセーフ)
    func requestKeyframe() {
        flagLock.lock()
        keyframeRequested = true
        flagLock.unlock()
    }

    /// セッションを破棄する (クライアント切断時)
    func stop() {
        sessionLock.lock()
        destroySessionLocked()
        sessionLock.unlock()
    }

    // MARK: - エンコード

    func encode(pixelBuffer: CVPixelBuffer, pts: CMTime) {
        encode(pixelBuffer: pixelBuffer, pts: pts, duration: CMTime.invalid)
    }

    func encode(pixelBuffer: CVPixelBuffer, pts: CMTime, duration: CMTime) {
        let w = Int32(CVPixelBufferGetWidth(pixelBuffer))
        let h = Int32(CVPixelBufferGetHeight(pixelBuffer))

        sessionLock.lock()
        defer { sessionLock.unlock() }

        flagLock.lock()
        let recreate = needsRecreate
        needsRecreate = false
        flagLock.unlock()
        if session != nil && (recreate || w != sessionWidth || h != sessionHeight) {
            destroySessionLocked()
        }
        if session == nil {
            if !createSessionLocked(width: w, height: h) {
                return
            }
            // 新しいセッションの最初はキーフレーム
            requestKeyframe()
        }
        guard let s = session else { return }

        flagLock.lock()
        let force = keyframeRequested
        keyframeRequested = false
        flagLock.unlock()

        var props: CFDictionary? = nil
        if force {
            props = [kVTEncodeFrameOptionKey_ForceKeyFrame: true] as CFDictionary
        }

        var frameDuration = duration
        if !(duration.isValid && duration.seconds > 0) {
            frameDuration = CMTime(value: 1, timescale: CMTimeScale(max(1, fps)))
        }

        let startNs = DispatchTime.now().uptimeNanoseconds
        let status = VTCompressionSessionEncodeFrame(
            s,
            imageBuffer: pixelBuffer,
            presentationTimeStamp: pts,
            duration: frameDuration,
            frameProperties: props,
            infoFlagsOut: nil,
            outputHandler: { [weak self] status, _, sampleBuffer in
                self?.handleOutput(status: status, sampleBuffer: sampleBuffer, startNs: startNs)
            }
        )
        if status != noErr {
            // セッションが無効になった場合などは作り直す
            noteFailure()
            if force { requestKeyframe() }
            destroySessionLocked()
        }
    }

    /// エンコード失敗を数える。低遅延モードで失敗が続いたら、このサイズでは低遅延モードを使わない。
    private func noteFailure() {
        flagLock.lock()
        consecutiveFailures += 1
        if usingLowLatency && consecutiveFailures >= 3 {
            lowLatencyBlockedKey = sizeKey(sessionWidthSnapshot, sessionHeightSnapshot)
            needsRecreate = true
            consecutiveFailures = 0
        }
        flagLock.unlock()
    }

    private func sizeKey(_ w: Int32, _ h: Int32) -> Int64 {
        return (Int64(w) << 32) | Int64(UInt32(bitPattern: h))
    }

    // MARK: - セッション管理 (sessionLock を保持して呼ぶ)

    private func createSessionLocked(width: Int32, height: Int32) -> Bool {
        var newSession: VTCompressionSession?
        let codecType: CMVideoCodecType = useHevc ? kCMVideoCodecType_HEVC : kCMVideoCodecType_H264
        // 低遅延レート制御は 4K などの大きい解像度で動かないことがあるため、1080p 以下のときだけ使う
        flagLock.lock()
        let blocked = lowLatencyBlockedKey == sizeKey(width, height)
        flagLock.unlock()
        var lowLatency = !blocked && Int(width) * Int(height) <= 1920 * 1088
        var status: OSStatus = -1
        if lowLatency {
            let lowLatencySpec = [kVTVideoEncoderSpecification_EnableLowLatencyRateControl: true] as CFDictionary
            status = VTCompressionSessionCreate(
                allocator: nil,
                width: width,
                height: height,
                codecType: codecType,
                encoderSpecification: lowLatencySpec,
                imageBufferAttributes: nil,
                compressedDataAllocator: nil,
                outputCallback: nil,
                refcon: nil,
                compressionSessionOut: &newSession
            )
        }
        if status != noErr || newSession == nil {
            lowLatency = false
            newSession = nil
            status = VTCompressionSessionCreate(
                allocator: nil,
                width: width,
                height: height,
                codecType: codecType,
                encoderSpecification: nil,
                imageBufferAttributes: nil,
                compressedDataAllocator: nil,
                outputCallback: nil,
                refcon: nil,
                compressionSessionOut: &newSession
            )
        }
        guard status == noErr, let s = newSession else {
            return false
        }

        VTSessionSetProperty(s, key: kVTCompressionPropertyKey_RealTime, value: kCFBooleanTrue)
        VTSessionSetProperty(s, key: kVTCompressionPropertyKey_AllowFrameReordering, value: kCFBooleanFalse)

        if useHevc {
            VTSessionSetProperty(
                s,
                key: kVTCompressionPropertyKey_ProfileLevel,
                value: kVTProfileLevel_HEVC_Main_AutoLevel
            )
        } else {
            let profileStatus = VTSessionSetProperty(
                s,
                key: kVTCompressionPropertyKey_ProfileLevel,
                value: kVTProfileLevel_H264_ConstrainedHigh_AutoLevel
            )
            if profileStatus != noErr {
                VTSessionSetProperty(
                    s,
                    key: kVTCompressionPropertyKey_ProfileLevel,
                    value: kVTProfileLevel_H264_High_AutoLevel
                )
            }
        }

        applyBitrateLocked(s)
        VTSessionSetProperty(s, key: kVTCompressionPropertyKey_ExpectedFrameRate, value: NSNumber(value: fps))
        VTSessionSetProperty(s, key: kVTCompressionPropertyKey_MaxKeyFrameInterval, value: NSNumber(value: fps * 2))
        VTSessionSetProperty(s, key: kVTCompressionPropertyKey_MaxKeyFrameIntervalDuration, value: NSNumber(value: 2))

        VTCompressionSessionPrepareToEncodeFrames(s)

        session = s
        sessionWidth = width
        sessionHeight = height
        flagLock.lock()
        usingLowLatency = lowLatency
        sessionWidthSnapshot = width
        sessionHeightSnapshot = height
        consecutiveFailures = 0
        flagLock.unlock()
        return true
    }

    private func updateBitrateLocked(_ session: VTCompressionSession) {
        if applyBitrateLocked(session) != noErr {
            // Some encoders reject a live update. Open the next session with the new rate.
            destroySessionLocked()
            requestKeyframe()
        }
    }

    @discardableResult
    private func applyBitrateLocked(_ s: VTCompressionSession) -> OSStatus {
        let result = VTSessionSetProperty(s, key: kVTCompressionPropertyKey_AverageBitRate, value: NSNumber(value: bitrateBps))
        // 低遅延レート制御では未対応の場合があるため、失敗しても無視する
        let bytesPerSecond = Int(Double(bitrateBps) * 1.5 / 8.0)
        let limits = [NSNumber(value: bytesPerSecond), NSNumber(value: 1)] as CFArray
        VTSessionSetProperty(s, key: kVTCompressionPropertyKey_DataRateLimits, value: limits)
        return result
    }

    private func destroySessionLocked() {
        if let s = session {
            VTCompressionSessionInvalidate(s)
        }
        session = nil
        sessionWidth = 0
        sessionHeight = 0
    }

    // MARK: - 出力処理

    private func handleOutput(status: OSStatus, sampleBuffer: CMSampleBuffer?, startNs: UInt64) {
        guard status == noErr, let sb = sampleBuffer, CMSampleBufferDataIsReady(sb) else {
            // status が成功でバッファが無いのはフレームが間引かれただけなので数えない
            if status != noErr { noteFailure() }
            return
        }
        flagLock.lock()
        consecutiveFailures = 0
        flagLock.unlock()

        // キーフレーム判定: NotSync が無い or false ならキーフレーム
        var isKey = true
        if let attachments = CMSampleBufferGetSampleAttachmentsArray(sb, createIfNecessary: false) as? [[CFString: Any]],
           let first = attachments.first {
            if let notSync = first[kCMSampleAttachmentKey_NotSync] as? Bool, notSync {
                isKey = false
            }
        }

        var out = Data()
        var nalHeaderLength: Int32 = 4

        var isHevc = false
        if let fd0 = CMSampleBufferGetFormatDescription(sb) {
            isHevc = (CMFormatDescriptionGetMediaSubType(fd0) == kCMVideoCodecType_HEVC)
        }

        if isKey, let fd = CMSampleBufferGetFormatDescription(sb) {
            var count: Int = 0
            let st0 = parameterSet(fd, hevc: isHevc,
                index: 0,
                pointerOut: nil,
                sizeOut: nil,
                countOut: &count,
                headerOut: &nalHeaderLength
            )
            if st0 == noErr {
                for i in 0..<count {
                    var ptr: UnsafePointer<UInt8>?
                    var size: Int = 0
                    let st = parameterSet(fd, hevc: isHevc,
                        index: i,
                        pointerOut: &ptr,
                        sizeOut: &size,
                        countOut: nil,
                        headerOut: nil
                    )
                    if st == noErr, let p = ptr, size > 0 {
                        out.append(contentsOf: startCode)
                        out.append(p, count: size)
                    }
                }
            }
        }

        guard let block = CMSampleBufferGetDataBuffer(sb) else { return }
        let total = CMBlockBufferGetDataLength(block)
        if total <= 0 { return }
        var raw = [UInt8](repeating: 0, count: total)
        let copyStatus = raw.withUnsafeMutableBytes { buf -> OSStatus in
            guard let base = buf.baseAddress else { return -1 }
            return CMBlockBufferCopyDataBytes(block, atOffset: 0, dataLength: total, destination: base)
        }
        if copyStatus != kCMBlockBufferNoErr { return }

        let lenSize = Int(nalHeaderLength)
        if lenSize < 1 || lenSize > 4 { return }
        var offset = 0
        while offset + lenSize <= total {
            var nalLen = 0
            for i in 0..<lenSize {
                nalLen = (nalLen << 8) | Int(raw[offset + i])
            }
            offset += lenSize
            if nalLen <= 0 || offset + nalLen > total { break }
            out.append(contentsOf: startCode)
            out.append(contentsOf: raw[offset..<(offset + nalLen)])
            offset += nalLen
        }

        if out.isEmpty { return }
        let nowNs = DispatchTime.now().uptimeNanoseconds
        let encodeMs = nowNs >= startNs ? Double(nowNs - startNs) / 1_000_000.0 : 0
        onEncoded?(out, isKey, isHevc, CMSampleBufferGetPresentationTimeStamp(sb), encodeMs)
    }

    /// H.264 / HEVC のパラメータセット取得を 1 つにまとめた薄いラッパ
    private func parameterSet(
        _ fd: CMFormatDescription,
        hevc: Bool,
        index: Int,
        pointerOut: UnsafeMutablePointer<UnsafePointer<UInt8>?>?,
        sizeOut: UnsafeMutablePointer<Int>?,
        countOut: UnsafeMutablePointer<Int>?,
        headerOut: UnsafeMutablePointer<Int32>?
    ) -> OSStatus {
        if hevc {
            return CMVideoFormatDescriptionGetHEVCParameterSetAtIndex(
                fd,
                parameterSetIndex: index,
                parameterSetPointerOut: pointerOut,
                parameterSetSizeOut: sizeOut,
                parameterSetCountOut: countOut,
                nalUnitHeaderLengthOut: headerOut
            )
        }
        return CMVideoFormatDescriptionGetH264ParameterSetAtIndex(
            fd,
            parameterSetIndex: index,
            parameterSetPointerOut: pointerOut,
            parameterSetSizeOut: sizeOut,
            parameterSetCountOut: countOut,
            nalUnitHeaderLengthOut: headerOut
        )
    }
}
