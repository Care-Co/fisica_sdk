import Foundation
import CoreBluetooth

/// OS BLE only. The app wires ready/bytes/lost to a FisicaSession on one executor.
public final class BLETransport: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    public var onDevice: ((CBPeripheral)->Void)?
    public var onReady: (()->Void)?
    public var onBytes: (([UInt8])->Void)?
    public var onLost: (()->Void)?
    public var onError: ((Error)->Void)?
    public var onBluetoothState: ((CBManagerState)->Void)?
    private let queue: DispatchQueue
    private var central: CBCentralManager!
    private var peripheral: CBPeripheral?, write: CBCharacteristic?, notify: CBCharacteristic?
    private var writeID: CBUUID?, notifyID: CBUUID?
    private var outgoing: [Data] = [], writing = false, ready = false
    public init(queue: DispatchQueue = .main) { self.queue = queue; super.init(); central = CBCentralManager(delegate: self, queue: queue) }
    public func scan(services: [CBUUID]? = nil) { queue.async { self.central.scanForPeripherals(withServices: services) } }
    public func stopScan() { queue.async { self.central.stopScan() } }
    /// Choose the advertised Scale explicitly; use its verified characteristic UUIDs.
    public func connect(_ device: CBPeripheral, writeUUID: String, notifyUUID: String) {
        queue.async {
            self.central.stopScan(); self.peripheral = device; device.delegate = self
            self.writeID = CBUUID(string: writeUUID); self.notifyID = CBUUID(string: notifyUUID)
            self.ready = false; self.central.connect(device)
        }
    }
    private var writeType: CBCharacteristicWriteType { write?.properties.contains(.write) == true ? .withResponse : .withoutResponse }
    public func send(_ bytes: [UInt8]) {
        queue.async {
            guard self.ready, let device = self.peripheral else { self.fail("BLE is not ready"); return }
            let size = device.maximumWriteValueLength(for: self.writeType)
            for start in stride(from: 0, to: bytes.count, by: size) { self.outgoing.append(Data(bytes[start..<min(start+size,bytes.count)])) }
            self.pump()
        }
    }
    public func close() { queue.async { self.central.stopScan(); self.ready = false; self.outgoing.removeAll(); if let p = self.peripheral { self.central.cancelPeripheralConnection(p) } } }
    private func fail(_ message: String) { onError?(FisicaError(code: 1001, message: message)) }
    private func pump() {
        guard !writing, !outgoing.isEmpty, let device = peripheral, let characteristic = write else { return }
        if writeType == .withoutResponse {
            while !outgoing.isEmpty && device.canSendWriteWithoutResponse { device.writeValue(outgoing.removeFirst(), for: characteristic, type: .withoutResponse) }
        } else { writing = true; device.writeValue(outgoing.removeFirst(), for: characteristic, type: .withResponse) }
    }
    public func centralManagerDidUpdateState(_ central: CBCentralManager) { onBluetoothState?(central.state) }
    public func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral, advertisementData: [String:Any], rssi RSSI: NSNumber) { onDevice?(peripheral) }
    public func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) { peripheral.discoverServices(nil) }
    public func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) { ready = false; onError?(error ?? FisicaError(code: 1001,message: "BLE connect failed")); onLost?() }
    public func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        ready = false; writing = false; write = nil; notify = nil; outgoing.removeAll(); if let error { onError?(error) }; onLost?()
    }
    public func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        if let error { onError?(error); return }; for service in peripheral.services ?? [] { peripheral.discoverCharacteristics(nil, for: service) }
    }
    public func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        if let error { onError?(error); return }
        for c in service.characteristics ?? [] { if c.uuid == writeID { write = c }; if c.uuid == notifyID { notify = c } }
        if let n = notify, write != nil, !ready { peripheral.setNotifyValue(true, for: n) }
    }
    public func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
        if let error { onError?(error); return }
        if characteristic.uuid == notifyID && characteristic.isNotifying && !ready { ready = true; onReady?() }
    }
    public func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        if let error { onError?(error); return }; if characteristic.uuid == notifyID, let data = characteristic.value { onBytes?(Array(data)) }
    }
    public func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        writing = false; if let error { outgoing.removeAll(); onError?(error); return }; pump()
    }
    public func peripheralIsReady(toSendWriteWithoutResponse peripheral: CBPeripheral) { pump() }

}
