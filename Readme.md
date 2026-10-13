# Fisica SDK

Fisica Scale SDK with a shared C++ native core and independently versioned wrappers.
Python 2.4.0 retains the measurement API and delegates packet processing, session states, ACK deadlines and analysis
to core 1.4.0 (C ABI 1.4). The Dart/Flutter wrapper is 0.5.0; Swift, Kotlin and C# are 0.2.0.
Every company uses the same binary per platform; company and contract information is
supplied at runtime as [entitlement information](doc/entitlement.md) and never blocks a feature.
No device firmware change is required. The entitlement server is specified but not yet implemented.

## Install a binary Python release

```sh
pip install /path/to/fisica-2.2.0-py3-none-PLATFORM.whl
```

Use a wheel for your OS/architecture. Python 3.9+ is required. macOS arm64 and
Windows x64 binary build paths are available. Windows includes an MSVC CI build
and Python installation matrix; see [Windows distribution](doc/windows-distribution.md)
for commands and verification limits. Linux and mobile targets require their own
builds and validation. Source distributions are intentionally disabled.

Optional presentation dependencies:

```sh
pip install 'fisica[visualization]'
pip install 'fisica[gui]'
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

[wrappers/flutter](wrappers/flutter/README.md) contains a working Dart FFI wrapper for
decoding, command generation, framing, analysis and compatibility checks. It loads
a trusted, platform-built native binary. Android/iOS BLE transports and mobile
packaging are future work; desktop macOS FFI tests pass. Flutter Web is not supported.

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
first, BLE/USB access, unrestricted partner calibration/raw data, offline use,
and the remaining hardware/platform validation work.

Policy (2026-10-14): the base SDK license comes with genuine hardware, has no date
expiry and covers ordinary commercial apps and services after registration. Separate
agreements are only for exceptions such as OEM supply, rebranding or SDK resale.
The [LICENSE](LICENSE) is Version 2.0, written to this policy and marked as a draft
for review until legal review is complete ([review notes](doc/license-review.md),
[Version 1.0](doc/LICENSE-1.0.txt)). It does not change existing agreements, packages
already delivered, or rights granted under earlier licenses such as MIT.
Wrappers may be modified; applications may redistribute wrappers and the unmodified
compiled core. Core sources are not distributed. Previously licensed versions and
third-party rights remain separate. The optional GUI uses PyQt5, which has separate
GPL/commercial terms; see the
[provider's licensing FAQ](https://riverbankcomputing.com/commercial/license-faq)
before redistributing a proprietary GUI product.

```python
sdk.configure_entitlement(file='fisica-entitlement.fse')   # works on a PC that was never online
print(sdk.entitlement_status())   # base license, confirmation freshness, contract state
```

Missing, outdated, rejected, ended or revoked entitlement information only produces
a status and a `FisicaEntitlementWarning`; connection, measurement and analysis keep
working. Online refresh of the information runs only when an `endpoint` is given. The
SDK does not track its own use: no usage, last-use time or call counts are collected
or reported. See [entitlement](doc/entitlement.md).

```sh
python tools/build_release.py --validation
```

The trusted builder produces the same binary for every company, includes the license
in each archive and writes a SHA-256 `artifact-manifest.json`. The core currently
trusts a test signing key only, so packaging requires `--validation` until production
keys are configured.
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
