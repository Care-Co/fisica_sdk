# Fisica Scale partner samples

Install the provided binary SDK packages first; do not compile the private core.
Full installation instructions are in `INSTALL.md` in the sample ZIP, or
[partner-quickstart.md](../../doc/partner-quickstart.md) in the SDK repository.

- Python: `python/main.py` (USB/BLE or `--mock scale-mock.json`).
- C#: `dotnet/Quickstart.csproj` (USB/BLE or `mock scale-mock.json`).
- Swift: `swift/ScaleApp.swift`, a complete SwiftUI App source for Xcode.
- Kotlin/Java: `android/` has two minimal Android applications.
- Flutter: `flutter/prepare.py` creates standard host projects with permissions;
  `flutter/lib/main.dart` uses SDK BLE/desktop USB adapters and a synthetic demo.
- C/C++: public header/library consumers built by the root `CMakeLists.txt`.

The mock data is synthetic and is not evidence of hardware, firmware or clinical
accuracy. No contract token or activation server is needed to run these samples.
All measurements and analysis run in the common binary core.
