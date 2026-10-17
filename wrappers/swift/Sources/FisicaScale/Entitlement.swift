import Foundation
import CFisica

/// The license of one SDK instance as the native core decides it.
///
/// Full mode: an SDK key activated for this app (bundle ID) or this PC, within its key expiry
/// and its trial or contract. Basic mode is everything else: connection, measurement, settings,
/// weight and foot size keep working; raw data, every frame and report export need full mode.
public struct EntitlementStatus: Equatable {
    public let mode: FisicaMode
    /// Why the SDK is in basic mode; `.none` in full mode. `message` explains it.
    public let reason: FisicaModeReason
    /// Features open now; empty in basic mode.
    public let features: [FisicaFeature]
    /// The key's contract by its signed dates, kind and track (trial, extension, contract).
    public let contract: FisicaContract
    public let contractKind: FisicaContractKind
    public let track: FisicaTrack
    /// Attribution of the verified key; empty without one.
    public let company: String, contractReference: String, key: String, grants: [String]
    /// The app or PC the key is activated for (`.none` and empty until activated).
    public let bindType: FisicaBindType, bind: String
    /// This app's ID or this PC's hash as the core reads it (`.none` when it cannot).
    public let targetType: FisicaBindType, target: String
    /// Unix seconds; 0 when not set. `keyUntil`: the key's expiry; `fullUntil`: the end of full
    /// mode for its trial or contract (contract end plus grace).
    public let verifiedAt: UInt64, refreshBy: UInt64, keyUntil: UInt64, fullUntil: UInt64
    public let contractStart: UInt64, contractEnd: UInt64, revokedAt: UInt64
    public let lastImport: FisicaEntitlementOutcome
    public let keyEnvironment: FisicaKeyEnvironment
    public let warnings: [FisicaEntitlementWarning]
    /// FAC1 code to enter in the developer portal while the loaded key is not activated
    /// (for a device that is never online); nil otherwise.
    public let activationCode: String?
    public var isFull: Bool { mode == .full }
    public func has(_ feature: FisicaFeature) -> Bool { features.contains(feature) }
    /// What basic mode means here and what to do; nil in full mode.
    public var message: String? { reason.message }
}

/// Result of one exchange with the server.
public struct EntitlementSyncResult: Equatable {
    /// The key was activated for this app or PC, or renewed, in this exchange.
    public let activated: Bool, refreshed: Bool
    /// The network or storage problem, or nil. Never a license state.
    public let error: String?
}

/// What to show while pairing: an owner or admin enters `userCode` at `verificationURI`
/// (`verificationURIComplete` already holds the code). The code is valid for `expiresIn` seconds.
public struct PairingCode: Equatable {
    public let userCode: String, verificationURI: String, verificationURIComplete: String
    public let expiresIn: Int
}

/// How pairing ended. `.approved`: the key is loaded and stored.
public enum PairingResult: Equatable { case approved, denied, expired, cancelled }

public extension FisicaModeReason {
    /// Why the SDK is in basic mode and what to do; nil for `.none` (full mode).
    var message: String? {
        let basic = " Basic mode keeps connection, measurement, weight and foot size; raw data and full-rate frames need full mode."
        switch self {
        case .none: return nil
        case .noKey: return "No SDK key. Create one in the developer portal and import it." + basic
        case .legacyToken: return "This is a contract confirmation, not an SDK key. Create an SDK key in the developer portal." + basic
        case .notActivated: return "The SDK key is not activated yet: connect to the internet, or enter the activation code in the developer portal and import the file it gives." + basic
        case .noTarget: return "The SDK cannot read this app's ID or this PC's identity, so a key cannot be activated here." + basic
        case .otherTarget: return "This SDK key is activated for another app or PC." + basic
        case .revoked: return "The SDK key was released or its contract was revoked." + basic
        case .clockRollback: return "This device's clock is earlier than a time the SDK has already seen; correct the clock." + basic
        case .keyExpired: return "The SDK key expired: connect to renew it, or import a renewal file from the developer portal." + basic
        case .notStarted: return "The contract of this SDK key has not started yet." + basic
        case .fullEnded: return "The trial or contract of this SDK key has ended." + basic
        }
    }
}
public extension FisicaEntitlementWarning {
    var message: String {
        switch self {
        case .renewDue: return "The SDK key is due for renewal: connect to the internet or import a renewal file."
        case .grace: return "The contract has ended; full mode continues during the grace period."
        case .fullEnding: return "Full mode ends within 7 days (trial or contract end)."
        case .importRejected: return "The last SDK key or file was rejected (format, unknown signing key, signature or another app/PC)."
        case .testKey: return "The SDK key is signed with a TEST key; this is not a production license."
        }
    }
}

