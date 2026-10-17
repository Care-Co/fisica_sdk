# Windows Python binary distribution

## Target and distribution boundary

The initial Windows target is Windows 10/11 x64 with x64 CPython 3.9 or newer.
The Python wheel is `fisica-<python-wrapper-version>-py3-none-win_amd64.whl`.
`fisica_sdk/_native/fisica_core.dll` exports the existing C ABI; it does not link
against a particular CPython ABI. A separate Windows ARM64 wheel (`win_arm64`, built
with an ARM64 compiler or cross compiler) is supplied as well, but it has not been run on
ARM64 hardware ([support matrix](platform-support.md)). 32-bit Python wheels are not
provided. This work does not change server APIs, firmware or the wire protocol.

Customer artifacts contain public Python adapters plus a compiled DLL. They never
include `native/src`, build tools, samples, tests, `.cpp`, `.obj`, `.pdb`, `.lib`,
`.exp`, `.ilk` or other compiler sidecars. MSVC object/import-library output is
directed to the internal generated build directory, not the wheel. Source
distributions remain disabled. The native ZIP contains only the DLL, public C
header and release metadata. Keep the source repository private.

## Customer installation

```powershell
py -3.11 -m pip install .\fisica-3.0.0-py3-none-win_amd64.whl
py -3.11 -c "from fisica_sdk import NativeCore; print(NativeCore().version_info())"
```

Verify that Python reports 64-bit (`struct.calcsize('P') == 8`). Compiler tools and
core sources are not required on the customer's computer. Image rendering is
optional via `fisica[visualization]`. Actual USB serial drivers,
Bluetooth availability and Scale connectivity still need device integration QA.
Serial-port enumeration and Bluetooth backends are loaded only when discovery or
Bluetooth connection is requested, so SDK initialization/offline analysis does not
require those device-discovery APIs to be available.

## Trusted MSVC build

Use Visual Studio 2022 Build Tools with Desktop development with C++, the Windows
SDK and an **x64 Native Tools** developer shell. Start from the private checkout.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade build pytest numpy packaging pyserial bleak
python tools/native_build.py
python -m pytest -q
python tools/build_release.py --output release
python tools/verify_wheel.py .\release\3.0.0\fisica-3.0.0-py3-none-win_amd64.whl
```

`build_release.py` empties `release\3.0.0\` first, so build each platform into its own
`--output`. Its wheel and core ZIP trust the production portal key only, and `verify_wheel.py`
checks that an installed release wheel rejects a test-signed key. For QA with test-signed
keys build with `--validation` and verify with `python tools/verify_wheel.py --validation ...`,
which checks full mode instead.

The native build uses `/MT` to link the C++ runtime statically, `/fp:strict` to
retain the legacy numeric behavior, an x64 linker target and non-debug release
output. The DLL still uses ordinary Windows system APIs. CMake uses the same
static MSVC runtime and floating-point policy for native host-library builds.

Build output is separated by platform under `.build/python/<platform>/`, so a
prior macOS build cannot leak a dylib into the Windows wheel. Build/audit tools
check PE signatures, the x64 machine field, PE32+ format, the DLL flag and the
wheel filename/internal tag. A renamed x86/ARM64 binary or compiler sidecar fails
the release audit. This check is not a code-signing or malware certification.

## CI artifacts and installation matrix

Removed on 2026-10-17 until a self-hosted Windows runner exists (CI uses only self-hosted
runners; see `doc/platform-support.md`, CI). The workflow below is kept in git history
(`git show a44754b:.github/workflows/windows-wheel.yml`). It builds with `--validation`, so a
restored copy must call `tools/verify_wheel.py --validation` and name its artifacts with a
`-validation` suffix.

`.github/workflows/windows-wheel.yml` provided manual and internal PR builds on
`windows-2022`. It initializes an x64 MSVC environment, builds the core, runs the
test suite, produces and audits the wheel/native ZIP, then installs the wheel
into a clean environment outside the checkout. GUI/visualization extras are also
installed and smoke-tested on Python 3.11. The **same wheel** is installed
on Python 3.9, 3.11, 3.13 and 3.14 in separate jobs.

The workflow is restricted to the private source repository and internal PRs.
It uploads only the explicit audited Windows wheel/native ZIP and artifact manifest paths, retaining
them for 14 days. It does not upload the checkout, internal build tree or logs
as distribution archives. It does not publish to PyPI or create a GitHub Release.
Actions use pinned commit revisions. Pushing or triggering this workflow is a
separate external action; the configuration alone is not evidence of a CI pass.

## Trusted cross-build verification

An explicit `win_amd64` target can be built with a Windows x64 cross compiler on
a trusted non-Windows host. No cross compiler is selected implicitly.

```sh
FISICA_BUILD_PLATFORM=win_amd64 CXX=/trusted/toolchain/bin/x86_64-w64-mingw32-clang++ \
  python tools/build_release.py --output .build/windows-releases
