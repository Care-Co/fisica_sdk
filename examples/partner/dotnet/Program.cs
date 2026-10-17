using CareCo.Fisica;
using System.Text.Json;

// --key FILE: SDK key from the developer portal for full mode; --endpoint URL: activate and renew it online.
// --pair: without a stored key, show a code to approve in the developer portal; the key is stored for this PC.
// Without --key the key stored for this PC (paired or imported earlier) is used and renewed online
// (https://developer.fisicahealthcare.com unless --endpoint is given).
string? keyFile = null, endpoint = null;
bool pair = false;
var rest = new List<string>();
for (int i = 0; i < args.Length; i++) {
    if (args[i] == "--key" && i + 1 < args.Length) keyFile = args[++i];
    else if (args[i] == "--endpoint" && i + 1 < args.Length) endpoint = args[++i];
    else if (args[i] == "--pair") pair = true;
    else rest.Add(args[i]);
}
args = rest.ToArray();
if (args.Length == 1 && args[0] == "list-usb") {
    Console.WriteLine(string.Join(Environment.NewLine, SerialTransport.PortNames())); return;
}
if (args.Length == 1 && args[0] == "scan-ble") {
#if WINDOWS
    var scanner = new BleTransport();
    scanner.Device += (address, name, rssi) => {
        if (name.Contains(FisicaBleProfile.NameContains)) Console.WriteLine($"{address}: {name} ({rssi} dBm)");
    };
    scanner.Error += error => Console.Error.WriteLine(error.Message);
    try { scanner.Scan(); await Task.Delay(TimeSpan.FromSeconds(5)); }
    finally { scanner.StopScan(); scanner.Close(); }
#else
    Console.Error.WriteLine("BLE scanning requires the Windows-specific target framework");
#endif
    return;
}
if (args.Length != 2 || args[0] is not ("usb" or "ble" or "mock")) {
    Console.WriteLine("Usage: list-usb | scan-ble | usb COM7 | ble BLUETOOTH_ADDRESS | mock PATH_TO_scale-mock.json [--key sdk-key.fse | --pair] [--endpoint URL]");
    Console.WriteLine("Serial ports: " + string.Join(", ", SerialTransport.PortNames()));
    return;
}
var core = new FisicaCore();
// Without a key activated for this app or PC the SDK runs in basic mode: measurement, weight and
// foot size work; frames arrive at most once a second without raw data.
using var license = new FisicaEntitlement(core, endpoint: endpoint ?? (keyFile == null ? FisicaEntitlement.DefaultEndpoint : null));
if (keyFile != null) {
    license.ImportFile(keyFile);
    if (endpoint != null) await license.SyncAsync();  // activate a new key before measuring
} else if (pair && license.Status().Key.Length == 0) {
    var paired = await license.PairAsync(code => {
        Console.WriteLine($"Pairing code: {code.UserCode}");
        Console.WriteLine($"Approve it at {code.VerificationUri} (valid for {code.ExpiresIn / 60} minutes). Waiting...");
        try { System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(code.VerificationUriComplete) { UseShellExecute = true }); }
        catch (Exception) { /* no browser here: open the address yourself */ }
    });
    Console.WriteLine($"Pairing: {paired}");
}
var status = license.Status();
Console.WriteLine($"License: {status.Mode.ToString().ToLowerInvariant()} mode" + (status.Message == null ? "" : $" ({status.Reason}: {status.Message})"));
if (status.ActivationCode != null) Console.WriteLine($"Offline activation code for the developer portal: {status.ActivationCode}");
IFisicaTransport transport;
var link = FisicaLink.Usb;
if (args[0] == "mock") {
    using var json = JsonDocument.Parse(File.ReadAllText(args[1]));
    var script = json.RootElement;
    var mock = new MockTransport();
    foreach (var reply in script.GetProperty("replies").EnumerateObject()) {
        var chunks = new List<byte[]> { Convert.FromHexString(reply.Value.GetString()!) };
        if (reply.Name == "START_CONTINUOUS_MEASUREMENT") {
            var frame = Convert.FromHexString(script.GetProperty("measurement_hex").GetString()!);
            chunks.AddRange(Enumerable.Repeat(frame, script.GetProperty("frame_count").GetInt32()));
        }
        mock.Reply(core.Command(reply.Name), chunks.ToArray());
    }
    transport = mock;
} else if (args[0] == "ble") {
#if WINDOWS
    var ble = new BleTransport();
    ble.Select(ulong.Parse(args[1]));
    transport = ble; link = FisicaLink.Ble;
#else
    Console.Error.WriteLine("BLE requires the Windows-specific target framework"); return;
#endif
} else transport = new SerialTransport(args[1]);
await using var client = new FisicaClient(core, transport, license);
client.Error += error => Console.Error.WriteLine(error.Message);
client.Frame += frame => Console.WriteLine($"Frame: {frame.Weight:F2} kg" + (frame.Raw != null ? ", with raw data" : ""));
try {
    await client.ConnectAsync(link);
    await client.StartAsync();
    await Task.Delay(TimeSpan.FromSeconds(5));
    await client.StopAsync();
    var report = client.Report();
    Console.WriteLine(report == null ? "No stable result" : $"Weight: {report.Weight} kg; frames: {report.Frames.Count}");
} finally { await client.CloseAsync(); }
