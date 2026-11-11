import Foundation
import VideoToolbox
import CoreMedia
import CoreVideo

/// VideoToolbox を使った低遅延 H.264 エンコーダ。出力は Annex B 形式。
final class H264Encoder {
    /// エンコード済みアクセスユニット (Annex B) とキーフレームかどうか
    var onEncoded: ((Data, Bool) -> Void)?

    private var session: VTCompressionSession?
    private var sessionWidth: Int32 = 0
    private var sessionHeight: Int32 = 0
    private var fps: Int = 60
    private var bitrateBps: Int = 20_000_000

    private let sessionLock = NSLock()
    private let flagLock = NSLock()
    private var keyframeRequested = false

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
                applyBitrateLocked(s)
            }
        }
    }

    /// ビットレートを動的に変更する (Mbps)
    func setBitrate(mbps: Int) {
        sessionLock.lock()
        defer { sessionLock.unlock() }
        bitrateBps = max(1, mbps) * 1_000_000
        if let s = session {
            applyBitrateLocked(s)
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

    func encode(pixelBuffer: CVPixelBuffer, pts: CMTime, duration: CMTime) {
        let w = Int32(CVPixelBufferGetWidth(pixelBuffer))
        let h = Int32(CVPixelBufferGetHeight(pixelBuffer))

        sessionLock.lock()
        defer { sessionLock.unlock() }

        if session != nil && (w != sessionWidth || h != sessionHeight) {
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

        let status = VTCompressionSessionEncodeFrame(
            s,
            imageBuffer: pixelBuffer,
            presentationTimeStamp: pts,
            duration: frameDuration,
            frameProperties: props,
            infoFlagsOut: nil,
            outputHandler: { [weak self] status, _, sampleBuffer in
                self?.handleOutput(status: status, sampleBuffer: sampleBuffer)
            }
        )
        if status != noErr {
            // セッションが無効になった場合などは作り直す
            if force { requestKeyframe() }
            destroySessionLocked()
        }
    }

    // MARK: - セッション管理 (sessionLock を保持して呼ぶ)

    private func createSessionLocked(width: Int32, height: Int32) -> Bool {
        var newSession: VTCompressionSession?
        let lowLatencySpec = [kVTVideoEncoderSpecification_EnableLowLatencyRateControl: true] as CFDictionary
        var status = VTCompressionSessionCreate(
            allocator: nil,
            width: width,
            height: height,
            codecType: kCMVideoCodecType_H264,
            encoderSpecification: lowLatencySpec,
            imageBufferAttributes: nil,
            compressedDataAllocator: nil,
            outputCallback: nil,
            refcon: nil,
            compressionSessionOut: &newSession
        )
        if status != noErr || newSession == nil {
            newSession = nil
            status = VTCompressionSessionCreate(
                allocator: nil,
                width: width,
                height: height,
                codecType: kCMVideoCodecType_H264,
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

        applyBitrateLocked(s)
        VTSessionSetProperty(s, key: kVTCompressionPropertyKey_ExpectedFrameRate, value: NSNumber(value: fps))
        VTSessionSetProperty(s, key: kVTCompressionPropertyKey_MaxKeyFrameInterval, value: NSNumber(value: fps * 2))
        VTSessionSetProperty(s, key: kVTCompressionPropertyKey_MaxKeyFrameIntervalDuration, value: NSNumber(value: 2))

        VTCompressionSessionPrepareToEncodeFrames(s)

        session = s
        sessionWidth = width
        sessionHeight = height
        return true
    }

    private func applyBitrateLocked(_ s: VTCompressionSession) {
        VTSessionSetProperty(s, key: kVTCompressionPropertyKey_AverageBitRate, value: NSNumber(value: bitrateBps))
        // 低遅延レート制御では未対応の場合があるため、失敗しても無視する
        let bytesPerSecond = Int(Double(bitrateBps) * 1.5 / 8.0)
        let limits = [NSNumber(value: bytesPerSecond), NSNumber(value: 1)] as CFArray
        VTSessionSetProperty(s, key: kVTCompressionPropertyKey_DataRateLimits, value: limits)
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

    private func handleOutput(status: OSStatus, sampleBuffer: CMSampleBuffer?) {
        guard status == noErr, let sb = sampleBuffer, CMSampleBufferDataIsReady(sb) else { return }

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

        if isKey, let fd = CMSampleBufferGetFormatDescription(sb) {
            var count: Int = 0
            let st0 = CMVideoFormatDescriptionGetH264ParameterSetAtIndex(
                fd,
                parameterSetIndex: 0,
                parameterSetPointerOut: nil,
                parameterSetSizeOut: nil,
                parameterSetCountOut: &count,
                nalUnitHeaderLengthOut: &nalHeaderLength
            )
            if st0 == noErr {
                for i in 0..<count {
                    var ptr: UnsafePointer<UInt8>?
                    var size: Int = 0
                    let st = CMVideoFormatDescriptionGetH264ParameterSetAtIndex(
                        fd,
                        parameterSetIndex: i,
                        parameterSetPointerOut: &ptr,
                        parameterSetSizeOut: &size,
                        parameterSetCountOut: nil,
                        nalUnitHeaderLengthOut: nil
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
        onEncoded?(out, isKey)
    }
}
