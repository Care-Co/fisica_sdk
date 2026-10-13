using CareCo.Fisica;
using System.Text.Json;
using System.Security.Cryptography;
var core=new FisicaCore(args[0]);
using var vectors=JsonDocument.Parse(File.ReadAllText(args[1]+"/sessions.json"));
foreach(var vector in vectors.RootElement.EnumerateArray()) {
    using var session=new FisicaSession(core);
    foreach(var step in vector.GetProperty("steps").EnumerateArray()) {
        long now=step.GetProperty("now_ms").GetInt64();
        if(step.TryGetProperty("action",out var action)) session.Action(action.GetUInt32(),step.GetProperty("value").GetInt64(),now);
        else if(step.TryGetProperty("feed_hex",out var feed)) session.Feed(Convert.FromHexString(feed.GetString()!),now);
        else if(step.TryGetProperty("command",out var command)) session.Command(command.GetString()!,now);
        else session.Tick(now);
        var events=session.Drain(); var expected=step.GetProperty("events");
        if(events.Count!=expected.GetArrayLength()) throw new Exception("event count mismatch");
        for(int i=0;i<events.Count;i++) {
            var e=events[i]; for(int j=0;j<5;j++) if(e.Metadata[j]!=expected[i][j].GetUInt32()) throw new Exception("metadata mismatch");
            if(Convert.ToHexString(e.Payload).ToLowerInvariant()!=expected[i][5].GetString()) throw new Exception("payload mismatch");
        }
    }
}
using var packets=JsonDocument.Parse(File.ReadAllText(args[1]+"/legacy-processing.json"));
foreach(var v in packets.RootElement.EnumerateArray()) {
    var m=core.Decode(Convert.FromHexString(v.GetProperty("packet_hex").GetString()!));
    if(m.Weight!=v.GetProperty("weight").GetDouble()) throw new Exception("weight mismatch");
    byte[] data=new byte[m.Display.Length*8];Buffer.BlockCopy(m.Display,0,data,0,data.Length);
    if(Convert.ToHexString(SHA256.HashData(data)).ToLowerInvariant()!=v.GetProperty("display_sha256").GetString()) throw new Exception("display mismatch");
}
var grid=new double[841];grid[4*29+3]=grid[4*29+20]=100;
var analysis=core.Analyze(Enumerable.Range(0,12).SelectMany(_=>grid).ToArray(),Enumerable.Repeat(70.5,12).ToArray(),29,29);
if(analysis==null || !analysis.SequenceEqual(new double[]{70.5,1,1,1,1})) throw new Exception("analysis mismatch");
Console.WriteLine("C# shared session and processing vectors passed");
