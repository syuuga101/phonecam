import Foundation

/// 端末の名前と機種名。iOS 16 以降は UIDevice.current.name が一般アプリでは「iPhone」しか返さないため、
/// アプリ側で名前を決める。
enum DeviceName {
    static let defaultsKey = "customDeviceName"
    static let maxNameLength = 60

    /// utsname の machine (例: "iPhone14,5")
    static var identifier: String {
        var info = utsname()
        uname(&info)
        let machine = withUnsafeBytes(of: &info.machine) { raw -> String in
            let bytes = raw.prefix(while: { $0 != 0 })
            return String(decoding: bytes, as: UTF8.self)
        }
        // シミュレータは実機の識別子を環境変数で持つ
        if machine == "arm64" || machine == "x86_64",
           let sim = ProcessInfo.processInfo.environment["SIMULATOR_MODEL_IDENTIFIER"] {
            return sim
        }
        return machine
    }

    /// 人が読める機種名 (例: "iPhone 13")
    static var modelName: String { modelName(for: identifier) }

    static func modelName(for id: String) -> String {
        if let name = table[id] { return name }
        if id.hasPrefix("iPhone") { return "iPhone (\(id))" }
        if id.hasPrefix("iPad") { return "iPad (\(id))" }
        return id.isEmpty ? "iPhone" : id
    }

    /// 設定された名前。未設定 (空白のみ含む) なら機種名。
    static var current: String {
        let stored = UserDefaults.standard.string(forKey: defaultsKey)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return stored.isEmpty ? modelName : stored
    }

    /// UTF-8 で maxBytes 以内になるよう、文字 (Character) 境界で切り詰める。
    static func truncate(_ text: String, maxBytes: Int) -> String {
        var result = ""
        var used = 0
        for ch in text {
            let n = String(ch).utf8.count
            if used + n > maxBytes { break }
            result.append(ch)
            used += n
        }
        return result
    }

    private static let table: [String: String] = [
        // iPhone 11 以降
        "iPhone12,1": "iPhone 11", "iPhone12,3": "iPhone 11 Pro", "iPhone12,5": "iPhone 11 Pro Max",
        "iPhone12,8": "iPhone SE (第2世代)",
        "iPhone13,1": "iPhone 12 mini", "iPhone13,2": "iPhone 12",
        "iPhone13,3": "iPhone 12 Pro", "iPhone13,4": "iPhone 12 Pro Max",
        "iPhone14,4": "iPhone 13 mini", "iPhone14,5": "iPhone 13",
        "iPhone14,2": "iPhone 13 Pro", "iPhone14,3": "iPhone 13 Pro Max",
        "iPhone14,6": "iPhone SE (第3世代)",
        "iPhone14,7": "iPhone 14", "iPhone14,8": "iPhone 14 Plus",
        "iPhone15,2": "iPhone 14 Pro", "iPhone15,3": "iPhone 14 Pro Max",
        "iPhone15,4": "iPhone 15", "iPhone15,5": "iPhone 15 Plus",
        "iPhone16,1": "iPhone 15 Pro", "iPhone16,2": "iPhone 15 Pro Max",
        "iPhone17,3": "iPhone 16", "iPhone17,4": "iPhone 16 Plus",
        "iPhone17,1": "iPhone 16 Pro", "iPhone17,2": "iPhone 16 Pro Max",
        "iPhone17,5": "iPhone 16e",
        "iPhone18,3": "iPhone 17", "iPhone18,1": "iPhone 17 Pro", "iPhone18,2": "iPhone 17 Pro Max",
        "iPhone18,4": "iPhone Air",
        // iPad
        "iPad11,6": "iPad (第8世代)", "iPad11,7": "iPad (第8世代)",
        "iPad12,1": "iPad (第9世代)", "iPad12,2": "iPad (第9世代)",
        "iPad13,18": "iPad (第10世代)", "iPad13,19": "iPad (第10世代)",
        "iPad15,7": "iPad (A16)", "iPad15,8": "iPad (A16)",
        "iPad11,1": "iPad mini (第5世代)", "iPad11,2": "iPad mini (第5世代)",
        "iPad14,1": "iPad mini (第6世代)", "iPad14,2": "iPad mini (第6世代)",
        "iPad16,1": "iPad mini (A17 Pro)", "iPad16,2": "iPad mini (A17 Pro)",
        "iPad11,3": "iPad Air (第3世代)", "iPad11,4": "iPad Air (第3世代)",
        "iPad13,1": "iPad Air (第4世代)", "iPad13,2": "iPad Air (第4世代)",
        "iPad13,16": "iPad Air (第5世代)", "iPad13,17": "iPad Air (第5世代)",
        "iPad14,8": "iPad Air 11インチ (M2)", "iPad14,9": "iPad Air 11インチ (M2)",
        "iPad14,10": "iPad Air 13インチ (M2)", "iPad14,11": "iPad Air 13インチ (M2)",
        "iPad15,3": "iPad Air 11インチ (M3)", "iPad15,4": "iPad Air 11インチ (M3)",
        "iPad15,5": "iPad Air 13インチ (M3)", "iPad15,6": "iPad Air 13インチ (M3)",
        "iPad13,4": "iPad Pro 11インチ (第3世代)", "iPad13,5": "iPad Pro 11インチ (第3世代)",
        "iPad13,6": "iPad Pro 11インチ (第3世代)", "iPad13,7": "iPad Pro 11インチ (第3世代)",
        "iPad13,8": "iPad Pro 12.9インチ (第5世代)", "iPad13,9": "iPad Pro 12.9インチ (第5世代)",
        "iPad13,10": "iPad Pro 12.9インチ (第5世代)", "iPad13,11": "iPad Pro 12.9インチ (第5世代)",
        "iPad14,3": "iPad Pro 11インチ (第4世代)", "iPad14,4": "iPad Pro 11インチ (第4世代)",
        "iPad14,5": "iPad Pro 12.9インチ (第6世代)", "iPad14,6": "iPad Pro 12.9インチ (第6世代)",
        "iPad16,3": "iPad Pro 11インチ (M4)", "iPad16,4": "iPad Pro 11インチ (M4)",
        "iPad16,5": "iPad Pro 13インチ (M4)", "iPad16,6": "iPad Pro 13インチ (M4)",
    ]
}
