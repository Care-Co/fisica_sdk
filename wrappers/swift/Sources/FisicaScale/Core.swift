import Foundation
import CFisica

public struct FisicaError: Error, LocalizedError {
    public let code: Int32; public let message: String
    public var errorDescription: String? { message }
}
/// A feature that needs full mode (`FisicaStatus.featureNotLicensed`): an SDK key activated
/// for this app or PC whose features include the one asked for. It is not a measurement,
/// transport or input error; `FisicaEntitlement.status()` says why the SDK is in basic mode.
public struct FisicaLicenseError: Error, LocalizedError {
    public let code: Int32; public let message: String
    public var errorDescription: String? { message }
}
func check(_ status: Int32) throws {
    guard status != 0 else { return }
    let message = String(cString: fisica_core_error_message(status))
    if status == Int32(FisicaStatus.featureNotLicensed.rawValue) { throw FisicaLicenseError(code: status, message: message) }
    throw FisicaError(code: status, message: message)
}
/// One measurement frame. `display` (the processed 29x29 grid, row-major) and `weight` (kg)
/// come in every mode. `raw` (the 29x22 sensor grid) and `packet` (the 645 bytes as received)
/// are full-mode data: present only while the license opens raw data, otherwise nil.
/// Values are relative sensor units, not calibrated kPa.
public struct Measurement {
    public static let displayCells = 841, rawCells = 638, packetBytes = 645
    public let weight: Double
    public let display: [Double]
    public let raw: [Double]?
    public let packet: [UInt8]?
    public var hasRawData: Bool { raw != nil && packet != nil }
    /// Packets carry no battery field; read the level with `FisicaClient.readBattery`.
    public var batteryValid: Bool { false }
    public init(weight: Double, display: [Double], raw: [Double]? = nil, packet: [UInt8]? = nil) {
        self.weight = weight; self.display = display; self.raw = raw; self.packet = packet
    }
    /// Fields of a session MEASUREMENT payload (ABI 2): 842 little-endian doubles [weight,
    /// 29x29 display], then, only with raw data, 638 doubles (29x22) and the 645-byte packet.
    public init?(sessionPayload payload: [UInt8]) {
        let head = 8 * (1 + Measurement.displayCells), full = head + 8 * Measurement.rawCells + Measurement.packetBytes
        guard payload.count >= head else { return nil }
        let values = payload.withUnsafeBytes { bytes in
            (0..<(payload.count >= full ? 1 + Measurement.displayCells + Measurement.rawCells : 1 + Measurement.displayCells)).map {
                Double(bitPattern: UInt64(littleEndian: bytes.loadUnaligned(fromByteOffset: 8 * $0, as: UInt64.self)))
            }
        }
        weight = values[0]
        display = Array(values[1...Measurement.displayCells])
        if payload.count >= full {
            raw = Array(values[(1 + Measurement.displayCells)...])
            packet = Array(payload[(full - Measurement.packetBytes)..<full])
        } else { raw = nil; packet = nil }
    }
}
public final class FisicaCore {
    public let version: String, policyJSON: String
    public init(onDeprecation: (String)->Void = { NSLog("Fisica: %@", $0) }) throws {
        let abi = fisica_core_abi_version()
        guard abi >> 16 == 2 else {
            throw FisicaError(code: 12, message: "Require C ABI 2.x")
        }
        var notices: UInt32 = 0
        try check(fisica_core_check_wrapper("swift", FisicaRelease.sdkVersion, FisicaRelease.wrapperVersion, &notices))
        if notices & 1 != 0 { onDeprecation("SDK \(FisicaRelease.sdkVersion) is deprecated") }
        if notices & 2 != 0 { onDeprecation("Swift wrapper \(FisicaRelease.wrapperVersion) is deprecated") }
        version = String(cString: fisica_core_version())
        policyJSON = String(cString: fisica_core_policy_json())
    }
    public func command(_ name: String, value: Double = 0, payload: [UInt8] = []) throws -> [UInt8] {
        guard !name.contains("\0") else { throw FisicaError(code: 1, message: "NUL in command") }
        var output = [UInt8](repeating: 0, count: 4102), length: UInt32 = 0
        try check(fisica_core_command(name, value, payload, UInt32(payload.count), &output, UInt32(output.count), &length))
        return Array(output.prefix(Int(length)))
    }
    /// Raw and display grids of one 645-byte packet. They are full-mode data: this needs a
    /// license whose features include raw data and throws `FisicaLicenseError` otherwise
    /// (also without a license). A `FisicaClient` hands out display frames in every mode.
    public func decode(_ bytes: [UInt8], license: FisicaEntitlement? = nil) throws -> Measurement {
        var raw = [Double](repeating: 0, count: Measurement.rawCells), display = [Double](repeating: 0, count: Measurement.displayCells)
        var metrics = [Double](repeating: 0, count: 2)
        try check(FisicaEntitlement.withLicense(license) { handle, now in
            fisica_core_decode_packet(handle, now, bytes, UInt32(bytes.count), &raw, UInt32(raw.count),
                                      &display, UInt32(display.count), &metrics, UInt32(metrics.count))
        })
        return Measurement(weight: metrics[0], display: display, raw: raw, packet: bytes)
    }
    /// Payload bytes of a fixed or variable-length command response.
    public func responsePayload(_ packet: [UInt8]) throws -> [UInt8] {
        var output = [UInt8](repeating: 0, count: 4096), length: UInt32 = 0
        try check(fisica_core_response_payload(packet, UInt32(packet.count), &output, UInt32(output.count), &length))
        return Array(output.prefix(Int(length)))
    }
    /// Weight and foot dimensions from measurement packets as received: `[weight_kg,
    /// left_width_cm, left_length_cm, right_width_cm, right_length_cm]`, or nil without
    /// a stable measurement. The analysis itself runs in the shared core; it needs no license.
    public func analyzePackets(_ packets: [[UInt8]]) throws -> [Double]? {
        guard !packets.isEmpty, packets.allSatisfy({ $0.count == Measurement.packetBytes }) else {
            throw FisicaError(code: 1, message: "Expected 645-byte measurement packets")
        }
        var result = [Double](repeating: 0, count: 5)
        let status = fisica_core_analyze_packets(packets.flatMap { $0 }, UInt32(packets.count), &result, 5)
        if status == 3 { return nil }; try check(status); return result
    }
    /// The same analysis from display grids and per-frame weights (e.g. collected frames).
    public func analyze(_ matrices: [Double], weights: [Double], rows: UInt32, cols: UInt32) throws -> [Double]? {
        guard UInt64(weights.count) * UInt64(rows) * UInt64(cols) == matrices.count else {
            throw FisicaError(code: 1, message: "Matrix length mismatch")
        }
        var result = [Double](repeating: 0, count: 5)
        let status = fisica_core_analyze(matrices, weights, UInt32(weights.count), rows, cols, &result, 5)
        if status == 3 { return nil }; try check(status); return result
    }
}
public struct SessionEvent {
    public let metadata: [UInt32], payload: [UInt8]
    public var kind: UInt32 { metadata[0] }
    public var state: UInt32 { metadata[1] }
    public var status: UInt32 { metadata[2] }
    public var deviceError: UInt32 { metadata[3] }
    public var operation: UInt32 { metadata[4] }
    /// The frame of a MEASUREMENT event (raw grid and packet only when the license opens raw data).
    public var measurement: Measurement? {
        kind == FisicaSessionEvent.measurement.rawValue ? Measurement(sessionPayload: payload) : nil
    }
}
/// Calls are serialized per handle. Host supplies monotonic ms, ticks and transport.
/// The session starts in basic mode: apply a license with `license(_:nowMs:)` before START.
public final class FisicaSession {
    private var handle: OpaquePointer?
    private let lock = NSRecursiveLock()
    private let core: FisicaCore
    // A buffer this large holds every event's payload (ABI 2 measurement frames included).
    private var buffer = [UInt8](repeating: 0, count: Int(FisicaLimit.eventBytes.rawValue))
    public init(core: FisicaCore) throws { self.core = core; try check(fisica_core_session_create(&handle)) }
    deinit { close() }
    public func close() { lock.lock(); defer { lock.unlock() }; fisica_core_session_destroy(handle); handle = nil }
    private func time(_ now: Int64) throws -> UInt64 {
        guard handle != nil && now >= 0 else { throw FisicaError(code: 6, message: "Closed session or invalid clock") }; return UInt64(now)
    }
    public func action(_ action: UInt32, value: Int64 = 0, nowMs: Int64) throws {
        lock.lock(); defer { lock.unlock() }; let now = try time(nowMs)
        try check(fisica_core_session_action(handle, action, value, now))
    }
    public func tick(_ nowMs: Int64) throws { lock.lock(); defer { lock.unlock() }; try check(fisica_core_session_tick(handle, try time(nowMs))) }
    public func feed(_ bytes: [UInt8], nowMs: Int64) throws {
        lock.lock(); defer { lock.unlock() }; try check(fisica_core_session_feed(handle, bytes, UInt32(bytes.count), try time(nowMs)))
    }
    public func command(_ name: String, value: Double = 0, payload: [UInt8] = [], nowMs: Int64) throws {
        lock.lock(); defer { lock.unlock() }
        guard !name.contains("\0") else { throw FisicaError(code: 1, message: "NUL in command") }
        try check(fisica_core_session_command(handle, name, value, payload, UInt32(payload.count), try time(nowMs)))
    }
    /// Copy the license decision of `entitlement` into this session (nil: basic mode). Full mode
    /// hands out every frame with raw data; basic mode at most one display frame per
    /// `FisicaLimit.basicFrameIntervalMs`. Call it again after importing, activating or renewing a key.
    public func license(_ entitlement: FisicaEntitlement?, nowMs: Int64) throws {
        lock.lock(); defer { lock.unlock() }; let now = try time(nowMs)
        try check(FisicaEntitlement.withLicense(entitlement) { license, seconds in
            fisica_core_session_license(handle, license, seconds, now)
        })
    }
    /// Weight and foot size from every measurement packet since the last START, in any mode:
    /// `[weight_kg, left_width_cm, left_length_cm, right_width_cm, right_length_cm]`, or nil.
    public func analyze() throws -> [Double]? {
        lock.lock(); defer { lock.unlock() }; _ = try time(0)
        var result = [Double](repeating: 0, count: 5)
        let status = fisica_core_session_analyze(handle, &result, 5)
        if status == 3 { return nil }; try check(status); return result
    }
    public func poll() throws -> SessionEvent? {
        lock.lock(); defer { lock.unlock() }; _ = try time(0)
        var metadata = [UInt32](repeating: 0, count: 8)
        let status = fisica_core_session_poll(handle, &metadata, 8, &buffer, UInt32(buffer.count))
        if status == 3 { return nil }; try check(status)
        return SessionEvent(metadata: metadata, payload: Array(buffer.prefix(Int(metadata[7]))))
    }
    public func drain() throws -> [SessionEvent] { var events: [SessionEvent] = []; while let e = try poll() { events.append(e) }; return events }
}
