import Foundation
import CoreBluetooth

/// OS BLE only. Use it as the `FisicaTransport` of a `FisicaClient` on the same queue,
/// or wire ready/bytes/lost to a `FisicaSession` yourself.
public final class BLETransport: NSObject, FisicaTransport, CBCentralManagerDelegate, CBPeripheralDelegate {
    public var onDevice: ((CBPeripheral) -> Void)?
    public var onReady: (() -> Void)?
    public var onBytes: (([UInt8]) -> Void)?
    public var onLost: (() -> Void)?
    public var onSendFailure: ((Error) -> Void)?
    public var onError: ((Error) -> Void)?
    public var onBluetoothState: ((CBManagerState) -> Void)?
    /// Seconds allowed from connect until notifications are enabled.
    public var readyTimeout: TimeInterval = 15
    private let queue: DispatchQueue
    private var central: CBCentralManager!
    private var peripheral: CBPeripheral?, write: CBCharacteristic?, notify: CBCharacteristic?
    private var writeID: CBUUID?, notifyID: CBUUID?
    private var outgoing: [Data] = [], writing = false, ready = false
    private var pendingServices = 0
    private var deadline: DispatchWorkItem?

    public init(queue: DispatchQueue = .main) { self.queue = queue; super.init(); central = CBCentralManager(delegate: self, queue: queue) }

    /// Start only after `onBluetoothState` reports `.poweredOn`.
    public func scan(services: [CBUUID]? = nil) { queue.async { self.central.scanForPeripherals(withServices: services) } }
    public func stopScan() { queue.async { self.central.stopScan() } }

    /// Choose the advertised Scale and its GATT profile for a later `open()`.
    public func select(_ device: CBPeripheral, profile: FisicaBleProfile = .scale) {
        queue.async { self.peripheral = device; self.writeID = CBUUID(string: profile.write); self.notifyID = CBUUID(string: profile.notify) }
    }
    /// Connect to the selected Scale. `onReady` follows once notifications are enabled.
    public func open() {
        queue.async { [weak self] in
            guard let self else { return }
            guard let device = self.peripheral, self.writeID != nil, self.notifyID != nil else {
                self.fail("Select a Scale and profile before opening"); self.onLost?(); return
            }
            self.central.stopScan(); device.delegate = self
            self.ready = false; self.write = nil; self.notify = nil; self.writing = false; self.outgoing.removeAll()
            self.deadline?.cancel()
            let deadline = DispatchWorkItem { [weak self] in
                guard let self, !self.ready else { return }
                self.fail("BLE connection was not ready in time"); self.abandon()
            }
            self.deadline = deadline
            self.queue.asyncAfter(deadline: .now() + self.readyTimeout, execute: deadline)
            self.central.connect(device)
        }
    }
    /// Choose the advertised Scale explicitly with custom characteristic UUIDs and connect.
    public func connect(_ device: CBPeripheral, writeUUID: String, notifyUUID: String) {
        select(device, profile: FisicaBleProfile(write: writeUUID, notify: notifyUUID)); open()
    }

    private var writeType: CBCharacteristicWriteType { write?.properties.contains(.write) == true ? .withResponse : .withoutResponse }
    public func send(_ bytes: [UInt8]) {
        queue.async {
            guard self.ready, let device = self.peripheral else {
                self.onSendFailure?(FisicaError(code: 1001, message: "BLE is not ready")); return
            }
            let size = device.maximumWriteValueLength(for: self.writeType)
            for start in stride(from: 0, to: bytes.count, by: size) { self.outgoing.append(Data(bytes[start..<min(start + size, bytes.count)])) }
            self.pump()
        }
    }
    public func close() {
        queue.async {
            self.central.stopScan(); self.deadline?.cancel(); self.ready = false; self.outgoing.removeAll()
            if let p = self.peripheral { self.central.cancelPeripheralConnection(p) }
        }
    }
    private func fail(_ message: String) { onError?(FisicaError(code: 1001, message: message)) }
    /// Give up on a connection that cannot become ready; the disconnect callback reports the loss.
    private func abandon() {
        deadline?.cancel()
        if let p = peripheral { central.cancelPeripheralConnection(p) }
    }
    private func pump() {
        guard !writing, !outgoing.isEmpty, let device = peripheral, let characteristic = write else { return }
        if writeType == .withoutResponse {
            while !outgoing.isEmpty && device.canSendWriteWithoutResponse { device.writeValue(outgoing.removeFirst(), for: characteristic, type: .withoutResponse) }
        } else { writing = true; device.writeValue(outgoing.removeFirst(), for: characteristic, type: .withResponse) }
    }

    public func centralManagerDidUpdateState(_ central: CBCentralManager) { onBluetoothState?(central.state) }
    public func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral, advertisementData: [String: Any], rssi RSSI: NSNumber) { onDevice?(peripheral) }
    public func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) { peripheral.discoverServices(nil) }
    public func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        ready = false; deadline?.cancel()
        onError?(error ?? FisicaError(code: 1001, message: "BLE connect failed")); onLost?()
    }
    public func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        ready = false; writing = false; write = nil; notify = nil; outgoing.removeAll(); deadline?.cancel()
        if let error { onError?(error) }; onLost?()
    }
    public func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        if let error { onError?(error); abandon(); return }
        let services = peripheral.services ?? []
        pendingServices = services.count
        if services.isEmpty { fail("Scale exposes no GATT services"); abandon(); return }
        for service in services { peripheral.discoverCharacteristics(nil, for: service) }
    }
    public func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        if let error { onError?(error); abandon(); return }
        for c in service.characteristics ?? [] { if c.uuid == writeID { write = c }; if c.uuid == notifyID { notify = c } }
        pendingServices -= 1
        if let n = notify, write != nil {
            if !ready && !n.isNotifying { peripheral.setNotifyValue(true, for: n) }
        } else if pendingServices <= 0 {
            // Every service was searched: report it instead of waiting forever.
            fail("Scale characteristic UUIDs not found"); abandon()
        }
    }
    public func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
        if let error { onError?(error); abandon(); return }
        if characteristic.uuid == notifyID && characteristic.isNotifying && !ready { ready = true; deadline?.cancel(); onReady?() }
    }
    public func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        if let error { onError?(error); return }; if characteristic.uuid == notifyID, let data = characteristic.value { onBytes?(Array(data)) }
    }
    public func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        writing = false; if let error { outgoing.removeAll(); onSendFailure?(error); return }; pump()
    }
    public func peripheralIsReady(toSendWriteWithoutResponse peripheral: CBPeripheral) { pump() }
}
