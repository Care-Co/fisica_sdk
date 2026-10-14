# Fisica Scale Kotlin / Java wrapper

Installation and separate runnable Kotlin/Java Android apps: [partner quickstart](../../doc/partner-quickstart.md).
Use the supplied AAR, not a customer build of private SDK sources.

AAR with public Kotlin classes and compiled `libfisica_core.so` + `libfisica_jni.so`.
Android API 23+, arm64-v8a/armeabi-v7a/x86_64. Add Kotlin stdlib (2.0.21-compatible).
Every library is 16 KB page compatible (Android 15+, required by Google Play): all LOAD
segments are 16 KB aligned and the RELRO segment ends on a 16 KB boundary. Build the app
with AGP 8.5.1+ so the uncompressed libraries are also 16 KB zip-aligned in the APK/AAB,
and check the APK with `zipalign -c -P 16 -v 4`.
Runtime uses `System.loadLibrary`, with no source compilation or download. The JNI
marshaler source is public; private algorithm and state sources are absent.

```kotlin
val core = FisicaCore()
val ble = BleTransport(context, bluetoothManager.adapter)
ble.onDevice = { result ->
    if (result.device.name?.contains(FisicaBleProfile.NAME_CONTAINS) == true) {
        ble.stopScan()
        ble.select(result.device)                 // default profile; pass your own if needed
        val client = FisicaClient(core, ble, callbacks = ContextCompat.getMainExecutor(context))
        client.onFrame = { frame -> /* frame.weight, frame.display (29x29), frame.raw (29x22) */ }
        client.connect(FisicaLink.BLE) { _, error ->
            if (error == null) client.start(5000) { _, _ -> }
        }
        // later: val report = client.report(); client.close()
    }
}
ble.scan()
```

- `FisicaClient` runs the clock, moves bytes and turns core events into callbacks on
  the executor you pass. Command order, ACK waits, timeouts and recovery decisions are
  in the shared core.
- Completions receive either an event or an error. A rejected or unconfirmed command
  is a `FisicaCommandException` (`code` SDK status, `deviceCode` the Scale's code).
- `connect` completes with the firmware probe; an unanswered probe does not prevent
  use. A transport that never becomes ready fails after `timeoutMs` (default 15 s).
- `close` releases resources even when the Scale does not confirm. On `onLinkLost`,
  reopen the same Scale with `connect`.
- `report()` analyzes the collected packets in the core and returns unrounded values.
- `start` clears the frames of the previous measurement once the core accepts it, so
  measuring several people in a row needs no `clearFrames()`. A reconnect keeps them.
  Only the latest `maxFrames` (at most `FisicaLimit.ANALYSIS_FRAMES`, the most one
  analysis accepts) are kept; `droppedFrames` counts the older ones dropped.
- `connect(link)` takes `FisicaLink.BLE` or `FisicaLink.USB`. Without it the transport's
  own link is used (`FisicaLinkTransport`; `BleTransport` is BLE), otherwise BLE. A link
  other than the transport's fails with `INVALID_ARGUMENT` before anything is sent.
- Codes are generated constants: `FisicaStatus`, `FisicaSessionState`, `FisicaSessionEvent`, ...

Transports: `BleTransport` (Android GATT), `MockTransport` (scripted, no hardware; runs
on a plain JVM), or your own `FisicaTransport`. `BleTransport` reports an error instead
of throwing when Bluetooth is off, unsupported or not permitted, requests MTU 517
before discovery, splits writes by the negotiated MTU, falls back to 23 when the
request is refused and resets on reconnect (the splitting rule is unit-tested on a JVM). `FisicaBleProfile.SCALE` comes from the
existing Python SDK and is not yet confirmed on production firmware
(`FisicaBleProfile.VERIFIED` is false). Grant BLUETOOTH_SCAN/CONNECT on Android 12+ and
the legacy location permissions where required. Online entitlement refresh needs the
INTERNET permission in the app; the AAR does not declare it.

Entitlement: `FisicaEntitlement` imports a server-signed token or file and reports
three separate values (base license, confirmation freshness, contract state) plus
warning bits through `onWarning`. Pass it to `FisicaClient` so warnings appear when features are used.
It never blocks a feature. Pass `storage` (for example `context.filesDir`) to keep the
signed token across launches for offline verification. With `endpoint` it refreshes the
information in the background (`sync()`, or `syncNow()` on a worker thread); without an
endpoint no network is used. A failed exchange only sets `lastSyncError`. The SDK does
not track its own use; see `doc/entitlement.md`.

Keep `com.carenco.fisica.Bridge` and the `FisicaException` constructor in R8 (consumer
rules are included). The low-level `FisicaSession` remains available.

Verified: shared vectors, client, entitlement (including online refresh and storage
against the mock server) and MTU write splitting on a JVM through JNI;
`BleTransport` compiles against android-35. Not verified: any Android device, real
BLE, MTU negotiation with a Scale, and BLE continuous measurement, which the protocol
document describes as USB-only. Android USB and Wi-Fi adapters are not supplied.
