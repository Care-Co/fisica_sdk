import Foundation
import CryptoKit
@main struct Contract {
    static func bytes(_ hex: String) -> [UInt8] { stride(from:0,to:hex.count,by:2).map { i in let start=hex.index(hex.startIndex,offsetBy:i); return UInt8(hex[start..<hex.index(start,offsetBy:2)],radix:16)! } }
    static func main() throws {
        let core=try FisicaCore(); let root=CommandLine.arguments[1]
        func fixtures(_ name: String) throws -> [[String:Any]] { try JSONSerialization.jsonObject(with:Data(contentsOf:URL(fileURLWithPath:root+"/"+name))) as! [[String:Any]] }
        for v in try fixtures("sessions.json") {
            let session=try FisicaSession(core:core); defer { session.close() }
            for step in v["steps"] as! [[String:Any]] {
                let now=(step["now_ms"] as! NSNumber).int64Value
                if let a=step["action"] as? NSNumber { try session.action(a.uint32Value,value:(step["value"] as! NSNumber).int64Value,nowMs:now) }
                else if let hex=step["feed_hex"] as? String { try session.feed(bytes(hex),nowMs:now) }
                else if let name=step["command"] as? String { try session.command(name,nowMs:now) }
                else { try session.tick(now) }
                let actual=try session.drain().map { e -> [Any] in [e.kind,e.state,e.status,e.deviceError,e.operation,e.payload.map { String(format:"%02x",$0) }.joined()] }
                let actualBytes=try JSONSerialization.data(withJSONObject:actual)
                let expectedBytes=try JSONSerialization.data(withJSONObject:step["events"]!)
                precondition(actualBytes==expectedBytes)
            }
        }
        for v in try fixtures("legacy-processing.json") {
            let result=try core.decode(bytes(v["packet_hex"] as! String)); precondition(result.weight==v["weight"] as! Double)
            let hash=result.display.withUnsafeBytes { SHA256.hash(data:Data($0)).map { String(format:"%02x",$0) }.joined() }
            precondition(hash==v["display_sha256"] as! String)
        }
        var grid=[Double](repeating:0,count:841); grid[4*29+3]=100; grid[4*29+20]=100
        let analysis=try core.analyze(Array(repeating:grid,count:12).flatMap {$0},weights:Array(repeating:70.5,count:12),rows:29,cols:29)
        precondition(analysis==[70.5,1,1,1,1])
        print("Swift shared session and processing vectors passed")
    }
}
