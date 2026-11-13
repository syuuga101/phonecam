import Foundation

/// A one-second sample of the Wi-Fi video sender. Send completion measures
/// local TCP backpressure, not end-to-end latency.
struct VideoSendSample {
    var pendingBytes = 0
    var pendingSeconds: Double = 0
    var slowestSend: Double = 0
    var droppedFrames = 0
    var sentBytes = 0
}

struct AdaptiveBitrate {
    private(set) var ceiling = 20
    private(set) var target = 20
    private(set) var enabled = false
    private var congestedSeconds = 0
    private var healthySeconds = 0
    private var holdSeconds = 0

    var reduced: Bool { enabled && target < ceiling }
    private var floor: Int { max(1, Int(ceil(Double(ceiling) * 0.30))) }

    mutating func configure(ceiling newCeiling: Int, enabled newEnabled: Bool) {
        let limit = min(max(newCeiling, 1), 100)
        guard limit != ceiling || newEnabled != enabled else { return }
        let wasReduced = reduced
        ceiling = limit
        enabled = newEnabled
        target = newEnabled && wasReduced ? min(ceiling, max(floor, target)) : ceiling
        resetWindow()
    }

    mutating func resetConnection() {
        enabled = false
        target = ceiling
        resetWindow()
    }

    mutating func resetWindow() {
        congestedSeconds = 0
        healthySeconds = 0
        holdSeconds = 0
    }

    mutating func update(_ sample: VideoSendSample, streaming: Bool) {
        guard enabled, streaming else {
            resetWindow()
            return
        }
        // A single large keyframe can cause a short spike. Require two busy
        // samples unless a send has already been blocked for half a second.
        let blocked = sample.pendingSeconds >= 0.5
        let byteLimit = target * 1_000_000 / 8 / 8  // 125 ms of video
        let busy = blocked || sample.slowestSend >= 0.15
            || sample.droppedFrames >= 3
            || (sample.pendingSeconds >= 0.10 && sample.pendingBytes >= byteLimit)
        if holdSeconds > 0 { holdSeconds -= 1 }
        if busy {
            healthySeconds = 0
            congestedSeconds += 1
            if holdSeconds == 0 && (blocked || congestedSeconds >= 2) && target > floor {
                target = max(floor, min(target - 1, Int(Double(target) * 0.75)))
                congestedSeconds = 0
                holdSeconds = 2
            }
        } else {
            congestedSeconds = 0
            if sample.sentBytes > 0 && sample.droppedFrames == 0 && sample.slowestSend < 0.08 {
                healthySeconds += 1
                if healthySeconds >= 8 && target < ceiling {
                    target = min(ceiling, target + max(1, ceiling / 10))
                    healthySeconds = 0
                }
            } else {
                healthySeconds = 0
            }
        }
    }
}
