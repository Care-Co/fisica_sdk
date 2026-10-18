using CareCo.Fisica;
using System.Collections.Concurrent;
using System.Net;
using System.Net.Sockets;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
var core=new FisicaCore(args[0]);
// Shared vectors plus keys activated for this PC (flat key=value from tools/verify_bindings.py).
var values=File.ReadAllLines(args[2]).Select(line => line.Split('=',2)).ToDictionary(p => p[0],p => p[1]);
void Require(bool condition,string what) { if(!condition) throw new Exception("C# contract failed: "+what); }
Exception? Catch(Action action) { try { action(); return null; } catch(Exception error) { return error; } }
const ulong Day=86400;
ulong now=ulong.Parse(values["now"]);
byte[] packet=Convert.FromHexString(values["packet"]);
var target=(Type: values["target_type"]=="pc" ? FisicaBindType.Pc : FisicaBindType.App, Value: values["target"]);
// A license in memory at the vectors' time, holding one of the vectors' keys.
FisicaEntitlement License(string? key=null,ulong? at=null) {
    ulong time=at ?? now; var license=new FisicaEntitlement(core,() => time,storage: null);
    if (key!=null) Require(license.ImportToken(values[key])==FisicaEntitlementOutcome.Accepted,key+" accepted");
    return license;
}
// The developer portal's check of an activation (FAC1) or release (FRC1) code; returns its members.
Dictionary<string,string> ReadCode(string code,string prefix) {
    var parts=code.Split('.');
    Require(parts.Length==3 && parts[0]==prefix,prefix+" code format");
    var check=Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes((prefix=="FRC1" ? "fisica-release-code-v1|" : "")+prefix+"."+parts[1])));
    Require(parts[2]==check[..8].ToLowerInvariant(),prefix+" check value");
    var body=parts[1].Replace('-','+').Replace('_','/'); body=body.PadRight(body.Length+(4-body.Length%4)%4,'=');
    return JsonSerializer.Deserialize<Dictionary<string,string>>(Convert.FromBase64String(body))!;
}

using var vectors=JsonDocument.Parse(File.ReadAllText(args[1]+"/sessions.json"));
foreach(var vector in vectors.RootElement.EnumerateArray()) {
    using var session=new FisicaSession(core);
    foreach(var step in vector.GetProperty("steps").EnumerateArray()) {
        long ms=step.GetProperty("now_ms").GetInt64();
        if(step.TryGetProperty("action",out var action)) session.Action(action.GetUInt32(),step.GetProperty("value").GetInt64(),ms);
        else if(step.TryGetProperty("feed_hex",out var feed)) session.Feed(Convert.FromHexString(feed.GetString()!),ms);
        else if(step.TryGetProperty("command",out var command)) session.Command(command.GetString()!,ms);
        else session.Tick(ms);
        var events=session.Drain(); var expected=step.GetProperty("events");
        if(events.Count!=expected.GetArrayLength()) throw new Exception("event count mismatch");
        for(int i=0;i<events.Count;i++) {
            var e=events[i]; for(int j=0;j<5;j++) if(e.Metadata[j]!=expected[i][j].GetUInt32()) throw new Exception("metadata mismatch");
            if(Convert.ToHexString(e.Payload).ToLowerInvariant()!=expected[i][5].GetString()) throw new Exception("payload mismatch");
        }
    }
}
Measurement reference;
using (var full=License("full")) {
    // Decoding a packet yourself returns raw data: it needs full mode.
    using var packets=JsonDocument.Parse(File.ReadAllText(args[1]+"/legacy-processing.json"));
    foreach(var v in packets.RootElement.EnumerateArray()) {
        var m=core.Decode(Convert.FromHexString(v.GetProperty("packet_hex").GetString()!),full);
        if(m.Weight!=v.GetProperty("weight").GetDouble()) throw new Exception("weight mismatch");
        byte[] data=new byte[m.Display.Length*8];Buffer.BlockCopy(m.Display,0,data,0,data.Length);
        if(Convert.ToHexString(SHA256.HashData(data)).ToLowerInvariant()!=v.GetProperty("display_sha256").GetString()) throw new Exception("display mismatch");
    }
    reference=core.Decode(packet,full);
    Require(reference is { Weight: 70.5, Raw.Length: 638, Display.Length: 841, Packet.Length: 645 },"decode in full mode");
}
Require(Catch(() => core.Decode(packet)) is FisicaLicenseException { Code: (int)FisicaStatus.FeatureNotLicensed },"decode without a license");
using (var basic=License("unbound")) Require(Catch(() => core.Decode(packet,basic)) is FisicaLicenseException,"decode in basic mode");
using (var limited=License("limited")) Require(Catch(() => core.Decode(packet,limited)) is FisicaLicenseException,"decode without the raw data feature");
var grid=new double[841];grid[4*29+3]=grid[4*29+20]=100;
var analysis=core.Analyze(Enumerable.Range(0,12).SelectMany(_=>grid).ToArray(),Enumerable.Repeat(70.5,12).ToArray(),29,29);
if(analysis==null || !analysis.SequenceEqual(new double[]{70.5,1,1,1,1})) throw new Exception("analysis mismatch");
Console.WriteLine("C# shared session and processing vectors passed");

