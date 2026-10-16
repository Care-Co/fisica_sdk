# Fisica SDK

Fisica Scale SDK with a shared C++ native core and independently versioned wrappers.
Python 3.0.0 retains the measurement API and delegates packet processing, session states, ACK deadlines,
licensing and analysis to core 2.0.0 (C ABI 2.0). The Dart/Flutter wrapper is 0.6.0; Swift, Kotlin and C# are 0.3.0.
Every company uses the same binary per platform. An SDK key from the Fisica developer portal,
https://developer.fisicahealthcare.com, is activated for one app or PC and opens **full mode**;
without one the SDK runs in **basic mode** (see below). No device firmware change is required.

## Install a binary Python release

```sh
python -m pip install /path/to/fisica-3.0.0-py3-none-PLATFORM.whl
```

Use a wheel for your OS/architecture. Python 3.9+ is required. macOS universal2 and
Windows x64/ARM64 binary build paths are available. Windows includes an MSVC CI build
and Python installation matrix; see [Windows distribution](doc/windows-distribution.md)
for commands and verification limits. Python packages for Linux/mobile are not supplied.
Source distributions are intentionally disabled.

[Partner installation guide](doc/partner-quickstart.md) covers every language and
[minimal applications](examples/partner/README.md). Use the supplied binary packages;
there is no need to build private core sources.

Optional presentation dependencies:

```sh
python -m pip install '/path/to/fisica-3.0.0-py3-none-PLATFORM.whl[visualization]'
python -m pip install '/path/to/fisica-3.0.0-py3-none-PLATFORM.whl[gui]'
```

## Python measurement API

```python
from fisica_sdk import FisicaSDK

sdk = FisicaSDK()
print(sdk.version_info())
devices = sdk.scan_devices()
if devices:
    sdk.connect(devices[0])
    try:
        sdk.set_metadata(id='partner-session-123', name='Member')
        print(sdk.get_battery(), '%')   # read from the Scale; also read on connect and each start
        sdk.start_measurement(duration=10)
        sdk.wait()
        report = sdk.analyze()
        if report is not None:
            sdk.export_report(output='measurement.json')
    finally:
        sdk.disconnect()
        sdk.wait()
```

To integrate your own transport or replay data, use the core directly:

```python
from fisica_sdk import NativeCore, NativeSession

core = NativeCore()
session = NativeSession(core)
# session.action(1, value=2)  # USB transport connected; 1 for BLE
# Send kind=1 event payloads in order, feed received bytes, and tick regularly.
# After firmware probe result, session.action(2, value=-1) starts continuous mode.
# result = core.decode_packet(measurement_event['payload'])
# result.raw_grid and result.sensor_matrix are separate data representations.
# action(3) stops; action(4) releases mode; await RESULT events, then close IO.
# session.close() frees the handle, without performing IO.
```

Import and initialization perform no network/device operations. Compatibility checks
are offline: old supported SDK/wrapper versions warn; unsupported versions, ABI or
result schemas fail before processing. Each wrapper has its own version/support
manifest. Low-level legacy command builders warn with replacement/removal versions.

## Dart / Flutter

[wrappers/flutter](wrappers/flutter/README.md) supplies the common core through Dart FFI,
including the high-level `FisicaClient`, frame collection and reports. The optional
[fisica_flutter package](wrappers/flutter_transport/README.md) provides BLE adapters
for Android/iOS/macOS/Windows and USB serial on Windows/macOS. The platform ZIP includes
prebuilt core libraries. Flutter Web and mobile USB are not supported; actual device
verification remains separate from builds and Mock tests.

## Trusted development and binary release

```sh
python -m pip install build pytest numpy packaging pyserial bleak
python tools/native_build.py
python -m pytest -q
python tools/build_release.py
```

Build artifacts are written to `.build/releases/<sdk-version>/`. Native core ZIPs
contain only compiled libraries, public C headers and release metadata. Python wheels
include public adapters plus the binary; core implementation sources, tests, sample
data and debug artifacts are excluded and audited. Do not distribute this source
checkout or internal build directories.

Compiled distribution raises the cost of reverse engineering; it does not make
host-side code or device communications impossible to inspect. No firmware-level
command authorization is added.

## Requirements, licensing and entitlement

The [SDK requirements](doc/sdk-requirements.md) define Windows 10/11 x64
first, BLE/USB access, unrestricted partner calibration, raw data in full mode, offline use,
and the remaining hardware/platform validation work.

Policy (2026-10-17): the base SDK license comes with genuine hardware, has no date expiry
and is basic mode, for any use including commercial, without registration. Full mode needs
an SDK key of a trial, a non-commercial extension or a commercial contract, activated for the
app or PC. Separate agreements are for exceptions such as OEM supply, rebranding or SDK resale.
The [LICENSE](LICENSE) is Version 2.0, rewritten on 2026-10-17 to this policy and marked as a
draft for review until legal review is complete ([review notes](doc/license-review.md),
[policy history](doc/policy-history.md), [Version 1.0](doc/LICENSE-1.0.txt)). It does not change
existing agreements, packages already delivered, or rights granted under earlier licenses such as MIT.
Wrappers may be modified; applications may redistribute wrappers and the unmodified
compiled core. Core sources are not distributed and may not be reverse engineered. Previously licensed versions and
third-party rights remain separate. The optional GUI uses PyQt5, which has separate
GPL/commercial terms; see the
[provider's licensing FAQ](https://riverbankcomputing.com/commercial/license-faq)
before redistributing a proprietary GUI product.

```python
sdk.configure_entitlement(file='sdk-key.fse', endpoint='https://developer.fisicahealthcare.com')
print(sdk.entitlement_status())   # mode, reason, features, key and trial/contract dates
sdk.activation_code()             # offline PCs: enter this code in the portal, import the file it gives
sdk.release_key()                 # free the seat on this app or PC; returns the portal release code
```

The core decides the mode ([licensing plan](doc/licensing-plan.md), sections 2 and 4.5).
**Full mode** needs a key activated for this app's ID or this PC, within the key's expiry
and the trial or contract (plus 30 days of grace after a contract ends). **Basic mode**
keeps connection, measurement, settings, weight and foot size; frames come at most once
a second without raw data, decoding raw grids raises `FisicaLicenseError`, and report
exports hold the summary only. Online, a new key is activated automatically and renewed
before it expires; nothing else is sent. The SDK does not track its own use: no usage,
last-use time or call counts are collected or reported. See [license and SDK keys](doc/entitlement.md).

```sh
python tools/build_release.py --validation
```

The trusted builder produces the same binary for every company, includes the license
in each archive and writes a SHA-256 `artifact-manifest.json`. Customer builds (no
`--validation`) trust only the production portal key; `--validation` builds also trust
the repository test key for QA with test-signed tokens.
Read `sdk.version_info()` for release information and
`sdk.get_device_info()` for queried firmware and its verification state. Unverified
firmware warns and continues without an opt-in flag.

For host integration, `sdk.connect(device, transport=custom_transport)` accepts a
`ByteTransport` using the Serial/USB command profile. `MockTransport` scripts bytes
without implementing measurement algorithms. See the requirements and Windows guide
for an offline example and the distinction between Mock and hardware verification.

See [native SDK architecture and maintenance](doc/native-sdk.md) for version rules,
wrapper extension, CMake/cross-build instructions, compatibility limits and QA.
The [legacy Korean guide](doc/Readme_ko.md) documents the existing measurement methods;
installation/version/architecture guidance in this README takes precedence.

Session/ACK migration checks: [verification record](doc/session-core-verification.md).

Multi-platform packages and language support: [support matrix](doc/platform-support.md).
