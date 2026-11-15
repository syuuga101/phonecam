@main
struct LoopbackChecks {
    static func main() {
        precondition(LoopbackAddress.isLoopback([127, 0, 0, 1]))
        precondition(LoopbackAddress.isLoopback([127, 1, 2, 3]))
        precondition(!LoopbackAddress.isLoopback([192, 168, 1, 7]))
        precondition(LoopbackAddress.isLoopback([UInt8](repeating: 0, count: 15) + [1]), "::1")
        var mapped = [UInt8](repeating: 0, count: 10) + [0xFF, 0xFF, 127, 0, 0, 1]
        precondition(LoopbackAddress.isLoopback(mapped), "::ffff:127.0.0.1")
        mapped[12] = 192
        precondition(!LoopbackAddress.isLoopback(mapped), "::ffff:192.x is Wi-Fi")
        precondition(!LoopbackAddress.isLoopback([UInt8](repeating: 0, count: 16)), ":: is not loopback")
        precondition(!LoopbackAddress.isLoopback([]))
    }
}
