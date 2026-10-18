# Fisica native core wrapper for Dart/Flutter

This independently versioned package supplies the common core through Dart FFI.
Use `FisicaClient` for connection, measurement, frame collection and reports.
For ready-made BLE/desktop USB adapters add the sibling `fisica_flutter` package.
See [installation and minimal app](../../doc/partner-quickstart.md).
The low-level `FisicaSession`, `nextPacket` and `command` APIs remain available.

```dart
import 'package:fisica_core/fisica_core.dart';

final core = FisicaCore.platform();
// SDK key from the developer portal; without one the SDK runs in basic mode.
// The activated key is stored in the app's private storage (desktop: the per-user
// Fisica folder), so it is still there after a restart.
final license = FisicaEntitlement(
  core,
  endpoint: 'https://developer.fisicahealthcare.com',
)..importToken(sdkKey);
await license.sync(); // activates a new key for this app or PC
final client = FisicaClient(core, selectedTransport, entitlement: license);
await client.connect(link: FisicaLink.usb); // or FisicaLink.ble
await client.start();
// Collect frames, then await client.stop(); client.report();
// Always await client.close(), including on errors; close the license last.
```

Use the supplied platform ZIP, which includes Android libraries, Apple XCFrameworks
and Windows DLLs. `FisicaCore.platform()` loads the packaged/linked library and the
Apple podspec retains C symbols. iOS app signing is still the host app's responsibility.
Browser/Flutter Web is not supported by this native FFI package.

`wrapper.json` is the version/policy source. Keep `pubspec.yaml` synchronized and run
`python tools/sync_flutter_metadata.py` from the trusted repository root. `FisicaCore`
asks the core (`fisica_core_check_wrapper`, the policy every wrapper uses) whether it
supports this SDK, ABI and wrapper release, offline, before decoding or analysis.
Supported older versions produce warnings; unsupported versions fail explicitly.

The wrapper and compiled core use the Fisica Scale SDK License Version 2.0 (draft),
included in the wrapper ZIP and core bundle. The base SDK license comes with genuine Fisica
hardware, has no date-based expiry and is basic mode, for any use including commercial;
full mode needs an SDK key of a trial, extension or contract activated for the app or PC.
Wrapper modifications and inclusion in your own applications are allowed under its terms;
OEM supply, rebranding, standalone SDK resale and use with other hardware require a separate
agreement. Private core sources are never included and may not be reverse engineered.
`versionInfo` reports release and entitlement
key information; the binary carries no company or delivery identifier. Native errors retain their numeric `code`. `decodeResponse` exposes
fixed and variable query payloads without reimplementing the protocol in Dart.

See [native SDK maintenance](../../doc/native-sdk.md) for release/build/security details.

## Platform packages (0.6)

Use `fisica-flutter-0.6.0-platforms.zip`, not a source checkout. It includes prebuilt
Android libraries, Apple XCFrameworks and Windows x64/ARM64 DLLs. `FisicaCore.platform()`
uses the linked process symbols on iOS/macOS and the packaged DLL/.so on Windows/Android.
The Apple podspec retains C symbols for Dart FFI; no private source build is performed.
iOS apps target 15.0 or later and macOS apps 12.0 or later (Xcode 27 rejects lower deployment
targets), so iOS 13/14 devices are not supported.
Desktop Windows uses CMake to copy the matching architecture's DLL.

`FisicaClient` adapts core operations to Futures and native events to Streams.
Use the sibling `fisica_flutter` package's `FisicaBleTransport` or desktop
`FisicaUsbTransport`, or implement `FisicaTransport` using the app's own provider.
Connect must complete only after reads/notifications and writes are ready.
Core owns all command ordering, ACK deadlines, duration and recovery decisions.

- `connect(link: ...)` takes `FisicaLink.ble` or `FisicaLink.usb`. Without it the
  transport's own link is used (`FisicaLinkTransport`; the BLE adapter is BLE, the USB
  adapter USB); any other `FisicaTransport` must be given it. A missing link, or one
  other than the transport's, fails with `FisicaArgumentError` before anything is sent.
  `profile:` is the deprecated name of the same value.
- Only the same operation is busy (a second `start()` while one is pending); the core
  orders the rest, for example a `stop()` sent while `start()` waits for its ACK.
  Failures arrive as Future errors, never as a synchronous throw.
- After `close()`, `connect()`, `start()`, `stop()` and `readBattery()` fail with
  `FisicaStateError` (`FisicaStatus.cancelled`), the code `close()` gives what it
  cancels, as in every other wrapper.
- After a link loss the stream emits an error and a `reconnect` event and the session
  is RECOVERING. Call `connect()` again on the same client (optionally with
  `transport:` to replace the transport; the old one is closed): the core resumes a
  measurement it still wants, with its original deadline, and the frames are kept.
  `close()` after a loss frees resources without an error.
