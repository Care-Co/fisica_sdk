import Foundation

/// Byte link to one Scale. The app or SDK adapter owns the OS I/O; no protocol rules here.
/// Deliver every callback on the client's queue.
public protocol FisicaTransport: AnyObject {
    /// The Scale link this transport carries, or nil when it can carry either; then
    /// `FisicaClient.connect` must be given the link. A different link is rejected before anything is sent.
    var link: FisicaLink? { get }
    /// Reads and writes both work.
    var onReady: (() -> Void)? { get set }
    var onBytes: (([UInt8]) -> Void)? { get set }
    /// The link ended or could not be established.
    var onLost: (() -> Void)? { get set }
    /// A write did not reach the Scale; the command outcome is unknown.
    var onSendFailure: ((Error) -> Void)? { get set }
    var onError: ((Error) -> Void)? { get set }
    func open()
    func send(_ bytes: [UInt8])
    func close()
}
public extension FisicaTransport { var link: FisicaLink? { nil } }

/// Scripted link for development and tests without hardware: request bytes -> response chunks.
/// It contains no device behavior; unknown requests are recorded and get no reply.
public final class MockTransport: FisicaTransport {
    public var onReady: (() -> Void)?, onBytes: (([UInt8]) -> Void)?, onLost: (() -> Void)?
    public var onSendFailure: ((Error) -> Void)?, onError: ((Error) -> Void)?
    /// Set to model a transport that carries only one link kind.
    public var link: FisicaLink?
    public var exchanges: [[UInt8]: [[UInt8]]]
    public private(set) var sent: [[UInt8]] = []
    public private(set) var isOpen = false
    /// Set false to model a link that never becomes ready.
    public var becomesReady = true
    public init(exchanges: [[UInt8]: [[UInt8]]] = [:]) { self.exchanges = exchanges }
    public func open() { isOpen = true; if becomesReady { onReady?() } }
    public func send(_ bytes: [UInt8]) {
        guard isOpen else { onSendFailure?(FisicaError(code: 1001, message: "Mock transport is closed")); return }
        sent.append(bytes)
        for chunk in exchanges[bytes] ?? [] { onBytes?(chunk) }
    }
    public func feed(_ bytes: [UInt8]) { onBytes?(bytes) }
    public func drop() { isOpen = false; onLost?() }
    public func close() { isOpen = false }
}

/// A command the Scale rejected or did not confirm. `code` is the SDK status and
/// `deviceCode` the Scale's own 16-bit code.
public struct FisicaCommandError: Error {
    public let event: SessionEvent
    public var code: UInt32 { event.status }
    public var deviceCode: UInt32 { event.deviceError }
}

/// Result of the shared analysis of a measurement. Centimetres use the nominal 1 cm cell;
/// values are not clinically calibrated. Weight and foot size work in every license mode and
/// come from every packet the core received, whatever `frames` holds.
public struct SessionReport {
    public let weight: Double, leftWidth: Double, leftLength: Double, rightWidth: Double, rightLength: Double
    /// The frames the client received: every packet in full mode, at most one display frame a
    /// second without raw data in basic mode.
    public let frames: [Measurement]
    /// The client's last `readBattery` reading in percent, or nil. Packets carry no battery field.
    public let battery: Int?
    public var frameCount: Int { frames.count }
    public var batteryValid: Bool { battery != nil }
}

/// Connect, measure, stop and report over any transport. Command order, ACK waits,
/// timeouts and recovery decisions live in the shared core; this class only runs the
/// clock, moves bytes and turns core events into callbacks.
/// Every callback and completion runs on `queue`; read `frames` and call `report()` there.
/// Keep the client in a property until `close` completes: the transport holds it only weakly, and
/// a client released mid-operation closes the link and cancels what it was waiting for.
///
/// The license decides what the core hands out. Without an `entitlement`, or in basic mode,
/// `onFrame` gets at most one display frame (29x29 grid and weight) a second without raw data;
/// full mode (an SDK key activated for this app or PC) gets every packet with the raw grid and
/// packet. The client applies the license before each start, when it changes and every 30 s.
public final class FisicaClient {
    public typealias Link = FisicaLink
    public typealias Completion = (Result<SessionEvent, Error>) -> Void
    public let core: FisicaCore
    public let transport: FisicaTransport
    public let entitlement: FisicaEntitlement?
    public var onFrame: ((Measurement) -> Void)?
    public var onEvent: ((SessionEvent) -> Void)?
    /// The link dropped. Reopen the same Scale with `connect`; the core resumes a measurement it still wants.
    public var onLinkLost: (() -> Void)?
    public var onError: ((Error) -> Void)?
    /// Frames of the current measurement. `start` clears them; a reconnect keeps them.
    /// Only the latest `maxFrames` are kept. In basic mode this is a sample (one a second).
    public private(set) var frames: [Measurement] = []
    /// Oldest frames dropped beyond `maxFrames` since the last `start` or `clearFrames`.
    public private(set) var droppedFrames = 0
    /// How many recent frames to keep, 1...`FisicaLimit.analysisFrames` (the most one
    /// analysis accepts). This also bounds memory in long sessions.
    public var maxFrames = Int(FisicaLimit.analysisFrames.rawValue) {
        didSet { maxFrames = min(max(maxFrames, 1), Int(FisicaLimit.analysisFrames.rawValue)); trimFrames() }
    }
    public private(set) var firmwareVersion: String?
    /// Battery level in percent from the last `readBattery`, or nil. Measurement packets carry no battery field.
    public private(set) var battery: Int?
    public private(set) var state: FisicaSessionState = .disconnected

