import XCTest
import CryptoKit
@testable import FisicaScale

/// Shared fixtures (FISICA_FIXTURES) and license vectors (FISICA_LICENSE_VECTORS, written by
/// tools/verify_bindings.py: the shared keys plus keys activated for this PC). Under xctest the
/// core binds keys to the PC, like any command-line program.
enum Vectors {
    static let day: UInt64 = 86400
    static var environment: [String: String] { ProcessInfo.processInfo.environment }
    static func fixture(_ name: String) throws -> Any {
        try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: environment["FISICA_FIXTURES"]! + "/" + name)))
    }
    static func license() throws -> [String: Any] {
        let path = environment["FISICA_LICENSE_VECTORS"] ?? environment["FISICA_FIXTURES"]! + "/entitlement.json"
        return try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: path))) as! [String: Any]
    }
    /// A key or value; the keys activated for this PC exist only in FISICA_LICENSE_VECTORS.
    static func string(_ name: String) throws -> String {
        guard let value = try license()[name] as? String else { throw XCTSkip("\(name): run tools/verify_bindings.py swift for this PC's keys") }
        return value
    }
    static func now() throws -> UInt64 { (try license()["now"] as! NSNumber).uint64Value }
    static func bytes(_ hex: String) -> [UInt8] {
        stride(from: 0, to: hex.count, by: 2).map { i in let start = hex.index(hex.startIndex, offsetBy: i); return UInt8(hex[start..<hex.index(start, offsetBy: 2)], radix: 16)! }
    }
    static func packet() throws -> [UInt8] {
        bytes((try fixture("legacy-processing.json") as! [[String: Any]]).first { $0["id"] as? String == "synthetic-recorded-layout" }!["packet_hex"] as! String)
    }
    /// A context holding `key` (nil: no key), in memory only, at `clock` (default: the vectors' now).
    static func entitlement(_ core: FisicaCore, key: String?, at clock: UInt64? = nil) throws -> FisicaEntitlement {
        let now = try clock ?? Vectors.now()
        let entitlement = try FisicaEntitlement(core: core, storage: nil, clock: { now })
        if let key { _ = try entitlement.importToken(try string(key)) }
        return entitlement
    }
    /// Fields of an FAC1/FRC1 code after checking its prefix and the developer portal's check value.
    static func readCode(_ code: String, _ prefix: String) throws -> [String: String] {
        let parts = code.split(separator: ".").map(String.init)
        XCTAssertEqual(parts.count, 3); XCTAssertEqual(parts.first, prefix)
        let material = (prefix == "FRC1" ? "fisica-release-code-v1|" : "") + prefix + "." + parts[1]
        XCTAssertEqual(parts[2], String(SHA256.hash(data: Data(material.utf8)).map { String(format: "%02x", $0) }.joined().prefix(8)))
        var body = parts[1].replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        while body.count % 4 != 0 { body += "=" }
        return try JSONSerialization.jsonObject(with: try XCTUnwrap(Data(base64Encoded: body))) as! [String: String]
    }
}

