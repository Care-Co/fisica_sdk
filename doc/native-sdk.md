# Native SDK architecture and maintenance

The native implementation is maintained in this trusted repository. Distribute only
audited binary artifacts, not this checkout. Python transport/UI adapters and Dart
FFI adapters are public source; device protocol, framing, pressure processing and
measurement analysis implementations are compiled into the core. Session states, ACK correlation, command deadlines and measurement duration also
run in the shared core. Host threads, transport reopening and callbacks stay in wrappers.

## Boundaries

- `native/include/fisica_core.h`: public C ABI, caller-owned buffers and status codes.
- `native/src/fisica_core.cpp`, `fisica_session.cpp`: private processing/session implementations, no Python/OS dependency.
- `fisica_sdk/native.py`: Python ctypes binding and data conversion.
- `fisica_sdk/parser.py`, `analyzer.py`, `device.py`: legacy API adapters.
- `wrappers/flutter`: Dart FFI package for a future Flutter host application's OS transport.
- USB/BLE connections are OS adapters in each wrapper. No firmware changes.
- `native/src/fisica_entitlement.cpp`: runtime entitlement context (signature check and status,
  no usage record); see [entitlement](entitlement.md). Vendored Monocypher lives in
  `native/src/third_party/` and is compiled into the private core only.
- GUI/heatmap drawing is optional, public wrapper presentation code.

The core does not require OpenCV, NumPy, Python or Qt. It requires the platform's C++
runtime. Every wrapper must check ABI and compatibility policy before numeric calls.
No source fallback, library download or automatic compilation happens at runtime.
Missing/incompatible binaries produce an actionable error. Import performs no
network or device access. Online package-update checks are explicitly opt-in.

## Independent versions

| Component | Authoritative file | Current version |
|---|---|---|
| SDK release | `sdk-release.json` | 2.4.0 |
| Native core | `sdk-release.json` | 1.4.0 |
| ABI | `sdk-release.json` | 1.3 |
| Python wrapper / PyPI package | `wrappers/python/wrapper.json` | 2.4.0 |
| Flutter wrapper / Dart package | `wrappers/flutter/wrapper.json` | 0.5.0 |
| Swift/Kotlin/C# wrappers | `wrappers/{swift,kotlin,dotnet}/wrapper.json` | 0.2.0 |
| Result schema | `sdk-release.json` | 1 |

Use semantic versioning per component. A wrapper patch release does not require a
core version bump. A core patch release that preserves behavior/ABI does not require
a wrapper bump. A breaking C ABI change requires ABI major and core major bumps.
An additive ABI change increments minor while retaining every existing signature.
Changing result field meaning or layout requires a result-schema version change.
Current wrappers understand result schema 1; new schema support must be explicit.

`sdk_support` and each wrapper's `support` contain `minimum`, `deprecated_before`,
and `maximum_exclusive`. Below minimum, at/above maximum, incompatible ABI and
prereleases fail before numeric calls. Supported but old SDK/wrapper versions emit
a visible deprecation message with the upgrade threshold. All checks use the policy
embedded in the loaded core and wrapper metadata packaged with that wrapper.
No remote registry is required. Already-distributed SDKs do not gain these checks
retroactively; replace them with a new wrapper release.

Python API deprecations use `deprecated_apis` entries with `since`, `removed_in`, and
`replacement`. `ATCommands.build_command` and `build_variable_command` retain their
legacy behavior with a visible `FisicaDeprecationWarning` in wrapper 2.x; policy
removes them in 3.0.0. Add entries and call `warn_deprecated_api` in future deprecated
Python APIs. Dart provides `warnDeprecatedApi` for the same lifecycle and a callback
to route warnings to a Flutter logger. Do not globally suppress these warnings.

## Adding or updating a wrapper

1. Add `wrappers/<name>/wrapper.json` with an independent version, required core range,
   ABI major/minimum minor, support window and API deprecation entries.
2. Implement an OS transport adapter and a thin C ABI binding. Keep device/analysis
   algorithms in the core. Strings are UTF-8; matrix buffers are row-major doubles.
3. Read `fisica_core_version`, `fisica_core_abi_version` and `fisica_core_policy_json`
   first. Validate the wrapper's own requirements and registered support window.
4. Test buffers/capacities, errors, deprecation and shared numeric fixtures before
   publishing. A new wrapper adds metadata/bindings without changing Python.
5. Build a new SDK release so the core registers that wrapper's support policy.

For Flutter, update `wrapper.json` and the package version in `pubspec.yaml`, then run
`python tools/sync_flutter_metadata.py`. Version drift causes build/test failure.
The generated Dart file is public version metadata, not implementation source.

## Trusted development build

```sh
python -m pip install build pytest numpy packaging pyserial bleak
python tools/native_build.py
python -m pytest -q
python tools/build_release.py
```

