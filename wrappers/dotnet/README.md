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
`MockTransport` (scripted, no hardware), or your own `IFisicaTransport`. A missing or
busy port is reported through `Error` and `Lost`, never thrown into the caller.
Finding the Scale's COM port by USB VID and a Windows BLE transport are not included
yet; `FisicaBleProfile` carries the default GATT UUIDs for an app-provided BLE link.

Entitlement: `FisicaEntitlement` imports a server-signed token or file and reports
three separate values (base license, confirmation freshness, contract state) plus
warnings through `Warning`. Pass it to `FisicaClient` to record real use. It never
blocks a feature. Sending `PendingReport` and storing `ExportState` are left to the app
until the server API is final; see `doc/entitlement.md`.

`new FisicaCore()` loads the packaged core; an explicit trusted path can be supplied.
One core binary is loaded per process. The low-level `FisicaSession` remains available.

Verified: shared vectors, client, entitlement and serial open-failure handling on
macOS and on the Windows CI runner. Not verified: a real COM port with a Scale, real
Windows drivers, and Windows ARM64 execution.