final class ContractTests: XCTestCase {
    func testSharedSessions() throws {
        let core=try FisicaCore()
        for v in try Vectors.fixture("sessions.json") as! [[String:Any]] {
            let session=try FisicaSession(core:core); defer { session.close() }
            for step in v["steps"] as! [[String:Any]] {
                let now=(step["now_ms"] as! NSNumber).int64Value
                if let action=step["action"] as? NSNumber { try session.action(action.uint32Value,value:(step["value"] as! NSNumber).int64Value,nowMs:now) }
                else if let hex=step["feed_hex"] as? String { try session.feed(Vectors.bytes(hex),nowMs:now) }
                else if let command=step["command"] as? String { try session.command(command,nowMs:now) }
                else { try session.tick(now) }
                let actual=try session.drain().map { e -> [Any] in [e.kind,e.state,e.status,e.deviceError,e.operation,e.payload.map { String(format:"%02x",$0) }.joined()] }
                XCTAssertEqual(try JSONSerialization.data(withJSONObject:actual),try JSONSerialization.data(withJSONObject:step["events"]!),v["id"] as! String)
            }
        }
    }
    func testDecodingPacketsNeedsFullModeWithRawData() throws {
        let core = try FisicaCore(), packets = try Vectors.fixture("legacy-processing.json") as! [[String: Any]]
        for v in packets {
            // Raw and processed grids are full-mode data: a distinct license error, not a technical one.
            XCTAssertThrowsError(try core.decode(Vectors.bytes(v["packet_hex"] as! String))) {
                XCTAssertEqual(($0 as? FisicaLicenseError)?.code, Int32(FisicaStatus.featureNotLicensed.rawValue))
            }
        }
        XCTAssertThrowsError(try core.command("unknown")) { XCTAssertEqual(($0 as! FisicaError).code,4) }
        let full = try Vectors.entitlement(core, key: "full")
        for v in packets {
            let bytes = Vectors.bytes(v["packet_hex"] as! String), result = try core.decode(bytes, license: full)
            XCTAssertEqual(result.weight, v["weight"] as! Double)
            XCTAssertEqual(result.raw?.count, 638); XCTAssertEqual(result.display.count, 841); XCTAssertEqual(result.packet, bytes)
        }
        // Full mode whose contract opens export only: every frame, but no raw data.
        let limited = try Vectors.entitlement(core, key: "limited")
        XCTAssertEqual(try limited.status().mode, .full); XCTAssertEqual(try limited.status().features, [.export, .fullRate])
        XCTAssertThrowsError(try core.decode(try Vectors.packet(), license: limited)) { XCTAssertTrue($0 is FisicaLicenseError) }
    }
}