    private let queue: DispatchQueue
    private let session: FisicaSession
    private let started = DispatchTime.now()
    private var waiters: [UInt32: Completion] = [:]
    private var timer: DispatchSourceTimer?
    private var readyDeadline: DispatchWorkItem?
    private var linkReady = false, closed = false, opened = false
    // License as applied to the session: when, and which revision.
    private var licensedAt: Int64 = 0, licenseRevision = -1
    // The core's analysis of the last measurement, kept when the session is released.
    private var finalAnalysis: [Double]?
    private static let licenseInterval: Int64 = 30_000

    public init(core: FisicaCore, transport: FisicaTransport, entitlement: FisicaEntitlement? = nil, queue: DispatchQueue = .main) throws {
        self.core = core; self.transport = transport; self.entitlement = entitlement; self.queue = queue
        session = try FisicaSession(core: core)
    }
    /// Released without `close` (nothing held the client but an operation in flight, say): the link is
    /// closed and every pending completion fails with `cancelled`, on `queue` as always. Keep the client
    /// in a property until it is closed.
    deinit {
        timer?.cancel(); readyDeadline?.cancel()
        if !closed {
            // The transport's callbacks hold the client weakly; they do nothing from now on.
            let transport = self.transport, pending = waiters, opened = self.opened
            if opened || !pending.isEmpty {
                queue.async {
                    if opened { transport.close() }
                    for waiter in pending.values {
                        waiter(.failure(FisicaError(code: Int32(FisicaStatus.cancelled.rawValue), message: "Client released without close")))
                    }
                }
            }
        }
        session.close()
    }

    private var now: Int64 { Int64((DispatchTime.now().uptimeNanoseconds - started.uptimeNanoseconds) / 1_000_000) }
    private func operation(_ value: FisicaSessionOperation) -> UInt32 { value.rawValue }

    /// Open the transport and probe firmware. Completes with the probe result; an
    /// unanswered probe is reported in its `status` and does not prevent use.
    /// `link` defaults to the transport's own `link`; a transport that does not declare one
    /// must be given it. A missing link, or one other than the transport's, fails with
    /// `invalidArgument` before anything is sent.
    public func connect(_ link: FisicaLink? = nil, timeout: TimeInterval = 15, completion: @escaping Completion) {
        queue.async { [weak self] in
            guard let self else { return }
            let probe = self.operation(.probe)
            guard !self.closed, !self.linkReady, self.waiters[probe] == nil else {
                completion(.failure(FisicaError(code: Int32(FisicaStatus.busy.rawValue), message: "Already connected or connecting"))); return
            }
            let carried = self.transport.link
            guard let link = link ?? carried else {
                completion(.failure(FisicaError(code: Int32(FisicaStatus.invalidArgument.rawValue),
                                                message: "This transport does not declare its link: pass .ble or .usb"))); return
            }
            if let carried, carried != link {
                completion(.failure(FisicaError(code: Int32(FisicaStatus.invalidArgument.rawValue),
                                                message: "This transport carries \(carried), not \(link)"))); return
            }
            self.waiters[probe] = completion
            self.transport.onReady = { [weak self] in self?.queue.async { self?.ready(link) } }
            self.transport.onBytes = { [weak self] bytes in self?.queue.async { self?.received(bytes) } }
            self.transport.onLost = { [weak self] in self?.queue.async { self?.lost(sendFailure: false) } }
            self.transport.onSendFailure = { [weak self] error in self?.queue.async { self?.onError?(error); self?.lost(sendFailure: true) } }
            self.transport.onError = { [weak self] error in self?.queue.async { self?.onError?(error) } }
            let deadline = DispatchWorkItem { [weak self] in
                guard let self, !self.linkReady else { return }
                self.transport.close()
                self.waiters.removeValue(forKey: probe)?(.failure(FisicaError(code: 1001, message: "Transport was not ready in time")))
            }
            self.readyDeadline = deadline
            self.queue.asyncAfter(deadline: .now() + timeout, execute: deadline)
            self.entitlement?.checkStatus()
            self.opened = true
            self.transport.open()
        }
    }

