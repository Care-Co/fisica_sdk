import XCTest
@testable import FisicaScale
final class ContractTests: XCTestCase {
    func fixtures(_ name: String) throws -> Any {
        let path=ProcessInfo.processInfo.environment["FISICA_FIXTURES"]!
        return try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath:path+"/"+name)))
    }
    func bytes(_ hex: String) -> [UInt8] { stride(from:0,to:hex.count,by:2).map { i in let start=hex.index(hex.startIndex,offsetBy:i); return UInt8(hex[start..<hex.index(start,offsetBy:2)],radix:16)! } }
    func testSharedSessions() throws {
        let core=try FisicaCore()
        for v in try fixtures("sessions.json") as! [[String:Any]] {
            let session=try FisicaSession(core:core); defer { session.close() }
            for step in v["steps"] as! [[String:Any]] {
                let now=(step["now_ms"] as! NSNumber).int64Value
                if let action=step["action"] as? NSNumber { try session.action(action.uint32Value,value:(step["value"] as! NSNumber).int64Value,nowMs:now) }
                else if let hex=step["feed_hex"] as? String { try session.feed(bytes(hex),nowMs:now) }
                else if let command=step["command"] as? String { try session.command(command,nowMs:now) }
                else { try session.tick(now) }
                let actual=try session.drain().map { e -> [Any] in [e.kind,e.state,e.status,e.deviceError,e.operation,e.payload.map { String(format:"%02x",$0) }.joined()] }
                XCTAssertEqual(try JSONSerialization.data(withJSONObject:actual),try JSONSerialization.data(withJSONObject:step["events"]!),v["id"] as! String)
            }
        }
    }
    func testSharedPackets() throws {
        let core=try FisicaCore()
        for v in try fixtures("legacy-processing.json") as! [[String:Any]] {
            let result=try core.decode(bytes(v["packet_hex"] as! String)); XCTAssertEqual(result.weight,v["weight"] as! Double)
            XCTAssertEqual(result.raw.count,638); XCTAssertEqual(result.display.count,841)
        }
        XCTAssertThrowsError(try core.command("unknown")) { XCTAssertEqual(($0 as! FisicaError).code,4) }
    }
}

