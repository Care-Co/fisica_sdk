# Fisica native core wrapper for Dart/Flutter

This independently versioned package supplies the common core through Dart FFI.
Use `FisicaClient` for connection, measurement, frame collection and reports.
For ready-made BLE/desktop USB adapters add the sibling `fisica_flutter` package.
See [installation and minimal app](../../doc/partner-quickstart.md).
The low-level `FisicaSession`, `nextPacket` and `command` APIs remain available.

```dart
import 'package:fisica_core/fisica_core.dart';

final core = FisicaCore.platform();
final client = FisicaClient(core, selectedTransport);
await client.connect(link: FisicaLink.usb); // or FisicaLink.ble
await client.start();
// Collect frames, then await client.stop(); client.report();
// Always await client.close(), including on errors.
```

Use the supplied platform ZIP, which includes Android libraries, Apple XCFrameworks
and Windows DLLs. `FisicaCore.platform()` loads the packaged/linked library and the
Apple podspec retains C symbols. iOS app signing is still the host app's responsibility.
Browser/Flutter Web is not supported by this native FFI package.

`wrapper.json` is the version/policy source. Keep `pubspec.yaml` synchronized and run
`python tools/sync_flutter_metadata.py` from the trusted repository root. The wrapper
checks SDK, core, ABI and wrapper support policy offline before decoding or analysis.
Supported older versions produce warnings; unsupported versions fail explicitly.

The wrapper and compiled core use the Fisica Scale SDK License Version 2.0, included
in the wrapper ZIP and core bundle. The base SDK license comes with genuine Fisica
hardware, has no date-based expiry and covers non-commercial and ordinary commercial
use. Wrapper modifications and inclusion in your own applications are allowed under
its terms; OEM supply, rebranding, standalone SDK resale and use with other hardware
require a separate agreement. Private core sources are never included.
`versionInfo` reports release and entitlement
key information; the binary carries no company or delivery identifier. Native errors retain their numeric `code`. `decodeResponse` exposes
fixed and variable query payloads without reimplementing the protocol in Dart.

See [native SDK maintenance](../../doc/native-sdk.md) for release/build/security details.

## Platform packages (0.5)

Use `fisica-flutter-0.5.0-platforms.zip`, not a source checkout. It includes prebuilt
Android libraries, Apple XCFrameworks and Windows x64/ARM64 DLLs. `FisicaCore.platform()`
uses the linked process symbols on iOS/macOS and the packaged DLL/.so on Windows/Android.
The Apple podspec retains C symbols for Dart FFI; no private source build is performed.
Desktop Windows uses CMake to copy the matching architecture's DLL.

`FisicaClient` adapts core operations to Futures and native events to Streams.
Use the sibling `fisica_flutter` package's `FisicaBleTransport` or desktop
`FisicaUsbTransport`, or implement `FisicaTransport` using the app's own provider.
Connect must complete only after reads/notifications and writes are ready.
Core owns all command ordering, ACK deadlines, duration and recovery decisions.

- `connect(link: ...)` takes `FisicaLink.ble` or `FisicaLink.usb`. Without it the
  transport's own link is used (`FisicaLinkTransport`; the BLE adapter is BLE, the USB
  adapter USB), otherwise BLE. A different link fails with `FisicaArgumentError` before
  anything is sent. `profile:` is the deprecated name of the same value.
- After a link loss the stream emits an error and a `reconnect` event and the session
  is RECOVERING. Call `connect()` again on the same client (optionally with
  `transport:` to replace the transport; the old one is closed): the core resumes a
  measurement it still wants, with its original deadline, and the frames are kept.
  `close()` after a loss frees resources without an error.
- `start()` clears the frames of the previous measurement once the core accepts it, so
  measuring several people in a row needs no `clearFrames()`. Only the latest
  `maxFrames` (at most `FisicaLimit.analysisFrames`, the most one analysis accepts) are
  kept; `droppedFrames` counts the older ones dropped. This also bounds memory.
The core package stays usable from pure Dart. The optional Flutter transport package
adds OS plugins without adding Bluetooth dependencies to the pure Dart binding.
Applications handle platform permissions, identity selection, firmware warnings and
RECONNECT events. Mobile USB/Wi-Fi are not included. Same-header stale ACKs remain a
legacy protocol limitation. Android/iOS physical measurements are still unverified.

## Reports, codes and entitlement (0.5)

`FisicaClient` collects decoded frames and `report()` returns weight and foot
dimensions computed by the shared core from the received packets (29x29 display grid
and per-frame weight; unrounded). Codes are generated classes such as `FisicaStatus`,
`FisicaSessionState`, `FisicaSessionEventKind` and `FisicaContract`, and
`FisicaBleProfile.scale` carries the default GATT UUIDs (from the Python SDK, not yet
confirmed on production firmware) for the BLE plugin your app uses.

`FisicaEntitlement` imports a server-signed token or file and reports three separate
values (base license, confirmation freshness, contract state) plus warning bits. Pass
it as `FisicaClient(core, transport, entitlement: ...)` so warnings appear when features are used. It
never blocks a feature. Pass `storageDirectory` (for example the app support directory)
to keep the signed token across launches for offline verification. With `endpoint` it
refreshes the information in the background (`await sync()` to run it now); without an
endpoint no network is used. A failed exchange only sets `lastSyncError`. The SDK does
not track its own use; see `doc/entitlement.md`.
