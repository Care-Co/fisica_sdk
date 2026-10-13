# Fisica Scale C# / .NET wrapper

`CareCo.Fisica.Scale` NuGet (net8.0+) with native assets for win-x64, win-arm64,
osx-x64 and osx-arm64. Depends on `System.IO.Ports` for the USB serial transport.

```csharp
var core = new FisicaCore();
var transport = new SerialTransport("COM7");          // pick from SerialTransport.PortNames()
await using var client = new FisicaClient(core, transport);
client.Frame += frame => { /* frame.Weight, frame.Display (29x29), frame.Raw (29x22) */ };
await client.ConnectAsync(FisicaLink.Usb);
await client.StartAsync(TimeSpan.FromSeconds(5));
// ... after the measurement
await client.StopAsync();
var report = client.Report();                          // weight and foot dimensions
await client.CloseAsync();
```

- `FisicaClient` runs the clock, moves bytes and turns core events into tasks and
  events. Command order, ACK waits, timeouts and recovery decisions are in the shared core.
- A rejected or unconfirmed command throws `FisicaCommandException` (`Code` SDK status,
  `DeviceCode` the Scale's code). `ConnectAsync` completes with the firmware probe; an
  unanswered probe does not prevent use. A transport that never becomes ready fails
  after `timeout` (default 15 s).
- `CloseAsync` releases resources even when the Scale does not confirm. On `LinkLost`,
  reopen the same Scale with `ConnectAsync`.
- `Report()` analyzes the collected packets in the core and returns unrounded values.
- Events are raised on the thread that delivered the input; marshal to the UI thread
  in the app. Codes are generated enums: `FisicaStatus`, `FisicaSessionState`, ...

Transports: `SerialTransport` (USB virtual COM port, 9600 8N1 like the Python SDK),
`BleTransport` (Windows BLE), `MockTransport` (scripted, no hardware), or your own
`IFisicaTransport`. A missing or busy port is reported through `Error` and `Lost`,
never thrown into the caller. Finding the Scale's COM port by USB VID is not included yet.

`BleTransport` uses WinRT and is part of the package only for apps targeting
`net8.0-windows10.0.19041.0` or later:

```csharp
var ble = new BleTransport();
ble.Device += (address, name, rssi) => {
    if (!name.Contains(FisicaBleProfile.NameContains)) return;
    ble.StopScan(); ble.Select(address);          // default profile; pass your own if needed
    _ = client.ConnectAsync(FisicaLink.Ble);
};
ble.Scan();
```

It reports a missing or switched-off radio, an unknown device, missing characteristic
UUIDs and a ready timeout as events, splits writes by the MTU Windows negotiated
(`Mtu`, 23 until known) and resets on reconnect. `FisicaBleProfile.Scale` comes from the
Python SDK and is not yet confirmed on production firmware.

Entitlement: `FisicaEntitlement` imports a server-signed token or file and reports
three separate values (base license, confirmation freshness, contract state) plus
warnings through `Warning`. Pass it to `FisicaClient` to record real use. It never
blocks a feature. State is stored under LocalApplicationData by default (`storage`).
With `endpoint` it refreshes the information and sends the minimal usage report in the
background (`Sync()` or `await SyncAsync()`); without an endpoint no network is used.
A failed exchange only sets `LastSyncError`. See `doc/entitlement.md`.

`new FisicaCore()` loads the packaged core; an explicit trusted path can be supplied.
One core binary is loaded per process. The low-level `FisicaSession` remains available.

Verified: shared vectors, client, entitlement (including online refresh, usage report
and storage against the mock server) and serial open-failure handling on macOS and on
the Windows CI runner; `BleTransport` compiles on both and its failure paths run on
the Windows runner, which has no Bluetooth device. Not verified: a real COM port or BLE
connection with a Scale, real Windows drivers, and Windows ARM64 execution.