`CXX` may select a trusted compiler executable. macOS defaults to a host-architecture
binary targeting macOS 11.0; set `MACOSX_DEPLOYMENT_TARGET` consistently if needed.
Linux uses a native C++ compiler. Windows builds require an MSVC developer shell.
macOS arm64 has been executed locally. Windows x64 now has a native MSVC build/CI
path, PE architecture auditing and explicit trusted cross-build support; see
[Windows distribution](windows-distribution.md) for commands and validation limits.
Other targets require their own build and real-device tests. Wheels use a platform tag matching the binary
and a Python-independent `py3-none` ABI tag. Python requires 3.9 or newer.

For Android/iOS cross-builds, generate metadata on the trusted build host, then use
`native/CMakeLists.txt` with the appropriate NDK/Xcode toolchain. There is no automatic
mobile binary publishing. Provide built libraries through an Android host package or
an iOS signed framework/static-link host package; Flutter does not compile private
sources on the customer's computer. iOS static linking requires a static CMake build
and `DynamicLibrary.process()` with exported symbols retained by the host linker.

`tools/build_release.py` emits a Python wheel and a platform-specific core ZIP under
`.build/releases/<sdk-version>/`. The core ZIP contains only a binary, public header
and release metadata/license. A separately versioned Flutter ZIP contains public Dart
bindings, package manifest and version policy, without test/build directories.
The wheel contains public Python adapters and the compiled core.
`tools/audit_artifacts.py` rejects core sources/debug artifacts and invalid macOS
architecture tags. Source distributions intentionally fail; use `build --wheel`.
Keep all build output and source-baseline backups internal. Do not publish `native/`,
`tools/`, `.build/`, samples or tests. New releases use the Fisica Scale SDK License
for wrappers and core; rights to previously licensed source are not revoked.
No company, contract or delivery identifier is compiled in: one binary per platform,
architecture and core version serves every company, and the release tools write a
hashed `artifact-manifest.json`. The core trusts the server public keys listed in
`native/entitlement-keys.json`. Customer packages trust only its production keys;
`--validation` QA builds also trust the test key, whose secret is in this repository. Runtime entitlement never locks a feature.

## Measurement compatibility and limits

- Raw packet remains 645 bytes with a 29x22 raw grid. Display grid remains 29x29 with
  a center gap and legacy processing. Raw values are not calibrated physical kPa.
- The legacy battery placeholder 99 is preserved; `battery_valid` is false in the
  decoded result, frames, reports and serialized output. No actual battery-reading
  support is claimed. Raw packet/grid data now survives high-level serialization.
- Weight stabilization, IQR processing and nominal 1cm-cell foot dimensions retain
  existing behavior. Stabilization can return a result with fewer than ten retained
  frames, matching the old implementation; this is not a new quality guarantee.
- Streaming supports both the saved-sample (639/640) and previous SDK (638/639)
  first-footer layouts of the 645-byte packet, partial chunks and multiple
  complete packets. Complete measurements take priority over command-shaped bytes
  in the grid. Command responses are accepted at the buffer start or ahead of a
  known complete measurement, not from inside an incomplete grid. The legacy wire
  protocol has no newly added checksum/authentication; arbitrary corruption and
  ambiguous framing cannot be completely resolved without firmware changes.
- `NativeCore.decode_packet` exposes raw/display arrays separately, while legacy
  `MeasurementFrame` and `SessionReport` shapes remain usable.
- ABI 1.1 adds variable query response payload extraction. Wrappers preserve native
  error codes; firmware query timeout/unknown firmware emits a warning, not a lock.
- Receiver reconnect selects the original device, using USB serial number when
  available. Physical reconnect/driver behavior still needs real-device QA.
- Custom `ByteTransport` and scripted `MockTransport` can use the Serial/USB profile.
  BLE injection remains future work. Lifecycle/ACK handling now shares the C ABI session.
- The attached 644-byte document and corrected single-measurement command differ
  from existing APIs. Do not change those APIs without hardware evidence; see
  [requirements and protocol discrepancies](sdk-requirements.md).
- Native compilation raises reverse-engineering cost. Device command bytes and
  measurement results remain observable by the host. SDK-only restrictions cannot
  authorize commands on behalf of firmware or safely hide shared secret keys.

## Verification

Python tests cover frame fragmentation/concatenation, command-like grid bytes,
regression fixtures captured before migration, numeric/length validation, capacity
canaries, missing binaries and independent version/deprecation behavior. Dart tests
load the same core to verify decoding, commands, analysis and compatibility handling.
Python and Dart consume the same JSON golden vectors. `tests/native_contract.cpp`
exercises the public ABI with native buffer guards; build
it with UBSan on a supported toolchain. An ASan runtime on this macOS host stalled in
runtime initialization before `main`, so that run is not memory-safety evidence.
Mac USB session start/stop/duration/release was verified on firmware 2.0.2-RC1.
Actual Windows/BLE, physical cable reconnection and Android/iOS execution remain unverified.

```sh
cd wrappers/flutter
dart pub get
dart analyze
FISICA_CORE_TEST_LIBRARY=/absolute/path/libfisica_core.dylib dart test
```


## Shared session state and ACK handling (ABI 1.2)

`fisica_core_session_create/destroy/action/command/feed/tick/poll` form an additive
C ABI. Existing numeric/command functions and legacy Python SDK signatures remain.
`NativeSession` (Python) and `FisicaSession` (Dart) bind this exact API. Session events
and error codes are declared in the public header. All state is per handle; serialize
calls and destruction to each handle, while separate handles are independent.

