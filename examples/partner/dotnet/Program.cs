using CareCo.Fisica;
using System.Text.Json;

// The same steps as every partner sample: SDK and core version, the license, connect to the chosen Scale
// (or the synthetic mock), read the battery, measure for 5 seconds, stop, show the report, disconnect.
// --key FILE: SDK key from the developer portal for full mode; --endpoint URL: activate and renew it online.
// --pair: without a stored key, show a code to approve in the developer portal; the key is stored for this PC.
// Without --key the key stored for this PC (paired or imported earlier) is used and renewed online
// (https://developer.fisicahealthcare.com unless --endpoint is given).
try { return await Run(args); }
catch (Exception error) {  // one clear line instead of a stack trace, for example a missing key file or core
    Console.Error.WriteLine($"Error: {error.Message}");
    return 1;
}

static async Task<int> Run(string[] args) {
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
        Console.WriteLine(string.Join(Environment.NewLine, SerialTransport.PortNames())); return 0;
    }
    if (args.Length == 1 && args[0] == "scan-ble") {
#if WINDOWS
        var scanner = new BleTransport();
        scanner.Device += (address, name, rssi) => {
            if (name.Contains(FisicaBleProfile.NameContains)) Console.WriteLine($"{address}: {name} ({rssi} dBm)");
        };
        scanner.Error += error => Console.Error.WriteLine($"Error: {error.Message}");
        try { scanner.Scan(); await Task.Delay(TimeSpan.FromSeconds(5)); }
        finally { scanner.StopScan(); scanner.Close(); }
        return 0;
#else
        Console.Error.WriteLine("Error: BLE scanning requires the Windows-specific target framework"); return 1;
#endif
    }
    if (args.Length != 2 || args[0] is not ("usb" or "ble" or "mock")) {
        Console.WriteLine("Usage: list-usb | scan-ble | usb COM7 | ble BLUETOOTH_ADDRESS | mock PATH_TO_scale-mock.json [--key sdk-key.fse | --pair] [--endpoint URL]");
        Console.WriteLine("Serial ports: " + string.Join(", ", SerialTransport.PortNames()));
        return 2;
    }
    var core = new FisicaCore();
    Console.WriteLine($"Fisica SDK {FisicaRelease.SdkVersion} (core {core.Version})");
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
        Console.WriteLine($"Pairing: {paired.ToString().ToLowerInvariant()}");
    }
    ShowLicense(license.Status());

    IFisicaTransport transport;
    var link = FisicaLink.Usb;  // MockTransport declares no link, so ConnectAsync is always given one
    var name = args[1];
    if (args[0] == "mock") {
        transport = ScriptedMock(core, args[1]);
        name = "mock";
    } else if (args[0] == "ble") {
#if WINDOWS
        var ble = new BleTransport();
        ble.Select(ulong.Parse(args[1]));
        transport = ble; link = FisicaLink.Ble;
#else
        Console.Error.WriteLine("Error: BLE requires the Windows-specific target framework"); return 1;
#endif
    } else transport = new SerialTransport(args[1]);
    await using var client = new FisicaClient(core, transport, license);
    string? problem = null;
    client.Error += error => problem ??= error.Message;  // transport problems arrive here first; shown once below
    try {
        await client.ConnectAsync(link);
        Console.WriteLine($"Connected to {name} (firmware {client.FirmwareVersion ?? "unknown"})");
        try { await client.ReadBatteryAsync(); }  // measurement packets carry no battery field; the report shows this reading
        catch (Exception error) when (error is FisicaCommandException or FisicaException) { /* the report then shows "Battery: not read" */ }
        Console.WriteLine("Measuring for 5 seconds...");
        await client.StartAsync();
        await Task.Delay(TimeSpan.FromSeconds(5));
        await client.StopAsync();
        ShowReport(client.Report());
        return 0;
    } catch (Exception error) {
        Console.Error.WriteLine($"Error: {problem ?? error.Message}");
        return 1;
    } finally {
        await client.CloseAsync();  // always, also after an error
        Console.WriteLine("Disconnected");
    }
}

static void ShowLicense(EntitlementStatus status) {
    // "full mode", or "basic mode (no_key)" with the SDK's explanation of the reason.
    var reason = JsonNamingPolicy.SnakeCaseLower.ConvertName(status.Reason.ToString());
    Console.WriteLine(status.IsFull ? "License: full mode" : $"License: basic mode ({reason})");
    if (status.Message != null) Console.WriteLine(status.Message);
    if (status.ActivationCode != null) Console.WriteLine($"Activation code: {status.ActivationCode}");
}

static void ShowReport(SessionReport? report) {
    if (report == null) { Console.WriteLine("No stable result; check the load on the Scale and the received frames"); return; }
    // Weight and foot size come from every packet, in basic and full mode alike.
    Console.WriteLine($"Weight: {report.Weight:F1} kg");
    Console.WriteLine($"Left foot: length {report.LeftLength:F1} cm, width {report.LeftWidth:F1} cm");
    Console.WriteLine($"Right foot: length {report.RightLength:F1} cm, width {report.RightWidth:F1} cm");
    Console.WriteLine($"Frames: {report.FrameCount}");
    Console.WriteLine(report.Battery is int battery ? $"Battery: {battery}%" : "Battery: not read");
}

// Scripted synthetic replies from scale-mock.json; no measurement or protocol logic.
static MockTransport ScriptedMock(FisicaCore core, string path) {
    using var json = JsonDocument.Parse(File.ReadAllText(path));
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
    return mock;
}