/// One SDK instance's license: its SDK key, the key's activation here, and renewal.
///
/// Signature checks, the app or PC a key is activated for and the full/basic decision are made
/// by the native core, which also limits what a measurement session hands out. This class stores
/// the key, activates it online, renews it and reports the status. Pass it to `FisicaClient`.
///
/// Online (with `endpoint`), an unactivated key is activated for this app or PC automatically
/// and renewed before it expires. Offline, enter `activationCode()` in the developer portal and
/// import the key file it gives. Network failures never change the license.
/// It does not track SDK use: the stored state is the signed key and the latest time seen.
public final class FisicaEntitlement: @unchecked Sendable {  // every use of the context is under its lock
    private var handle: OpaquePointer?
    private let lock = NSRecursiveLock()
    private let clock: () -> UInt64
    private let endpoint: URL?, storage: URL?, online: Bool, timeout: TimeInterval
    // Serial: one exchange with the server at a time.
    private let exchange = DispatchQueue(label: "fisica.entitlement.sync")
    private var nextSync = DispatchTime.now(), syncing = false
    private var syncError: String?
    private var announced: EntitlementStatus?
    private var statusHandler: ((EntitlementStatus) -> Void)?
    private var warningHandler: (([FisicaEntitlementWarning], EntitlementStatus) -> Void)?
    /// Changes whenever the loaded key may have changed, so a client applies it again.
    private(set) var revision = 0
    private static let activatePath = "/sdk/v1/entitlement/activate", refreshPath = "/sdk/v1/entitlement/refresh"
    private static let pairingPath = "/sdk/v1/pairing", pairingTokenPath = "/sdk/v1/pairing/token"
    /// The developer portal, for pairing.
    public static let defaultEndpoint = URL(string: "https://developer.fisicahealthcare.com")!

    /// Called when the status changes (import, activation, renewal, release, or time passing as
    /// seen by `checkStatus`), possibly on a background queue.
    public var onStatus: ((EntitlementStatus) -> Void)? {
        get { lock.lock(); defer { lock.unlock() }; return statusHandler }
        set { lock.lock(); statusHandler = newValue; lock.unlock() }
    }
    /// Called with the warnings that newly appeared, possibly on a background queue.
    public var onWarning: (([FisicaEntitlementWarning], EntitlementStatus) -> Void)? {
        get { lock.lock(); defer { lock.unlock() }; return warningHandler }
        set { lock.lock(); warningHandler = newValue; lock.unlock() }
    }
    /// Last storage or network problem, or nil. Never a license state.
    public var lastSyncError: String? { lock.lock(); defer { lock.unlock() }; return syncError }
    /// Per-user Application Support directory used when no storage directory is given.
    public static var defaultStorage: URL? {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?.appendingPathComponent("Fisica/sdk", isDirectory: true)
    }