{
    // License states, decided by the core from signed keys and this PC, which the core reads itself.
    FisicaEntitlementWarning seen=0; var changes=new List<EntitlementStatus>();
    using var license=License(); license.Warning+=(fresh,_) => seen|=fresh; license.Changed+=changes.Add;
    var status=license.Status();
    Require(status is { Mode: FisicaMode.Basic, Reason: FisicaModeReason.NoKey, Features: 0, Contract: FisicaContract.Unverified, Company: "", Key: "", ActivationCode: null }
        && status.Message!.Contains("Basic mode") && !status.IsFull,"no key is basic mode");
    Require(status.TargetType==target.Type && status.Target==target.Value,"the core reads this PC");
    Require(license.ActivationCode()==null && Catch(() => license.Release()) is FisicaException { Code: (int)FisicaStatus.InvalidState },"nothing to activate or release");
    Require(license.ImportToken(values["tampered"])==FisicaEntitlementOutcome.RejectedSignature && license.Status() is { Reason: FisicaModeReason.NoKey, Company: "" }
        && seen.HasFlag(FisicaEntitlementWarning.ImportRejected),"tampered key rejected");
    Require(license.ImportToken(values["unknown_key"])==FisicaEntitlementOutcome.RejectedKey,"unknown signing key");
    Require(license.ImportToken(values["legacy"])==FisicaEntitlementOutcome.Accepted
        && license.Status() is { Mode: FisicaMode.Basic, Reason: FisicaModeReason.LegacyToken, Contract: FisicaContract.Active, Company: "acme" },"a contract token is not an SDK key");
    Require(license.ImportToken(values["unbound"])==FisicaEntitlementOutcome.Accepted,"unbound key");
    status=license.Status();
    Require(status is { Mode: FisicaMode.Basic, Reason: FisicaModeReason.NotActivated, Features: 0, BindType: FisicaBindType.None, Key: "key_test_1",
        Track: FisicaTrack.Trial, KeyEnvironment: FisicaKeyEnvironment.Test } && status.Warnings.HasFlag(FisicaEntitlementWarning.TestKey),"unactivated key is basic mode");
    var code=ReadCode(status.ActivationCode!,"FAC1");
    Require(code["k"]=="key_test_1" && code["t"]==values["target_type"] && code["b"]==target.Value && license.ActivationCode()==status.ActivationCode,"FAC1 code for this PC");
    Require(Catch(() => license.Release()) is FisicaException { Code: (int)FisicaStatus.InvalidState },"an unactivated key cannot be released");
    Require(license.ImportToken(values["unbound_older"])==FisicaEntitlementOutcome.IgnoredOlder,"older key file ignored");
    Require(license.ImportToken(values["full"])==FisicaEntitlementOutcome.Accepted,"activated key");
    status=license.Status();
    Require(status is { Mode: FisicaMode.Full, Reason: FisicaModeReason.None, Company: "acme", ContractReference: "ctr-1", Key: "key_test_1",
        Track: FisicaTrack.Trial, Contract: FisicaContract.Active, ActivationCode: null, Message: null }
        && status.Features==(FisicaFeature.RawData|FisicaFeature.Export|FisicaFeature.Advanced|FisicaFeature.FullRate) && status.Has(FisicaFeature.RawData|FisicaFeature.FullRate)
        && status.BindType==target.Type && status.Bind==target.Value && status.VerifiedAt==now && status.KeyUntil==now+90*Day && status.FullUntil==now+14*Day,"activated key is full mode with every feature");
    Require(license.ImportToken(values["full_older"])==FisicaEntitlementOutcome.IgnoredOlder,"older activated key ignored");
    Require(license.ImportToken(values["other_target"])==FisicaEntitlementOutcome.RejectedTarget
        && license.Status() is { Mode: FisicaMode.Full, Key: "key_test_1", LastImport: FisicaEntitlementOutcome.RejectedTarget } && license.Status().Bind==target.Value
        && license.Status().Warnings.HasFlag(FisicaEntitlementWarning.ImportRejected),"a key activated elsewhere never replaces this PC's key");
    Require(changes.Count>=3 && changes[^1].IsFull && changes.Any(c => c.Reason==FisicaModeReason.NotActivated),"Changed events");
    // The Warning event may pass several bits at once: the list form has a message for each.
    var both=FisicaEntitlementWarning.TestKey|FisicaEntitlementWarning.ImportRejected;
    Require(FisicaLicenseMessages.Warning(both)=="" && FisicaLicenseMessages.Warnings(both).SequenceEqual(new[]{
        FisicaLicenseMessages.Warning(FisicaEntitlementWarning.ImportRejected),FisicaLicenseMessages.Warning(FisicaEntitlementWarning.TestKey)})
        && FisicaLicenseMessages.Warnings(0).Count==0,"a message for each warning bit");
    Require(license.Status().WarningMessages.SequenceEqual(FisicaLicenseMessages.Warnings(both)) && seen==both,"status warning messages");
    // Using the SDK leaves no trace: the stored state is the signed key and the latest time seen.
    Require(Encoding.ASCII.GetString(license.ExportState())==$"FSS1\nC {values["full"]}\nT {now}\n","no usage state");
    using var restored=License(); restored.RestoreState(license.ExportState());
    Require(restored.Status() is { Mode: FisicaMode.Full, Key: "key_test_1" },"restore verifies the stored key");
}
{
    // Every basic reason and limit comes from signed dates and features.
    using (var limited=License("limited"))
        Require(limited.Status() is { Mode: FisicaMode.Full } s && s.Features==(FisicaFeature.Export|FisicaFeature.FullRate) && !s.Has(FisicaFeature.RawData),"features narrow full mode");
    using (var revoked=License("revoked",now+Day)) Require(revoked.Status() is { Mode: FisicaMode.Basic, Reason: FisicaModeReason.Revoked, RevokedAt: > 0 },"released key");
    using (var other=License()) {
        Require(other.ImportToken(values["other_target"])==FisicaEntitlementOutcome.RejectedTarget && other.Status() is { Reason: FisicaModeReason.NoKey },"other PC's key is never loaded");
    }
    ulong time=now; using var license=new FisicaEntitlement(core,() => time,storage: null); license.ImportToken(values["full"]);
    time=now+8*Day; Require(license.Status() is { Mode: FisicaMode.Full } ending && ending.Warnings.HasFlag(FisicaEntitlementWarning.FullEnding),"full mode ending soon");
    time=now+15*Day; Require(license.Status() is { Mode: FisicaMode.Basic, Reason: FisicaModeReason.FullEnded, Features: 0 },"trial ended");
    time=now+15*Day-1800; Require(license.Status().Reason==FisicaModeReason.FullEnded,"a clock correction is not a rollback");
    time=now+13*Day; Require(license.Status() is { Mode: FisicaMode.Basic, Reason: FisicaModeReason.ClockRollback },"clock set back more than an hour");
}
{
    // The stored key wins over an older original key file; release gives the portal code and forgets the key.
    var directory=Directory.CreateTempSubdirectory("fisica-dotnet-").FullName;
    try {
        using (var original=new FisicaEntitlement(core,() => now,storage: directory)) {
            var file=Path.Combine(directory,"activated.fse"); File.WriteAllText(file,values["full"]+"\n");
            Require(original.ImportFile(file)==FisicaEntitlementOutcome.Accepted && original.Status().IsFull,"key file import");
            File.Delete(file);
        }
        Require(Directory.GetFiles(directory).Length==1,"activated key stored");
        using var reopened=new FisicaEntitlement(core,() => now,storage: directory);
        Require(reopened.Status() is { Mode: FisicaMode.Full, Key: "key_test_1" },"stored key loaded without importing one");
        Require(reopened.ImportToken(values["full_older"])==FisicaEntitlementOutcome.IgnoredOlder && reopened.Status() is { Mode: FisicaMode.Full, VerifiedAt: var at } && at==now,"stored key wins");
        var released=ReadCode(reopened.Release(),"FRC1");
        Require(released["k"]=="key_test_1" && released["t"]==values["target_type"] && released["b"]==target.Value,"FRC1 release code");
        Require(reopened.Status() is { Mode: FisicaMode.Basic, Reason: FisicaModeReason.NoKey, Key: "" } && Directory.GetFiles(directory).Length==0,"release forgets the key");
    } finally { Directory.Delete(directory,true); }
}
{
    // The default directory is the one the Python SDK uses on this PC (only compared here: nothing is written there).
    var home=Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
    var expected=OperatingSystem.IsWindows() ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Fisica","sdk")
        : OperatingSystem.IsMacOS() ? Path.Combine(home,"Library","Application Support","Fisica","sdk")
        : Path.Combine(Environment.GetEnvironmentVariable("XDG_STATE_HOME") is { Length: >0 } state ? state : Path.Combine(home,".local","state"),"fisica-sdk");
    Require(FisicaEntitlement.DefaultStorage==expected,$"default storage {FisicaEntitlement.DefaultStorage}, expected {expected}");
}
{
    // The session hands out frames by mode: basic at most one per 1000 ms of session time without raw data, full every packet.
    foreach (var mode in new[]{"basic","full"}) {
        using var license=License(mode=="full" ? "full" : null);
        using var session=new FisicaSession(core);
        long ms=1000;
        session.Action((uint)FisicaSessionAction.Connected,(long)FisicaLink.Usb,ms); session.Drain();
        session.Feed(Convert.FromHexString("24434701322e302e31fff0"),ms); session.Drain();
        session.License(license,ms);
        session.Action((uint)FisicaSessionAction.Start,-1,ms); session.Drain();
        foreach (var name in new[]{"SET_DEVICE_USB","START_CONTINUOUS_MEASUREMENT"}) {
            var command=core.Command(name); session.Feed(new byte[]{0x24,command[1],command[2],1,0,0,0xff,0xf0},ms); session.Drain();
        }
        var frames=new List<Measurement>();
        for (int i=0;i<10;i++) {
            ms+=300; session.Feed(packet,ms);
            frames.AddRange(session.Drain().Where(e => e.Kind==(uint)FisicaSessionEvent.Measurement).Select(e => Measurement.FromPayload(e.Payload)));
        }
        Require(frames.Count==(mode=="full" ? 10 : 3),mode+" frame rate: "+frames.Count);
        Require(frames.All(f => f.Weight==reference.Weight && f.Display.SequenceEqual(reference.Display)),mode+" frames carry weight and display");
        Require(mode=="full" ? frames.All(f => f.Packet!.SequenceEqual(packet) && f.Raw!.SequenceEqual(reference.Raw!)) : frames.All(f => f.Packet==null && f.Raw==null),mode+" raw data");
        Require(session.Analyze()![0]==reference.Weight,mode+" analysis of every packet");
    }
}

