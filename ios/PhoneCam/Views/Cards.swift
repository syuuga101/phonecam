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

struct InfoRow: View {
    let title: String
    let value: String

    var body: some View {
        HStack {
            Text(title)
                .font(.system(size: 15))
                .foregroundColor(Color(white: 0.6))
            Spacer()
            Text(value)
                .font(.system(size: 15, weight: .medium, design: .monospaced))
                .foregroundColor(.white)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }
}

extension ContentView {
    var infoCard: some View {
        VStack(spacing: 0) {
            InfoRow(title: model.wifiAllowed ? "Wi-Fi アドレス" : "Wi-Fi接続",
                    value: model.wifiAllowed ? model.wifiAddress : "オフ")
            Divider().background(Color(white: 0.2))
            InfoRow(title: "映像", value: model.formatText)
            Divider().background(Color(white: 0.2))
            InfoRow(title: "ビットレート", value: model.bitrateText)
            Divider().background(Color(white: 0.2))
            InfoRow(title: "送信fps", value: "\(model.sentFps)")
            Divider().background(Color(white: 0.2))
            InfoRow(title: "温度状態", value: model.thermalText)
            Divider().background(Color(white: 0.2))
            InfoRow(title: "バッテリー", value: model.batteryText)
        }
        .background(cardColor)
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }

    var togglesCard: some View {
        VStack(spacing: 0) {
            Toggle("プレビュー", isOn: $model.previewEnabled)
                .tint(.green)
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
            if !model.previewEnabled && rotationHint {
                Text("プレビューをオンにすると映る範囲を確認できます")
                    .font(.system(size: 13))
                    .foregroundColor(Color(white: 0.55))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 16)
                    .padding(.bottom, 10)
            }
            Divider().background(Color(white: 0.2))
            Toggle("接続時に振動", isOn: $model.vibrateOnConnect)
                .tint(.green)
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
            Divider().background(Color(white: 0.2))
            Toggle("画面を暗くする", isOn: Binding(
                get: { model.dimScreen },
                set: { model.setDim($0) }
            ))
            .tint(.green)
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            Divider().background(Color(white: 0.2))
            Toggle("画面を真っ黒にする", isOn: Binding(
                get: { model.screenBlack },
                set: { model.setScreenBlack($0) }
            ))
            .tint(.green)
            .disabled(!model.isConnected)
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            if !model.isConnected {
                Text("PCと接続中のみ使えます")
                    .font(.system(size: 13))
                    .foregroundColor(Color(white: 0.55))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 16)
                    .padding(.bottom, 10)
            }
        }
        .foregroundColor(.white)
        .background(cardColor)
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }

    var wifiCard: some View {
        VStack(spacing: 0) {
            Toggle("Wi-Fiからの接続を許可", isOn: Binding(
                get: { model.wifiAllowed },
                set: { model.setWifiAllowed($0) }
            ))
            .tint(.green)
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            if model.wifiAllowed {
                Divider().background(Color(white: 0.2))
                VStack(spacing: 6) {
                    Text("ペアリングコード")
                        .font(.system(size: 13))
                        .foregroundColor(Color(white: 0.55))
                    Text(model.pairingCode)
                        .font(.system(size: 44, weight: .semibold, design: .monospaced))
                        .kerning(6)
                        .lineLimit(1)
                        .minimumScaleFactor(0.5)
                        .foregroundColor(.white)
                    Button(action: { model.regeneratePairingCode() }) {
                        Text("コードを変更")
                            .font(.system(size: 15, weight: .medium))
                            .foregroundColor(Color.blue)
                    }
                    .padding(.top, 2)
                    Text("PCの「ペアリングコード」欄に入力してください。USB接続では不要です。")
                        .font(.system(size: 13))
                        .foregroundColor(Color(white: 0.55))
                        .multilineTextAlignment(.center)
                        .padding(.top, 2)
                }
                .frame(maxWidth: .infinity)
                .padding(.horizontal, 16)
                .padding(.vertical, 14)
            }
        }
        .foregroundColor(.white)
        .background(cardColor)
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }

    var recordRow: some View {
        HStack(spacing: 10) {
            Text("録画")
                .font(.system(size: 17))
                .foregroundColor(.white)
            if model.recActive {
                Circle()
                    .fill(Color.red)
                    .frame(width: 10, height: 10)
                Text("REC " + ContentView.timeText(model.recSeconds))
                    .font(.system(size: 15, weight: .medium, design: .monospaced))
                    .foregroundColor(.red)
            }
            Spacer()
            Button(action: { model.toggleRecording() }) {
                Text(model.recActive ? "停止" : "開始")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundColor(.white)
                    .padding(.horizontal, 18)
                    .padding(.vertical, 7)
                    .background(model.recActive ? Color.red : Color(white: 0.28))
                    .clipShape(Capsule())
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(cardColor)
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }

    var footnotes: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("マイク: \(model.micText)")
                .font(.system(size: 13))
                .foregroundColor(Color(white: 0.55))

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
                .foregroundColor(Color(white: 0.55))
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