    private func ready(_ link: FisicaLink) {
        guard !closed, !linkReady else { return }
        linkReady = true; readyDeadline?.cancel()
        if timer == nil {
            let source = DispatchSource.makeTimerSource(queue: queue)
            source.schedule(deadline: .now() + .milliseconds(20), repeating: .milliseconds(20))
            source.setEventHandler { [weak self] in
                guard let self else { return }
                self.step { try $0.tick($1) }
                // Picks up activation, renewal, release and the end of full mode.
                if (self.entitlement?.revision ?? 0) != self.licenseRevision || self.now - self.licensedAt >= FisicaClient.licenseInterval {
                    self.applyLicense()
                }
            }
            source.resume(); timer = source
        }
        step { try $0.action(FisicaSessionAction.connected.rawValue, value: Int64(link.rawValue), nowMs: $1) }
    }
    /// Copy the current license into the session (on `queue`).
    private func applyLicense() {
        step { session, now in
            licenseRevision = entitlement?.revision ?? 0; licensedAt = now  // a failure is retried after the interval
            try session.license(entitlement, nowMs: now)
        }
    }
    private func received(_ bytes: [UInt8]) {
        guard linkReady else { return }
        let limit = Int(FisicaLimit.feedBytes.rawValue)
        for start in stride(from: 0, to: bytes.count, by: limit) {
            step { try $0.feed(Array(bytes[start..<min(start + limit, bytes.count)]), nowMs: $1) }
        }
    }
    private func lost(sendFailure: Bool) {
        guard !closed else { return }
        if !linkReady {
            // Never became ready: nothing to recover, report the failed connection.
            readyDeadline?.cancel()
            waiters.removeValue(forKey: operation(.probe))?(.failure(FisicaError(code: 1001, message: "Transport could not connect")))
            return
        }
        linkReady = false
        step { try $0.action(FisicaSessionAction.lost.rawValue, value: sendFailure ? 1 : 0, nowMs: $1) }
    }

    /// Run one core call, then deliver everything it produced.
    private func step(_ call: (FisicaSession, Int64) throws -> Void) {
        guard !closed else { return }
        do {
            try call(session, now)
            for event in try session.drain() { deliver(event) }
        } catch { onError?(error) }
    }
    private func deliver(_ event: SessionEvent) {
        state = FisicaSessionState(rawValue: event.state) ?? state
        switch FisicaSessionEvent(rawValue: event.kind) {
        case .transmit: transport.send(event.payload)
        case .measurement:
            if let frame = event.measurement { frames.append(frame); trimFrames(); onFrame?(frame) }
        case .result:
            let probe = event.operation == operation(.probe)
            if probe, event.status == 0, let payload = try? core.responsePayload(event.payload) {
                let version = String(decoding: payload, as: UTF8.self).trimmingCharacters(in: CharacterSet(charactersIn: "\0 \r\n"))
                firmwareVersion = version.isEmpty ? nil : version
            }
            // A failed firmware probe is informational; the core lets use continue.
            waiters.removeValue(forKey: event.operation)?(event.status == 0 || probe ? .success(event) : .failure(FisicaCommandError(event: event)))
        case .reconnect: onLinkLost?()
        default: break
        }
        onEvent?(event)
    }