/// SDK keys as seats: the core decides full or basic mode for this PC.
final class LicenseTests: XCTestCase {
    func testNoKeyIsBasicModeAndTheCoreReadsThisPC() throws {
        let core = try FisicaCore(), now = try Vectors.now(), target = try Vectors.string("target")
        let entitlement = try Vectors.entitlement(core, key: nil)
        let status = try entitlement.status()
        XCTAssertEqual(status.mode, .basic); XCTAssertEqual(status.reason, .noKey); XCTAssertTrue(status.features.isEmpty)
        XCTAssertEqual(status.targetType, .pc); XCTAssertEqual(status.target, target); XCTAssertEqual(try Vectors.string("target_type"), "pc")
        XCTAssertEqual(status.company, ""); XCTAssertNil(status.activationCode); XCTAssertNotNil(status.message); XCTAssertFalse(status.isFull)
        XCTAssertNil(entitlement.activationCode())
        XCTAssertThrowsError(try entitlement.release()) { XCTAssertEqual(($0 as? FisicaError)?.code, Int32(FisicaStatus.invalidState.rawValue)) }
        // No usage tracking: the state is the latest time seen and nothing else.
        XCTAssertEqual(String(decoding: try entitlement.exportState(), as: UTF8.self), "FSS1\nT \(now)\n")
    }
    func testUnactivatedKeyIsBasicModeWithAnActivationCode() throws {
        let core = try FisicaCore(), target = try Vectors.string("target")
        let entitlement = try Vectors.entitlement(core, key: nil)
        XCTAssertEqual(try entitlement.importToken(try Vectors.string("unbound")), .accepted)
        let status = try entitlement.status()
        XCTAssertEqual(status.mode, .basic); XCTAssertEqual(status.reason, .notActivated); XCTAssertEqual(status.bindType, .none)
        XCTAssertEqual(status.company, "acme"); XCTAssertEqual(status.contractReference, "ctr-1"); XCTAssertEqual(status.key, "key_test_1")
        XCTAssertEqual(status.track, .trial); XCTAssertEqual(status.keyEnvironment, .test)
        // The developer portal's FAC1 parser accepts the code: key, this PC and the check value.
        let code = try XCTUnwrap(status.activationCode)
        XCTAssertEqual(entitlement.activationCode(), code)
        XCTAssertEqual(try Vectors.readCode(code, "FAC1"), ["k": "key_test_1", "t": "pc", "b": target])
        XCTAssertEqual(try entitlement.importToken(try Vectors.string("unbound_older")), .ignoredOlder)
        XCTAssertThrowsError(try entitlement.release())  // nothing activated here to release
    }
    func testActivatedKeyIsFullModeWithEveryFeature() throws {
        let core = try FisicaCore(), now = try Vectors.now(), target = try Vectors.string("target")
        let entitlement = try Vectors.entitlement(core, key: "full")
        let status = try entitlement.status()
        XCTAssertEqual(status.mode, .full); XCTAssertEqual(status.reason, .none); XCTAssertTrue(status.isFull); XCTAssertNil(status.message)
        XCTAssertEqual(status.features, FisicaFeature.allCases); XCTAssertTrue(status.has(.rawData) && status.has(.export))
        XCTAssertEqual(status.key, "key_test_1"); XCTAssertEqual(status.bindType, .pc); XCTAssertEqual(status.bind, target)
        XCTAssertEqual(status.track, .trial); XCTAssertEqual(status.contract, .active)
        XCTAssertEqual(status.verifiedAt, now); XCTAssertEqual(status.keyUntil, now + 90 * Vectors.day); XCTAssertEqual(status.fullUntil, now + 14 * Vectors.day)
        XCTAssertTrue(status.warnings.contains(.testKey)); XCTAssertNil(status.activationCode)
        XCTAssertEqual(try entitlement.importToken(try Vectors.string("full_older")), .ignoredOlder)
    }
    func testRejectedKeysNeverReplaceTheKeyThisPCHolds() throws {
        let core = try FisicaCore()
        let entitlement = try Vectors.entitlement(core, key: "full")
        XCTAssertEqual(try entitlement.importToken(try Vectors.string("other_target")), .rejectedTarget)
        var status = try entitlement.status()
        XCTAssertEqual(status.mode, .full); XCTAssertEqual(status.key, "key_test_1"); XCTAssertEqual(status.lastImport, .rejectedTarget)
        XCTAssertTrue(status.warnings.contains(.importRejected))
        XCTAssertEqual(try entitlement.importToken(try Vectors.string("tampered")), .rejectedSignature)
        XCTAssertEqual(try entitlement.importToken(try Vectors.string("unknown_key")), .rejectedKey)
        XCTAssertEqual(try entitlement.importToken("not a key"), .rejectedFormat)
        status = try entitlement.status()
        XCTAssertEqual(status.mode, .full); XCTAssertEqual(status.key, "key_test_1")
        // Without a verified key a rejected one leaves basic mode with no attribution.
        let empty = try Vectors.entitlement(core, key: "tampered")
        XCTAssertEqual(try empty.status().reason, .noKey); XCTAssertEqual(try empty.status().company, "")
    }
    func testLegacyAndRevokedKeysAreBasicMode() throws {
        let core = try FisicaCore(), now = try Vectors.now()
        let legacy = try Vectors.entitlement(core, key: "legacy").status()
        XCTAssertEqual(legacy.mode, .basic); XCTAssertEqual(legacy.reason, .legacyToken); XCTAssertEqual(legacy.contract, .active)
        let revoked = try Vectors.entitlement(core, key: "revoked", at: now + Vectors.day).status()
        XCTAssertEqual(revoked.mode, .basic); XCTAssertEqual(revoked.reason, .revoked); XCTAssertEqual(revoked.revokedAt, now + Vectors.day)
        XCTAssertNotNil(revoked.message)
    }
    func testReleaseGivesThePortalCodeAndForgetsTheKey() throws {
        let core = try FisicaCore(), now = try Vectors.now(), target = try Vectors.string("target")
        let storage = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: storage) }
        let entitlement = try FisicaEntitlement(core: core, storage: storage, clock: { now })
        XCTAssertEqual(try entitlement.importToken(try Vectors.string("full")), .accepted)
        // One file per seat: the core names it from company, contract and this PC's target.
        let digest = SHA256.hash(data: Data("acme\nctr-1\n\(target)".utf8)).map { String(format: "%02x", $0) }.joined()
        let state = storage.appendingPathComponent("entitlement-\(digest.prefix(16)).state")
        XCTAssertTrue(FileManager.default.fileExists(atPath: state.path))
        let code = try entitlement.release()
        XCTAssertEqual(try Vectors.readCode(code, "FRC1"), ["k": "key_test_1", "t": "pc", "b": target])
        let status = try entitlement.status()
        XCTAssertEqual(status.mode, .basic); XCTAssertEqual(status.reason, .noKey); XCTAssertEqual(status.key, "")
        XCTAssertFalse(FileManager.default.fileExists(atPath: state.path))
        XCTAssertThrowsError(try entitlement.release())
    }
    func testOfflineActivationFileWinsOverTheOriginalKeyFile() throws {
        let core = try FisicaCore(), now = try Vectors.now(), target = try Vectors.string("target")
        let storage = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: storage) }
        let original = try FisicaEntitlement(core: core, storage: storage, clock: { now })
        XCTAssertEqual(try original.importToken(try Vectors.string("unbound_older")), .accepted)
        XCTAssertNotNil(try original.status().activationCode)
        // The portal turns the activation code into a key file activated for this PC.
        let file = storage.appendingPathComponent("activated.fse")
        try Data((try Vectors.string("full") + "\n").utf8).write(to: file)
        XCTAssertEqual(try original.importFile(file), .accepted)
        XCTAssertEqual(try original.status().mode, .full)
        XCTAssertEqual(original.syncNow(), EntitlementSyncResult(activated: false, refreshed: false, error: nil))  // no endpoint: nothing leaves the PC
        // A later start with the original key file still finds the activated key.
        let reopened = try FisicaEntitlement(core: core, storage: storage, clock: { now })
        XCTAssertEqual(try reopened.importToken(try Vectors.string("unbound_older")), .accepted)
        XCTAssertEqual(try reopened.status().mode, .full); XCTAssertEqual(try reopened.status().bind, target)
    }
    func testStatusAndWarningsAreAnnouncedOnce() throws {
        let core = try FisicaCore(), now = try Vectors.now()
        var clock = now, statuses: [EntitlementStatus] = [], warnings: [FisicaEntitlementWarning] = []
        let entitlement = try FisicaEntitlement(core: core, storage: nil, clock: { clock })
        entitlement.onStatus = { statuses.append($0) }
        entitlement.onWarning = { new, _ in warnings += new }
        try entitlement.importToken(try Vectors.string("full"))
        XCTAssertEqual(statuses.last?.mode, .full); XCTAssertEqual(warnings, [.testKey])
        entitlement.checkStatus(); entitlement.checkStatus()
        XCTAssertEqual(statuses.count, 1)
        clock = now + 8 * Vectors.day  // full mode ends within 7 days
        entitlement.checkStatus(); entitlement.checkStatus()
        XCTAssertEqual(warnings, [.testKey, .fullEnding]); XCTAssertEqual(statuses.count, 2)
        clock = now + 14 * Vectors.day  // the trial ended
        entitlement.checkStatus()
        XCTAssertEqual(statuses.last?.mode, .basic); XCTAssertEqual(statuses.last?.reason, .fullEnded); XCTAssertTrue(statuses.last!.features.isEmpty)
    }
}