    /// - Parameters:
    ///   - endpoint: Server base URL for activation, renewal and pairing. Without it no network is used.
    ///   - storage: Directory for the state file (one per seat); nil keeps state in memory only. The key stored
    ///     there for this app or PC (paired or imported earlier) is loaded at once.
    ///   - online: false keeps a configured endpoint unused.
    ///   - timeout: Seconds per server request.
    public init(core: FisicaCore, endpoint: URL? = nil, storage: URL? = FisicaEntitlement.defaultStorage, online: Bool = true,
                timeout: TimeInterval = 5, clock: @escaping () -> UInt64 = { UInt64(max(0, Date().timeIntervalSince1970)) }) throws {
        self.clock = clock; self.endpoint = endpoint; self.storage = storage; self.online = online; self.timeout = timeout
        try check(fisica_core_entitlement_create(&handle))
        if loadStored() { notify(); sync() }
    }
    /// The key stored for this app or PC, if any. The core names a state file from the company, the
    /// contract and the target, so a file it names again after restoring it belongs here. With several,
    /// a full-mode key wins, then the most recently verified one.
    private func loadStored() -> Bool {
        guard let storage, let files = try? FileManager.default.contentsOfDirectory(at: storage, includingPropertiesForKeys: nil) else { return false }
        var best: (full: Bool, verified: UInt64, state: [UInt8])?
        for file in files.sorted(by: { $0.lastPathComponent < $1.lastPathComponent })
            where file.lastPathComponent.hasPrefix("entitlement-") && file.pathExtension == "state" {
            guard let data = try? Data(contentsOf: file) else { continue }
            let state = [UInt8](data)
            var probe: OpaquePointer?
            guard fisica_core_entitlement_create(&probe) == 0 else { continue }
            defer { fisica_core_entitlement_destroy(probe) }
            var name = [UInt8](repeating: 0, count: 256), length: UInt32 = 0
            var values = [UInt64](repeating: 0, count: Int(FisicaLimit.statusFields.rawValue))
            guard fisica_core_entitlement_restore(probe, state, UInt32(state.count)) == 0,
                  fisica_core_entitlement_text(probe, FisicaEntitlementText.stateName.rawValue, &name, UInt32(name.count), &length) == 0,
                  String(decoding: name.prefix(Int(length)), as: UTF8.self) == file.lastPathComponent,
                  fisica_core_entitlement_status(probe, clock(), &values, UInt32(values.count)) == 0 else { continue }
            let full = values[0] == UInt64(FisicaMode.full.rawValue)
            if let current = best, (current.full && !full) || (current.full == full && current.verified >= values[8]) { continue }
            best = (full, values[8], state)
        }
        lock.lock(); defer { lock.unlock() }
        guard let best, let handle else { return false }
        guard fisica_core_entitlement_restore(handle, best.state, UInt32(best.state.count)) == 0 else { return false }
        revision += 1
        return true
    }
    deinit { close() }
    public func close() {
        lock.lock(); defer { lock.unlock() }
        fisica_core_entitlement_destroy(handle); handle = nil; revision += 1
    }
    private func open() throws -> OpaquePointer {
        guard let handle else { throw FisicaError(code: 6, message: "Closed entitlement context") }; return handle
    }
    /// Runs `body` with the core handle (nil: basic mode) and wall-clock seconds, serialized with
    /// every other use of the context.
    static func withLicense<T>(_ license: FisicaEntitlement?, _ body: (OpaquePointer?, UInt64) throws -> T) rethrows -> T {
        guard let license else { return try body(nil, UInt64(max(0, Date().timeIntervalSince1970))) }
        license.lock.lock(); defer { license.lock.unlock() }
        return try body(license.handle, license.clock())
    }
    /// True while full mode hands out every frame.
    var fullRate: Bool {
        lock.lock(); defer { lock.unlock() }
        guard let handle else { return false }
        var values = [UInt64](repeating: 0, count: Int(FisicaLimit.statusFields.rawValue))
        guard fisica_core_entitlement_status(handle, clock(), &values, UInt32(values.count)) == 0 else { return false }
        return values[2] & UInt64(FisicaFeature.fullRate.rawValue) != 0
    }

