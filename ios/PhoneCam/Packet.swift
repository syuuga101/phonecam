import Foundation

/// 通信プロトコルのメッセージ種別
enum PacketType {
    static let video: UInt8 = 1
    static let videoHevc: UInt8 = 2
    static let status: UInt8 = 3
    static let pong: UInt8 = 4
    static let timing: UInt8 = 5
    static let audio: UInt8 = 6
    static let control: UInt8 = 10
}

/// フレーミング: 1バイトの種別 + 4バイト(ビッグエンディアン)のペイロード長 + ペイロード
enum Packet {
    static func encode(type: UInt8, payload: Data) -> Data {
        var out = Data()
        out.reserveCapacity(5 + payload.count)
        out.append(type)
        let len = UInt32(payload.count)
        out.append(UInt8((len >> 24) & 0xFF))
        out.append(UInt8((len >> 16) & 0xFF))
        out.append(UInt8((len >> 8) & 0xFF))
        out.append(UInt8(len & 0xFF))
        out.append(payload)
        return out
    }
}

/// 受信バイト列からメッセージを取り出すパーサ
struct PacketParser {
    private var buffer = Data()
    private static let maxPayload = 16 * 1024 * 1024

    mutating func append(_ data: Data) {
        buffer.append(data)
    }

    mutating func reset() {
        buffer = Data()
    }

    /// 完全なメッセージが溜まっていれば1つ取り出す
    mutating func next() -> (type: UInt8, payload: Data)? {
        guard buffer.count >= 5 else { return nil }
        let base = buffer.startIndex
        let type = buffer[base]
        let b1 = Int(buffer[base + 1])
        let b2 = Int(buffer[base + 2])
        let b3 = Int(buffer[base + 3])
        let b4 = Int(buffer[base + 4])
        let len = (b1 << 24) | (b2 << 16) | (b3 << 8) | b4
        if len > PacketParser.maxPayload {
            // 壊れたストリーム: 破棄する
            buffer = Data()
            return nil
        }
        guard buffer.count >= 5 + len else { return nil }
        let payload = Data(buffer[(base + 5)..<(base + 5 + len)])
        // startIndex がずれないように新しい Data を作り直す
        buffer = Data(buffer[(base + 5 + len)..<buffer.endIndex])
        return (type, payload)
    }
}
