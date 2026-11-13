import Foundation
import CoreImage
import CoreVideo
import Metal
import Vision

/// 背景ぼかし / グリーンバック / 色調整を GPU (Core Image + Vision) で行う。
/// process は videoQueue 上でのみ呼ぶ。設定の更新 (apply) は任意のスレッドから呼べる。
final class EffectsProcessor {
    private struct Settings {
        var mode = "off"          // off / blur / green
        var strength = 0.6
        var quality = "balanced"  // fast / balanced
        var brightness = 0.0
        var contrast = 1.0
        var saturation = 1.0
        var sharpness = 0.0
    }

    private let lock = NSLock()
    private var settings = Settings()
    private var avgMs = 0.0

    // 以下は process (videoQueue) からのみ触る。エフェクトを使うまで作らない (lazy)
    private lazy var ciContext: CIContext = {
        let options: [CIContextOption: Any] = [
            .workingColorSpace: NSNull(),
            .cacheIntermediates: false
        ]
        if let dev = MTLCreateSystemDefaultDevice() {
            return CIContext(mtlDevice: dev, options: options)
        }
        return CIContext(options: options)
    }()
    private lazy var segRequest: VNGeneratePersonSegmentationRequest = {
        let r = VNGeneratePersonSegmentationRequest()
        r.outputPixelFormat = kCVPixelFormatType_OneComponent8
        return r
    }()
    private lazy var seqHandler = VNSequenceRequestHandler()
    private var pool: CVPixelBufferPool?
    private var poolWidth = 0
    private var poolHeight = 0

    private static func clamp(_ v: Double, _ lo: Double, _ hi: Double) -> Double {
        if !v.isFinite { return lo }
        return min(max(v, lo), hi)
    }

    // MARK: - 設定

    /// 制御 JSON の "background" / "color" を永続状態へマージする
    func apply(_ json: [String: Any]) {
        lock.lock()
        defer { lock.unlock() }
        if let bg = json["background"] as? [String: Any] {
            if let m = bg["mode"] as? String, (m == "off" || m == "blur" || m == "green") {
                settings.mode = m
            }
            if let n = bg["strength"] as? NSNumber {
                settings.strength = EffectsProcessor.clamp(n.doubleValue, 0, 1)
            }
            if let q = bg["quality"] as? String, (q == "fast" || q == "balanced") {
                settings.quality = q
            }
        }
        if let c = json["color"] as? [String: Any] {
            if let n = c["brightness"] as? NSNumber {
                settings.brightness = EffectsProcessor.clamp(n.doubleValue, -0.5, 0.5)
            }
            if let n = c["contrast"] as? NSNumber {
                settings.contrast = EffectsProcessor.clamp(n.doubleValue, 0.5, 1.5)
            }
            if let n = c["saturation"] as? NSNumber {
                settings.saturation = EffectsProcessor.clamp(n.doubleValue, 0, 2)
            }
            if let n = c["sharpness"] as? NSNumber {
                settings.sharpness = EffectsProcessor.clamp(n.doubleValue, 0, 1)
            }
        }
    }

    /// ステータス用 (background, color, processingMs)
    func statusFields() -> [String: Any] {
        lock.lock()
        let s = settings
        let ms = avgMs
        lock.unlock()
        return [
            "background": [
                "mode": s.mode,
                "strength": s.strength,
                "quality": s.quality
            ] as [String: Any],
            "color": [
                "brightness": s.brightness,
                "contrast": s.contrast,
                "saturation": s.saturation,
                "sharpness": s.sharpness
            ] as [String: Any],
            "processingMs": ms
        ]
    }

    private func colorActive(_ s: Settings) -> Bool {
        let eps = 0.005
        return abs(s.brightness) > eps
            || abs(s.contrast - 1.0) > eps
            || abs(s.saturation - 1.0) > eps
            || s.sharpness > eps
    }

    // MARK: - 処理

