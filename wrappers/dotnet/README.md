# Fisica Scale C# / .NET wrapper

Installation and a USB/BLE/Mock console application: [partner quickstart](../../doc/partner-quickstart.md).

Delivered for now as the wrapper source (`fisica-dotnet-<version>.zip`, project `Fisica.Scale.csproj`,
net8.0+) plus the core library from the core ZIP for each platform (win-x64, win-arm64, macOS). A
`CareCo.Fisica.Scale` NuGet package with the native assets comes once Windows builds run in CI.
Depends on `System.IO.Ports` for the USB serial transport.

```csharp
var core = new FisicaCore();
using var license = new FisicaEntitlement(core, endpoint: "https://developer.fisicahealthcare.com");
license.ImportFile("sdk-key.fse");                    // SDK key from the developer portal; omit for basic mode
var transport = new SerialTransport("COM7");          // pick from SerialTransport.PortNames()
await using var client = new FisicaClient(core, transport, license);
client.Frame += frame => { /* frame.Weight, frame.Display (29x29); full mode: frame.Raw (29x22), frame.Packet */ };
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
  reopen the same Scale with `ConnectAsync`. A client dropped without `CloseAsync` is still
  collected, also while its license lives on.
- `Report()` returns unrounded weight and foot size from the core's analysis of every
  packet it received since the start, in every mode, and still after `CloseAsync`.
- `StartAsync` clears the frames of the previous measurement once the core accepts it,
  so measuring several people in a row needs no `ClearFrames()`. A reconnect keeps them.
  Only the latest `MaxFrames` (at most `FisicaLimit.AnalysisFrames`, the most one
  analysis accepts) are kept; `DroppedFrames` counts the older ones dropped.
- `ConnectAsync(link)` takes `FisicaLink.Usb` or `FisicaLink.Ble`. Without it the
  transport's own `Link` is used (`SerialTransport` USB, `BleTransport` BLE); a transport
  that does not declare one (`Link` null, such as `MockTransport`) must be given it. A
  missing link, or one other than the transport's, fails with `InvalidArgument` before
  anything is sent.
- The client works on a thread-pool thread, never on the caller's thread, so blocking
  serial writes do not hold up a UI thread. `Frame`, `Event`, `LinkLost` and `Error` are
  raised there, or on the `SynchronizationContext` passed as `callbacks`
  (`new FisicaClient(core, transport, license, SynchronizationContext.Current)` on the UI
  thread). A handler that throws is reported through `Error`; the client carries on.
  Codes are generated enums: `FisicaStatus`, `FisicaSessionState`, ...

Transports: `SerialTransport` (USB virtual COM port, 115200 8N1 with DTR and RTS on,
like the Python SDK; with DTR off the Scale's ESP32 reboots or stays in reset),
`BleTransport` (Windows BLE), `MockTransport` (scripted, no hardware), or your own
`IFisicaTransport`. A missing or busy port is reported through `Error` and `Lost`,
never thrown into the caller. A failed read closes and releases the port before `Lost`, so
`ConnectAsync` opens it afresh; after a failed write the port stays open and `ConnectAsync`
reconnects over it at once. Finding the Scale's COM port by USB VID is not included yet.

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

License: the shared core decides between two modes and limits what it hands out itself.

- **Full mode**: an SDK key (a seat from the developer portal) activated for this app's
  ID or this PC's hash, within its key expiry and its trial or contract. Every frame,
  with `Raw` and `Packet`; `FisicaCore.Decode` works.
- **Basic mode**: everything else (no key, a key not activated yet or activated for
  another app or PC, an expired key, an ended trial or contract, a clock set back).
  Connection, measurement, settings, battery, weight and foot size work; frames come at
  most once a second, without `Raw` and `Packet`, and `Decode` throws
  `FisicaLicenseException` (`FisicaStatus.FeatureNotLicensed`), which is distinct from
  measurement and communication errors.

`FisicaEntitlement` imports a key or key file (`ImportToken`, `ImportFile`). `Status()`
gives the mode, the reason for basic mode (`Message` explains it), the open features,
company, contract, key, track, what the key is activated for and this app or PC, the
key's dates and warnings, and the `ActivationCode` while the key is not activated. With
`endpoint` a new key is activated for this app or PC automatically and renewed before it
expires (`Sync()` in the background or `await SyncAsync()`); without an endpoint no
network is used. Offline, enter `ActivationCode()` in the developer portal and import the
file it gives. `Release()` frees the key on this app or PC: it forgets the key, deletes
the stored key and returns the code to enter in the portal. The activated key is stored
per company and contract under LocalApplicationData by default (`storage`) and wins over
an older original key file. A failed exchange only sets `LastSyncError`; it never changes
the license or throws. An answer that arrives after `Release()` or after another key was
imported is dropped: a released key stays released and the newer key stays. `Warning` and
`Changed` report new warnings and license changes; `Warning` may pass several bits at once,
and `FisicaLicenseMessages.Warnings(bits)` (or `Status().WarningMessages`) gives a message for
each.
The key stored for this app or PC (paired or imported earlier) is loaded when the
`FisicaEntitlement` is created; importing the original key file again then reports
`IgnoredOlder`. Without a key, `await PairAsync(code => ...)` shows a code that an owner or
admin approves at the developer portal's `/activate` page (`DefaultEndpoint`); the key arrives
activated for this app or PC and is stored. Only the target is sent.
Pass the license to `FisicaClient`: it is applied to the session before every start,
every 30 seconds and whenever it changes. The SDK does not track its own use; the stored
state is the signed key and the latest time seen. See `doc/licensing-plan.md`.

`new FisicaCore()` loads the packaged core; an explicit trusted path can be supplied.
One core binary is loaded per process. The low-level `FisicaSession` remains available.

Verified: shared vectors, client in basic and full mode, license states (including
online activation, renewal and storage against the mock server, and a released key) and serial open-failure handling on macOS and,
until 2026-10-17, on the GitHub Windows runner; on macOS only since then: server answers arriving after a release or another
import, and serial reconnects and read failures over a stand-in port. `BleTransport` compiles on both and its failure paths ran on
that runner, which has no Bluetooth device. Windows CI is paused until a self-hosted Windows runner exists. Not verified: a real COM port or BLE
connection with a Scale, real Windows drivers, and Windows ARM64 execution.
