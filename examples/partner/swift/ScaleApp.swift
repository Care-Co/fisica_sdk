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
    @Published var license = "License: basic mode"
    private let ble = BLETransport()
    private var core: FisicaCore?
    private var entitlement: FisicaEntitlement?
    private var client: FisicaClient?

    init() {
        do {
            let core = try FisicaCore()
            // Online, the developer portal activates the SDK key for this app's bundle ID and renews it.
            let entitlement = try FisicaEntitlement(core: core, endpoint: FisicaEntitlement.defaultEndpoint)
            entitlement.onStatus = { [weak self] status in DispatchQueue.main.async { self?.show(status) } }
            // Optional: the SDK key file from the developer portal, added to the app as fisica.fse. The key is
            // bound to this bundle ID, one seat for every install, so it ships inside the app (pairing is for PCs).
            // Without a key the app runs in basic mode: weight and foot size, one preview frame a second.
            if let key = Bundle.main.url(forResource: "fisica", withExtension: "fse") { try entitlement.importFile(key) }
            self.core = core; self.entitlement = entitlement
            show(try entitlement.status())
        } catch { status = error.localizedDescription }
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
    private func show(_ status: EntitlementStatus) {
        // Full mode adds raw data and every frame; basic mode says why (status.message explains it).
        license = "License: \(status.isFull ? "full" : "basic") mode" + (status.isFull ? "" : " (\(status.reason))")
        if let code = status.activationCode { license += "\nOffline activation code: \(code)" }
    }
    func measure(_ device: CBPeripheral) {
        guard client == nil, let core else { return }
        do {
            ble.stopScan(); ble.select(device)
            let client = try FisicaClient(core: core, transport: ble, entitlement: entitlement)
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
                                // Weight and foot size come from every packet in both modes.
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
            Text(model.license).font(.footnote)
            Text(model.status)
            List(model.devices, id: \.identifier) { device in
                Button(device.name ?? device.identifier.uuidString) { model.measure(device) }
            }
        }.onDisappear { model.close() }
    }
}
