import AVFoundation
import SwiftUI
import UIKit

struct WirelessScanner: View {
    @ObservedObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var invite: WirelessInvite?
    @State private var message: LocalizedStringKey = "wireless.scan.prompt"
    @State private var client: WirelessPairClient?
    @State private var scanning = true

    var body: some View {
        NavigationStack {
            VStack {
                QRPreview(model: model, scanning: scanning, onCode: read) {
                    message = "wireless.scan.camera_error"
                }
                Text(message).padding()
                if let invite {
                    Text(invite.name).lineLimit(1)
                    Button("wireless.scan.pair") { pair(invite) }
                        .buttonStyle(.borderedProminent)
                }
                if !scanning && client == nil {
                    Button("wireless.scan.retry") {
                        invite = nil
                        message = "wireless.scan.prompt"
                        scanning = true
                    }
                }
            }
            .navigationTitle("wireless.scan.title")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("wireless.scan.close") { dismiss() }
                }
            }
        }
        .onDisappear { client?.cancel() }
    }

    private func read(_ text: String) {
        guard scanning else { return }
        scanning = false
        invite = WirelessInvite(text)
        message = invite == nil ? "wireless.scan.invalid" : "wireless.scan.confirm"
    }

    private func pair(_ invite: WirelessInvite) {
        self.invite = nil
        message = "wireless.scan.sending"
        let request = WirelessPairClient()
        client = request
        request.send(invite, identity: model.wirelessIdentity, code: model.pairingCode) { success in
            client = nil
            if success {
                model.showToast(String(localized: "wireless.scan.success"), haptic: .success)
                dismiss()
            } else {
                message = "wireless.scan.failed"
            }
        }
    }
}

private struct QRPreview: UIViewRepresentable {
    let model: AppModel
    let scanning: Bool
    let onCode: (String) -> Void
    let onError: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(model: model, onCode: onCode, onError: onError) }

    func makeUIView(context: Context) -> ScannerView {
        let view = ScannerView()
        view.preview.session = context.coordinator.session
        view.preview.videoGravity = .resizeAspectFill
        context.coordinator.start()
        return view
    }

    func updateUIView(_ view: ScannerView, context: Context) {
        context.coordinator.scanning = scanning
    }

    static func dismantleUIView(_ view: ScannerView, coordinator: Coordinator) {
        coordinator.stop()
    }

    final class ScannerView: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        var preview: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
    }

    final class Coordinator: NSObject, AVCaptureMetadataOutputObjectsDelegate {
        let session = AVCaptureSession()
        private let queue = DispatchQueue(label: "phonecam.qr.camera")
        private let model: AppModel
        private let onCode: (String) -> Void
        private let onError: () -> Void
        var scanning = true
        private var stopped = false

        init(model: AppModel, onCode: @escaping (String) -> Void, onError: @escaping () -> Void) {
            self.model = model
            self.onCode = onCode
            self.onError = onError
        }

        func start() {
            model.setScannerActive(true) { [weak self] in
                guard let self, !self.stopped else { return }
                AVCaptureDevice.requestAccess(for: .video) { [weak self] granted in
                    DispatchQueue.main.async {
                        guard let self, !self.stopped else { return }
                        guard granted else { self.onError(); return }
                        self.queue.async { self.configure() }
                    }
                }
            }
        }

        private func configure() {
            guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back),
                  let input = try? AVCaptureDeviceInput(device: device), session.canAddInput(input) else {
                DispatchQueue.main.async { if !self.stopped { self.onError() } }
                return
            }
            session.addInput(input)
            let output = AVCaptureMetadataOutput()
            guard session.canAddOutput(output) else {
                DispatchQueue.main.async { if !self.stopped { self.onError() } }
                return
            }
            session.addOutput(output)
            guard output.availableMetadataObjectTypes.contains(.qr) else {
                DispatchQueue.main.async { if !self.stopped { self.onError() } }
                return
            }
            output.setMetadataObjectsDelegate(self, queue: .main)
            output.metadataObjectTypes = [.qr]
            session.startRunning()
        }

        func stop() {
            stopped = true
            queue.async {
                if self.session.isRunning { self.session.stopRunning() }
                DispatchQueue.main.async { self.model.setScannerActive(false) }
            }
        }

        func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput metadataObjects: [AVMetadataObject],
                            from connection: AVCaptureConnection) {
            guard scanning, !stopped,
                  let code = metadataObjects.compactMap({ ($0 as? AVMetadataMachineReadableCodeObject)?.stringValue }).first
            else { return }
            scanning = false
            onCode(code)
        }
    }
}
