import SwiftUI
import UIKit

struct MutePill: View {
    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "mic.slash.fill")
                .font(.system(size: 12, weight: .semibold))
            Text("ミュート中")
                .font(.system(size: 13, weight: .semibold))
        }
        .foregroundColor(.white)
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(Color.red)
        .clipShape(Capsule())
    }
}

/// 行の左に置く SF Symbols (幅 28pt の列にそろえる)
struct RowIcon: View {
    let symbol: String

    var body: some View {
        Image(systemName: symbol)
            .font(.system(size: 17))
            .foregroundStyle(.secondary)
            .frame(width: 28, alignment: .leading)
    }
}

struct InfoRow: View {
    let symbol: String
    let title: String
    let value: String

    var body: some View {
        HStack(spacing: 12) {
            RowIcon(symbol: symbol)
            Text(title)
                .foregroundStyle(.secondary)
            Spacer()
            Text(value)
                .font(.system(size: 17, design: .monospaced))
                .foregroundStyle(.primary)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
    }
}

extension ContentView {
    /// 「配信情報」セクション (inset grouped List の中に置く)
    var infoSection: some View {
        Section {
            InfoRow(symbol: "cable.connector", title: "接続",
                    value: model.isConnected ? model.connectionKind : "未接続")
            if model.wifiAllowed {
                InfoRow(symbol: "wifi", title: "Wi-Fi アドレス", value: model.wifiAddress)
            }
            InfoRow(symbol: "video", title: "映像", value: model.formatText)
            InfoRow(symbol: "speedometer", title: "ビットレート", value: model.bitrateText)
            InfoRow(symbol: "film", title: "送信 fps", value: "\(model.sentFps)")
            InfoRow(symbol: "thermometer.medium", title: "温度状態", value: model.thermalText)
            InfoRow(symbol: "battery.100", title: "バッテリー", value: model.batteryText)
        } header: {
            Text("配信情報")
        }
    }

    var recordRow: some View {
        HStack(spacing: 12) {
            RowIcon(symbol: "circle.inset.filled")
            Text("録画")
            Spacer()
            if model.recActive {
                Text(ContentView.timeText(model.recSeconds))
                    .font(.system(size: 17, design: .monospaced))
                    .foregroundStyle(.red)
            }
            Circle()
                .fill(Color.red)
                .frame(width: 10, height: 10)
            Button(action: { model.toggleRecording() }) {
                Text(model.recActive ? "停止" : "録画開始")
            }
            .buttonStyle(.borderless)
        }
    }

    var footnotes: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("マイク: \(model.micText)")
                .font(.system(size: 13))
                .foregroundStyle(.secondary)

            if !model.recError.isEmpty {
                Text(model.recError)
                    .font(.system(size: 13))
                    .foregroundColor(.orange)
            }

            if let err = model.errorText {
                Text(err)
                    .font(.system(size: 13))
                    .foregroundColor(.orange)
            }

            Text("アプリは前面に表示したままにしてください")
                .font(.system(size: 13))
                .foregroundStyle(.secondary)
        }
    }

    static func timeText(_ seconds: Double) -> String {
        let total = max(0, Int(seconds))
        let h = total / 3600
        let m = (total % 3600) / 60
        let sec = total % 60
        if h > 0 {
            return String(format: "%d:%02d:%02d", h, m, sec)
        }
        return String(format: "%02d:%02d", m, sec)
    }
}
