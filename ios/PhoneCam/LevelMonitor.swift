import Foundation
import CoreMotion

/// 水平メーター用の傾き取得。CMDeviceMotion の重力ベクトルだけを使う
/// (加速度 / ジャイロ系は NSMotionUsageDescription 不要。必要なのは CMMotionActivity / 歩数計など)。
/// start/stop はメインスレッドから呼ぶ。onUpdate は CoreMotion のキュー上で呼ばれる。
final class LevelMonitor {
    /// (水平からのずれ [度, -45...45], 机の上に水平に置かれているか)
    var onUpdate: ((Double, Bool) -> Void)?

    private let manager = CMMotionManager()
    private let queue: OperationQueue = {
        let q = OperationQueue()
        q.name = "phonecam.motion"
        q.maxConcurrentOperationCount = 1
        return q
    }()
    private var running = false
    private var smoothed = 0.0      // queue 上でのみ使う
    private var lastSent = Double.nan
    private var lastFlat = false

    func start() {
        guard !running, manager.isDeviceMotionAvailable else { return }
        running = true
        queue.addOperation { [weak self] in
            self?.smoothed = 0
            self?.lastSent = Double.nan
        }
        manager.deviceMotionUpdateInterval = 1.0 / 20.0
        manager.startDeviceMotionUpdates(to: queue) { [weak self] motion, _ in
            guard let self = self, let g = motion?.gravity else { return }
            let flat = abs(g.z) > 0.8
            var dev = 0.0
            if !flat {
                // 縦持ち正立で 0、時計回りに傾けると正。90° ごとの最寄りからのずれ = 撮影画像の水平からのずれ
                let deg = atan2(g.x, -g.y) * 180.0 / Double.pi
                var d = deg.truncatingRemainder(dividingBy: 90.0)
                if d > 45 { d -= 90 } else if d < -45 { d += 90 }
                dev = d
            }
            if abs(dev - self.smoothed) > 40 {
                self.smoothed = dev   // 45° をまたいだときは平滑化しない
            } else {
                self.smoothed = self.smoothed * 0.6 + dev * 0.4
            }
            let rounded = (self.smoothed * 10).rounded() / 10
            if flat != self.lastFlat || self.lastSent.isNaN || abs(rounded - self.lastSent) >= 0.1 {
                self.lastFlat = flat
                self.lastSent = rounded
                self.onUpdate?(rounded, flat)
            }
        }
    }

    func stop() {
        guard running else { return }
        running = false
        manager.stopDeviceMotionUpdates()
    }
}
