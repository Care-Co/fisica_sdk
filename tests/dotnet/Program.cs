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

// High-level client, transports and entitlement against the same core, without hardware.
var values=File.ReadAllLines(args[2]).Select(line => line.Split('=',2)).ToDictionary(p => p[0],p => p[1]);
byte[] packet=Convert.FromHexString(values["packet"]);
void Require(bool condition,string what) { if(!condition) throw new Exception("C# client contract failed: "+what); }
MockTransport Scale(int frames=12) {
    var transport=new MockTransport();
    transport.Reply(core.Command("GET_FIRMWARE_VERSION"),Convert.FromHexString("24434701322e302e31fff0"));
    foreach(var name in new[]{"SET_DEVICE_USB","SET_DEVICE_BLE","START_CONTINUOUS_MEASUREMENT","STOP_CONTINUOUS_MEASUREMENT","SET_DEVICE_STOP"}) {
        var command=core.Command(name); IEnumerable<byte> ack=new byte[]{0x24,command[1],command[2],1,0,0,0xff,0xf0};
        if(name=="START_CONTINUOUS_MEASUREMENT") for(int i=0;i<frames;i++) ack=ack.Concat(packet);
        transport.Reply(command,ack.ToArray());
    }
    return transport;
}
async Task<Exception?> Failure(Task task) { try { await task.WaitAsync(TimeSpan.FromSeconds(5)); return null; } catch(Exception error) { return error; } }
{
    var transport=Scale(); var client=new FisicaClient(core,transport); int received=0; client.Frame+=_ => Interlocked.Increment(ref received);
    await client.ConnectAsync(FisicaLink.Usb).WaitAsync(TimeSpan.FromSeconds(5));
    Require(client.FirmwareVersion=="2.0.1","firmware");
    await client.StartAsync().WaitAsync(TimeSpan.FromSeconds(5));
    Require(client.State==FisicaSessionState.Measuring,"measuring state");
    await client.StopAsync().WaitAsync(TimeSpan.FromSeconds(5));
    Require(received==12 && client.Frames.Count==12,"frames");
    var report=client.Report()!;
    Require(report.Weight==70.5 && report.FrameCount==12,"report");
    Require(report.Frames[0].Raw.Length==638 && report.Frames[0].Display.Length==841 && report.Frames[0].Packet.Length==645,"frame shapes");
    await client.CloseAsync().WaitAsync(TimeSpan.FromSeconds(5));
    Require(!transport.IsOpen && transport.Sent[^1].SequenceEqual(core.Command("SET_DEVICE_STOP")),"release");
    Require(transport.Sent.Any(s => s.SequenceEqual(core.Command("SET_DEVICE_USB"))),"usb mode");
}
{
    var transport=new MockTransport { BecomesReady=false }; var client=new FisicaClient(core,transport);
    var error=await Failure(client.ConnectAsync(FisicaLink.Ble,TimeSpan.FromMilliseconds(200)));
    Require(error is FisicaException { Code: 1001 } && !transport.IsOpen,"ready timeout");
    await client.CloseAsync();
}
{
    var transport=Scale(); var start=core.Command("START_CONTINUOUS_MEASUREMENT");
    transport.Reply(start,new byte[]{0x24,start[1],start[2],0,0,7,0xff,0xf0});
    var client=new FisicaClient(core,transport);
    await client.ConnectAsync().WaitAsync(TimeSpan.FromSeconds(5));
    var error=await Failure(client.StartAsync()) as FisicaCommandException;
    Require(error is { DeviceCode: 7, Code: FisicaStatus.DeviceRejected },"device rejection");
    await Failure(client.CloseAsync());
    Require(!transport.IsOpen,"release after rejection");
}
{
    var transport=Scale(); var client=new FisicaClient(core,transport);
    await client.ConnectAsync().WaitAsync(TimeSpan.FromSeconds(5));
    var lost=new TaskCompletionSource(); client.LinkLost+=() => lost.TrySetResult();
    transport.Drop(); await lost.Task.WaitAsync(TimeSpan.FromSeconds(5));
    Require(client.State==FisicaSessionState.Recovering,"recovering");
    await client.ConnectAsync().WaitAsync(TimeSpan.FromSeconds(5));
    Require(client.State==FisicaSessionState.Idle,"reconnected");
    await client.CloseAsync().WaitAsync(TimeSpan.FromSeconds(5));
}
{
    // The link comes from the transport; a different one fails before anything is sent.
    var transport=Scale(); transport.Link=FisicaLink.Ble; var client=new FisicaClient(core,transport);
    var error=await Failure(client.ConnectAsync(FisicaLink.Usb));
    Require(error is FisicaException { Code: (int)FisicaStatus.InvalidArgument } && !transport.IsOpen && transport.Sent.Count==0,"link mismatch");
    await client.ConnectAsync().WaitAsync(TimeSpan.FromSeconds(5));
    await client.StartAsync().WaitAsync(TimeSpan.FromSeconds(5));
    Require(transport.Sent.Any(s => s.SequenceEqual(core.Command("SET_DEVICE_BLE"))),"transport link is the default");
    await client.CloseAsync().WaitAsync(TimeSpan.FromSeconds(5));
    Require(new SerialTransport("COM9").Link==FisicaLink.Usb && ((IFisicaTransport)new MockTransport()).Link==null,"transport links");
}
{
    // Start clears the previous measurement; frames stay bounded.
    var transport=Scale(8); var client=new FisicaClient(core,transport);
    Require(await Failure(Task.Run(() => client.MaxFrames=(int)FisicaLimit.AnalysisFrames+1)) is ArgumentOutOfRangeException,"frame limit");
    client.MaxFrames=5;
    await client.ConnectAsync().WaitAsync(TimeSpan.FromSeconds(5));
    await client.StartAsync().WaitAsync(TimeSpan.FromSeconds(5));
    await client.StopAsync().WaitAsync(TimeSpan.FromSeconds(5));
    Require(client.Frames.Count==5 && client.DroppedFrames==3 && client.Report()!.Weight==70.5,"bounded frames");
    var start=core.Command("START_CONTINUOUS_MEASUREMENT"); transport.Reply(start,new byte[]{0x24,start[1],start[2],1,0,0,0xff,0xf0});
    await client.StartAsync().WaitAsync(TimeSpan.FromSeconds(5));
    Require(client.Frames.Count==0 && client.DroppedFrames==0,"start clears frames");
    await client.CloseAsync().WaitAsync(TimeSpan.FromSeconds(5));
}
{
    ulong now=ulong.Parse(values["now"]),clock=now; FisicaEntitlementWarning seen=0;
    using var entitlement=new FisicaEntitlement(core,() => clock,storage: null); entitlement.Warning+=(fresh,_) => seen|=fresh;
    Require(entitlement.Status().Contract==FisicaContract.Unverified,"unverified");
    Require(entitlement.ImportToken(values["tampered"])==FisicaEntitlementOutcome.RejectedSignature && entitlement.Status().Company=="","tampered");
    Require(entitlement.ImportToken(values["valid"])==FisicaEntitlementOutcome.Accepted,"valid");
    var status=entitlement.Status();
    Require(status is { BaseLicense: FisicaBaseLicense.Granted, Verification: FisicaVerification.Fresh, Contract: FisicaContract.Active, Company: "acme", ContractReference: "ctr-1", KeyEnvironment: FisicaKeyEnvironment.Test },"fresh status");
    Require(entitlement.ImportToken(values["older"])==FisicaEntitlementOutcome.IgnoredOlder,"older ignored");
    clock=now+31*86400; status=entitlement.Status();
    Require(status is { BaseLicense: FisicaBaseLicense.Granted, Verification: FisicaVerification.RefreshDue, Contract: FisicaContract.Active },"refresh due is not expiry");
    Require(entitlement.ImportToken(values["revoked"])==FisicaEntitlementOutcome.Accepted && entitlement.Status().Contract==FisicaContract.Revoked,"revoked");
    var transport=Scale(); var client=new FisicaClient(core,transport,entitlement);
    await client.ConnectAsync().WaitAsync(TimeSpan.FromSeconds(5));
    await client.StartAsync().WaitAsync(TimeSpan.FromSeconds(5));
    await client.StopAsync().WaitAsync(TimeSpan.FromSeconds(5));
    Require(client.Report()!.Weight==70.5,"measurement with revoked contract");
    await client.CloseAsync().WaitAsync(TimeSpan.FromSeconds(5));
    // Using the SDK leaves no trace: the stored state is still only the signed token.
    Require(System.Text.Encoding.ASCII.GetString(entitlement.ExportState())=="FSS1\nC "+values["revoked"]+"\n","no usage state");
    using var restored=new FisicaEntitlement(core,() => clock,storage: null); restored.RestoreState(entitlement.ExportState());
    Require(restored.Status().Company=="acme","restore");
    var expected=FisicaEntitlementWarning.ImportRejected|FisicaEntitlementWarning.TestKey|FisicaEntitlementWarning.ContractRevoked;
    Require((seen&expected)==expected,"warnings");
}
{
    // Serial transport: a missing port is reported as an error and a loss, never thrown into the caller.
    var serial=new SerialTransport("fisica-missing-port"); bool lost=false; Exception? reported=null;
    serial.Lost+=() => lost=true; serial.Error+=e => reported=e; serial.Open();
    Require(lost && reported is FisicaException { Code: 1001 },"serial open failure");
    serial.Close();
}
#if WINDOWS
{
    // Build machines have no Scale and usually no Bluetooth radio: every failure must arrive as an event.
    var ble=new BleTransport { ReadyTimeout=TimeSpan.FromSeconds(5) };
    var errors=new List<Exception>(); var lost=new TaskCompletionSource(); var sendFailed=new TaskCompletionSource();
    ble.Error+=e => { lock(errors) errors.Add(e); }; ble.Lost+=() => lost.TrySetResult(); ble.SendFailed+=_ => sendFailed.TrySetResult();
    ble.Open();                                   // Nothing selected yet.
    await lost.Task.WaitAsync(TimeSpan.FromSeconds(10));
    lock(errors) Require(errors.Count>0 && errors.All(e => e is FisicaException { Code: 1001 }),"BLE open without a selection is reported");
    ble.Send(new byte[]{1}); await sendFailed.Task.WaitAsync(TimeSpan.FromSeconds(5));
    ble.Scan(); await Task.Delay(500); ble.StopScan();
    Require(ble.Mtu==BleTransport.DefaultMtu,"default MTU before a connection");
    var client=new FisicaClient(core,ble);
    ble.Select(0x0000C0FFEE01,new FisicaBleProfile(FisicaBleProfile.NordicUart.Write,FisicaBleProfile.NordicUart.Notify));
    var failure=await Failure(client.ConnectAsync(FisicaLink.Ble,TimeSpan.FromSeconds(20)));
    Require(failure is FisicaException { Code: 1001 },"BLE connect to an absent Scale fails cleanly: "+failure);
    await client.CloseAsync();
    ble.Close();
    Console.WriteLine("C# Windows BLE transport: failure paths verified without a Scale");
}
#endif
if (values.TryGetValue("endpoint",out var endpoint)) {
    // Online confirmation and token storage against the reference mock server.
    ulong clock=ulong.Parse(values["server_now"]); string directory=values["storage"];
    using var entitlement=new FisicaEntitlement(core,() => clock,endpoint,directory);
    Require(entitlement.ImportToken(values["valid"])==FisicaEntitlementOutcome.Accepted,"online import");
    Require(await entitlement.SyncAsync()==null,"sync: "+entitlement.LastSyncError);
    Require(entitlement.Status().VerifiedAt==clock,"refresh adopts the server-signed time");
    entitlement.CheckStatus();
    Require(await entitlement.SyncAsync()==null,"second refresh");
    // A later start with the app's older token still finds the newer stored confirmation.
    using var reopened=new FisicaEntitlement(core,() => clock,null,directory);
    reopened.ImportToken(values["valid"]);
    Require(reopened.Status().VerifiedAt==clock,"stored confirmation restored");
    // A communication failure is recorded, raises nothing and changes no state.
    using var offline=new FisicaEntitlement(core,() => clock,"http://127.0.0.1:9",Path.Combine(directory,"offline"));
    offline.ImportToken(values["valid"]); offline.CheckStatus();
    Require((await offline.SyncAsync())!.StartsWith("network:"),"network failure recorded");
    Require(offline.Status() is { Contract: FisicaContract.Active, Verification: FisicaVerification.Fresh },"failure is not expiry");
}
Console.WriteLine("C# client, transports and entitlement contract passed");
