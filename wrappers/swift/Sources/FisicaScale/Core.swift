import Foundation
import CFisica

public struct FisicaError: Error { public let code: Int32; public let message: String }
private func check(_ status: Int32) throws {
    if status != 0 { throw FisicaError(code: status, message: String(cString: fisica_core_error_message(status))) }
}
public struct Measurement {
    public let raw: [Double], display: [Double], metrics: [Double]
    public var weight: Double { metrics[0] }
    public var batteryValid: Bool { false }
}
public final class FisicaCore {
    public let version: String, policyJSON: String
    public init(onDeprecation: (String)->Void = { NSLog("Fisica: %@", $0) }) throws {
        let abi = fisica_core_abi_version()
        guard abi >> 16 == 1 && abi & 65535 >= 3 else {
            throw FisicaError(code: 12, message: "Require C ABI 1.3+")
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
    public func decode(_ bytes: [UInt8]) throws -> Measurement {
        var raw = [Double](repeating: 0, count: 638), display = [Double](repeating: 0, count: 841), metrics = [Double](repeating: 0, count: 2)
        try check(fisica_core_decode_packet(bytes, UInt32(bytes.count), &raw, 638, &display, 841, &metrics, 2))
        return Measurement(raw: raw, display: display, metrics: metrics)
    }
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
}
/// Calls are serialized per handle. Host supplies monotonic ms, ticks and transport.
public final class FisicaSession {
    private var handle: OpaquePointer?
    private let lock = NSRecursiveLock()
    private let core: FisicaCore
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
    public func poll() throws -> SessionEvent? {
        lock.lock(); defer { lock.unlock() }; _ = try time(0)
        var metadata = [UInt32](repeating: 0, count: 8), bytes = [UInt8](repeating: 0, count: 4102)
        let status = fisica_core_session_poll(handle, &metadata, 8, &bytes, UInt32(bytes.count))
        if status == 3 { return nil }; try check(status)
        return SessionEvent(metadata: metadata, payload: Array(bytes.prefix(Int(metadata[7]))))
    }
    public func drain() throws -> [SessionEvent] { var events: [SessionEvent] = []; while let e = try poll() { events.append(e) }; return events }
}
