# Fisica Scale Swift wrapper

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
warnings through `onWarning`. Pass it to `FisicaClient` to record real use. It never
blocks a feature. Sending `pendingReport` and storing `exportState` are left to the app
until the server API is final; see `doc/entitlement.md`.

The low-level `FisicaSession` remains available for hosts that drive the core directly.

Verified: `swift test` on macOS (shared vectors, client, entitlement through
`MockTransport`). Not verified: any real Scale over BLE, iOS devices, and BLE
continuous measurement, which the protocol document describes as USB-only.
