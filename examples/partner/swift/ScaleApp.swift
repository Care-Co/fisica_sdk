import SwiftUI
import CoreBluetooth
import FisicaScale

// The same steps as every partner sample: SDK and core version, the license, pick a Scale (or the
// synthetic demo), Connect (reads the battery), Measure (5 s), the report, Disconnect.

@main
struct ScaleApp: App {
    var body: some Scene { WindowGroup { ScaleView() } }
}

/// The synthetic Scale of scale-mock.json: recorded replies, no measurement or protocol logic.
private struct DemoScript: Decodable {
    let replies: [String: String]
    let measurementHex: String
    let frameCount: Int
    enum CodingKeys: String, CodingKey { case replies, measurementHex = "measurement_hex", frameCount = "frame_count" }
}

final class ScaleModel: ObservableObject {
    /// The Scale to connect: one found over BLE, or the synthetic demo Scale (no hardware).
    enum Choice { case ble(CBPeripheral), demo([[UInt8]: [[UInt8]]]) }

    @Published var version = ""
    @Published var license = "License: basic mode"
    @Published var status = "Waiting for Bluetooth"
    @Published var devices: [CBPeripheral] = []
    @Published var selected: String?
    @Published var bluetoothOn = false
    @Published var connected = false
    @Published var busy = false
    @Published var hasClient = false
    @Published var report: SessionReport?
    private var choice: Choice?
    private let ble = BLETransport()
    private var core: FisicaCore?
    private var entitlement: FisicaEntitlement?
    private var client: FisicaClient? { didSet { hasClient = client != nil } }
    static let noResult = "No stable result; check the load on the Scale and the received frames"

    init() {
        do {
            let core = try FisicaCore()
            version = "Fisica SDK \(FisicaRelease.sdkVersion) (core \(core.version))"
            // Online, the developer portal activates the SDK key for this app's bundle ID and renews it.
            let entitlement = try FisicaEntitlement(core: core, endpoint: FisicaEntitlement.defaultEndpoint)
            entitlement.onStatus = { [weak self] status in DispatchQueue.main.async { self?.show(status) } }
            // Optional: the SDK key file from the developer portal, added to the app as fisica.fse. The key is
            // bound to this bundle ID, one seat for every install, so it ships inside the app (pairing is for PCs).
            // Without a key the app runs in basic mode: weight and foot size, one preview frame a second.
            if let key = Bundle.main.url(forResource: "fisica", withExtension: "fse") { try entitlement.importFile(key) }
            self.core = core; self.entitlement = entitlement
            show(try entitlement.status())
        } catch { status = "Error: \(Self.describe(error))" }
        ble.onBluetoothState = { [weak self] state in
            guard let self, self.core != nil else { return }  // keep a core error on screen
            self.bluetoothOn = state == .poweredOn
            self.status = state == .poweredOn ? "Scan for Scales, or try the demo" : "Enable Bluetooth / allow access"
        }
        ble.onDevice = { [weak self] device in
            guard let self, device.name?.contains(FisicaBleProfile.nameContains) == true else { return }
            if !self.devices.contains(where: { $0.identifier == device.identifier }) { self.devices.append(device) }
        }
        ble.onError = { [weak self] error in self?.status = "Error: \(Self.describe(error))" }
    }

    private func show(_ status: EntitlementStatus) {
        // "full mode", or "basic mode (no_key)" with the SDK's explanation of the reason.
        let reason = String(describing: status.reason)
            .replacingOccurrences(of: "([A-Z])", with: "_$1", options: .regularExpression).lowercased()
        var lines = [status.isFull ? "License: full mode" : "License: basic mode (\(reason))"]
        if let message = status.message { lines.append(message) }
        if let code = status.activationCode { lines.append("Activation code: \(code)") }
        license = lines.joined(separator: "\n")
    }

