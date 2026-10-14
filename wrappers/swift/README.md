# Fisica Scale Swift wrapper

Installation and a complete SwiftUI application: [partner quickstart](../../doc/partner-quickstart.md).
Use the provided ZIP as an Xcode local package; it includes the private compiled core.

Swift package with public Swift sources and `CFisica.xcframework`. macOS 11+ and
iOS 13+; desktop universal, iOS arm64, simulator arm64/x86_64. No private source is
compiled in the consuming app. Link libc++.

```swift
let core = try FisicaCore()
let ble = BLETransport()                       // callbacks on the main queue
ble.onBluetoothState = { if $0 == .poweredOn { ble.scan() } }
ble.onDevice = { peripheral in
    guard peripheral.name?.contains(FisicaBleProfile.nameContains) == true else { return }
    ble.select(peripheral)                     // default profile; pass your own if needed
    let client = try! FisicaClient(core: core, transport: ble)
    client.onFrame = { frame in /* frame.weight, frame.display (29x29), frame.raw (29x22) */ }
    client.connect(.ble) { _ in
        client.start(durationMs: 5000) { _ in }
    }
    // later: let report = try client.report(); client.close()
}
```

- `FisicaClient` runs the clock, moves bytes and turns core events into callbacks.
  Command order, ACK waits, timeouts and recovery decisions are in the shared core.
- Every callback and completion runs on the client's queue (default main). Read
  `frames` and call `report()` there.
- `connect` completes with the firmware probe; an unanswered probe does not prevent
  use. A transport that never becomes ready fails after `timeout` (default 15 s).
- A rejected or unconfirmed command fails with `FisicaCommandError` (`code` is the SDK
  status, `deviceCode` the Scale's own code). `close` releases resources even when the
  Scale does not confirm.
- On `onLinkLost`, reopen the same Scale with `connect`; the core resumes a measurement
  it still wants.
- `report()` analyzes the collected packets in the core (29x29 display grid and
  per-frame weight) and returns unrounded values.
- `start` clears the frames of the previous measurement once the core accepts it, so
  measuring several people in a row needs no `clearFrames()`. A reconnect keeps them.
  Only the latest `maxFrames` (at most `FisicaLimit.analysisFrames`, the most one
  analysis accepts; larger values are clamped) are kept; `droppedFrames` counts the rest.
- `connect(_:)` always names the link (`.ble` or `.usb`). A transport that declares its
  own `link` (`BLETransport` is `.ble`) rejects a different one with `invalidArgument`
  before anything is sent.
- Codes are generated enums: `FisicaStatus`, `FisicaSessionState`, `FisicaSessionEvent`, ...

Transports: `BLETransport` (CoreBluetooth), `MockTransport` (scripted, no hardware), or
your own `FisicaTransport`. `BLETransport` reports "characteristic UUIDs not found"
and a ready timeout instead of waiting forever. `FisicaBleProfile.scale` comes from the
existing Python SDK and is not yet confirmed on production firmware
(`FisicaBleProfile.verified` is false); pass `FisicaBleProfile(write:notify:)` to override.
Add `NSBluetoothAlwaysUsageDescription` and, when sandboxed on macOS, the Bluetooth
entitlement.

Entitlement: `FisicaEntitlement` imports a server-signed token or file and reports
three separate values (base license, confirmation freshness, contract state) plus
warnings through `onWarning`. Pass it to `FisicaClient` so warnings appear when features are used. It
never blocks a feature. The signed token is stored under Application Support by default
(`storage:`) for offline verification. With `endpoint:` it refreshes the information in
the background (`sync()`, or `syncNow()` off the main thread); without an endpoint no
network is used. A failed exchange only sets `lastSyncError`. The SDK does not track
its own use; see `doc/entitlement.md`.

The low-level `FisicaSession` remains available for hosts that drive the core directly.

Verified: `swift test` on macOS (shared vectors, client, entitlement through
`MockTransport`; online refresh and storage against the mock server). Not verified: any real Scale over BLE, iOS devices, and BLE
continuous measurement, which the protocol document describes as USB-only.