    /// Import an SDK key or the content of a key file (activation or renewal file). Returns what
    /// happened; a rejected or older key, or one activated for another app or PC, leaves the
    /// current key untouched. A key activated or renewed earlier and stored for the same company
    /// and contract wins over an older original key file. Online, a new key is activated here.
    @discardableResult public func importToken(_ token: String) throws -> FisicaEntitlementOutcome {
        let outcome: UInt32
        do {
            lock.lock(); defer { lock.unlock() }
            let bytes = Array(token.utf8), now = clock(); var result: UInt32 = 0
            try check(fisica_core_entitlement_import(try open(), bytes, UInt32(bytes.count), now, &result))
            outcome = result
            if result == FisicaEntitlementOutcome.accepted.rawValue {
                nextSync = .now()  // A new key may need activation now.
                if let file = stateFile(), let state = try? Data(contentsOf: file) {
                    let stored = [UInt8](state); var ignored: UInt32 = 0
                    if fisica_core_entitlement_restore(handle, stored, UInt32(stored.count)) == 0 {
                        _ = fisica_core_entitlement_import(handle, bytes, UInt32(bytes.count), now, &ignored)
                    }
                }
                save()
            }
            revision += 1
        }
        notify()
        if outcome == FisicaEntitlementOutcome.accepted.rawValue { sync() }
        return FisicaEntitlementOutcome(rawValue: outcome) ?? .rejectedFormat
    }
    /// Offline setup: a key file from the developer portal works on a device that is never online.
    @discardableResult public func importFile(_ url: URL) throws -> FisicaEntitlementOutcome {
        try importToken(String(decoding: try Data(contentsOf: url), as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines))
    }
    private func stateFile() -> URL? {
        // One file per seat on this device, named by the core from the company, the contract and this
        // app's or PC's target, so every wrapper on a PC finds the same file. Never a usage record.
        guard let storage, let name = try? text(.stateName), !name.isEmpty else { return nil }
        return storage.appendingPathComponent(name)
    }
    private func save() {
        guard let file = stateFile(), let state = try? exportState() else { return }
        do {
            try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
            try Data(state).write(to: file, options: .atomic)
        } catch { syncError = "storage: \(error.localizedDescription)" }
    }
    private func delete(_ file: URL?) {
        guard let file, FileManager.default.fileExists(atPath: file.path) else { return }
        do { try FileManager.default.removeItem(at: file) } catch { syncError = "storage: \(error.localizedDescription)" }
    }
    private func text(_ field: FisicaEntitlementText) throws -> String {
        var output = [UInt8](repeating: 0, count: 8192), length: UInt32 = 0
        try check(fisica_core_entitlement_text(try open(), field.rawValue, &output, UInt32(output.count), &length))
        return String(decoding: output.prefix(Int(length)), as: UTF8.self)
    }
    /// A text field the core may not have (no target, no unactivated key): nil then.
    private func optionalText(_ field: FisicaEntitlementText) -> String? {
        do { return try text(field) } catch { return nil }
    }

