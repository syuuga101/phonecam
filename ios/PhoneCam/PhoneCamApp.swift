import SwiftUI
import UIKit

/// 画面の向きの固定 (設定「画面の向き」)。iOS 標準の向きロックは縦にしか固定できないので、アプリ側で制御する。
/// - 自動: 縦 / 横 (左右) すべて許可 (上下逆さまは除く)
/// - 縦で固定: 縦のみ
/// - 横で固定: landscapeLeft と landscapeRight の両方を許可 (どちらに倒しても横のまま、縦には戻らない)
/// 送出映像の向きは CameraController が重力 (RotationCoordinator) から決めており、画面 (UI) の向きとは独立。
/// そのため「横で固定」でも映像の向きは変わらない。
enum OrientationLock: String, CaseIterable, Identifiable {
    case auto, portrait, landscape

    static let defaultsKey = "orientationLock"

    var id: String { rawValue }

    var mask: UIInterfaceOrientationMask {
        switch self {
        case .auto: return .allButUpsideDown
        case .portrait: return .portrait
        case .landscape: return .landscape
        }
    }

    static var current: OrientationLock {
        let raw = UserDefaults.standard.string(forKey: defaultsKey) ?? ""
        return OrientationLock(rawValue: raw) ?? .auto
    }

    /// 現在の設定を、表示中の画面へ即座に反映する (メインスレッドで呼ぶ)
    static func apply() {
        let mask = current.mask
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        for scene in scenes {
            for window in scene.windows {
                window.rootViewController?.setNeedsUpdateOfSupportedInterfaceOrientations()
            }
            scene.requestGeometryUpdate(.iOS(interfaceOrientations: mask)) { _ in }
        }
    }
}

final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication,
                     supportedInterfaceOrientationsFor window: UIWindow?) -> UIInterfaceOrientationMask {
        return OrientationLock.current.mask
    }
}

@main
struct PhoneCamApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
                .environmentObject(model.metrics)
                .onAppear {
                    model.start()
                    OrientationLock.apply()
                }
        }
    }
}