```

The LLVM MinGW cross build uses a static C++ runtime, no debug output and the same
floating-point contraction policy as the macOS implementation. Run the resulting
wheel on Windows before production distribution. An MSVC build on Windows is the
Windows-native release validation path (CI paused until a Windows runner exists); a
cross-build is not an MSVC test.

`FISICA_PREBUILT_CORE=/trusted/path/fisica_core.dll` can package an already-built
DLL with `FISICA_BUILD_PLATFORM=win_amd64`. Only that validated DLL and public
metadata are copied, never neighboring sources/sidecars. Use it only for a DLL
built from the matching private release. Always run `tools/verify_wheel.py` on
Windows after packaging to confirm runtime compatibility and versions.

## License and common binary

Public wrappers and private compiled core use the Fisica Scale SDK License. The
wheel uses `License-Expression: LicenseRef-Fisica-Scale-SDK` and includes the license;
core and Flutter ZIPs also include it. The packaged text is Version 2.0, written to the
policy of 2026-10-17 in Korean with an English translation ([review notes](license-review.md)). Third-party libraries retain their own licenses.

```sh
python tools/build_release.py --validation --output release
```

Every company receives the same wheel and DLL: no partner or delivery identifier is
compiled in, and `artifact-manifest.json` records the artifact hashes. Company and
contract information is supplied at runtime ([entitlement](entitlement.md)) and never
blocks a feature. Without `--validation` the core trusts only the production portal
key; `--validation` also trusts the test signing key and is for QA, not for customers.

## Runtime and hardware-free checks

`scan_devices()` searches both USB and BLE. Use `scan_devices(transport='Serial')`
for wired-only discovery, avoiding Bluetooth startup. USB detection uses VID rather
than an English driver description. USB adapter serial numbers are provided for
recovery, but are not authenticated device IDs. Duplicate recovery candidates are
not selected automatically. A Windows USB/UART driver may need separate installation.

Custom `ByteTransport` implementations may be injected with the Serial/USB profile:

```python
from fisica_sdk import FisicaSDK, NativeCore, MockTransport

core = NativeCore()
transport = MockTransport({
    core.command('GET_FIRMWARE_VERSION'): [bytes.fromhex('24434701322e302e31fff0')],
})
sdk = FisicaSDK()
sdk.connect({'id': 'mock', 'type': 'Serial'}, transport=transport)
print(sdk.get_device_info())  # Synthetic 2.0.1; warns because not hardware-verified.
sdk.disconnect()
sdk.wait()
```

To measure with Mock, explicitly script ACKs for SET_DEVICE_USB, START_CONTINUOUS,
STOP_CONTINUOUS and SET_DEVICE_STOP (e.g. `242253010000fff0` for mode and
`245153010000fff0` for start/stop). Provide measured/synthetic byte chunks AFTER the
start ACK in the start-command exchange to simulate frames. Mock contains no analysis algorithms and does not synthesize successful
responses for every command. Frame/report exports preserve `raw_data`, `raw_grid`
and `battery_valid`. Firmware timeout or unknown version warns and continues.
Worker start/stop errors are surfaced by `wait()` instead of leaving it waiting forever.
Explicit disconnect waits for stop/release ACKs and always closes I/O. Failed/uncertain
release is reported, including close in FAULT; it is not counted as successful release.

On an online Windows preparation machine of the same architecture/Python version,
prepare binary dependencies for an offline installation:

```powershell
py -3.11 -m pip download --only-binary=:all: --dest wheelhouse '.\fisica-3.0.0-py3-none-win_amd64.whl[visualization]'
# Copy wheelhouse to the offline machine.
py -3.11 -m pip install --no-index --find-links wheelhouse 'fisica[visualization]==3.0.0'
```

Default SDK usage does not require the visualization extra. The SDK has no GUI of its own
since 3.0.0; applications show the rendered images themselves.

## Verification status

The current test suite (`python -m pytest tests -q`), the Dart contract and analysis, the
static-library and UBSan C ABI contracts and the macOS wheel checks pass locally. Earlier
releases also passed Windows x64 cross-build/PE/export/license/source-exclusion checks, and
Windows CPython 3.11.9 under Wine with NumPy 1.26.4 exercised the offline Mock connection,
firmware warning, measurement start/stop, raw frames, native analysis/report and deprecation.
Wine is run with external networking disabled; loopback IPC remains permitted for
Windows asyncio. It is not a Windows-native MSVC or Bluetooth/driver validation.

The physical USB test device reports `2.0.2-RC1` (temporary firmware). Mac USB version
query, received 645-byte frames, 639/643 footer offsets, raw-grid/display shapes and
raw export were verified.
Measurement stop and device-mode release success responses were also confirmed.
This does not establish support for the user-confirmed
planned production version `2.0.1`, or Windows/BLE hardware behavior. Calibration and
firmware were not changed. Protocol documents contain a 644-byte description and a
single-measurement command revision; those remain hardware-validation items.

The Windows-native MSVC CI (core build, tests, wheel audit, installation on Python 3.9, 3.11,
3.13 and 3.14, ARM64 cross-build) ran on GitHub-hosted `windows-2022` runners until
2026-10-17 and is paused until a self-hosted Windows runner exists; nothing runs it now.
Real Windows USB/BLE, live BLE measurement, Windows ARM and Android/iOS remain unverified.
The full checklist and implementation gaps are in [SDK requirements](sdk-requirements.md).
