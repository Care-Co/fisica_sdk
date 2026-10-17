# Fisica Scale partner samples

Install the provided binary SDK packages first; do not compile the private core.
Full installation instructions are in `INSTALL.md` in the sample ZIP, or
[partner-quickstart.md](../../doc/partner-quickstart.md) in the SDK repository.

- Python: `python/main.py` (USB/BLE or `--mock scale-mock.json`; `--key` or `--pair` for full mode).
- C#: `dotnet/Quickstart.csproj` (USB/BLE or `mock scale-mock.json`; `--key` or `--pair`). It builds the
  C# wrapper source ZIP extracted to `sdk/dotnet/` and copies the core from `sdk/core/` (a core ZIP).
- Swift: `swift/ScaleApp.swift`, a complete SwiftUI App source for Xcode.
- Kotlin/Java: `android/` has two minimal Android applications.
- Flutter: `flutter/prepare.py` creates standard host projects with permissions;
  `flutter/lib/main.dart` uses SDK BLE/desktop USB adapters and a synthetic demo.
- C/C++: public header/library consumers built by the root `CMakeLists.txt`
  (`fisica-c sample-frame.hex [sdk-key.fse]`).

The mock data is synthetic and is not evidence of hardware, firmware or clinical
accuracy. Without an SDK key the samples run in basic mode: measurement, weight and
foot size work, frames arrive at most once a second without raw data. Pass an SDK key
from the developer portal (activated for the app or PC) for full mode.

On a PC the key can come without copying it: `python -m fisica_sdk license pair` (or the
Python and C# samples' `--pair`) shows a code, an owner or admin approves it at the portal's
`/activate` page, and the key is stored for this PC. The Python and C# samples then use it
without `--key`, and renew it online.

```
python -m fisica_sdk license pair
python python/main.py --mock scale-mock.json
dotnet run --project dotnet -- --pair mock scale-mock.json
```

Mobile apps (iOS, Android, Flutter on a phone) keep the key inside the app: it is bound to
the app ID, one seat for every install, so pairing one phone would not license the others.
All measurements, licensing and analysis run in the common binary core.
