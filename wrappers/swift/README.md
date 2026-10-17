# Fisica Scale Swift wrapper

Installation and a complete SwiftUI application: [partner quickstart](../../doc/partner-quickstart.md).
Use the provided ZIP as an Xcode local package; it includes the private compiled core.

Swift package with public Swift sources and `CFisica.xcframework`. macOS 11+ and
iOS 13+; desktop universal, iOS arm64, simulator arm64/x86_64. No private source is
compiled in the consuming app. Link libc++.

```swift
let core = try FisicaCore()
let license = try FisicaEntitlement(core: core, endpoint: URL(string: "https://developer.fisicahealthcare.com"))
try license.importToken(sdkKey)                // SDK key from the developer portal; optional
let ble = BLETransport()                       // callbacks on the main queue
ble.onBluetoothState = { if $0 == .poweredOn { ble.scan() } }
ble.onDevice = { peripheral in
    guard peripheral.name?.contains(FisicaBleProfile.nameContains) == true else { return }
    ble.select(peripheral)                     // default profile; pass your own if needed
    let client = try! FisicaClient(core: core, transport: ble, entitlement: license)
    client.onFrame = { frame in /* frame.weight, frame.display (29x29); full mode: frame.raw (29x22), frame.packet */ }
    client.connect(.ble) { _ in
        client.start(durationMs: 5000) { _ in }
    }
    // later: let report = try client.report(); client.close()
}
```

## License: basic and full mode

The native core decides the mode from a server-signed SDK key and limits what it hands out;
the wrapper only stores the key, talks to the server and reports the status.

- **Full mode**: an SDK key activated for this app (its bundle ID; command-line tools and
  tests are bound to the PC) within its key expiry and its trial or contract. Every frame
  with the raw 29x22 grid and the 645-byte packet, `FisicaCore.decode`, and the features the
  key opens (`status().features`: raw data, export, advanced, full rate).
- **Basic mode**: everything else (no key, not activated, another app or PC, expired, released,
  revoked, clock set back). Connection, measurement, settings, battery, weight and foot size work;
  `onFrame` gets at most one display frame a second without raw data, and decoding packets
  yourself throws `FisicaLicenseError` (`FisicaStatus.featureNotLicensed`), distinct from
  measurement and transport errors. `report()` still analyzes every packet in the core, so
  weight and foot size are the same in both modes.

`FisicaEntitlement.status()` gives the mode, the reason for basic mode (`reason.message`
explains it), open features, company, contract, key, track, what the key is bound to and this
app's or PC's target, the key's dates (`keyUntil`, `fullUntil`, `refreshBy`, contract dates,
`revokedAt`) and warnings (`onStatus`/`onWarning` report changes).

- Online (`endpoint:`), an imported key that is not activated yet is activated for this app or
  PC automatically (`POST /sdk/v1/entitlement/activate` with the core's request: the bind type
  and target, nothing else), and renewed with an empty refresh when renewal is due or the key or
  full mode ended. A key the server reports released is let go and its stored state deleted.
  Exchanges run in the background (`sync()`, or `syncNow()` off the main thread); a failure only
  sets `lastSyncError` and never changes the license.
- Offline: show `activationCode()` (FAC1), enter it in the developer portal, and import the key
  file it gives with `importFile(_:)`; renewal files work the same way.
- The activated key is stored per seat under Application Support (`storage:`), loaded when the
  `FisicaEntitlement` is created, and wins over an older original key file imported again.
- Without a key, `try await license.pair { code in ... }` shows a code that an owner or admin
  approves at the developer portal's `/activate` page (`FisicaEntitlement.defaultEndpoint`); the
  key arrives activated for this app and is stored. Only the bundle ID or PC hash is sent.
- `release()` forgets the key activated here, deletes the stored state and returns the FRC1
  code to enter in the developer portal, which frees the seat.
- `FisicaClient` applies the license to its session before each start, whenever the key
  changes and every 30 s. The SDK does not track its own use: the stored state is the signed key
  and the latest time seen.

- `FisicaClient` runs the clock, moves bytes and turns core events into callbacks.
  Command order, ACK waits, timeouts and recovery decisions are in the shared core.
- Every callback and completion runs on the client's queue (default main). Read
  `frames` and call `report()` there.
- `connect` completes with the firmware probe; an unanswered probe does not prevent
  use. A transport that never becomes ready fails after `timeout` (default 15 s).
- A rejected or unconfirmed command fails with `FisicaCommandError` (`code` is the SDK
  status, `deviceCode` the Scale's own code). `close` releases resources even when the
  Scale does not confirm. A second `close` while one is pending fails with `busy` and
  leaves the first alone; once closed, `close` completes at once with nil.
- On `onLinkLost`, reopen the same Scale with `connect`; the core resumes a measurement
  it still wants.
- `report()` analyzes the measurement in the core and returns unrounded values from every
  packet the core received since `start`, in every mode, and still after `close`.
- `start` clears the frames of the previous measurement once the core accepts it, so
  measuring several people in a row needs no `clearFrames()`. A reconnect keeps them.
  Only the latest `maxFrames` (at most `FisicaLimit.analysisFrames`, the most one
  analysis accepts; larger values are clamped) are kept; `droppedFrames` counts the rest.
- `connect(_:)` takes `.ble` or `.usb`. Without it the transport's own `link` is used
  (`BLETransport` is `.ble`); a transport that does not declare one (`MockTransport` unless
  set) must be given it. A missing link, or one other than the transport's, fails with
  `invalidArgument` before anything is sent.
- Codes are generated enums: `FisicaStatus`, `FisicaSessionState`, `FisicaSessionEvent`, ...

Transports: `BLETransport` (CoreBluetooth), `MockTransport` (scripted, no hardware), or
your own `FisicaTransport`. `BLETransport` reports "characteristic UUIDs not found"
and a ready timeout instead of waiting forever. `FisicaBleProfile.scale` comes from the
existing Python SDK and is not yet confirmed on production firmware
(`FisicaBleProfile.verified` is false); pass `FisicaBleProfile(write:notify:)` to override.
Add `NSBluetoothAlwaysUsageDescription` and, when sandboxed on macOS, the Bluetooth
entitlement.

The low-level `FisicaSession` remains available for hosts that drive the core directly.

Verified: `swift test` on macOS through `tools/verify_bindings.py swift` (shared vectors, client
and license modes through `MockTransport`; online activation, renewal and storage against the
mock developer portal). Not verified: any real Scale over BLE, iOS devices, and BLE
continuous measurement, which the protocol document describes as USB-only.