/// The session hands out frames by license mode; weight and foot size come from every packet.
final class SessionLicenseTests: XCTestCase {
    func testSessionHandsOutFramesByMode() throws {
        let core = try FisicaCore(), packet = try Vectors.packet()
        let full = try Vectors.entitlement(core, key: "full"), limited = try Vectors.entitlement(core, key: "limited")
        let reference = try core.decode(packet, license: full)
        // Ten packets 300 ms apart: basic mode hands out at most one frame per 1000 ms of session time.
        for (license, expected, raw) in [(nil, 3, false), (limited, 10, false), (full, 10, true)] as [(FisicaEntitlement?, Int, Bool)] {
            let session = try FisicaSession(core: core); defer { session.close() }
            var now: Int64 = 1000
            try session.action(FisicaSessionAction.connected.rawValue, value: Int64(FisicaLink.usb.rawValue), nowMs: now)
            try session.feed(Vectors.bytes("24434701322e302e31fff0"), nowMs: now)
            try session.license(license, nowMs: now)
            try session.action(FisicaSessionAction.start.rawValue, value: -1, nowMs: now)
            for name in ["SET_DEVICE_USB", "START_CONTINUOUS_MEASUREMENT"] {
                let command = try core.command(name)
                try session.feed([0x24, command[1], command[2], 1, 0, 0, 0xff, 0xf0], nowMs: now)
            }
            var frames: [FisicaScale.Measurement] = []
            for _ in 0..<10 {
                now += 300
                try session.feed(packet, nowMs: now)
                frames += try session.drain().compactMap { $0.measurement }
            }
            XCTAssertEqual(frames.count, expected)
            XCTAssertTrue(frames.allSatisfy { $0.weight == reference.weight && $0.display == reference.display })
            XCTAssertEqual(frames.first?.hasRawData, raw)
            XCTAssertEqual(frames.first?.packet, raw ? packet : nil); XCTAssertEqual(frames.first?.raw, raw ? reference.raw : nil)
            XCTAssertEqual(try session.analyze()?[0], reference.weight)  // every packet, in every mode
        }
    }
}

