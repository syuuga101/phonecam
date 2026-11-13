@main
struct AdaptiveBitrateChecks {
    static func main() {
        let busy = VideoSendSample(pendingBytes: 400_000, pendingSeconds: 0.3,
                                   slowestSend: 0.2, droppedFrames: 6)
        let clear = VideoSendSample(slowestSend: 0.01, sentBytes: 1_000_000)
        var wifi = AdaptiveBitrate()
        var usb = AdaptiveBitrate()
        wifi.configure(ceiling: 20, enabled: true)
        usb.configure(ceiling: 30, enabled: false)
        wifi.update(busy, streaming: true)
        precondition(wifi.target == 20, "Ignore a single congestion spike")
        wifi.update(busy, streaming: true)
        precondition(wifi.target == 15 && wifi.reduced)
        for _ in 0..<20 {
            wifi.configure(ceiling: 20, enabled: true)
            wifi.update(busy, streaming: true)
            usb.update(busy, streaming: true)
        }
        precondition(wifi.target == 6 && usb.target == 30 && !usb.reduced)
        for _ in 0..<16 { wifi.update(VideoSendSample(), streaming: true) }
        precondition(wifi.target == 6, "No recovery when no video completes")
        for _ in 0..<7 { wifi.update(clear, streaming: true) }
        precondition(wifi.target == 6)
        wifi.update(clear, streaming: true)
        precondition(wifi.target == 8)
        for _ in 0..<64 { wifi.update(clear, streaming: true) }
        precondition(wifi.target == 20 && !wifi.reduced)
        wifi.update(VideoSendSample(pendingSeconds: 0.6), streaming: true)
        precondition(wifi.target == 15, "React immediately to a blocked send")
        for _ in 0..<16 { wifi.update(clear, streaming: false) }
        precondition(wifi.target == 15)
        wifi.configure(ceiling: 10, enabled: true)
        precondition(wifi.target == 10)
        wifi.update(VideoSendSample(pendingSeconds: 0.6), streaming: true)
        precondition(wifi.reduced)
        wifi.configure(ceiling: 10, enabled: false)
        precondition(wifi.target == 10 && !wifi.reduced)
        wifi.configure(ceiling: 10, enabled: true)
        wifi.update(VideoSendSample(pendingSeconds: 0.6), streaming: true)
        wifi.resetConnection()
        precondition(wifi.target == 10 && !wifi.enabled && !wifi.reduced)
    }
}
