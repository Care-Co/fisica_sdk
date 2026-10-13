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
    private let endpoint: URL?, storage: URL?, online: Bool
    // Serial: one exchange at a time, so a report is never confirmed twice.
    private let exchange = DispatchQueue(label: "fisica.entitlement.sync")
    private var nextSync = DispatchTime.now(), syncing = false
    /// Called whenever a new warning appears, so the app can show or log it.
    public var onWarning: (([FisicaEntitlementWarning], EntitlementStatus) -> Void)?
    private var warned: UInt64 = 0
    /// Last storage or network problem, or nil. Never an entitlement state.
    public private(set) var lastSyncError: String?
    /// Per-user Application Support directory used when no storage directory is given.
    public static var defaultStorage: URL? {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?.appendingPathComponent("Fisica/sdk", isDirectory: true)
    }

    /// - Parameters:
    ///   - endpoint: Server base URL for online confirmation and the minimal usage report. Without it no network is used.
    ///   - storage: Directory for the state file; nil keeps state in memory only.
    ///   - online: false keeps a configured endpoint unused.
    public init(core: FisicaCore, endpoint: URL? = nil, storage: URL? = FisicaEntitlement.defaultStorage, online: Bool = true,
                clock: @escaping () -> UInt64 = { UInt64(max(0, Date().timeIntervalSince1970)) }) throws {
        self.clock = clock; self.endpoint = endpoint; self.storage = storage; self.online = online
        try check(fisica_core_entitlement_create(&handle))
    }
    deinit { close() }
    public func close() { lock.lock(); defer { lock.unlock() }; fisica_core_entitlement_destroy(handle); handle = nil }
    private func open() throws -> OpaquePointer {
        guard let handle else { throw FisicaError(code: 6, message: "Closed entitlement context") }; return handle
    }

    /// Returns what happened to the token. A rejected or older token leaves verified information untouched.
    @discardableResult public func importToken(_ token: String) throws -> FisicaEntitlementOutcome {
        lock.lock()
        let bytes = Array(token.utf8); var outcome: UInt32 = 0
        do {
            try check(fisica_core_entitlement_import(try open(), bytes, UInt32(bytes.count), clock(), &outcome))
        } catch { lock.unlock(); throw error }
        if outcome == FisicaEntitlementOutcome.accepted.rawValue {
            // Stored usage and a possibly newer confirmation win over an older file.
            if let file = stateFile(), let state = try? Data(contentsOf: file) {
                let stored = [UInt8](state); var ignored: UInt32 = 0
                if fisica_core_entitlement_restore(handle, stored, UInt32(stored.count)) == 0 {
                    _ = fisica_core_entitlement_import(handle, bytes, UInt32(bytes.count), clock(), &ignored)
                }
            }
            save()
        }
        notify()
        lock.unlock()
        if outcome == FisicaEntitlementOutcome.accepted.rawValue { sync() }
        return FisicaEntitlementOutcome(rawValue: outcome) ?? .rejectedFormat
    }
    private func stateFile() -> URL? {
        guard let storage, let company = try? text(.company), !company.isEmpty, let contract = try? text(.contract) else { return nil }
        // Named by company and contract, the only attribution unit; never an installation or device id.
        let name = String("\(company)-\(contract)".map { $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "_" || $0 == "-") ? $0 : "_" })
        return storage.appendingPathComponent("entitlement-\(name).state")
    }
    private func save() {
        guard let file = stateFile(), let state = try? exportState() else { return }
        do {
            try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
            try Data(state).write(to: file, options: .atomic)
        } catch { lastSyncError = "storage: \(error.localizedDescription)" }
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
        lock.lock()
        guard let handle else { lock.unlock(); return }
        _ = fisica_core_entitlement_mark_used(handle, clock())
        save(); notify()
        lock.unlock()
        sync()
    }

    private func post(_ path: String, token: String, body: Data) throws -> Data {
        guard let endpoint, let url = URL(string: endpoint.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + path) else {
            throw FisicaError(code: 1001, message: "Invalid endpoint")
        }
        var request = URLRequest(url: url, timeoutInterval: 5)
        request.httpMethod = "POST"; request.httpBody = body
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let done = DispatchSemaphore(value: 0)
        var result: Result<Data, Error> = .failure(FisicaError(code: 1001, message: "No response"))
        URLSession.shared.dataTask(with: request) { data, response, error in
            if let error { result = .failure(error) }
            else if let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) { result = .success(data ?? Data()) }
            else { result = .failure(FisicaError(code: 1001, message: "HTTP \((response as? HTTPURLResponse)?.statusCode ?? 0)")) }
            done.signal()
        }.resume()
        done.wait()
        return try result.get()
    }
    private func exchangeOnce() -> String? {
        guard online, endpoint != nil else { return nil }
        do {
            lock.lock(); let token = handle == nil ? "" : ((try? text(.token)) ?? ""); lock.unlock()
            if token.isEmpty { return nil }  // Nothing verified: nothing to refresh, nothing attributable to report.
            let response = try JSONSerialization.jsonObject(with: try post("/sdk/v1/entitlement/refresh", token: token, body: Data("{}".utf8))) as? [String: Any]
            guard let refreshed = response?["token"] as? String else { throw FisicaError(code: 1001, message: "Unexpected refresh response") }
            lock.lock()
            if handle != nil { let bytes = Array(refreshed.utf8); var ignored: UInt32 = 0; _ = fisica_core_entitlement_import(handle, bytes, UInt32(bytes.count), clock(), &ignored) }
            lock.unlock()
            while let report = try pendingReport(reportID: (0..<16).map { _ in UInt8.random(in: 0...255) }) {
                _ = try post("/sdk/v1/usage", token: report.token, body: Data(report.body))
                try reportDone()  // Cleared only after the server confirmed.
            }
            lastSyncError = nil
        } catch { lastSyncError = "network: \((error as? FisicaError)?.message ?? error.localizedDescription)" }
        lock.lock(); if handle != nil { save(); notify() }; lock.unlock()
        return lastSyncError
    }
    /// Online confirmation, then the pending usage reports, on the calling thread (do not call on the
    /// main thread). Returns nil or the problem. A failure changes no entitlement state: it is never
    /// an expiry or a revocation.
    @discardableResult public func syncNow() -> String? { exchange.sync { exchangeOnce() } }
    /// Background `syncNow`, at most hourly unless forced. Never blocks or throws into the caller.
    public func sync(force: Bool = false) {
        guard online, endpoint != nil else { return }
        lock.lock()
        if syncing || (!force && DispatchTime.now() < nextSync) { lock.unlock(); return }
        syncing = true; nextSync = .now() + .seconds(3600)
        lock.unlock()
        exchange.async { [weak self] in
            guard let self else { return }
            _ = self.exchangeOnce()
            self.lock.lock(); self.syncing = false; self.lock.unlock()
        }
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
