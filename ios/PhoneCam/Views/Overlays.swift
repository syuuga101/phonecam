import SwiftUI
import UIKit

/// 真っ黒の省電力画面の上に出す、小さく暗い状態表示 (カメラが動いていることが分かるように)
struct LiveStatusPill: View {
    let text: String
    let live: Bool
    let muted: Bool

    var body: some View {
        HStack(spacing: 5) {
            Circle()
                .fill(live ? Color.green : Color(white: 0.5))
                .frame(width: 5, height: 5)
            Text(text)
                .font(.system(size: 11, weight: .medium))
            if muted {
                Image(systemName: "mic.slash.fill")
                    .font(.system(size: 10))
                    .foregroundColor(Color.red)
            }
        }
        .foregroundColor(Color(white: 0.65))
        .padding(.horizontal, 9)
        .padding(.vertical, 4)
        .background(Color(white: 0.1))
        .clipShape(Capsule())
        .opacity(0.55)
    }
}

extension ContentView {
    var blackPillText: String {
        if model.isConnected && model.streamActiveUI { return "配信中" }
        if model.recActive { return "録画中" }
        return "待機中"
    }

    /// 真っ黒の省電力画面 (タップで解除)
    @ViewBuilder
    var blackOverlay: some View {
            if model.screenBlack {
                ZStack(alignment: .top) {
                    Color.black
                        .ignoresSafeArea()
                        .contentShape(Rectangle())
                        .onTapGesture { model.setScreenBlack(false) }
                    LiveStatusPill(text: blackPillText,
                                   live: model.isConnected || model.recActive,
                                   muted: model.micEnabled && model.micMuted)
                        .padding(.top, 8)
                        .allowsHitTesting(false)
                }
            }
    }

    /// 上部のトースト
    @ViewBuilder
    var toastOverlay: some View {
            if let t = model.toast {
                Text(t)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(.white)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 9)
                    .background(Color(white: 0.2))
                    .clipShape(Capsule())
                    .padding(.top, 10)
                    .transition(.move(edge: .top).combined(with: .opacity))
                    .allowsHitTesting(false)
            }
    }
}