    public func status() throws -> EntitlementStatus {
        lock.lock(); defer { lock.unlock() }
        var values = [UInt64](repeating: 0, count: Int(FisicaLimit.statusFields.rawValue))
        try check(fisica_core_entitlement_status(try open(), clock(), &values, UInt32(values.count)))
        let reason = FisicaModeReason(rawValue: UInt32(values[1])) ?? .none
        return EntitlementStatus(
            mode: FisicaMode(rawValue: UInt32(values[0])) ?? .basic, reason: reason,
            features: FisicaFeature.allCases.filter { values[2] & UInt64($0.rawValue) != 0 },
            contract: FisicaContract(rawValue: UInt32(values[3])) ?? .unverified,
            contractKind: FisicaContractKind(rawValue: UInt32(values[4])) ?? .none,
            track: FisicaTrack(rawValue: UInt32(values[5])) ?? .none,
            company: try text(.company), contractReference: try text(.contract), key: try text(.key),
            grants: try text(.grants).split(separator: ",").map(String.init),
            bindType: FisicaBindType(rawValue: UInt32(values[6])) ?? .none, bind: try text(.bind),
            targetType: FisicaBindType(rawValue: UInt32(values[7])) ?? .none, target: try text(.target),
            verifiedAt: values[8], refreshBy: values[9], keyUntil: values[10], fullUntil: values[11],
            contractStart: values[12], contractEnd: values[13], revokedAt: values[14],
            lastImport: FisicaEntitlementOutcome(rawValue: UInt32(values[15])) ?? .none,
            keyEnvironment: FisicaKeyEnvironment(rawValue: UInt32(values[16])) ?? .none,
            warnings: FisicaEntitlementWarning.allCases.filter { values[17] & UInt64($0.rawValue) != 0 },
            activationCode: reason == .notActivated ? optionalText(.activationCode) : nil)
    }
    /// FAC1 code for activating the loaded key in the developer portal, for a device that is
    /// never online; nil without an unactivated key or when the core cannot read this app or PC.
    public func activationCode() -> String? {
        lock.lock(); defer { lock.unlock() }
        return optionalText(.activationCode)
    }
    /// Release the key activated here: the core forgets it and returns the FRC1 code to enter in
    /// the developer portal, and the stored key is deleted. The SDK is in basic mode afterwards.
    /// Throws `FisicaError` (invalid state) without a key activated for this app or PC.
    public func release() throws -> String {
        let code: String
        do {
            lock.lock(); defer { lock.unlock() }
            let file = stateFile()
            var output = [UInt8](repeating: 0, count: 1024), length: UInt32 = 0
            try check(fisica_core_entitlement_release(try open(), &output, UInt32(output.count), &length))
            code = String(decoding: output.prefix(Int(length)), as: UTF8.self)
            delete(file)
            revision += 1
        }
        notify()
        return code
    }

    private func notify() {
        lock.lock()
        guard handle != nil, let status = try? status() else { lock.unlock(); return }
        let previous = announced; announced = status
        let seen = previous?.warnings ?? []
        let new = status.warnings.filter { !seen.contains($0) }
        let onStatus = statusHandler, onWarning = warningHandler
        lock.unlock()
        if previous != status { onStatus?(status) }
        if !new.isEmpty { onWarning?(new, status) }
    }
    /// Re-evaluate the license for the current time and activate or renew in the background
    /// when due. Called from feature paths; it records nothing about the use and never blocks.
    public func checkStatus() {
        notify()
        sync()
    }