    private func request(_ action: FisicaSessionAction, value: Int64, waitFor: FisicaSessionOperation,
                         prepare: (() -> Void)? = nil, accepted: (() -> Void)? = nil, completion: @escaping Completion) {
        queue.async { self.begin(action, value: value, waitFor: waitFor, prepare: prepare, accepted: accepted, completion: completion) }
    }
    /// On `queue`: wait for `waitFor` after the core call; busy while that operation is pending.
    private func begin(_ action: FisicaSessionAction, value: Int64, waitFor: FisicaSessionOperation,
                       prepare: (() -> Void)? = nil, accepted: (() -> Void)? = nil, completion: @escaping Completion) {
        let key = operation(waitFor)
        guard !closed, waiters[key] == nil else {
            completion(.failure(FisicaError(code: Int32(FisicaStatus.busy.rawValue), message: "Another operation is pending"))); return
        }
        waiters[key] = completion
        prepare?()
        do {
            try session.action(action.rawValue, value: value, nowMs: now)
            accepted?()
            for event in try session.drain() { deliver(event) }
        } catch { waiters.removeValue(forKey: key)?(.failure(error)) }
    }
    /// Start measuring; `durationMs` nil measures until `stop`. Completes when the Scale confirmed the start.
    /// The current license is applied first. Frames of the previous measurement are cleared once
    /// the core accepts the start.
    public func start(durationMs: Int64? = nil, completion: @escaping Completion) {
        entitlement?.checkStatus()
        request(.start, value: durationMs ?? -1, waitFor: .begin, prepare: { [weak self] in self?.applyLicense() },
                accepted: { [weak self] in self?.clearFrames() }, completion: completion)
    }
    public func stop(completion: @escaping Completion) { request(.stop, value: 0, waitFor: .end, completion: completion) }

    /// Ask the Scale for its battery level (GET_BATTERY_LEVEL), idle or during a measurement, without
    /// interrupting it. A refused or unanswered query fails only this call; the session carries on.
    public func readBattery(completion: @escaping (Result<Int, Error>) -> Void) {
        queue.async { [self] in
            let key = self.operation(.request)
            guard !self.closed, self.waiters[key] == nil else {
                completion(.failure(FisicaError(code: Int32(FisicaStatus.busy.rawValue), message: "Another operation is pending"))); return
            }
            self.waiters[key] = { [weak self] result in
                completion(result.flatMap { event in
                    let data = (try? self?.core.responsePayload(event.payload)) ?? []
                    let percent = data.count == 2 ? Int(data[0]) << 8 | Int(data[1]) : -1
                    guard let self, (0...100).contains(percent) else { return .failure(FisicaCommandError(event: event)) }
                    self.battery = percent
                    return .success(percent)
                })
            }
            do {
                try self.session.command("GET_BATTERY_LEVEL", nowMs: self.now)
                for event in try self.session.drain() { self.deliver(event) }
            } catch { self.waiters.removeValue(forKey: key)?(.failure(error)) }
        }
    }

    /// Stop if needed, release the Scale's measurement mode, then close the transport.
    /// Resources are released even when the Scale does not confirm. A second close while one
    /// is pending fails with `busy` and leaves the first alone; once closed, close completes
    /// at once with nil and does nothing.
    public func close(completion: ((Result<SessionEvent, Error>?) -> Void)? = nil) {
        queue.async {
            guard !self.closed else { completion?(nil); return }
            guard self.waiters[self.operation(.close)] == nil else {
                completion?(.failure(FisicaError(code: Int32(FisicaStatus.busy.rawValue), message: "Close is already pending"))); return
            }
            guard self.linkReady else { self.teardown(); completion?(nil); return }
            self.begin(.disconnect, value: 0, waitFor: .close) { result in self.teardown(); completion?(result) }
        }
    }
    private func teardown() {
        closed = true; linkReady = false
        timer?.cancel(); timer = nil; readyDeadline?.cancel()
        transport.onReady = nil; transport.onBytes = nil; transport.onLost = nil; transport.onSendFailure = nil; transport.onError = nil
        // Keep the core's analysis of the last measurement for `report()` after close.
        finalAnalysis = try? session.analyze()
        transport.close(); session.close()
        let pending = waiters; waiters.removeAll()
        for waiter in pending.values { waiter(.failure(FisicaError(code: Int32(FisicaStatus.cancelled.rawValue), message: "Client closed"))) }
    }

    /// Weight and foot size of the current measurement; nil without a stable measurement.
    /// The core analyzes every packet it received since `start`, in every mode (`frames` may
    /// hold only some of them). After `close` it returns the last measurement's analysis.
    public func report() throws -> SessionReport? {
        entitlement?.checkStatus()
        guard let result = try closed ? finalAnalysis : session.analyze() else { return nil }
        return SessionReport(weight: result[0], leftWidth: result[1], leftLength: result[2], rightWidth: result[3], rightLength: result[4],
                             frames: frames, battery: battery)
    }
    public func clearFrames() { frames.removeAll(); droppedFrames = 0 }
    private func trimFrames() {
        let excess = frames.count - maxFrames
        if excess > 0 { frames.removeFirst(excess); droppedFrames += excess }
    }
}
