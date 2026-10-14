import SwiftUI
import CoreBluetooth
import FisicaScale

@main
struct ScaleApp: App {
    var body: some Scene { WindowGroup { ScaleView() } }
}

final class ScaleModel: ObservableObject {
    @Published var devices: [CBPeripheral] = []
    @Published var status = "Waiting for Bluetooth"
    private let ble = BLETransport()
    private var client: FisicaClient?

    init() {
        ble.onBluetoothState = { [weak self] state in
            self?.status = state == .poweredOn ? "Choose a Scale" : "Enable Bluetooth / allow access"
            if state == .poweredOn { self?.ble.scan() }
        }
        ble.onDevice = { [weak self] device in
            guard let self, device.name?.contains(FisicaBleProfile.nameContains) == true else { return }
            if !self.devices.contains(where: { $0.identifier == device.identifier }) { self.devices.append(device) }
        }
        ble.onError = { [weak self] error in self?.status = error.localizedDescription }
    }
    func measure(_ device: CBPeripheral) {
        guard client == nil else { return }
        do {
            ble.stopScan(); ble.select(device)
            let client = try FisicaClient(core: FisicaCore(), transport: ble)
            self.client = client
            client.onError = { [weak self] error in self?.finish(error) }
            status = "Connecting"
            client.connect(.ble) { [weak self] result in
                if case .failure(let error) = result { self?.finish(error); return }
                client.start { result in
                    if case .failure(let error) = result { self?.finish(error); return }
                    self?.status = "Measuring for 5 seconds"
                    DispatchQueue.main.asyncAfter(deadline: .now() + 5) {
                        client.stop { result in
                            do {
                                _ = try result.get()
                                let report = try client.report()
                                self?.status = report.map { "Weight: \($0.weight) kg; frames: \($0.frames.count)" } ?? "No stable result"
                            } catch { self?.status = error.localizedDescription }
                            client.close { _ in self?.client = nil }
                        }
                    }
                }
            }
        } catch { finish(error) }
    }
    private func finish(_ error: Error) {
        status = error.localizedDescription
        client?.close { [weak self] _ in self?.client = nil }
    }
    func close() { ble.stopScan(); client?.close { [weak self] _ in self?.client = nil } }
}

struct ScaleView: View {
    @StateObject private var model = ScaleModel()
    var body: some View {
        VStack {
            Text(model.status)
            List(model.devices, id: \.identifier) { device in
                Button(device.name ?? device.identifier.uuidString) { model.measure(device) }
            }
        }.onDisappear { model.close() }
    }
}
