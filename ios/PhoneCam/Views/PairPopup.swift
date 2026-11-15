import SwiftUI

/// PC から「ペアリング要求」が来たときに、ペアリングコードを大きく出すポップアップ (アプリが前面にあるときだけ)。
/// コードはこの画面にだけ出し、ネットワークには送らない。
struct PairPopupOverlay: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        ZStack {
            if let popup = model.pairPopup {
                Color.black.opacity(0.45)
                    .ignoresSafeArea()
                    .transition(.opacity)
                    .accessibilityHidden(true)
                PairPopupCard(
                    popup: popup,
                    close: { model.dismissPairPopup(id: popup.id) },
                    decline: { model.declinePairPopup() }
                )
                .padding(.horizontal, 28)
                .frame(maxWidth: 420)
                .transition(.scale(scale: 0.94).combined(with: .opacity))
            }
        }
        .animation(.easeOut(duration: 0.2), value: model.pairPopup)
    }
}

private struct PairPopupCard: View {
    let popup: PairPopup
    let close: () -> Void
    let decline: () -> Void

    var body: some View {
        VStack(spacing: 18) {
            VStack(spacing: 6) {
                Text("pairpopup.title \(popup.pcName)")
                    .font(.system(size: 17, weight: .semibold))
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                Text("pairpopup.instruction")
                    .font(.system(size: 14))
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }

            Text(PairRequestRules.displayCode(popup.code))
                .font(.system(size: 46, weight: .semibold, design: .rounded))
                .monospacedDigit()
                .minimumScaleFactor(0.5)
                .lineLimit(1)
                .padding(.vertical, 14)
                .frame(maxWidth: .infinity)
                .background(Color.primary.opacity(0.08), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .accessibilityLabel(popup.code.map(String.init).joined(separator: ", "))

            Text("pairpopup.footer")
                .font(.system(size: 12))
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: 10) {
                Button(role: .destructive, action: decline) {
                    Text("pairpopup.decline").frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                Button(action: close) {
                    Text("pairpopup.close").frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
            }
            .controlSize(.large)
        }
        .padding(22)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
        .shadow(color: .black.opacity(0.25), radius: 24, y: 8)
        .accessibilityElement(children: .contain)
        .accessibilityAddTraits(.isModal)
    }
}