/// High-level client and license against the same core, without hardware.
final class ClientTests: XCTestCase {
    func scale(_ core: FisicaCore, frames: Int = 12) throws -> MockTransport {
        let packet = try Vectors.packet()
        var exchanges: [[UInt8]: [[UInt8]]] = [try core.command("GET_FIRMWARE_VERSION"): [Vectors.bytes("24434701322e302e31fff0")]]
        for name in ["SET_DEVICE_USB", "SET_DEVICE_BLE", "START_CONTINUOUS_MEASUREMENT", "STOP_CONTINUOUS_MEASUREMENT", "SET_DEVICE_STOP"] {
            let command = try core.command(name)
            exchanges[command] = [[0x24, command[1], command[2], 1, 0, 0, 0xff, 0xf0]]
        }
        exchanges[try core.command("START_CONTINUOUS_MEASUREMENT")]![0] += Array(repeating: packet, count: frames).flatMap { $0 }
        return MockTransport(exchanges: exchanges)
    }
    func waitFor(_ name: String, _ body: (@escaping () -> Void) -> Void) { let done = expectation(description: name); body { done.fulfill() }; wait(for: [done], timeout: 5) }
    func pause(_ seconds: Double) { waitFor("pause") { done in DispatchQueue.main.asyncAfter(deadline: .now() + seconds, execute: done) } }
    func ok(_ result: Result<SessionEvent, Error>, _ line: UInt = #line) { if case .failure(let error) = result { XCTFail("\(error)", line: line) } }
    func failure(_ result: Result<SessionEvent, Error>) -> Error? { if case .failure(let error) = result { return error }; XCTFail("Expected failure"); return nil }

    func testConnectMeasureStopReportCloseInBasicMode() throws {
        let core = try FisicaCore(), transport = try scale(core)
        let client = try FisicaClient(core: core, transport: transport)
        var received = 0
        client.onFrame = { _ in received += 1 }
        waitFor("connect") { done in client.connect(.usb) { result in self.ok(result); done() } }
        XCTAssertEqual(client.firmwareVersion, "2.0.1")
        waitFor("start") { done in client.start { result in self.ok(result); done() } }
        XCTAssertEqual(client.state, .measuring)
        waitFor("stop") { done in client.stop { result in self.ok(result); done() } }
        // No key: the 12 packets arrived within a second, so one display frame without raw data.
        XCTAssertEqual(received, 1)
        let report = try XCTUnwrap(try client.report())
        XCTAssertEqual(report.weight, 70.5); XCTAssertEqual(report.frameCount, 1)  // the core analyzed all 12 packets
        XCTAssertEqual(report.frames[0].display.count, 841); XCTAssertNil(report.frames[0].raw); XCTAssertNil(report.frames[0].packet)
        waitFor("close") { done in client.close { result in XCTAssertNotNil(result); done() } }
        XCTAssertEqual(try client.report()?.weight, 70.5)
        XCTAssertTrue(transport.sent.contains(try core.command("SET_DEVICE_USB")))
        XCTAssertEqual(transport.sent.last, try core.command("SET_DEVICE_STOP"))
        XCTAssertFalse(transport.isOpen)
    }
    func testFullModeGetsEveryFrameWithRawData() throws {
        let core = try FisicaCore(), transport = try scale(core)
        let client = try FisicaClient(core: core, transport: transport, entitlement: try Vectors.entitlement(core, key: "full"))
        var received = 0
        client.onFrame = { _ in received += 1 }
        waitFor("connect") { done in client.connect(.usb) { result in self.ok(result); done() } }
        waitFor("start") { done in client.start { result in self.ok(result); done() } }
        waitFor("stop") { done in client.stop { result in self.ok(result); done() } }
        XCTAssertEqual(received, 12)
        let report = try XCTUnwrap(try client.report())
        XCTAssertEqual(report.weight, 70.5); XCTAssertEqual(report.frameCount, 12)
        XCTAssertEqual(report.frames[0].raw?.count, 638); XCTAssertEqual(report.frames[0].display.count, 841); XCTAssertEqual(report.frames[0].packet, try Vectors.packet())
        waitFor("close") { done in client.close { _ in done() } }
        XCTAssertEqual(try client.report()?.weight, 70.5)
    }
    func testMeasurementAndAnalysisWorkInEveryLicenseState() throws {
        let core = try FisicaCore()
        for key in [nil, "unbound", "tampered", "legacy", "revoked", "other_target", "limited", "full"] as [String?] {
            let entitlement = try Vectors.entitlement(core, key: key)
            let transport = try scale(core), client = try FisicaClient(core: core, transport: transport, entitlement: entitlement)
            let fullRate = key == "full" || key == "limited"
            waitFor("connect") { done in client.connect(.usb) { result in self.ok(result); done() } }
            waitFor("start") { done in client.start { result in self.ok(result); done() } }
            waitFor("stop") { done in client.stop { result in self.ok(result); done() } }
            XCTAssertEqual(client.frames.count, fullRate ? 12 : 1, key ?? "no key")
            XCTAssertEqual(client.frames.first?.hasRawData, key == "full", key ?? "no key")
            XCTAssertEqual(try client.report()?.weight, 70.5, key ?? "no key")
            waitFor("close") { done in client.close { _ in done() } }
        }
    }
    func testLicenseChangeReachesARunningMeasurement() throws {
        let core = try FisicaCore(), transport = try scale(core, frames: 0), now = try Vectors.now()
        let entitlement = try FisicaEntitlement(core: core, storage: nil, clock: { now })
        let client = try FisicaClient(core: core, transport: transport, entitlement: entitlement)
        waitFor("connect") { done in client.connect(.usb) { _ in done() } }
        waitFor("start") { done in client.start { result in self.ok(result); done() } }
        XCTAssertEqual(try entitlement.importToken(try Vectors.string("full")), .accepted)
        pause(0.2)  // the client notices the new key on its next tick
        transport.feed(Array(repeating: try Vectors.packet(), count: 12).flatMap { $0 })
        pause(0.1)
        XCTAssertEqual(client.frames.count, 12); XCTAssertTrue(client.frames.allSatisfy { $0.hasRawData })
        waitFor("stop") { done in client.stop { result in self.ok(result); done() } }
        XCTAssertEqual(try client.report()?.weight, 70.5)
        // Released: the next measurement is basic mode again.
        _ = try entitlement.release()
        transport.exchanges[try core.command("START_CONTINUOUS_MEASUREMENT")]![0] += Array(repeating: try Vectors.packet(), count: 12).flatMap { $0 }
        waitFor("restart") { done in client.start { result in self.ok(result); done() } }
        XCTAssertEqual(client.frames.count, 1); XCTAssertFalse(client.frames[0].hasRawData)
        waitFor("close") { done in client.close { _ in done() } }
    }
    func testBatteryIsReadFromTheScaleIdleOrMeasuring() throws {
        // Packets carry no battery field: the level comes from GET_BATTERY_LEVEL.
        let core = try FisicaCore(), transport = try scale(core), battery = try core.command("GET_BATTERY_LEVEL")
        transport.exchanges[battery] = [Vectors.bytes("243147010050fff0")]
        let client = try FisicaClient(core: core, transport: transport)
        waitFor("connect") { done in client.connect(.usb) { _ in done() } }
        XCTAssertNil(client.battery); XCTAssertFalse(transport.sent.contains(battery))
        waitFor("battery") { done in client.readBattery { result in XCTAssertEqual(try? result.get(), 80); done() } }
        waitFor("start") { done in client.start { result in self.ok(result); done() } }
        waitFor("battery while measuring") { done in client.readBattery { result in XCTAssertEqual(try? result.get(), 80); done() } }
        XCTAssertEqual(client.state, .measuring)
        waitFor("stop") { done in client.stop { result in self.ok(result); done() } }
        let report = try XCTUnwrap(try client.report())
        XCTAssertEqual(report.battery, 80); XCTAssertTrue(report.batteryValid); XCTAssertFalse(report.frames[0].batteryValid)
        transport.exchanges[battery] = [Vectors.bytes("243147000001fff0")]
        waitFor("refused battery") { done in client.readBattery { result in
            if case .failure(let error) = result { XCTAssertTrue(error is FisicaCommandError) } else { XCTFail("Expected failure") }
            done() } }
        XCTAssertEqual(client.state, .idle)
        waitFor("start after refusal") { done in client.start { result in self.ok(result); done() } }
        waitFor("close") { done in client.close { _ in done() } }
    }
    func testTransportThatNeverBecomesReadyFailsInsteadOfHanging() throws {
        let core = try FisicaCore(), transport = MockTransport(); transport.becomesReady = false
        let client = try FisicaClient(core: core, transport: transport)
        waitFor("timeout") { done in client.connect(.ble, timeout: 0.2) { result in
            XCTAssertEqual((self.failure(result) as? FisicaError)?.code, 1001); done() } }
        XCTAssertFalse(transport.isOpen)
    }
    func testRejectedStartIsAnErrorWithDeviceCodeAndCloseStillReleases() throws {
        let core = try FisicaCore(), transport = try scale(core)
        let start = try core.command("START_CONTINUOUS_MEASUREMENT")
        transport.exchanges[start] = [[0x24, start[1], start[2], 0, 0, 7, 0xff, 0xf0]]
        let client = try FisicaClient(core: core, transport: transport)
        waitFor("connect") { done in client.connect(.usb) { _ in done() } }
        waitFor("start") { done in client.start { result in
            let error = self.failure(result) as? FisicaCommandError
            XCTAssertEqual(error?.deviceCode, 7); XCTAssertEqual(error?.code, FisicaStatus.deviceRejected.rawValue); done() } }
        waitFor("close") { done in client.close { _ in done() } }
        XCTAssertFalse(transport.isOpen)
    }
    func testLinkLossAsksHostToReconnect() throws {
        let core = try FisicaCore(), transport = try scale(core)
        let client = try FisicaClient(core: core, transport: transport)
        waitFor("connect") { done in client.connect(.usb) { _ in done() } }
        waitFor("lost") { done in client.onLinkLost = { done() }; transport.drop() }
        XCTAssertEqual(client.state, .recovering)
        waitFor("reconnect") { done in client.connect(.usb) { result in self.ok(result); done() } }
        XCTAssertEqual(client.state, .idle)
        waitFor("close") { done in client.close { _ in done() } }
    }
    func testLinkMustMatchTheTransport() throws {
        let core = try FisicaCore(), transport = try scale(core)
        transport.link = .usb
        let client = try FisicaClient(core: core, transport: transport)
        waitFor("mismatch") { done in client.connect(.ble) { result in
            XCTAssertEqual((self.failure(result) as? FisicaError)?.code, Int32(FisicaStatus.invalidArgument.rawValue)); done() } }
        XCTAssertFalse(transport.isOpen); XCTAssertTrue(transport.sent.isEmpty)
        waitFor("connect") { done in client.connect(.usb) { result in self.ok(result); done() } }
        waitFor("close") { done in client.close { _ in done() } }
    }
    func testStartClearsThePreviousMeasurementAndFramesStayBounded() throws {
        let core = try FisicaCore(), transport = try scale(core, frames: 8)
        let client = try FisicaClient(core: core, transport: transport, entitlement: try Vectors.entitlement(core, key: "full"))
        client.maxFrames = Int(FisicaLimit.analysisFrames.rawValue) + 1
        XCTAssertEqual(client.maxFrames, Int(FisicaLimit.analysisFrames.rawValue))
        client.maxFrames = 5
        waitFor("connect") { done in client.connect(.usb) { _ in done() } }
        waitFor("start") { done in client.start { result in self.ok(result); done() } }
        waitFor("stop") { done in client.stop { _ in done() } }
        XCTAssertEqual(client.frames.count, 5); XCTAssertEqual(client.droppedFrames, 3)
        XCTAssertEqual(try client.report()?.weight, 70.5)
        transport.exchanges[try core.command("START_CONTINUOUS_MEASUREMENT")]![0].removeLast(8 * 645)
        waitFor("restart") { done in client.start { result in self.ok(result); done() } }
        XCTAssertTrue(client.frames.isEmpty); XCTAssertEqual(client.droppedFrames, 0)
        waitFor("close") { done in client.close { _ in done() } }
    }
    func testOnlineActivationRenewalAndStorage() throws {
        // Runs against the reference mock developer portal started by tools/verify_bindings.py. It holds
        // the unactivated `unbound` key and accepts only this PC's activation and empty refreshes.
        let environment = Vectors.environment
        guard let address = environment["FISICA_ENTITLEMENT_ENDPOINT"], let endpoint = URL(string: address),
              let directory = environment["FISICA_ENTITLEMENT_STORAGE"], let serverNow = UInt64(environment["FISICA_ENTITLEMENT_NOW"] ?? "") else {
            throw XCTSkip("Mock entitlement server not configured")
        }
        let core = try FisicaCore(), unbound = try Vectors.string("unbound"), target = try Vectors.string("target")
        let storage = URL(fileURLWithPath: directory, isDirectory: true)
        var clock = serverNow
        let entitlement = try FisicaEntitlement(core: core, endpoint: endpoint, storage: storage, clock: { clock })
        // Importing a new key activates it for this PC in the background; syncNow waits for that exchange.
        XCTAssertEqual(try entitlement.importToken(unbound), .accepted)
        XCTAssertEqual(entitlement.syncNow(), EntitlementSyncResult(activated: false, refreshed: false, error: nil))  // nothing due after it
        var status = try entitlement.status()
        XCTAssertEqual(status.mode, .full, entitlement.lastSyncError ?? ""); XCTAssertEqual(status.bindType, .pc); XCTAssertEqual(status.bind, target)
        XCTAssertEqual(status.verifiedAt, serverNow); XCTAssertEqual(status.keyUntil, serverNow + 90 * Vectors.day)
        XCTAssertNil(entitlement.lastSyncError)
        // A later start with the original key file finds the activated key stored for this contract.
        let reopened = try FisicaEntitlement(core: core, storage: storage, clock: { serverNow })
        XCTAssertEqual(try reopened.importToken(unbound), .accepted)
        XCTAssertEqual(try reopened.status().mode, .full); XCTAssertEqual(try reopened.status().bind, target)
        // 30 days after the last confirmation a renewal is due: an empty refresh.
        clock = serverNow + 31 * Vectors.day
        XCTAssertTrue(try entitlement.status().warnings.contains(.renewDue))
        XCTAssertEqual(entitlement.syncNow(), EntitlementSyncResult(activated: false, refreshed: true, error: nil))
        status = try entitlement.status()
        XCTAssertEqual(status.bind, target); XCTAssertEqual(status.key, "key_test_1")
        // A communication failure is recorded, raises nothing and changes no license state.
        let offline = try FisicaEntitlement(core: core, endpoint: URL(string: "http://127.0.0.1:9")!,
                                            storage: storage.appendingPathComponent("offline", isDirectory: true), timeout: 2, clock: { serverNow })
        XCTAssertEqual(try offline.importToken(unbound), .accepted)
        offline.checkStatus()
        let failed = offline.syncNow()
        XCTAssertEqual(failed.error?.hasPrefix("network:"), true); XCTAssertFalse(failed.activated)
        XCTAssertEqual(try offline.status().reason, .notActivated); XCTAssertEqual(offline.lastSyncError, failed.error)
    }
}
