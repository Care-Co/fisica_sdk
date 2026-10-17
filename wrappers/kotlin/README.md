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
// SDK key from the developer portal; without one the SDK runs in basic mode.
val license = FisicaEntitlement(core, "https://developer.fisicahealthcare.com", context.filesDir)
license.importToken(sdkKey)                       // activated for this app online, then stored
val ble = BleTransport(context, bluetoothManager.adapter)
ble.onDevice = { result ->
    if (result.device.name?.contains(FisicaBleProfile.NAME_CONTAINS) == true) {
        ble.stopScan()
        ble.select(result.device)                 // default profile; pass your own if needed
        val client = FisicaClient(core, ble, license, ContextCompat.getMainExecutor(context))
        client.onFrame = { frame -> /* frame.weight, frame.display (29x29); full mode: frame.raw (29x22), frame.packet */ }
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
- Completions receive either an event or an error, and every call completes: after
  `close`, with a `CANCELLED` error. A rejected or unconfirmed command is a
  `FisicaCommandException` (`code` SDK status, `deviceCode` the Scale's code). A callback
  or completion that throws is reported through `onError`.
- `connect` completes with the firmware probe; an unanswered probe does not prevent
  use. A transport that never becomes ready fails after `timeoutMs` (default 15 s).
- `close` releases resources even when the Scale does not confirm; a second `close` while
  one is pending fails with `BUSY`. On `onLinkLost`, reopen the same Scale with `connect`.
  A client or session dropped without `close` is still freed once it is collected.
- `report()` returns unrounded weight and foot size from the core's analysis of every packet
  it received since the start, in every mode, and still after `close`.
- `start` clears the frames of the previous measurement once the core accepts it, so
  measuring several people in a row needs no `clearFrames()`. A reconnect keeps them.
  Only the latest `maxFrames` (at most `FisicaLimit.ANALYSIS_FRAMES`, the most one
  analysis accepts) are kept; `droppedFrames` counts the older ones dropped.
- `connect(link)` takes `FisicaLink.BLE` or `FisicaLink.USB`. Without it the transport's
  own link is used (`FisicaLinkTransport`; `BleTransport` is BLE); a transport that does
  not declare one (a plain `FisicaTransport`, `MockTransport` unless set) must be given it.
  A missing link, or one other than the transport's, fails with `INVALID_ARGUMENT` before
  anything is sent.
- Codes are generated constants: `FisicaStatus`, `FisicaSessionState`, `FisicaSessionEvent`, ...

Transports: `BleTransport` (Android GATT), `MockTransport` (scripted, no hardware; runs
on a plain JVM), or your own `FisicaTransport`. `BleTransport` reports an error instead
of throwing when Bluetooth is off, unsupported or not permitted, requests MTU 517
before discovery, splits writes by the negotiated MTU, falls back to 23 when the
request is refused and resets on reconnect (the splitting rule is unit-tested on a JVM). `FisicaBleProfile.SCALE` comes from the
existing Python SDK and is not yet confirmed on production firmware
(`FisicaBleProfile.VERIFIED` is false). Grant BLUETOOTH_SCAN/CONNECT on Android 12+ and
the legacy location permissions where required.

License: an SDK key from the developer portal is a seat that is activated for one app
(on Android the package name, which the core reads itself; on a desktop JVM the PC).
The core decides the mode and limits what it hands out; the wrapper cannot widen it.

- Full mode: a key activated for this app, within its key expiry and its trial or
  contract. Every frame, plus raw data (`Measurement.raw`, `Measurement.packet`,
  `FisicaCore.decode`) when the key's features include it.
- Basic mode: everything else (no key, not activated, another app, expired, trial or
  contract ended, released, clock set back). Connection, measurement, settings, battery,
  weight and foot size work; frames arrive at most once a second without raw data, and
  `FisicaCore.decode` throws `FisicaLicenseException` (`FEATURE_NOT_LICENSED`).

`FisicaEntitlement(core, endpoint, storage)` holds the key. `importToken`/`importFile`
take a key or a key file (activation or renewal file from the portal). With `endpoint`, a
key that is not activated yet is activated for this app in the background and a key is
renewed before it expires (`sync()`, or `syncNow()` on a worker thread); the server only
receives the core's activation request and empty renewals. Without an endpoint no network
is used: show `activationCode()` (FAC1), enter it in the portal and import the file it
gives. `status()` gives the mode, the reason for basic mode, open features, the key and
its dates; `onStatus` and `onWarning` report changes. `release()` forgets the key and
returns the FRC1 code that frees the seat in the portal. Pass `storage` (for example
`context.filesDir`) to keep the activated key across launches; it is loaded when the
`FisicaEntitlement` is created and wins over the original key file the app imports again.
Without a key, `pair({ code -> ... })` on a worker thread shows a code that an owner or admin
approves at the portal's `/activate` page; the key arrives activated for this app and is stored. Network and storage failures never change the license and
only set `lastSyncError`. Online use needs the INTERNET permission in the app; the AAR
does not declare it. The SDK does not track its own use; see `doc/entitlement.md`.

Pass the license to `FisicaClient`: it applies it to the measurement session before every
start, whenever it changes and every 30 seconds (`licenseFeatures`). With the low-level
`FisicaSession`, call `license(entitlement, nowMs)` yourself and read frames with
`Measurement.fromEvent`; `analyze()` covers every packet since the start.

Keep `com.carenco.fisica.Bridge` and the `FisicaException`/`FisicaLicenseException`
constructors in R8 (consumer rules are included).

Verified: shared vectors, client, license (modes, gated decoding, frames by mode, release
codes, storage, online activation and renewal against the mock portal) and MTU write
splitting on a JVM through JNI; `BleTransport` compiles against android-35. Not verified:
any Android device (including the package-name target), real BLE, MTU negotiation with a
Scale, and BLE continuous measurement, which the protocol document describes as USB-only.
Android USB and Wi-Fi adapters are not supplied.