- A second `close()` while one is pending fails with `FisicaStateError`
  (`FisicaStatus.busy`); it no longer returns the pending close's future. Once closed,
  `close()` completes at once and does nothing, even when the first close failed.
- `start()` clears the frames of the previous measurement once the core accepts it, so
  measuring several people in a row needs no `clearFrames()`. Only the latest
  `maxFrames` (at most `FisicaLimit.analysisFrames`, the most one analysis accepts) are
  kept; `droppedFrames` counts the older ones dropped. This also bounds memory.
The core package stays usable from pure Dart. The optional Flutter transport package
adds OS plugins without adding Bluetooth dependencies to the pure Dart binding.
Applications handle platform permissions, identity selection, firmware warnings and
RECONNECT events. Mobile USB/Wi-Fi are not included. Same-header stale ACKs remain a
legacy protocol limitation. Android/iOS physical measurements are still unverified.

## Reports, codes and license modes (0.6)

`FisicaClient` collects decoded frames and `report()` returns weight and foot
dimensions computed by the shared core from every packet the session received since
`start()` (unrounded), in every mode and still after `close()`. A `FisicaSession`
dropped without `close()` is freed by a native finalizer once it is collected.
Codes are generated classes such as `FisicaStatus`, `FisicaSessionState`,
`FisicaSessionEventKind`, `FisicaMode` and `FisicaModeReason`, and
`FisicaBleProfile.scale` carries the default GATT UUIDs (from the Python SDK, not yet
confirmed on production firmware) for the BLE plugin your app uses.

The native core decides between two modes and limits what it hands out itself:

- **Full mode**: an SDK key activated for this app (iOS/macOS bundle ID, Android
  package name) or this PC (Windows, or a macOS command-line program), within the
  key's expiry and its trial or contract. Every packet arrives as a frame with the
  29x22 `rawGrid` and the 645-byte packet (`rawData`); `decodePacket` works.
- **Basic mode**: everything else (no key, a key not activated yet, activated for
  another app or PC, expired, released, trial or contract ended, clock set back).
  Connection, measurement, settings, battery, weight and foot size work; the session
  hands out at most one frame a second without `rawGrid`/`rawData`, `report()` still
  analyzes every packet (`FisicaSession.analyze`), and `decodePacket` throws
  `FisicaLicenseError` (`FisicaStatus.featureNotLicensed`).

`FisicaEntitlement` holds the SDK key. `importToken`/`importFile` take a key from the
developer portal or a key file it made for a device that is never online (activation
or renewal file). The key stored for this app or PC (paired or imported earlier) is
loaded at once, so after pairing the app needs no key of its own. Without a key, `await entitlement.pair((code) => ...)` shows a code that an
owner or admin approves at the portal's `/activate` page; the key arrives activated for this
app or PC and is stored (endpoint `FisicaEntitlement.defaultEndpoint`). `status()` returns the mode, the reason for basic mode (`message`
explains it), the open `FisicaFeature` bits, company, contract, key, the app ID or PC
hash it is activated for, its dates (`keyUntil`, `fullUntil`, `refreshBy`, contract
dates) and warning bits; `changes` emits whenever the license changes, and
`onWarning` gets newly raised warnings. Pass it as
`FisicaClient(core, transport, entitlement: ...)`: the client applies it to its session
before every start, every 30 seconds and whenever it changes.

With `endpoint`, a new key is activated for this app or PC in the background (`await
sync()` waits for it) and renewed before it ends; only the key (as the bearer token)
and the core's activation request (this app's ID or PC hash) are sent. A failed
exchange only sets `lastSyncError` and never changes the license; without an endpoint
no network is used. A key not activated yet shows an activation code
(`activationCode()`, FAC1) to enter in the developer portal from another device.
`release()` frees the key on this app or PC and returns the FRC1 code to enter in the
portal. An answer that arrives after `release()` or after another key was imported is
dropped: a released key stays released and the newer key stays. The activated key and
the latest time seen are kept across launches, one file per seat, in
`FisicaEntitlement.defaultStorageDirectory` unless `storageDirectory` names another
directory; `storageDirectory: null` keeps them in memory only (a paired key is then gone
after a restart and pairing again takes another seat). The default is the per-user
directory the Python and C# SDKs use on Windows (`%LOCALAPPDATA%\Fisica\sdk`), macOS
(`~/Library/Application Support/Fisica/sdk`, inside the container of a sandboxed app) and
Linux (`$XDG_STATE_HOME/fisica-sdk` or `~/.local/state/fisica-sdk`); on iOS the app's
`Library/Application Support/Fisica/sdk` (as the Swift SDK); on Android the app's files
directory `files/fisica-sdk` (as the Kotlin SDK), found from the cache directory Flutter
gives `Directory.systemTemp`, without path_provider. Where none can be found it is null
(memory only): pass a directory then. A stored key wins over an older original key file.
The SDK does not track its own use; see `doc/entitlement.md`.
