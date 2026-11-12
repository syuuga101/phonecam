import Foundation
import AVFoundation

/// AVAudioEngine + AVAudioSinkNode による低遅延マイク入力。
/// AVCaptureAudioDataOutput (~1024 サンプル以上のバッファ) より短い I/O バッファで Int16 LE モノラルを渡す。
final class LowLatencyMic {
    /// (Int16 LE モノラル PCM, サンプルレート, captureTime ホスト時刻秒)。内部の serial キュー上で呼ばれる
    var onSamples: ((Data, Int, Double) -> Void)?

    private let ctlQueue = DispatchQueue(label: "phonecam.llmic.ctl")
    private let sendQueue = DispatchQueue(label: "phonecam.llmic.send", qos: .userInteractive)

    // 以下は ctlQueue 上でのみ触る
    private var engine: AVAudioEngine?
    private var sink: AVAudioSinkNode?
    private var scratch: UnsafeMutablePointer<Int16>?
    private var scratchCapacity = 0
    private var wantRunning = false
    private var observing = false
    private(set) var sampleRate = 0

    private let runLock = NSLock()
    private var runningFlag = false
    /// エンジンが動作中か (どのスレッドからでも可)
    var isRunning: Bool {
        runLock.lock()
        defer { runLock.unlock() }
        return runningFlag
    }
    private func setRunning(_ v: Bool) {
        runLock.lock()
        runningFlag = v
        runLock.unlock()
    }

    deinit {
        NotificationCenter.default.removeObserver(self)
        scratch?.deallocate()
    }

    /// 開始する。成功したら true
    @discardableResult
    func start() -> Bool {
        return ctlQueue.sync {
            wantRunning = true
            addObserversIfNeeded()
            return startLocked()
        }
    }

    func stop() {
        ctlQueue.sync {
            wantRunning = false
            stopLocked()
        }
    }

    // MARK: - ctlQueue 上

    private func addObserversIfNeeded() {
        guard !observing else { return }
        observing = true
        let nc = NotificationCenter.default
        nc.addObserver(self, selector: #selector(handleInterruption(_:)),
                       name: AVAudioSession.interruptionNotification, object: nil)
        nc.addObserver(self, selector: #selector(handleReset),
                       name: AVAudioSession.mediaServicesWereResetNotification, object: nil)
        nc.addObserver(self, selector: #selector(handleReset),
                       name: .AVAudioEngineConfigurationChange, object: nil)
    }

    private func startLocked() -> Bool {
        stopLocked()

        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.playAndRecord, mode: .measurement, options: [.mixWithOthers, .defaultToSpeaker])
        } catch {
            NSLog("LowLatencyMic: setCategory failed: \(error)")
        }
        do { try session.setPreferredSampleRate(48000) } catch {
            NSLog("LowLatencyMic: setPreferredSampleRate failed: \(error)")
        }
        do { try session.setPreferredIOBufferDuration(0.005) } catch {
            NSLog("LowLatencyMic: setPreferredIOBufferDuration failed: \(error)")
        }
        do { try session.setActive(true) } catch {
            NSLog("LowLatencyMic: setActive failed: \(error)")
        }

        let eng = AVAudioEngine()
        let inputFormat = eng.inputNode.inputFormat(forBus: 0)
        let rate = Int(inputFormat.sampleRate)
        let channels = Int(inputFormat.channelCount)
        guard rate > 0, channels > 0 else {
            NSLog("LowLatencyMic: invalid input format \(inputFormat)")
            return false
        }
        let isInterleaved = inputFormat.isInterleaved
        let isFloat = inputFormat.commonFormat == .pcmFormatFloat32
        let isInt16 = inputFormat.commonFormat == .pcmFormatInt16
        guard isFloat || isInt16 else {
            NSLog("LowLatencyMic: unsupported format \(inputFormat)")
            return false
        }

        // RT スレッドで使う変換バッファ (十分大きく確保)
        let cap = 16384
        if scratchCapacity != cap {
            scratch?.deallocate()
            scratch = UnsafeMutablePointer<Int16>.allocate(capacity: cap)
            scratchCapacity = cap
        }
        guard let dst = scratch else { return false }
        let capacity = scratchCapacity
        let queue = sendQueue

        let node = AVAudioSinkNode { [unowned self] (timestamp, frameCount, audioBufferList) -> OSStatus in
            let frames = min(Int(frameCount), capacity)
            if frames <= 0 { return noErr }
            let list = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: audioBufferList))
            if list.count == 0 { return noErr }

            for i in 0..<frames {
                var sum: Float = 0
                var n: Float = 0
                if isInterleaved {
                    guard let p = list[0].mData else { break }
                    if isFloat {
                        let f = p.assumingMemoryBound(to: Float.self)
                        for c in 0..<channels { sum += f[i * channels + c]; n += 1 }
                    } else {
                        let s = p.assumingMemoryBound(to: Int16.self)
                        for c in 0..<channels { sum += Float(s[i * channels + c]) / 32768.0; n += 1 }
                    }
                } else {
                    for c in 0..<min(channels, list.count) {
                        guard let p = list[c].mData else { continue }
                        if isFloat {
                            sum += p.assumingMemoryBound(to: Float.self)[i]
                        } else {
                            sum += Float(p.assumingMemoryBound(to: Int16.self)[i]) / 32768.0
                        }
                        n += 1
                    }
                }
                var v = n > 0 ? sum / n : 0
                if !v.isFinite { v = 0 }
                v = min(max(v, -1.0), 1.0)
                dst[i] = Int16(v * 32767.0).littleEndian
            }

            let hostTime = timestamp.pointee.mHostTime
            let t = AVAudioTime.seconds(forHostTime: hostTime)
            let data = Data(bytes: dst, count: frames * 2)
            queue.async { [weak self] in
                self?.onSamples?(data, rate, t)
            }
            return noErr
        }

        eng.attach(node)
        eng.connect(eng.inputNode, to: node, format: inputFormat)
        eng.prepare()
        do {
            try eng.start()
        } catch {
            NSLog("LowLatencyMic: engine start failed: \(error)")
            eng.detach(node)
            return false
        }
        engine = eng
        sink = node
        sampleRate = rate
        setRunning(true)
        NSLog("LowLatencyMic: started rate=\(rate) ch=\(channels) ioBuffer=\(session.ioBufferDuration)")
        return true
    }

    private func stopLocked() {
        if let eng = engine {
            eng.stop()
            if let s = sink { eng.detach(s) }
        }
        engine = nil
        sink = nil
        sampleRate = 0
        setRunning(false)
    }

    // MARK: - 通知

    @objc private func handleInterruption(_ note: Notification) {
        guard let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
              let type = AVAudioSession.InterruptionType(rawValue: raw) else { return }
        if type == .began {
            ctlQueue.async { [weak self] in
                guard let self = self, self.wantRunning else { return }
                self.stopLocked()
            }
        } else {
            restart()
        }
    }

    @objc private func handleReset() {
        restart()
    }

    private func restart() {
        ctlQueue.asyncAfter(deadline: .now() + 0.3) { [weak self] in
            guard let self = self, self.wantRunning else { return }
            if !self.startLocked() {
                // もう一度だけ後で試す
                self.ctlQueue.asyncAfter(deadline: .now() + 1.0) { [weak self] in
                    guard let self = self, self.wantRunning, !self.isRunning else { return }
                    _ = self.startLocked()
                }
            }
        }
    }
}