- Core owns connection profile, firmware probe, mode/start/stop/release ordering,
  framing, matching header/type, RET and 16-bit device errors, 2000ms ACK deadlines,
  duration from start ACK, and the decision to resume after a link loss.
  Legacy Python `duration=0`/`None` remains unlimited; native START uses -1 for
  unlimited and 0 for zero milliseconds.
- Host supplies nondecreasing monotonic milliseconds, ticks regularly, feeds bytes,
  drains events, sends TRANSMIT bytes in order, and reports send/link failures. Polling
  frequency and transport writes affect wall-clock latency; core has no threads or IO.
- Start reaches MEASURING only after mode ACK and start ACK. Repeated measurements
  reuse the acknowledged mode. Stop reaches IDLE only after stop ACK. Disconnect
  stops an active measurement, then waits for mode release ACK before closing.
- Firmware NACK/timeout produces a result and an unverified warning while continuing.
  State-changing NACK/timeout enters FAULT. No automatic command retry occurs, including
  zero/calibration requests. Device code 1 is ambiguous and never assumed success.
- Link loss requests reconnect to the original device; the core resumes only if still
  requested, retaining the original duration deadline. A failed initial start is not
  resumed invisibly. Reopening/identity lookup remains host transport code.
- Missing/failed start, stop and release ACKs are observable. `FisicaSDK.wait()` raises
  stable errors for async measurement operations; zero/scale calls raise directly.
  `FisicaCommandError.code` is the SDK status, `device_code` the distinct device code.
  Stop/expiry during link loss reports TRANSPORT_LOST rather than claiming device stop.
  A close in FAULT closes host resources but reports INVALID_STATE because release
  cannot be confirmed. The device may still be measuring: reconnect deliberately.
- The old protocol has no sequence number. Header/type/RET matching cannot prove
  freshness of a duplicate same-header ACK, even across reopening. Never promise
  exactly-once execution or safe retries. Reopen clears host buffers but cannot erase
  queued firmware replies. Fixed SET ACKs on the live Scale contain zero DATA, so
  payload echo cannot be used for correlation. This requires firmware support to fix.
- The legacy BLE continuous sequence is retained; documents say USB-only and real BLE
  verification is still required. No CRC, version negotiation or device auth is invented.

The host facade's flags/async loop only schedule callbacks and freeze reports; they
are not authoritative device state. The receive loop stays alive after stop so later
commands and disconnect can still get ACKs. Frames are recorded before ACK waiters
are released, so slow application callbacks do not omit received frames from reports.
An active `disconnect()` delegates stop and release to the core instead of waiting
indefinitely for the application to stop measurement first. Custom MockTransport exchanges must now
include actual ACK bytes; sending bytes alone is no longer success.

Drain events after each call. Feed at most 65536 bytes (`FISICA_LIMIT_FEED_BYTES`) per call; internal framing is
bounded and the queue holds at most 1024 events. Queue/allocation failure returns
INTERNAL_ERROR and invalidates the session; close/reopen rather than ignoring it.
`poll` reports required payload length and retains an event on capacity failure.
Text V1 commands and session-managed mode/continuous/probe commands remain supported
by stateless `command`, but cannot bypass the session engine through `session_command`.
Do not mix untracked direct writes with an active managed session.

Shared `tests/fixtures/sessions.json` covers byte/event/state contracts in Python and
Dart. Additional native/Python tests cover NACK codes, late/unrelated ACKs, start/stop
cancellation, link recovery, duration, per-handle isolation and buffer canaries.

Final session release evidence: [session core verification](session-core-verification.md).

ABI 1.3 adds the shared offline wrapper-policy checker and status 12.
ABI 1.4 adds the runtime entitlement context and `fisica_core_analyze_packets`, plus
two constant-only enums (no new functions): `FisicaLink` (`FISICA_LINK_BLE`/`USB`, the
CONNECTED value) and `FisicaLimit` (`ANALYSIS_FRAMES` 19,949 per analysis call,
`FEED_BYTES`, `TOKEN_BYTES`). The core uses these values for its own checks, so the
header and the enforced limits cannot drift.

## Shared codes and binding verification

`python tools/sync_binding_metadata.py` generates, from the public C header, the
status/session/link/limit/entitlement codes for Swift, Kotlin, C#, Dart and Python, the default
BLE profile from `wrappers/ble-profile.json`, and the Apple podspec symbol list.
A test fails when a generated file is stale. Do not edit generated files.

`python tools/verify_bindings.py swift kotlin dotnet dart` builds a host core and runs
each binding's contract: shared session vectors plus connect, measure, stop, report
and entitlement through a Mock transport. Set `DART`, `DOTNET`, `FISICA_GRADLE_LIB`
or `ANDROID_SDK_ROOT` when the tools are not on the default paths. These runs need no
hardware and are not device, Android/iOS runtime or real Windows evidence.
Multiplatform builds/bindings and exact verification limits: [platform support](platform-support.md).