// High-level client and transports against the same core, without hardware.
// A mock Scale on a USB link (a transport that does not declare its link must be given one).
MockTransport Scale(int frames=12) {
    var transport=new MockTransport { Link=FisicaLink.Usb };
    transport.Reply(core.Command("GET_FIRMWARE_VERSION"),Convert.FromHexString("24434701322e302e31fff0"));
    foreach(var name in new[]{"SET_DEVICE_USB","SET_DEVICE_BLE","START_CONTINUOUS_MEASUREMENT","STOP_CONTINUOUS_MEASUREMENT","SET_DEVICE_STOP"}) {
        var command=core.Command(name); IEnumerable<byte> ack=new byte[]{0x24,command[1],command[2],1,0,0,0xff,0xf0};
        if(name=="START_CONTINUOUS_MEASUREMENT") for(int i=0;i<frames;i++) ack=ack.Concat(packet);
        transport.Reply(command,ack.ToArray());
    }
    return transport;
}
// A connected client the app forgets to close; only the returned weak reference remains.
[MethodImpl(MethodImplOptions.NoInlining)]
WeakReference<FisicaClient> Abandon(FisicaEntitlement? license=null) {
    var client=new FisicaClient(core,Scale(),license);
    client.ConnectAsync().WaitAsync(TimeSpan.FromSeconds(5)).GetAwaiter().GetResult();
    return new(client);
}
async Task<Exception?> Failure(Task task) { try { await task.WaitAsync(TimeSpan.FromSeconds(5)); return null; } catch(Exception error) { return error; } }
async Task Until(Func<bool> condition,string what) {
    for (var deadline=DateTime.UtcNow.AddSeconds(5);!condition();await Task.Delay(10)) Require(DateTime.UtcNow<deadline,what);
}
{
    // Basic mode (no key): the 12 packets arrive at once, so one frame without raw data; the core analyzes all of them.
    var transport=Scale(); var client=new FisicaClient(core,transport); int received=0; client.Frame+=_ => Interlocked.Increment(ref received);
    await client.ConnectAsync(FisicaLink.Usb).WaitAsync(TimeSpan.FromSeconds(5));
    Require(client.FirmwareVersion=="2.0.1","firmware");
    await client.StartAsync().WaitAsync(TimeSpan.FromSeconds(5));
    Require(client.State==FisicaSessionState.Measuring,"measuring state");
    await client.StopAsync().WaitAsync(TimeSpan.FromSeconds(5));
    Require(received==1 && client.Frames.Count==1,"basic frames: "+received);
    var report=client.Report()!;
    Require(report.Weight==70.5 && report.FrameCount==1,"basic report");
    Require(report.Frames[0] is { Raw: null, Packet: null, Weight: 70.5, Display.Length: 841 },"basic frame shape");
    await client.CloseAsync().WaitAsync(TimeSpan.FromSeconds(5));
    Require(client.Report()?.Weight==70.5,"report after close");
    Require(!transport.IsOpen && transport.Sent[^1].SequenceEqual(core.Command("SET_DEVICE_STOP")),"release");
    Require(transport.Sent.Any(s => s.SequenceEqual(core.Command("SET_DEVICE_USB"))),"usb mode");
}
foreach (var (key,frames,raw) in new[]{("full",12,true),("limited",12,false)}) {
    // Full mode: every packet; raw data only when the key opens it.
    using var license=License(key);
    var transport=Scale(); var client=new FisicaClient(core,transport,license);
    await client.ConnectAsync(FisicaLink.Usb).WaitAsync(TimeSpan.FromSeconds(5));
    await client.StartAsync().WaitAsync(TimeSpan.FromSeconds(5));
    await client.StopAsync().WaitAsync(TimeSpan.FromSeconds(5));
    var report=client.Report()!;
    Require(report.Weight==70.5 && report.FrameCount==frames,key+" frames: "+report.FrameCount);
    Require(raw ? report.Frames.All(f => f.Raw!.Length==638 && f.Packet!.SequenceEqual(packet)) : report.Frames.All(f => f.Raw==null && f.Packet==null),key+" raw data");
    await client.CloseAsync().WaitAsync(TimeSpan.FromSeconds(5));
}
{
    // The client applies a license that changes during a measurement: import, then release.
    using var license=License(); var transport=Scale(); var client=new FisicaClient(core,transport,license);
    await client.ConnectAsync().WaitAsync(TimeSpan.FromSeconds(5));
    await client.StartAsync().WaitAsync(TimeSpan.FromSeconds(5));
    await Until(() => client.Frames.Count==1,"basic before the key");
    license.ImportToken(values["full"]);
    transport.Feed(Enumerable.Repeat(packet,12).SelectMany(p => p).ToArray());
    await Until(() => client.Frames.Count==13,"full after the key: "+client.Frames.Count);
    Require(client.Frames.Skip(1).All(f => f.Raw!=null && f.Packet!=null),"raw data after the key");
    var released=ReadCode(license.Release(),"FRC1");
    Require(released["k"]=="key_test_1" && license.Status() is { Mode: FisicaMode.Basic, Reason: FisicaModeReason.NoKey },"release");
    transport.Feed(Enumerable.Repeat(packet,12).SelectMany(p => p).ToArray());
    await client.StopAsync().WaitAsync(TimeSpan.FromSeconds(5));  // Queued after the packets.
    Require(client.Frames.Count is 13 or 14 && client.Frames.Skip(13).All(f => f.Raw==null),"basic after the release: "+client.Frames.Count);
    // The measurement was not full-rate throughout: the core analyzed all 36 packets.
    Require(client.Report() is { Weight: 70.5 } summary && summary.FrameCount==client.Frames.Count,"report over every packet");
    await client.CloseAsync().WaitAsync(TimeSpan.FromSeconds(5));
}
foreach (var (key,at) in new (string?,ulong)[]{(null,now),("unbound",now),("tampered",now),("revoked",now+Day),("full",now+14*Day),("full",now)}) {
    // Connect, measure and analyze work in every license state.
    using var license=new FisicaEntitlement(core,() => at,storage: null); if (key!=null) license.ImportToken(values[key]);
    var client=new FisicaClient(core,Scale(),license);
    await client.ConnectAsync().WaitAsync(TimeSpan.FromSeconds(5));
    await client.StartAsync().WaitAsync(TimeSpan.FromSeconds(5));
    await client.StopAsync().WaitAsync(TimeSpan.FromSeconds(5));
    bool full=license.Status().IsFull;
    Require(client.Report() is { Weight: 70.5 } report && report.FrameCount==(full ? 12 : 1),$"measurement with {key} at {at}");
    await client.CloseAsync().WaitAsync(TimeSpan.FromSeconds(5));
}
{
    // Packets carry no battery field: the level comes from GET_BATTERY_LEVEL, idle or measuring.
    var transport=Scale(); var battery=core.Command("GET_BATTERY_LEVEL");
    transport.Reply(battery,Convert.FromHexString("243147010050fff0"));
    var client=new FisicaClient(core,transport);
    await client.ConnectAsync(FisicaLink.Usb).WaitAsync(TimeSpan.FromSeconds(5));
    Require(client.Battery==null && !transport.Sent.Any(s => s.SequenceEqual(battery)),"no battery query unless asked");
    Require(await client.ReadBatteryAsync().WaitAsync(TimeSpan.FromSeconds(5))==80 && client.Battery==80,"battery idle");
    await client.StartAsync().WaitAsync(TimeSpan.FromSeconds(5));
    Require(await client.ReadBatteryAsync().WaitAsync(TimeSpan.FromSeconds(5))==80 && client.State==FisicaSessionState.Measuring,"battery while measuring");
    await client.StopAsync().WaitAsync(TimeSpan.FromSeconds(5));
    var report=client.Report()!;
    Require(report.Battery==80 && report.BatteryValid && !report.Frames[0].BatteryValid,"battery in report");
    transport.Reply(battery,Convert.FromHexString("243147000001fff0"));
    Require(await Failure(client.ReadBatteryAsync()) is FisicaCommandException && client.State==FisicaSessionState.Idle,"refused battery keeps session");
    await client.StartAsync().WaitAsync(TimeSpan.FromSeconds(5));
    await client.CloseAsync().WaitAsync(TimeSpan.FromSeconds(5));
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
    // A transport that does not declare its link must be given one: no default, nothing sent without it.
    var transport=Scale(); transport.Link=null; var client=new FisicaClient(core,transport);
    var error=await Failure(client.ConnectAsync());
    Require(error is FisicaException { Code: (int)FisicaStatus.InvalidArgument } && !transport.IsOpen && transport.Sent.Count==0,"undeclared link needs one");
    await client.ConnectAsync(FisicaLink.Ble).WaitAsync(TimeSpan.FromSeconds(5));
    await client.StartAsync().WaitAsync(TimeSpan.FromSeconds(5));
    Require(transport.Sent.Any(s => s.SequenceEqual(core.Command("SET_DEVICE_BLE"))),"the link given is used");
    await client.CloseAsync().WaitAsync(TimeSpan.FromSeconds(5));
}
{
    // Handlers that throw are reported through Error (even one that throws itself) and never stop Start, Stop,
    // a reconnect or Close; the drained results still complete the tasks.
    var transport=Scale(); var client=new FisicaClient(core,transport); var errors=new ConcurrentQueue<Exception>();
    client.Error+=error => { errors.Enqueue(error); throw new InvalidOperationException("error handler"); };
    client.Frame+=_ => throw new InvalidOperationException("frame handler");
    client.Event+=_ => throw new InvalidOperationException("event handler");
    client.LinkLost+=() => throw new InvalidOperationException("link handler");
    await client.ConnectAsync().WaitAsync(TimeSpan.FromSeconds(5));
    transport.Drop(); await Until(() => client.State==FisicaSessionState.Recovering,"recovering despite a throwing LinkLost");
    await client.ConnectAsync().WaitAsync(TimeSpan.FromSeconds(5));
    await client.StartAsync().WaitAsync(TimeSpan.FromSeconds(5));
    await client.StopAsync().WaitAsync(TimeSpan.FromSeconds(5));
    Require(client.Frames.Count==1 && client.Report()?.Weight==70.5,"frames and report despite throwing handlers");
    await client.CloseAsync().WaitAsync(TimeSpan.FromSeconds(5));
    Require(client.Report()?.Weight==70.5 && !transport.IsOpen,"report after close");
    var messages=errors.Select(e => e.Message).ToHashSet();
    Require(messages.SetEquals(new[]{"frame handler","event handler","link handler"}),"handler errors reported: "+string.Join(",",messages));
}
{
    // A custom transport whose Send throws: reported and handled as a send failure; the start does not hang.
    var inner=Scale(); var transport=new ThrowingTransport(inner,core.Command("START_CONTINUOUS_MEASUREMENT"));
    var client=new FisicaClient(core,transport); var errors=new ConcurrentQueue<Exception>(); client.Error+=errors.Enqueue;
    await client.ConnectAsync().WaitAsync(TimeSpan.FromSeconds(5));
    var error=await Failure(client.StartAsync());
    Require(error is FisicaException or FisicaCommandException && client.State==FisicaSessionState.Fault,"start fails after a throwing Send: "+error);
    Require(errors.Any(e => e is IOException { Message: "write refused" }),"Send exception reported");
    await client.CloseAsync().WaitAsync(TimeSpan.FromSeconds(5));
    Require(!inner.IsOpen,"closed after a throwing Send");
}
{
    // Full mode keeps the analysis after close too; every call after close fails with Cancelled instead of hanging.
    using var license=License("full"); var client=new FisicaClient(core,Scale(),license);
    await client.ConnectAsync().WaitAsync(TimeSpan.FromSeconds(5));
    await client.StartAsync().WaitAsync(TimeSpan.FromSeconds(5));
    await client.StopAsync().WaitAsync(TimeSpan.FromSeconds(5));
    await client.CloseAsync().WaitAsync(TimeSpan.FromSeconds(5));
    Require(client.Report() is { Weight: 70.5, FrameCount: 12 },"full report after close");
    static bool Cancelled(Exception? error) => error is FisicaException { Code: (int)FisicaStatus.Cancelled };
    Require(Cancelled(await Failure(client.ConnectAsync())),"connect after close is cancelled");
    Require(Cancelled(await Failure(client.StartAsync())),"start after close is cancelled");
    Require(Cancelled(await Failure(client.StopAsync())),"stop after close is cancelled");
    Require(Cancelled(await Failure(client.ReadBatteryAsync())),"battery after close is cancelled");
    await client.CloseAsync().WaitAsync(TimeSpan.FromSeconds(5));
}
{
    // A connected client dropped without CloseAsync: its timer holds it weakly, so it is collected
    // (and its session's native memory with it).
    var dropped=Abandon();
    for (var deadline=DateTime.UtcNow.AddSeconds(10);;await Task.Delay(50)) {
        GC.Collect(); GC.WaitForPendingFinalizers(); GC.Collect();
        if (!dropped.TryGetTarget(out _)) break;
        Require(DateTime.UtcNow<deadline,"a client dropped without CloseAsync is collected");
    }
}
{
    // The same while its license lives on: Entitlement.Changed holds the client only weakly.
    using var license=License("full_older");
    var dropped=Abandon(license);
    for (var deadline=DateTime.UtcNow.AddSeconds(10);;await Task.Delay(50)) {
        GC.Collect(); GC.WaitForPendingFinalizers(); GC.Collect();
        if (!dropped.TryGetTarget(out _)) break;
        Require(DateTime.UtcNow<deadline,"a client dropped without CloseAsync is collected while its license lives on");
    }
    // The next change reaches no client and lets go of the relay; the license carries on.
    Require(license.ImportToken(values["full"])==FisicaEntitlementOutcome.Accepted && license.Status().VerifiedAt==now,"license after the client was collected");
}
{
    // A second close while the first waits for the Scale is Busy and leaves the first alone; once closed, close does nothing.
    var transport=Scale(); var release=core.Command("SET_DEVICE_STOP"); transport.Reply(release);  // never confirmed
    var client=new FisicaClient(core,transport);
    await client.ConnectAsync().WaitAsync(TimeSpan.FromSeconds(5));
    await client.StartAsync().WaitAsync(TimeSpan.FromSeconds(5));
    await client.StopAsync().WaitAsync(TimeSpan.FromSeconds(5));
    var first=client.CloseAsync();
    Require(await Failure(client.CloseAsync()) is FisicaException { Code: (int)FisicaStatus.Busy } && transport.IsOpen,"a second close while one is pending is busy");
    Require(await Failure(first) is FisicaCommandException && !transport.IsOpen && client.Report()?.Weight==70.5,"an unconfirmed close still releases");
    Require(await Failure(client.CloseAsync())==null && transport.Sent.Count(s => s.SequenceEqual(release))==1,"close after close does nothing");
}
{
    // Callbacks go to the SynchronizationContext given.
    var context=new CountingContext(); var transport=Scale(); var client=new FisicaClient(core,transport,callbacks: context);
    int received=0; client.Frame+=_ => Interlocked.Increment(ref received);
    await client.ConnectAsync().WaitAsync(TimeSpan.FromSeconds(5));
    await client.StartAsync().WaitAsync(TimeSpan.FromSeconds(5));
    await Until(() => Volatile.Read(ref received)==1,"frame through the context");
    Require(context.Posts>0,"callbacks posted to the context");
    await client.CloseAsync().WaitAsync(TimeSpan.FromSeconds(5));
}
{
    // Start clears the previous measurement; frames stay bounded (full mode hands out every packet).
    using var license=License("full");
    var transport=Scale(8); var client=new FisicaClient(core,transport,license);
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
    // Serial transport: a missing port is reported as an error and a loss, never thrown into the caller.
    var serial=new SerialTransport("fisica-missing-port"); bool lost=false; Exception? reported=null;
    serial.Lost+=() => lost=true; serial.Error+=e => reported=e; serial.Open();
    Require(lost && reported is FisicaException { Code: 1001 },"serial open failure");
    serial.Close();
    // Line settings a real Scale needs: 115200 8N1, and DTR asserted or its ESP32 reboots or stays in reset.
    using var port=new SerialTransport("COM9").CreatePort();
    Require(port is { BaudRate: 115200, DataBits: 8, Parity: System.IO.Ports.Parity.None, StopBits: System.IO.Ports.StopBits.One,
        Handshake: System.IO.Ports.Handshake.None, DtrEnable: true, RtsEnable: true } && !port.IsOpen,"serial line settings");
}
{
    // Serial transport over a stand-in port: opening it again raises Ready on the same port; a failed read
    // closes and disposes the port, and the next open uses a new one.
    var ports=new List<FakePort>();
    var serial=new SerialTransport("fake") { Ports=() => { var created=new FakePort(new()); lock(ports) ports.Add(created); return created; } };
    int ready=0, lost=0; serial.Ready+=() => Interlocked.Increment(ref ready); serial.Lost+=() => Interlocked.Increment(ref lost);
    serial.Open(); serial.Open();
    Require(ready==2 && ports.Count==1 && ports[0].IsOpen,"open again raises Ready on the same port");
    ports[0].FailRead();
    await Until(() => Volatile.Read(ref lost)==1 && ports[0].Disposed,"a failed read closes and disposes the port");
    serial.Open();
    Require(ready==3 && ports.Count==2 && ports[1].IsOpen && !ports[1].Disposed,"reopen after a failed read uses a new port");
    serial.Close();
    Require(ports[1].Disposed && lost==1,"close disposes the port and reports no loss");
}
{
    // A failed write puts the session in Fault while the port stays open; ConnectAsync then reconnects at once.
    var replies=new Dictionary<string,byte[]> { [Convert.ToHexString(core.Command("GET_FIRMWARE_VERSION"))]=Convert.FromHexString("24434701322e302e31fff0") };
    foreach (var name in new[]{"SET_DEVICE_USB","START_CONTINUOUS_MEASUREMENT","STOP_CONTINUOUS_MEASUREMENT","SET_DEVICE_STOP"}) {
        var command=core.Command(name); replies[Convert.ToHexString(command)]=new byte[]{0x24,command[1],command[2],1,0,0,0xff,0xf0};
    }
    var fake=new FakePort(replies);
    var serial=new SerialTransport("fake") { Ports=() => fake };
    var client=new FisicaClient(core,serial);
    await client.ConnectAsync().WaitAsync(TimeSpan.FromSeconds(5));
    fake.FailWrites=true;
    Require(await Failure(client.StartAsync()) is FisicaException or FisicaCommandException && client.State==FisicaSessionState.Fault,"start fails after a failed write: "+client.State);
    fake.FailWrites=false;
    var reconnect=await Failure(client.ConnectAsync(timeout: TimeSpan.FromSeconds(3)));
    Require(reconnect==null && client.FirmwareVersion=="2.0.1","reconnect over the open port after a fault: "+reconnect);
    await Failure(client.CloseAsync());
    Require(fake.Disposed,"close releases the port");
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
// A local stand-in for the developer portal: each request is answered with {"token": answer(path)}, as late as answer takes.
(string Address,List<(string Path,string Authorization,string Body)> Requests,HttpListener Server) Portal(Func<string,Task<string>> answer) {
    var probe=new TcpListener(IPAddress.Loopback,0); probe.Start(); int port=((IPEndPoint)probe.LocalEndpoint).Port; probe.Stop();
    var server=new HttpListener(); server.Prefixes.Add($"http://127.0.0.1:{port}/"); server.Start();
    var requests=new List<(string Path,string Authorization,string Body)>();
    _=Task.Run(async () => {
        while (server.IsListening) {
            HttpListenerContext context; try { context=await server.GetContextAsync(); } catch (Exception) { break; }
            _=Task.Run(async () => {  // a held answer does not hold up the next request
                try {
                    using var reader=new StreamReader(context.Request.InputStream); var body=await reader.ReadToEndAsync();
                    var path=context.Request.Url!.AbsolutePath;
                    lock(requests) requests.Add((path,context.Request.Headers["Authorization"] ?? "",body));
                    var reply=JsonSerializer.SerializeToUtf8Bytes(new { token=await answer(path) });
                    context.Response.ContentType="application/json"; context.Response.ContentLength64=reply.Length;
                    await context.Response.OutputStream.WriteAsync(reply); context.Response.Close();
                } catch (Exception) { /* The test stopped the server first. */ }
            });
        }
    });
    return ($"http://127.0.0.1:{port}",requests,server);
}
{
    // A renewal that reports the key released lets go of it: the stored key is deleted.
    var (address,requests,server)=Portal(_ => Task.FromResult(values["revoked"]));
    var directory=Directory.CreateTempSubdirectory("fisica-dotnet-").FullName;
    try {
        ulong time=now+Day;
        using var license=new FisicaEntitlement(core,() => time,address,directory);
        Require(license.ImportToken(values["full"])==FisicaEntitlementOutcome.Accepted && Directory.GetFiles(directory).Length==1,"stored key");
        Require(await license.SyncAsync(force: true) is { Refreshed: true, Error: null },"renewal: "+license.LastSyncError);
        Require(license.Status() is { Mode: FisicaMode.Basic, Reason: FisicaModeReason.Revoked } && Directory.GetFiles(directory).Length==0,"released key let go");
        lock(requests) Require(requests.Count==1 && requests[0]==("/sdk/v1/entitlement/refresh","Bearer "+values["full"],"{}"),"empty refresh with the key");
    } finally { server.Stop(); Directory.Delete(directory,true); }
}
foreach (var replaced in new[]{false,true}) {
    // A renewal answered only after the key was released, or after another key was imported, changes nothing:
    // a released key does not come back (nor its stored state) and the newer key stays, stored as it is.
    var answered=new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var (address,requests,server)=Portal(async path => {
        if (path.EndsWith("/refresh")) await answered.Task;
        return replaced ? values["revoked"] : values["full"];  // either is accepted for the key it was asked for
    });
    var directory=Directory.CreateTempSubdirectory("fisica-dotnet-").FullName;
    try {
        ulong time=now+Day;
        using var license=new FisicaEntitlement(core,() => time,address,directory);
        Require(license.ImportToken(values[replaced ? "full_older" : "full"])==FisicaEntitlementOutcome.Accepted,"key before the renewal");
        var renewal=license.SyncAsync(force: true);
        await Until(() => { lock(requests) return requests.Any(r => r.Path.EndsWith("/refresh")); },"renewal on its way");
        if (replaced) Require(license.ImportToken(values["full"])==FisicaEntitlementOutcome.Accepted,"another key while the answer is on its way");
        else Require(license.Release().StartsWith("FRC1.") && Directory.GetFiles(directory).Length==0,"released while the answer is on its way");
        answered.SetResult();
        Require(await renewal.WaitAsync(TimeSpan.FromSeconds(5)) is { Activated: false, Refreshed: false, Error: null },"late answer dropped: "+license.LastSyncError);
        var status=license.Status();
        if (replaced) {
            Require(status is { Mode: FisicaMode.Full, Key: "key_test_1" } && status.VerifiedAt==now && Directory.GetFiles(directory).Length==1,"the newer key stays");
            using var reopened=new FisicaEntitlement(core,() => time,null,directory);
            Require(reopened.Status() is { Mode: FisicaMode.Full } && reopened.Status().VerifiedAt==now,"the newer key stays stored");
        } else Require(status is { Mode: FisicaMode.Basic, Reason: FisicaModeReason.NoKey, Key: "" } && Directory.GetFiles(directory).Length==0,"the released key stays released");
        lock(requests) Require(requests.Count(r => r.Path.EndsWith("/refresh"))==1,"one renewal");
    } finally { server.Stop(); Directory.Delete(directory,true); }
}
if (values.TryGetValue("endpoint",out var endpoint)) {
    // Online activation and renewal against the reference mock developer portal, which holds the unactivated key.
    ulong clock=ulong.Parse(values["server_now"]); string directory=values["storage"];
    using var entitlement=new FisicaEntitlement(core,() => clock,endpoint,directory);
    Require(entitlement.ImportToken(values["unbound"])==FisicaEntitlementOutcome.Accepted,"online import");
    // Importing a new key starts its activation in the background; SyncAsync waits for the exchange.
    Require(await entitlement.SyncAsync() is { Error: null },"activation: "+entitlement.LastSyncError);
    var status=entitlement.Status();
    Require(status is { Mode: FisicaMode.Full, Reason: FisicaModeReason.None, Key: "key_test_1" } && status.BindType==target.Type && status.Bind==target.Value
        && status.VerifiedAt==clock && status.KeyUntil==clock+90*Day,"activated online for this PC");
    Require(await entitlement.SyncAsync() is { Activated: false, Refreshed: false, Error: null },"nothing due");
    entitlement.CheckStatus();
    Require(await entitlement.SyncAsync(force: true) is { Refreshed: true, Error: null },"forced renewal: "+entitlement.LastSyncError);
    // A later start finds the activated key; the original key file does not replace it.
    using var reopened=new FisicaEntitlement(core,() => clock,null,directory);
    Require(reopened.Status() is { Mode: FisicaMode.Full, VerifiedAt: var verified } && verified==clock
        && reopened.ImportToken(values["unbound"])==FisicaEntitlementOutcome.IgnoredOlder && reopened.Status().IsFull,"stored activated key restored");
    // Pairing without a key: the mock portal approves every code at once with an unbound key of the contract.
    string paired=Path.Combine(directory,"paired");
    using (var fresh=new FisicaEntitlement(core,() => clock,endpoint,paired)) {
        Require(fresh.Status() is { Reason: FisicaModeReason.NoKey },"no key before pairing");
        PairingCode? shown=null;
        Require(await fresh.PairAsync(code => shown=code)==PairingResult.Approved && shown is { UserCode.Length: 9 },"paired: "+fresh.LastSyncError);
        Require(fresh.Status() is { Mode: FisicaMode.Full, Key: "key_pair_1" } && fresh.Status().Bind==target.Value,"paired key activated for this PC");
    }
    using (var later=new FisicaEntitlement(core,() => clock,null,paired))
        Require(later.Status() is { Mode: FisicaMode.Full, Key: "key_pair_1" },"a later start finds the paired key");
    using (var releasing=new FisicaEntitlement(core,() => clock,endpoint,paired)) {
        var (releaseCode,releasedOnline)=await releasing.ReleaseAsync();
        Require(releasedOnline && releaseCode.StartsWith("FRC1.") && releasing.Status().Reason==FisicaModeReason.NoKey
            && !Directory.EnumerateFiles(paired,"*.state").Any(),"released online: "+releasing.LastSyncError);
    }
    using (var unreachable=new FisicaEntitlement(core,() => clock,null,null)) {
        bool refused=false;
        try { await unreachable.PairAsync(_ => { }); } catch (InvalidOperationException) { refused=true; }
        Require(refused,"pairing needs an endpoint");
    }
    // A communication failure is recorded, raises nothing and changes no state.
    using var offline=new FisicaEntitlement(core,() => clock,"http://127.0.0.1:9",Path.Combine(directory,"offline"));
    offline.ImportToken(values["unbound"]); offline.CheckStatus();
    Require((await offline.SyncAsync()).Error is {} problem && problem.StartsWith("network:") && offline.LastSyncError!.StartsWith("network:"),"network failure recorded");
    Require(offline.Status() is { Mode: FisicaMode.Basic, Reason: FisicaModeReason.NotActivated },"failure is not a license change");
    // Renewal when due: the trial ended, so the key is refreshed (the server's contract decides).
    clock+=14*Day;
    Require(await entitlement.SyncAsync() is { Refreshed: true, Error: null } && entitlement.Status().Reason==FisicaModeReason.FullEnded,"renewal when due");
}
Console.WriteLine("C# client, transports and license contract passed");

/// <summary>A USB link whose Send throws for one request instead of raising SendFailed.</summary>
sealed class ThrowingTransport(MockTransport inner,byte[] refused) : IFisicaTransport {
    public FisicaLink? Link => FisicaLink.Usb;
    public event Action? Ready { add => inner.Ready+=value; remove => inner.Ready-=value; }
    public event Action<byte[]>? Bytes { add => inner.Bytes+=value; remove => inner.Bytes-=value; }
    public event Action? Lost { add => inner.Lost+=value; remove => inner.Lost-=value; }
    public event Action<Exception>? SendFailed { add => inner.SendFailed+=value; remove => inner.SendFailed-=value; }
    public event Action<Exception>? Error { add => inner.Error+=value; remove => inner.Error-=value; }
    public void Open() => inner.Open();
    public void Send(byte[] bytes) { if (bytes.SequenceEqual(refused)) throw new IOException("write refused"); inner.Send(bytes); }
    public void Close() => inner.Close();
}
/// <summary>A serial port without hardware: replies to known requests, and fails reads or writes when told to.</summary>
sealed class FakePort(Dictionary<string,byte[]> replies) : ISerialPort {
    readonly BlockingCollection<byte[]> incoming=new();
    volatile bool failRead, opened, disposed;
    public volatile bool FailWrites;
    public bool IsOpen => opened; public bool Disposed => disposed;
    public void Open() => opened=true;
    public int Read(byte[] buffer,int offset,int count) {
        if (failRead || !opened) throw new IOException("device removed");
        if (!incoming.TryTake(out var chunk,50)) throw new TimeoutException();
        chunk.CopyTo(buffer,offset); return chunk.Length;  // replies are short
    }
    public void Write(byte[] buffer,int offset,int count) {
        if (FailWrites || !opened) throw new IOException("write failed");
        if (replies.TryGetValue(Convert.ToHexString(buffer,offset,count),out var reply)) incoming.Add(reply);
    }
    public void FailRead() => failRead=true;
    public void Close() => opened=false;
    public void Dispose() { opened=false; disposed=true; }
}
/// <summary>Counts what the client posts and runs it on the thread pool.</summary>
sealed class CountingContext : SynchronizationContext {
    int posts; public int Posts => Volatile.Read(ref posts);
    public override void Post(SendOrPostCallback callback,object? state) { Interlocked.Increment(ref posts); ThreadPool.QueueUserWorkItem(_ => callback(state)); }
}
