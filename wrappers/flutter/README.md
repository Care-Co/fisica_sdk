# Fisica native core wrapper for Dart/Flutter

This independently versioned package provides a working FFI binding to the common
core. It does not yet provide Android/iOS Bluetooth permissions, discovery or device
connections. Use `FisicaSession` for common state, ACK and timeout behavior. Supply
received USB/BLE bytes with `feed`, elapsed monotonic milliseconds with `tick`, and
send TRANSMIT event payloads through the host adapter. Decode MEASUREMENT payloads
with `core.decodePacket`. Standalone `nextPacket`/`command` APIs remain available.

```dart
import 'package:fisica_core/fisica_core.dart';

final core = FisicaCore.open('/trusted/path/libfisica_core.dylib',
    onDeprecation: (message) => appLogger.warning(message));
final session = FisicaSession(core);
final clock = Stopwatch()..start();
session.action(1, value: 2, nowMs: clock.elapsedMilliseconds); // USB connected
// Drain events: kind 1 => transport.write(event.payload), kind 3 => command result.
// session.feed(incomingBytes, nowMs: clock.elapsedMilliseconds);
// After firmware probe result, session.action(2, value: -1, nowMs: ...);
// Tick regularly, then drain: session.tick(clock.elapsedMilliseconds);
// Stop/release use actions 3/4; wait for their RESULT events before closing IO.
// Always call session.close() when done (only frees the native handle).
```

Use a platform-built binary from a trusted SDK release. Package Android libraries
for the target ABIs and iOS libraries through the host app/framework with appropriate
code signing/linking. For statically linked iOS builds, pass `DynamicLibrary.process()`
to `FisicaCore` and retain the exported C functions in the host linker configuration.
Desktop macOS FFI is tested; mobile transport and mobile packages are future work.
Browser/Flutter Web is not supported by this native FFI package.

`wrapper.json` is the version/policy source. Keep `pubspec.yaml` synchronized and run
`python tools/sync_flutter_metadata.py` from the trusted repository root. The wrapper
checks SDK, core, ABI and wrapper support policy offline before decoding or analysis.
Supported older versions produce warnings; unsupported versions fail explicitly.

The wrapper and compiled core use the Fisica Scale SDK License, included in the
wrapper ZIP and core bundle. Wrapper modifications and application redistribution
are allowed within the applicable non-commercial grant or commercial agreement;
private core sources are never included. `versionInfo` reports native partner/delivery
identifiers. Native errors retain their numeric `code`. `decodeResponse` exposes
fixed and variable query payloads without reimplementing the protocol in Dart.

See [native SDK maintenance](../../doc/native-sdk.md) for release/build/security details.

## Platform packages (0.4)

Use `fisica-flutter-0.4.0-platforms.zip`, not a source checkout. It includes prebuilt
Android libraries, Apple XCFrameworks and Windows x64/ARM64 DLLs. `FisicaCore.platform()`
uses the linked process symbols on iOS/macOS and the packaged DLL/.so on Windows/Android.
The Apple podspec retains C symbols for Dart FFI; no private source build is performed.
Desktop Windows uses CMake to copy the matching architecture's DLL.

`FisicaClient` adapts core operations to Futures and native events to Streams.
Implement `FisicaTransport` using the app's BLE/USB provider; connect must complete
only after reads/notifications and writes are ready. Use profile 1 for BLE, 2 for USB.
Core owns all command ordering, ACK deadlines, duration and recovery decisions.
Native Swift/Kotlin BLE adapters are available in their respective packages; this
Flutter package does not supply a method-channel bridge or a third-party BLE plugin.
Applications handle platform permissions, identity selection, firmware warnings and
RECONNECT events. Mobile USB/Wi-Fi are not included. Same-header stale ACKs remain a
legacy protocol limitation. Android/iOS physical measurements are still unverified.
