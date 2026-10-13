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
