import Foundation
import CFisica

/// Three independent facts. Nothing here enables or disables SDK features.
public struct EntitlementStatus {
    /// Granted with genuine hardware; it has no date expiry.
    public let baseLicense: FisicaBaseLicense
    /// How recently the server confirmed this information (30 days at most).
    public let verification: FisicaVerification
    /// State of the verified company's contract, by server-signed dates only.
    public let contract: FisicaContract
    public let contractKind: FisicaContractKind
    public let company: String, contractReference: String, grants: [String]
    /// Unix seconds; 0 when not set.
    public let verifiedAt: UInt64, refreshBy: UInt64, contractStart: UInt64, contractEnd: UInt64, revokedAt: UInt64
    public let lastImport: FisicaEntitlementOutcome
    public let keyEnvironment: FisicaKeyEnvironment
    public let warnings: [FisicaEntitlementWarning]
}

/// One SDK instance's verified company/contract context. Calls are serialized.
/// Import a server-signed token or file; status and warnings never block features.
public final class FisicaEntitlement {
    private var handle: OpaquePointer?
    private let lock = NSRecursiveLock()
    private let clock: () -> UInt64
    /// Called whenever a new warning appears, so the app can show or log it.
    public var onWarning: (([FisicaEntitlementWarning], EntitlementStatus) -> Void)?
    private var warned: UInt64 = 0

    public init(core: FisicaCore, clock: @escaping () -> UInt64 = { UInt64(max(0, Date().timeIntervalSince1970)) }) throws {
        self.clock = clock
        try check(fisica_core_entitlement_create(&handle))
    }
    deinit { close() }
    public func close() { lock.lock(); defer { lock.unlock() }; fisica_core_entitlement_destroy(handle); handle = nil }
    private func open() throws -> OpaquePointer {
        guard let handle else { throw FisicaError(code: 6, message: "Closed entitlement context") }; return handle
    }

    /// Returns what happened to the token. A rejected or older token leaves verified information untouched.
    @discardableResult public func importToken(_ token: String) throws -> FisicaEntitlementOutcome {
        lock.lock(); defer { lock.unlock() }
        let bytes = Array(token.utf8); var outcome: UInt32 = 0
        try check(fisica_core_entitlement_import(try open(), bytes, UInt32(bytes.count), clock(), &outcome))
        notify()
        return FisicaEntitlementOutcome(rawValue: outcome) ?? .rejectedFormat
    }
    /// Offline setup: a file downloaded on another PC works on a device that was never online.
    @discardableResult public func importFile(_ url: URL) throws -> FisicaEntitlementOutcome {
        try importToken(String(decoding: try Data(contentsOf: url), as: UTF8.self))
    }
    private func text(_ field: FisicaEntitlementText) throws -> String {
        var output = [UInt8](repeating: 0, count: 4096), length: UInt32 = 0
        try check(fisica_core_entitlement_text(try open(), field.rawValue, &output, UInt32(output.count), &length))
        return String(decoding: output.prefix(Int(length)), as: UTF8.self)
    }
    public func status() throws -> EntitlementStatus {
        lock.lock(); defer { lock.unlock() }
        var values = [UInt64](repeating: 0, count: 12)
        try check(fisica_core_entitlement_status(try open(), clock(), &values, 12))
        return EntitlementStatus(
            baseLicense: .granted,
            verification: FisicaVerification(rawValue: UInt32(values[1])) ?? .none,
            contract: FisicaContract(rawValue: UInt32(values[2])) ?? .unverified,
            contractKind: FisicaContractKind(rawValue: UInt32(values[3])) ?? .none,
            company: try text(.company), contractReference: try text(.contract),
            grants: try text(.grants).split(separator: ",").map(String.init),
            verifiedAt: values[4], refreshBy: values[5], contractStart: values[6], contractEnd: values[7], revokedAt: values[8],
            lastImport: FisicaEntitlementOutcome(rawValue: UInt32(values[9])) ?? .none,
            keyEnvironment: FisicaKeyEnvironment(rawValue: UInt32(values[10])) ?? .none,
            warnings: FisicaEntitlementWarning.allCases.filter { values[11] & UInt64($0.rawValue) != 0 })
    }
    private func notify() {
        guard let status = try? status() else { return }
        let bits = status.warnings.reduce(UInt64(0)) { $0 | UInt64($1.rawValue) }
        let new = status.warnings.filter { warned & UInt64($0.rawValue) == 0 }
        warned = bits
        if !new.isEmpty { onWarning?(new, status) }
    }
    /// Record real feature use (connect, measure, analyze). Does nothing without a verified contract.
    public func markUsed() {
        lock.lock(); defer { lock.unlock() }
        guard let handle else { return }
        _ = fisica_core_entitlement_mark_used(handle, clock())
        notify()
    }
    /// Next pending usage report: bearer token and JSON body for the host to send.
    /// `reportID` is 16 random bytes naming this report for retry de-duplication only.
    public func pendingReport(reportID: [UInt8]) throws -> (token: String, body: [UInt8])? {
        lock.lock(); defer { lock.unlock() }
        var token = [UInt8](repeating: 0, count: 4096), body = [UInt8](repeating: 0, count: 256)
        var tokenLength: UInt32 = 0, bodyLength: UInt32 = 0
        let status = fisica_core_entitlement_report(try open(), reportID, UInt32(reportID.count), &token, UInt32(token.count),
                                                    &tokenLength, &body, UInt32(body.count), &bodyLength)
        if status == 3 { return nil }; try check(status)
        return (String(decoding: token.prefix(Int(tokenLength)), as: UTF8.self), Array(body.prefix(Int(bodyLength))))
    }
    /// Call only after the server confirmed the report; the local record is then cleared.
    public func reportDone() throws { lock.lock(); defer { lock.unlock() }; try check(fisica_core_entitlement_report_done(try open())) }
    /// Opaque state for the host to store. Restoring verifies every stored token again.
    public func exportState() throws -> [UInt8] {
        lock.lock(); defer { lock.unlock() }
        var output = [UInt8](repeating: 0, count: 262144), length: UInt32 = 0
        try check(fisica_core_entitlement_export(try open(), &output, UInt32(output.count), &length))
        return Array(output.prefix(Int(length)))
    }
    public func restoreState(_ state: [UInt8]) throws {
        lock.lock(); defer { lock.unlock() }
        try check(fisica_core_entitlement_restore(try open(), state, UInt32(state.count)))
        notify()
    }
}
