using CareCo.Fisica;
using System.Text.Json;

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
    Console.WriteLine("Usage: list-usb | scan-ble | usb COM7 | ble BLUETOOTH_ADDRESS | mock PATH_TO_scale-mock.json");
    Console.WriteLine("Serial ports: " + string.Join(", ", SerialTransport.PortNames()));
    return;
}
var core = new FisicaCore();
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
await using var client = new FisicaClient(core, transport);
client.Error += error => Console.Error.WriteLine(error.Message);
client.Frame += frame => Console.WriteLine("Measurement frame received");
try {
    await client.ConnectAsync(link);
    await client.StartAsync();
    await Task.Delay(TimeSpan.FromSeconds(5));
    await client.StopAsync();
    var report = client.Report();
    Console.WriteLine(report == null ? "No stable result" : $"Weight: {report.Weight} kg; frames: {report.Frames.Count}");
} finally { await client.CloseAsync(); }