    func scan() {
        devices.removeAll()
        if case .ble = choice { choice = nil; selected = nil }
        ble.scan(); status = "Scanning for 5 seconds"
        DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak self] in
            guard let self, self.client == nil else { return }
            self.ble.stopScan()
            self.status = self.devices.isEmpty ? "No Scale found; check that it is powered on" : "Choose a Scale"
        }
    }

    func select(_ device: CBPeripheral) {
        choice = .ble(device); selected = device.identifier.uuidString
        status = "\(device.name ?? "Scale") selected; press Connect"
    }

    func selectDemo() {
        // Add scale-mock.json from the sample folder to the app target to try the app without a Scale.
        guard let core else { return }
        guard let url = Bundle.main.url(forResource: "scale-mock", withExtension: "json") else {
            status = "Add scale-mock.json to the app target for the demo"; return
        }
        do {
            let script = try JSONDecoder().decode(DemoScript.self, from: Data(contentsOf: url))
            var exchanges: [[UInt8]: [[UInt8]]] = [:]
            for (name, reply) in script.replies {
                exchanges[try core.command(name)] = [Self.bytes(reply)]
                    + (name == "START_CONTINUOUS_MEASUREMENT" ? Array(repeating: Self.bytes(script.measurementHex), count: script.frameCount) : [])
            }
            choice = .demo(exchanges); selected = "demo"
            status = "Demo without hardware selected; press Connect"
        } catch { status = "Error: \(Self.describe(error))" }
    }

    func connect() {
        guard let core, let choice, client == nil, !busy else { return }
        let transport: FisicaTransport, link: FisicaLink
        switch choice {
        case .ble(let device): ble.stopScan(); ble.select(device); transport = ble; link = .ble
        // MockTransport declares no link, so connect is always given one.
        case .demo(let exchanges): transport = MockTransport(exchanges: exchanges); link = .usb
        }
        do {
            let client = try FisicaClient(core: core, transport: transport, entitlement: entitlement)
            self.client = client; busy = true; report = nil; status = "Connecting"
            client.onError = { [weak self, weak client] error in if let client { self?.fail(error, of: client) } }
            client.onLinkLost = { [weak self, weak client] in
                if let client { self?.fail("The connection to the Scale was lost", of: client) }
            }
            client.connect(link) { [weak self] result in
                guard let self, self.client === client else { return }
                if case .failure(let error) = result { self.fail(error, of: client); return }
                // Measurement packets carry no battery field: read it now; the report shows this reading.
                client.readBattery { _ in
                    guard self.client === client else { return }
                    self.connected = true; self.busy = false
                    self.status = "Connected (firmware \(client.firmwareVersion ?? "unknown"))"
                }
            }
        } catch { status = "Error: \(Self.describe(error))" }
    }

    func measure() {
        guard let client, connected, !busy else { return }
        busy = true; report = nil; status = "Measuring for 5 seconds"
        client.start { [weak self] result in
            guard let self, self.client === client else { return }
            if case .failure(let error) = result { self.fail(error, of: client); return }
            DispatchQueue.main.asyncAfter(deadline: .now() + 5) {
                guard self.client === client else { return }
                client.stop { result in
                    guard self.client === client else { return }
                    do {
                        _ = try result.get()
                        // Weight and foot size come from every packet, in basic and full mode alike.
                        self.report = try client.report()
                        self.status = self.report == nil ? Self.noResult : "Measurement done"
                        self.busy = false
                    } catch { self.fail(error, of: client) }
                }
            }
        }
    }

    func disconnect() { release("Disconnected") }
    func close() { ble.stopScan(); release("Disconnected") }

    /// One clear line, and the client is always closed.
    private func fail(_ error: Error, of failed: FisicaClient) { fail(Self.describe(error), of: failed) }
    private func fail(_ message: String, of failed: FisicaClient) {
        guard client === failed else { return }
        release("Error: \(message)")
    }
    /// Close the client (the Scale leaves measurement mode); resources are freed even without an answer.
    private func release(_ message: String) {
        guard let current = client else { return }
        client = nil; connected = false; busy = true; status = message
        current.close { [weak self] _ in self?.busy = false }
    }

    static func describe(_ error: Error) -> String {
        if let command = error as? FisicaCommandError { return "Command rejected or unconfirmed (status \(command.code), device code \(command.deviceCode))" }
        return error.localizedDescription
    }
    private static func bytes(_ hex: String) -> [UInt8] {
        let digits = Array(hex.utf8)
        return stride(from: 0, to: digits.count - 1, by: 2).compactMap { UInt8(String(decoding: digits[$0...$0 + 1], as: UTF8.self), radix: 16) }
    }
    static func lines(_ report: SessionReport?) -> [String] {
        guard let report else { return ["Weight: -", "Left foot: -", "Right foot: -", "Frames: -", "Battery: -"] }
        return [String(format: "Weight: %.1f kg", report.weight),
                String(format: "Left foot: length %.1f cm, width %.1f cm", report.leftLength, report.leftWidth),
                String(format: "Right foot: length %.1f cm, width %.1f cm", report.rightLength, report.rightWidth),
                "Frames: \(report.frameCount)",
                "Battery: " + (report.battery.map { "\($0)%" } ?? "not read")]
    }
}

struct ScaleView: View {
    @StateObject private var model = ScaleModel()
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(model.version).font(.footnote)
            Text(model.license).font(.footnote)
            Text(model.status)
            HStack {
                Button("Scan for Scales") { model.scan() }.disabled(!model.bluetoothOn || model.hasClient)
                Button("Demo without hardware") { model.selectDemo() }.disabled(model.hasClient)
            }
            List(model.devices, id: \.identifier) { device in
                Button { model.select(device) } label: {
                    HStack {
                        Text(device.name ?? device.identifier.uuidString)
                        Spacer()
                        if model.selected == device.identifier.uuidString { Image(systemName: "checkmark") }
                    }
                }.disabled(model.hasClient)
            }
            HStack {
                Button("Connect") { model.connect() }.disabled(model.selected == nil || model.hasClient || model.busy)
                Button("Measure (5 s)") { model.measure() }.disabled(!model.connected || model.busy)
            }
            VStack(alignment: .leading) { ForEach(ScaleModel.lines(model.report), id: \.self) { Text($0) } }
            Button("Disconnect") { model.disconnect() }.disabled(!model.hasClient || model.busy && model.connected)
        }
        .padding()
        .onDisappear { model.close() }
    }
}