/// High-level client and entitlement against the same core, without hardware.
final class ClientTests: XCTestCase {
    func bytes(_ hex: String) -> [UInt8] { stride(from: 0, to: hex.count, by: 2).map { i in let start = hex.index(hex.startIndex, offsetBy: i); return UInt8(hex[start..<hex.index(start, offsetBy: 2)], radix: 16)! } }
    func fixture(_ name: String) throws -> [[String: Any]] {
        let path = ProcessInfo.processInfo.environment["FISICA_FIXTURES"]!
        return try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: path + "/" + name))) as! [[String: Any]]
    }
    func scale(_ core: FisicaCore, frames: Int = 12) throws -> MockTransport {
        let packet = bytes(try fixture("legacy-processing.json").first { $0["id"] as? String == "synthetic-recorded-layout" }!["packet_hex"] as! String)
        var exchanges: [[UInt8]: [[UInt8]]] = [try core.command("GET_FIRMWARE_VERSION"): [bytes("24434701322e302e31fff0")]]
        for name in ["SET_DEVICE_USB", "SET_DEVICE_BLE", "START_CONTINUOUS_MEASUREMENT", "STOP_CONTINUOUS_MEASUREMENT", "SET_DEVICE_STOP"] {
            let command = try core.command(name)
            exchanges[command] = [[0x24, command[1], command[2], 1, 0, 0, 0xff, 0xf0]]
        }
        exchanges[try core.command("START_CONTINUOUS_MEASUREMENT")]![0] += Array(repeating: packet, count: frames).flatMap { $0 }
        return MockTransport(exchanges: exchanges)
    }
    func waitFor(_ name: String, _ body: (@escaping () -> Void) -> Void) { let done = expectation(description: name); body { done.fulfill() }; wait(for: [done], timeout: 5) }
    func ok(_ result: Result<SessionEvent, Error>, _ line: UInt = #line) { if case .failure(let error) = result { XCTFail("\(error)", line: line) } }
    func failure(_ result: Result<SessionEvent, Error>) -> Error? { if case .failure(let error) = result { return error }; XCTFail("Expected failure"); return nil }

    func testConnectMeasureStopReportClose() throws {
        let core = try FisicaCore(), transport = try scale(core)
        let client = try FisicaClient(core: core, transport: transport)
        var received = 0
        client.onFrame = { _ in received += 1 }
        waitFor("connect") { done in client.connect(.usb) { result in self.ok(result); done() } }
        XCTAssertEqual(client.firmwareVersion, "2.0.1")
        waitFor("start") { done in client.start { result in self.ok(result); done() } }
        XCTAssertEqual(client.state, .measuring)
        waitFor("stop") { done in client.stop { result in self.ok(result); done() } }
        XCTAssertEqual(received, 12)
        let report = try XCTUnwrap(try client.report())
        XCTAssertEqual(report.weight, 70.5); XCTAssertEqual(report.frameCount, 12)
        XCTAssertEqual(report.frames[0].raw.count, 638); XCTAssertEqual(report.frames[0].display.count, 841); XCTAssertEqual(report.frames[0].packet.count, 645)
        waitFor("close") { done in client.close { result in XCTAssertNotNil(result); done() } }
        XCTAssertTrue(transport.sent.contains(try core.command("SET_DEVICE_USB")))
        XCTAssertEqual(transport.sent.last, try core.command("SET_DEVICE_STOP"))
        XCTAssertFalse(transport.isOpen)
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
        let client = try FisicaClient(core: core, transport: transport)
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
    func testEntitlementIsStatusOnlyAndNeverBlocksMeasurement() throws {
        let core = try FisicaCore()
        let tokens = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: ProcessInfo.processInfo.environment["FISICA_FIXTURES"]! + "/entitlement.json"))) as! [String: Any]
        let now = (tokens["now"] as! NSNumber).uint64Value
        var clock = now
        let entitlement = try FisicaEntitlement(core: core, storage: nil, clock: { clock })
        var seen: [FisicaEntitlementWarning] = []
        entitlement.onWarning = { new, _ in seen += new }
        XCTAssertEqual(try entitlement.status().contract, .unverified)
        XCTAssertEqual(try entitlement.importToken(tokens["tampered"] as! String), .rejectedSignature)
        XCTAssertEqual(try entitlement.status().company, "")
        XCTAssertEqual(try entitlement.importToken(tokens["valid"] as! String), .accepted)
        var status = try entitlement.status()
        XCTAssertEqual(status.baseLicense, .granted); XCTAssertEqual(status.verification, .fresh); XCTAssertEqual(status.contract, .active)
        XCTAssertEqual(status.company, "acme"); XCTAssertEqual(status.contractReference, "ctr-1"); XCTAssertEqual(status.keyEnvironment, .test)
        clock = now + 31 * 86400
        status = try entitlement.status()
        XCTAssertEqual(status.baseLicense, .granted); XCTAssertEqual(status.verification, .refreshDue); XCTAssertEqual(status.contract, .active)
        XCTAssertEqual(try entitlement.importToken(tokens["revoked"] as! String), .accepted)
        XCTAssertEqual(try entitlement.status().contract, .revoked)
        // Measurement still runs with a revoked contract and outdated confirmation.
        let transport = try scale(core), client = try FisicaClient(core: core, transport: transport, entitlement: entitlement)
        waitFor("connect") { done in client.connect(.usb) { _ in done() } }
        waitFor("start") { done in client.start { result in self.ok(result); done() } }
        waitFor("stop") { done in client.stop { _ in done() } }
        XCTAssertEqual(try client.report()?.weight, 70.5)
        waitFor("close") { done in client.close { _ in done() } }
        // Using the SDK leaves no trace: the stored state is still only the signed token.
        let state = String(decoding: try entitlement.exportState(), as: UTF8.self)
        XCTAssertEqual(state, "FSS1\nC " + (tokens["revoked"] as! String) + "\n")
        let restored = try FisicaEntitlement(core: core, storage: nil, clock: { clock })
        try restored.restoreState(try entitlement.exportState())
        XCTAssertEqual(try restored.status().company, "acme")
        XCTAssertTrue(seen.contains(.importRejected) && seen.contains(.testKey) && seen.contains(.contractRevoked))
    }
    func testOnlineConfirmationAndStorage() throws {
        // Runs against the reference mock server started by tools/verify_bindings.py.
        let environment = ProcessInfo.processInfo.environment
        guard let address = environment["FISICA_ENTITLEMENT_ENDPOINT"], let endpoint = URL(string: address),
              let directory = environment["FISICA_ENTITLEMENT_STORAGE"], let now = UInt64(environment["FISICA_ENTITLEMENT_NOW"] ?? "") else {
            throw XCTSkip("Mock entitlement server not configured")
        }
        let core = try FisicaCore()
        let tokens = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: environment["FISICA_FIXTURES"]! + "/entitlement.json"))) as! [String: Any]
        let valid = tokens["valid"] as! String, storage = URL(fileURLWithPath: directory, isDirectory: true)
        let entitlement = try FisicaEntitlement(core: core, endpoint: endpoint, storage: storage, clock: { now })
        XCTAssertEqual(try entitlement.importToken(valid), .accepted)
        XCTAssertNil(entitlement.syncNow(), entitlement.lastSyncError ?? "")
        XCTAssertEqual(try entitlement.status().verifiedAt, now)  // Server-signed confirmation time.
        entitlement.checkStatus()
        XCTAssertNil(entitlement.syncNow())
        // A later start with the app's older token still finds the newer stored confirmation.
        let reopened = try FisicaEntitlement(core: core, storage: storage, clock: { now })
        try reopened.importToken(valid)
        XCTAssertEqual(try reopened.status().verifiedAt, now)
        // A communication failure is recorded, raises nothing and changes no state.
        let offline = try FisicaEntitlement(core: core, endpoint: URL(string: "http://127.0.0.1:9")!,
                                            storage: storage.appendingPathComponent("offline", isDirectory: true), clock: { now })
        try offline.importToken(valid); offline.checkStatus()
        XCTAssertEqual(offline.syncNow()?.hasPrefix("network:"), true)
        XCTAssertEqual(try offline.status().contract, .active); XCTAssertEqual(try offline.status().verification, .fresh)
    }
}
