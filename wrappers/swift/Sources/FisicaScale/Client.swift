import Foundation

/// Byte link to one Scale. The app or SDK adapter owns the OS I/O; no protocol rules here.
/// Deliver every callback on the client's queue.
public protocol FisicaTransport: AnyObject {
    /// The Scale link this transport carries, or nil when it can carry either.
    /// `FisicaClient.connect` rejects a different link before anything is sent.
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

/// Result of the shared analysis over the collected frames. Centimetres use the
/// nominal 1 cm cell; values are not clinically calibrated.
public struct SessionReport {
    public let weight: Double, leftWidth: Double, leftLength: Double, rightWidth: Double, rightLength: Double
    public let frames: [Measurement]
    public var frameCount: Int { frames.count }
    public var batteryValid: Bool { false }
}

/// Connect, measure, stop and report over any transport. Command order, ACK waits,
/// timeouts and recovery decisions live in the shared core; this class only runs the
/// clock, moves bytes and turns core events into callbacks.
/// Every callback and completion runs on `queue`; read `frames` and call `report()` there.
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
    /// Only the latest `maxFrames` are kept.
    public private(set) var frames: [Measurement] = []
    /// Oldest frames dropped beyond `maxFrames` since the last `start` or `clearFrames`.
    public private(set) var droppedFrames = 0
    /// How many recent frames to keep, 1...`FisicaLimit.analysisFrames` (the most one
    /// analysis accepts). This also bounds memory in long sessions.
    public var maxFrames = Int(FisicaLimit.analysisFrames.rawValue) {
        didSet { maxFrames = min(max(maxFrames, 1), Int(FisicaLimit.analysisFrames.rawValue)); trimFrames() }
    }
    public private(set) var firmwareVersion: String?
    public private(set) var state: FisicaSessionState = .disconnected

    private let queue: DispatchQueue
    private let session: FisicaSession
    private let started = DispatchTime.now()
    private var waiters: [UInt32: Completion] = [:]
    private var timer: DispatchSourceTimer?
    private var readyDeadline: DispatchWorkItem?
    private var linkReady = false, closed = false

    public init(core: FisicaCore, transport: FisicaTransport, entitlement: FisicaEntitlement? = nil, queue: DispatchQueue = .main) throws {
        self.core = core; self.transport = transport; self.entitlement = entitlement; self.queue = queue
        session = try FisicaSession(core: core)
    }
    deinit { timer?.cancel(); session.close() }

    private var now: Int64 { Int64((DispatchTime.now().uptimeNanoseconds - started.uptimeNanoseconds) / 1_000_000) }
    private func operation(_ value: FisicaSessionOperation) -> UInt32 { value.rawValue }

    /// Open the transport and probe firmware. Completes with the probe result; an
    /// unanswered probe is reported in its `status` and does not prevent use.
    /// A link other than the transport's own `link` fails before anything is sent.
    public func connect(_ link: FisicaLink, timeout: TimeInterval = 15, completion: @escaping Completion) {
        queue.async { [weak self] in
            guard let self else { return }
            let probe = self.operation(.probe)
            guard !self.closed, !self.linkReady, self.waiters[probe] == nil else {
                completion(.failure(FisicaError(code: Int32(FisicaStatus.busy.rawValue), message: "Already connected or connecting"))); return
            }
            if let carried = self.transport.link, carried != link {
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
            self.transport.open()
        }
    }

    private func ready(_ link: FisicaLink) {
        guard !closed, !linkReady else { return }
        linkReady = true; readyDeadline?.cancel()
        if timer == nil {
            let source = DispatchSource.makeTimerSource(queue: queue)
            source.schedule(deadline: .now() + .milliseconds(20), repeating: .milliseconds(20))
            source.setEventHandler { [weak self] in self?.step { try $0.tick($1) } }
            source.resume(); timer = source
        }
        step { try $0.action(FisicaSessionAction.connected.rawValue, value: Int64(link.rawValue), nowMs: $1) }
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
            if let frame = try? core.decode(event.payload) { frames.append(frame); trimFrames(); onFrame?(frame) }
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
                         accepted: (() -> Void)? = nil, completion: @escaping Completion) {
        queue.async {
            let key = self.operation(waitFor)
            guard !self.closed, self.waiters[key] == nil else {
                completion(.failure(FisicaError(code: Int32(FisicaStatus.busy.rawValue), message: "Another operation is pending"))); return
            }
            self.waiters[key] = completion
            do {
                try self.session.action(action.rawValue, value: value, nowMs: self.now)
                accepted?()
                for event in try self.session.drain() { self.deliver(event) }
            } catch { self.waiters.removeValue(forKey: key)?(.failure(error)) }
        }
    }
    /// Start measuring; `durationMs` nil measures until `stop`. Completes when the Scale confirmed the start.
    /// Frames of the previous measurement are cleared once the core accepts the start.
    public func start(durationMs: Int64? = nil, completion: @escaping Completion) {
        entitlement?.checkStatus()
        request(.start, value: durationMs ?? -1, waitFor: .begin, accepted: { [weak self] in self?.clearFrames() }, completion: completion)
    }
    public func stop(completion: @escaping Completion) { request(.stop, value: 0, waitFor: .end, completion: completion) }

    /// Stop if needed, release the Scale's measurement mode, then close the transport.
    /// Resources are released even when the Scale does not confirm.
    public func close(completion: ((Result<SessionEvent, Error>?) -> Void)? = nil) {
        queue.async {
            guard !self.closed else { completion?(nil); return }
            guard self.linkReady else { self.teardown(); completion?(nil); return }
            self.request(.disconnect, value: 0, waitFor: .close) { result in self.teardown(); completion?(result) }
        }
    }
    private func teardown() {
        closed = true; linkReady = false
        timer?.cancel(); timer = nil; readyDeadline?.cancel()
        transport.onReady = nil; transport.onBytes = nil; transport.onLost = nil; transport.onSendFailure = nil; transport.onError = nil
        transport.close(); session.close()
        let pending = waiters; waiters.removeAll()
        for waiter in pending.values { waiter(.failure(FisicaError(code: Int32(FisicaStatus.cancelled.rawValue), message: "Client closed"))) }
    }

    /// Analyze the frames collected so far; nil without a stable measurement.
    public func report() throws -> SessionReport? {
        guard !frames.isEmpty else { return nil }
        entitlement?.checkStatus()
        guard let result = try core.analyzePackets(frames.map { $0.packet }) else { return nil }
        return SessionReport(weight: result[0], leftWidth: result[1], leftLength: result[2], rightWidth: result[3], rightLength: result[4], frames: frames)
    }
    public func clearFrames() { frames.removeAll(); droppedFrames = 0 }
    private func trimFrames() {
        let excess = frames.count - maxFrames
        if excess > 0 { frames.removeFirst(excess); droppedFrames += excess }
    }
}
