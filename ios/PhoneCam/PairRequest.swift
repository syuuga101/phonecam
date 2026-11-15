import Foundation

/// 認証前の「ペアリング要求」(プロトコル v1.6) の規則。ネットワークや UI には依存しない純粋なロジック。
/// PC が要求を送ると、iPhone は画面にペアリングコードをポップアップで出す。コードそのものは送らない。
/// 要求を受け付けても認証は通らない (コードの照合は SecurityManager のまま)。
enum PairRequestRules {
    /// 認証前の接続が送れる 1 メッセージの大きさの上限
    static let maxPreAuthBytes = 1024
    /// 同じ IP からの要求の最短間隔 (秒)
    static let minInterval: TimeInterval = 10
    /// ポップアップを出したままにする上限 (秒)。過ぎたらその接続を閉じて、ポップアップも消す
    static let popupSeconds: TimeInterval = 120
    /// 断った / 受け付けなかったときの返事を送ってから接続を閉じるまでの上限 (秒)
    static let rejectLinger: TimeInterval = 3
    static let nameLimit = 40
    static let idLimit = 64

    /// 表示用の PC 名: 制御文字・書式文字 (向きを変える文字を含む)・行区切りを除き、前後の空白を落として
    /// nameLimit 文字までにする。空なら "PC"。
    static func sanitizeName(_ raw: String) -> String {
        var kept = String.UnicodeScalarView()
        for scalar in raw.unicodeScalars {
            switch scalar.properties.generalCategory {
            case .control, .format, .surrogate, .privateUse, .unassigned, .lineSeparator, .paragraphSeparator:
                continue
            default:
                kept.append(scalar)
            }
            if kept.count >= nameLimit * 2 { break }
        }
        let trimmed = String(kept).trimmingCharacters(in: .whitespaces)
        let limited = String(String.UnicodeScalarView(trimmed.unicodeScalars.prefix(nameLimit)))
            .trimmingCharacters(in: .whitespaces)
        return limited.isEmpty ? "PC" : limited
    }

    /// 要求 ID: 英数字・"-"・"_" だけ、8〜64 文字
    static func isValidID(_ id: String) -> Bool {
        let scalars = Array(id.unicodeScalars)
        guard scalars.count >= 8, scalars.count <= idLimit else { return false }
        return scalars.allSatisfy { s in
            switch s.value {
            case 0x30...0x39, 0x41...0x5A, 0x61...0x7A, 0x2D, 0x5F:   // 0-9 A-Z a-z - _
                return true
            default:
                return false
            }
        }
    }

    /// コードを 3 桁ずつに分けて見やすくする ("123456" -> "123 456")。6 桁の数字でなければそのまま返す。
    static func displayCode(_ code: String) -> String {
        guard code.count == 6, code.allSatisfy({ $0 >= "0" && $0 <= "9" }) else { return code }
        let mid = code.index(code.startIndex, offsetBy: 3)
        return String(code[..<mid]) + " " + String(code[mid...])
    }
}

/// PC から届く認証前のメッセージ (種別 10 の JSON)。知らないキーや不正な内容は nil (無視する)。
enum PairRequestMessage: Equatable {
    case request(id: String, name: String)
    case cancel(id: String)

    init?(control obj: [String: Any]) {
        if let r = obj["pairRequest"] as? [String: Any] {
            guard let id = r["id"] as? String, PairRequestRules.isValidID(id) else { return nil }
            let name = (r["name"] as? String) ?? ""
            self = .request(id: id, name: PairRequestRules.sanitizeName(name))
            return
        }
        if let c = obj["pairCancel"] as? [String: Any] {
            guard let id = c["id"] as? String, PairRequestRules.isValidID(id) else { return nil }
            self = .cancel(id: id)
            return
        }
        return nil
    }
}

/// 要求の受け付けの可否: IP ごとの回数制限と、ポップアップを 1 つだけにする規則。
struct PairRequestGate {
    enum Decision: Equatable {
        case accept        // ポップアップを出してよい
        case rateLimited   // 同じ IP から短時間にまた来た
        case busy          // 別の IP の要求のポップアップが出ている
    }

    private var lastAccepted: [String: TimeInterval] = [:]
    let minInterval: TimeInterval

    init(minInterval: TimeInterval = PairRequestRules.minInterval) {
        self.minInterval = minInterval
    }

    /// now は単調増加の時刻 (秒)。activeIP は今ポップアップを出している要求の IP (無ければ nil)。
    /// 同じ IP からの新しい要求は、受け付ければ古い要求を置き換える (ポップアップは常に 1 つ)。
    mutating func evaluate(ip: String, activeIP: String?, now: TimeInterval) -> Decision {
        if let active = activeIP, active != ip { return .busy }
        if let last = lastAccepted[ip], now - last < minInterval { return .rateLimited }
        if lastAccepted.count >= 64 {
            lastAccepted = lastAccepted.filter { now - $0.value < minInterval }
        }
        lastAccepted[ip] = now
        return .accept
    }
}