    /// エフェクトが無効なら入力をそのまま返す。失敗時も入力を返す。
    func process(_ input: CVPixelBuffer) -> CVPixelBuffer {
        lock.lock()
        let s = settings
        lock.unlock()

        let colorOn = colorActive(s)
        let bgOn = (s.mode != "off")
        if !colorOn && !bgOn {
            lock.lock()
            avgMs = 0
            lock.unlock()
            return input
        }

        let t0 = DispatchTime.now().uptimeNanoseconds
        let result = render(input, s, colorOn: colorOn, bgOn: bgOn)
        let ms = Double(DispatchTime.now().uptimeNanoseconds - t0) / 1_000_000.0

        lock.lock()
        avgMs = (avgMs == 0) ? ms : (avgMs * 0.9 + ms * 0.1)
        lock.unlock()

        return result ?? input
    }

    private func render(_ input: CVPixelBuffer, _ s: Settings, colorOn: Bool, bgOn: Bool) -> CVPixelBuffer? {
        let width = CVPixelBufferGetWidth(input)
        let height = CVPixelBufferGetHeight(input)
        guard let out = makeOutputBuffer(width: width, height: height) else { return nil }

        let src = CIImage(cvPixelBuffer: input)
        let extent = src.extent

        // 色調整
        var fg = src
        if colorOn {
            fg = fg.applyingFilter("CIColorControls", parameters: [
                kCIInputBrightnessKey: s.brightness,
                kCIInputContrastKey: s.contrast,
                kCIInputSaturationKey: s.saturation
            ])
            if s.sharpness > 0.005 {
                fg = fg.applyingFilter("CISharpenLuminance", parameters: [
                    kCIInputSharpnessKey: s.sharpness * 2.0
                ])
            }
        }

        var composed = fg
        if bgOn {
            guard let maskBuffer = personMask(input, quality: s.quality) else { return nil }
            var mask = CIImage(cvPixelBuffer: maskBuffer)
            let me = mask.extent
            if me.width <= 0 || me.height <= 0 { return nil }
            mask = mask.transformed(by: CGAffineTransform(
                scaleX: extent.width / me.width,
                y: extent.height / me.height
            ))
            // 境界を少しなじませる
            mask = mask.clampedToExtent().applyingGaussianBlur(sigma: 1.5).cropped(to: extent)

            let background: CIImage
            if s.mode == "green" {
                background = CIImage(color: CIColor(red: 0, green: 1, blue: 0)).cropped(to: extent)
            } else {
                let sigma = 4.0 + s.strength * 36.0
                background = src.clampedToExtent().applyingGaussianBlur(sigma: sigma).cropped(to: extent)
            }
            composed = fg.applyingFilter("CIBlendWithMask", parameters: [
                kCIInputBackgroundImageKey: background,
                kCIInputMaskImageKey: mask
            ])
        }

        CVBufferPropagateAttachments(input, out)
        ciContext.render(composed, to: out, bounds: extent, colorSpace: CGColorSpace(name: CGColorSpace.sRGB))
        return out
    }

    private func personMask(_ input: CVPixelBuffer, quality: String) -> CVPixelBuffer? {
        segRequest.qualityLevel = (quality == "fast") ? .fast : .balanced
        do {
            try seqHandler.perform([segRequest], on: input, orientation: .up)
        } catch {
            return nil
        }
        guard let obs = (segRequest.results as? [VNPixelBufferObservation])?.first else { return nil }
        return obs.pixelBuffer
    }

    private func makeOutputBuffer(width: Int, height: Int) -> CVPixelBuffer? {
        if pool == nil || poolWidth != width || poolHeight != height {
            let attrs: [String: Any] = [
                kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,
                kCVPixelBufferWidthKey as String: width,
                kCVPixelBufferHeightKey as String: height,
                kCVPixelBufferIOSurfacePropertiesKey as String: [String: Any](),
                kCVPixelBufferMetalCompatibilityKey as String: true
            ]
            var newPool: CVPixelBufferPool?
            let st = CVPixelBufferPoolCreate(nil, nil, attrs as CFDictionary, &newPool)
            guard st == kCVReturnSuccess, let p = newPool else { return nil }
            pool = p
            poolWidth = width
            poolHeight = height
        }
        guard let p = pool else { return nil }
        var buffer: CVPixelBuffer?
        let st = CVPixelBufferPoolCreatePixelBuffer(nil, p, &buffer)
        if st != kCVReturnSuccess { return nil }
        return buffer
    }
}