    private func send(_ path: String, token: String?, body: Data) throws -> (status: Int, data: Data) {
        guard let endpoint, let url = URL(string: endpoint.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + path) else {
            throw FisicaError(code: 1001, message: "Invalid endpoint")
        }
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: timeout)
        request.httpMethod = "POST"; request.httpBody = body
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let done = DispatchSemaphore(value: 0)
        var result: Result<(Int, Data), Error> = .failure(FisicaError(code: 1001, message: "No response"))
        URLSession.shared.dataTask(with: request) { data, response, error in
            if let error { result = .failure(error) }
            else { result = .success(((response as? HTTPURLResponse)?.statusCode ?? 0, data ?? Data())) }
            done.signal()
        }.resume()
        done.wait()
        return try result.get()
    }
    private func post(_ path: String, token: String?, body: Data) throws -> Data {
        let (status, data) = try send(path, token: token, body: body)
        guard (200..<300).contains(status) else {
            // A refused activation (key in use elsewhere, released, trial already used) is reported.
            throw FisicaError(code: 1001, message: "HTTP \(status) \(String(decoding: data.prefix(200), as: UTF8.self))")
        }
        return data
    }

    /// Get a key without one: the developer portal approves a code shown here (device authorization).
    ///
    /// Only this app's ID or this PC's hash is sent. `onCode` receives the code to show; an owner or
    /// admin of the organization enters it at `verificationURI` and approves it with a new or unused
    /// key. The key, already activated for this app or PC, then arrives, is loaded and stored, and later
    /// instances load it by themselves. Blocks until the code is approved, denied or expires, or
    /// `isCancelled` returns true; do not call it on the main thread (see the async `pair`). Throws
    /// without an endpoint, without a readable app ID or PC, or when pairing cannot start.
    public func pairNow(onCode: (PairingCode) -> Void, isCancelled: () -> Bool = { false }) throws -> PairingResult {
        guard endpoint != nil else { throw FisicaError(code: 6, message: "Pairing needs the developer portal endpoint") }
        let request: String? = { lock.lock(); defer { lock.unlock() }; return optionalText(.activationRequest) }()
        guard let request else { throw FisicaError(code: 6, message: FisicaModeReason.noTarget.message ?? "No target") }
        guard let device = try JSONSerialization.jsonObject(with: try post(FisicaEntitlement.pairingPath, token: nil, body: Data(request.utf8))) as? [String: Any],
              let deviceCode = device["device_code"] as? String, let userCode = device["user_code"] as? String,
              let page = device["verification_uri"] as? String, let complete = device["verification_uri_complete"] as? String,
              let expiresIn = device["expires_in"] as? Int else { throw FisicaError(code: 1001, message: "Unexpected server response") }
        onCode(PairingCode(userCode: userCode, verificationURI: page, verificationURIComplete: complete, expiresIn: expiresIn))
        let body = try JSONSerialization.data(withJSONObject: ["device_code": deviceCode])
        var interval = max(0, (device["interval"] as? NSNumber)?.doubleValue ?? 5)
        // A key approved just before the code expires can still be received for a short while.
        let deadline = Date().addingTimeInterval(TimeInterval(expiresIn + 30))
        while true {
            var waited = 0.0, wait = max(0, min(interval, deadline.timeIntervalSinceNow))
            repeat {
                if isCancelled() { return .cancelled }
                let step = min(0.2, wait - waited)
                if step > 0 { Thread.sleep(forTimeInterval: step) }
                waited += step
            } while waited < wait
            if isCancelled() { return .cancelled }
            if let reply = try? send(FisicaEntitlement.pairingTokenPath, token: nil, body: body) {
                let answer = (try? JSONSerialization.jsonObject(with: reply.data)) as? [String: Any]
                if reply.status == 200, let token = answer?["token"] as? String {
                    let outcome = try importToken(token)
                    guard outcome == .accepted else { throw FisicaError(code: 6, message: "The paired key was not accepted here (\(outcome))") }
                    return .approved
                }
                switch answer?["error"] as? String {
                case "access_denied": return .denied
                case "expired_token": return .expired
                case "slow_down": interval += 5
                default: break
                }
            } else {
                lock.lock(); syncError = "network: pairing poll failed"; lock.unlock()  // keep polling until the code expires
            }
            if Date() >= deadline { return .expired }
        }
    }
    /// `pairNow` off the calling thread; cancelling the task stops waiting (`.cancelled`).
    @available(macOS 10.15, iOS 13.0, *)
    public func pair(onCode: @escaping (PairingCode) -> Void) async throws -> PairingResult {
        let cancelled = PairingCancel()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                DispatchQueue.global(qos: .utility).async {
                    continuation.resume(with: Result { try self.pairNow(onCode: onCode, isCancelled: { cancelled.isSet }) })
                }
            }
        } onCancel: { cancelled.set() }
    }
    /// What the server should do now: activate (with the core's request body), renew, or nothing.
    private func due(force: Bool) -> (path: String, token: String, body: Data)? {
        lock.lock(); defer { lock.unlock() }
        guard handle != nil, let status = try? status(), let token = try? text(.token), !token.isEmpty else { return nil }
        let refresh = (FisicaEntitlement.refreshPath, token, Data("{}".utf8))
        switch status.reason {
        case .noKey, .legacyToken, .otherTarget, .noTarget, .revoked: return force ? refresh : nil
        case .notActivated:
            // Exactly the core's request: this app's ID or this PC's hash, nothing else.
            guard let request = optionalText(.activationRequest) else { return force ? refresh : nil }
            return (FisicaEntitlement.activatePath, token, Data(request.utf8))
        case .keyExpired, .fullEnded, .notStarted: return refresh
        default: return status.warnings.contains(.renewDue) || force ? refresh : nil
        }
    }
    private func exchangeOnce(force: Bool) -> EntitlementSyncResult {
        guard online, endpoint != nil, let next = due(force: force) else {
            return EntitlementSyncResult(activated: false, refreshed: false, error: nil)
        }
        var accepted = false, failure: String?
        do {
            let response = try JSONSerialization.jsonObject(with: try post(next.path, token: next.token, body: next.body)) as? [String: Any]
            guard let issued = response?["token"] as? String else { throw FisicaError(code: 1001, message: "Unexpected server response") }
            lock.lock()
            if let handle {
                let bytes = Array(issued.utf8); var outcome: UInt32 = 0
                accepted = fisica_core_entitlement_import(handle, bytes, UInt32(bytes.count), clock(), &outcome) == 0 &&
                    outcome == FisicaEntitlementOutcome.accepted.rawValue
                if (try? status())?.reason == .revoked {
                    // The server reports this key released: the device lets go of it.
                    delete(stateFile())
                } else if accepted { save() }
                revision += 1
            }
            syncError = nil; nextSync = .now() + .seconds(3600)
            lock.unlock()
        } catch {
            // A communication failure is not an expiry, a release or a revocation.
            failure = "network: \((error as? FisicaError)?.message ?? error.localizedDescription)"
            lock.lock(); syncError = failure; lock.unlock()
        }
        notify()
        let activation = next.path == FisicaEntitlement.activatePath
        return EntitlementSyncResult(activated: accepted && activation, refreshed: accepted && !activation, error: failure)
    }
    /// Activate or renew now when due (force: renew anyway), on the calling thread after any
    /// background exchange; do not call it on the main thread. Never throws.
    @discardableResult public func syncNow(force: Bool = false) -> EntitlementSyncResult { exchange.sync { exchangeOnce(force: force) } }
    /// Background `syncNow`: at most once a minute until an exchange succeeds, then hourly unless
    /// forced. Never blocks or throws into the caller.
    public func sync(force: Bool = false) {
        guard online, endpoint != nil else { return }
        lock.lock()
        if syncing || (!force && DispatchTime.now() < nextSync) { lock.unlock(); return }
        syncing = true; nextSync = .now() + .seconds(60)
        lock.unlock()
        exchange.async { [weak self] in
            guard let self else { return }
            _ = self.exchangeOnce(force: force)
            self.lock.lock(); self.syncing = false; self.lock.unlock()
        }
    }
    /// Opaque state for the host to store: the verified key and the latest time seen. Restoring
    /// verifies the stored key again. It is not a record of SDK use.
    public func exportState() throws -> [UInt8] {
        lock.lock(); defer { lock.unlock() }
        var output = [UInt8](repeating: 0, count: 262144), length: UInt32 = 0
        try check(fisica_core_entitlement_export(try open(), &output, UInt32(output.count), &length))
        return Array(output.prefix(Int(length)))
    }
    public func restoreState(_ state: [UInt8]) throws {
        do {
            lock.lock(); defer { lock.unlock() }
            try check(fisica_core_entitlement_restore(try open(), state, UInt32(state.count)))
            revision += 1
        }
        notify()
    }
}

/// A cancellation flag shared between a task and the thread that waits for pairing.
private final class PairingCancel: @unchecked Sendable {
    private let lock = NSLock(); private var value = false
    var isSet: Bool { lock.lock(); defer { lock.unlock() }; return value }
    func set() { lock.lock(); value = true; lock.unlock() }
}
