# Fisica SDK

Fisica Scale SDK with a shared C++ native core and independently versioned wrappers.
Python 3.0.0 retains the measurement API and delegates packet processing, session states, ACK deadlines,
licensing and analysis to core 2.0.0 (C ABI 2.0). The Dart/Flutter wrapper is 0.6.0; Swift, Kotlin and C# are 0.3.0.
Every company uses the same binary per platform. An SDK key from the Fisica developer portal,
https://developer.fisicahealthcare.com, is activated for one app or PC and opens **full mode**;
without one the SDK runs in **basic mode** (see below). No device firmware change is required.

## Install a binary Python release

Download the packages for every language from the developer portal's
[downloads page](https://developer.fisicahealthcare.com/docs/downloads) after signing in with a Fisica or Fisica Pro
account (see [doc/partner-quickstart.md](doc/partner-quickstart.md)). PLATFORM is `win_amd64`, `win_arm64` or
`macosx_11_0_universal2`:

```sh
python -m pip install ./fisica-3.0.0-py3-none-PLATFORM.whl
```

Use a wheel for your OS/architecture. Python 3.9+ is required. Wheels are built for macOS
universal2 (`macosx_11_0_universal2`: one core for Apple Silicon and Intel) and Windows x64/ARM64
(MSVC or a trusted cross compiler). The Windows CI build and
Python installation matrix are paused until a self-hosted Windows runner exists; see
[Windows distribution](doc/windows-distribution.md) for commands and verification limits.
Python packages for Linux/mobile are not supplied (CI builds a Linux wheel only to test the
packaging). Source distributions are intentionally disabled. A file whose name contains
`validation` (`fisica-3.0.0+validation-...whl`, `...-validation.zip`) is an internal QA build:
do not install or pass it on, and tell Care&Co if you received one.

[Partner installation guide](doc/partner-quickstart.md) covers every language and
[minimal applications](examples/partner/README.md). Use the supplied binary packages;
there is no need to build private core sources.

Optional image rendering (`sdk.render()`, OpenCV and Pillow):

```sh
python -m pip install './fisica-3.0.0-py3-none-PLATFORM.whl[visualization]'
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
# poll() hands out MEASUREMENT events with the frame already decoded (display grid and metrics).
# core.decode_packet(packet, license) decodes a raw packet yourself and needs full mode.
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

`tools/native_build.py` without flags is the developer build the tests use: its core also
trusts the repository test key. Every customer build path trusts production keys only:
`python -m build --wheel`, `tools/build_release.py`, `tools/build_platforms.py`,
`tools/package_platforms.py` and the CMake build (`tools/native_build.py --metadata-only`).

`tools/build_release.py` empties `.build/releases/<sdk-version>/` and writes this host's wheel
and native core ZIP there. On macOS both are universal2 by default: one core with an arm64 and
an x86_64 slice, `fisica-3.0.0-py3-none-macosx_11_0_universal2.whl`. `--platform macosx_11_0_arm64`
(or `macosx_11_0_x86_64`) builds one architecture, `--platform win_amd64`/`win_arm64` a Windows
cross-build ([Windows distribution](doc/windows-distribution.md)). Native core ZIPs contain only compiled libraries, public C headers,
release metadata and the license. Python wheels include public adapters plus the binary; core
implementation sources, tests, sample data and debug artifacts are excluded and audited. The
Swift, Kotlin, C#, Flutter and sample packages come from `tools/build_platforms.py` and
`tools/package_platforms.py` ([support matrix](doc/platform-support.md)). Do not distribute this
source checkout or internal build directories.

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
The [LICENSE](LICENSE) is Version 2.0, written to this policy in Korean with an English
translation (the Korean text prevails) and governed by Korean law. It states that the SDK is not
a medical device and caps liability at what was paid for the SDK in the previous 12 months
([review notes](doc/license-review.md), [policy history](doc/policy-history.md), [Version 1.0](doc/LICENSE-1.0.txt)). It does not change
existing agreements, packages already delivered, or rights granted under earlier licenses such as MIT.
Wrappers may be modified; applications may redistribute wrappers and the unmodified
compiled core. Core sources are not distributed and may not be reverse engineered. Previously licensed versions and
third-party rights remain separate.

```python
sdk.configure_entitlement(file='sdk-key.fse', endpoint='https://developer.fisicahealthcare.com')
print(sdk.entitlement_status())   # mode, reason, features, key and trial/contract dates
sdk.activation_code()             # offline PCs: enter this code in the portal, import the file it gives
sdk.release_key()                 # free the seat on this app or PC; returns the portal release code
sdk.pair_device()                 # no key: show a code, an owner or admin approves it in the portal
```

Without a key, `python -m fisica_sdk license pair` shows a code, opens the portal's approval page
and stores the key it receives for this PC; scripts on this PC then find it without
`configure_entitlement()` (`license status`, `import`, `code` and `release` manage it).

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
and release metadata in each archive and writes a SHA-256 `artifact-manifest.json`. Customer
builds (no `--validation`) trust only the production portal key; `--validation` builds also trust
the repository test key for QA with test-signed tokens. Because that key's secret is in this
repository, a QA package must never pass for a customer one: `--validation` builds go to their
own directories (`.build/releases-validation`, `.build/platforms-validation`,
`.build/multiplatform-releases-validation`), every ZIP and AAR ends in `-validation`
(`fisica-core-2.0.0-macosx_11_0_universal2-validation.zip`), the wheel's version carries the local
label `+validation` (`fisica-3.0.0+validation-py3-none-macosx_11_0_universal2.whl`, which pip
installs, `pip list` shows and PyPI refuses), and `release.json` and the manifests say
`"validation": true`. `tools/audit_artifacts.py` and `tools/verify_wheel.py` reject an archive
whose core trusts the test key unless they also run with `--validation`, reject such an archive
without the validation name even then, and without `--validation` reject any archive named so.
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
