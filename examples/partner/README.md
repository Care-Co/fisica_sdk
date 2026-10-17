# Fisica Scale partner samples

Install the provided binary SDK packages first; do not compile the private core.
Full installation instructions are in `INSTALL.md` in the sample ZIP, or
[partner-quickstart.md](../../doc/partner-quickstart.md) in the SDK repository.

## The same flow in every sample

Every sample takes the same steps and shows the same labels:

1. The SDK and core version, then the license: `License: full mode`, or
   `License: basic mode (<reason>)` followed by the SDK's explanation of the reason (and
   `Activation code: ...` while a key waits for offline activation).
2. Connect to the chosen Scale (or the synthetic mock), read the battery, measure for 5 seconds, stop.
3. The report, in this order:

   ```
   Weight: 70.5 kg
   Left foot: length 30.2 cm, width 11.8 cm
   Right foot: length 30.2 cm, width 11.7 cm
   Frames: 1
   Battery: 80%
   ```

4. An error is one line (`Error: ...`), and the sample always disconnects.

The mobile apps (SwiftUI, Android Kotlin and Java, Flutter) show the same screen elements in
the same order: version and license, status, `Scan for Scales` (Flutter on a desktop also
`List USB ports`) with the Scales found, `Demo without hardware`, `Connect` (it also reads the
battery), `Measure (5 s)`, the report, `Disconnect`.

## Samples

- Python: `python/main.py` (USB/BLE or `--mock scale-mock.json`; `--key` or `--pair` for full mode).
- C#: `dotnet/Quickstart.csproj` (USB/BLE or `mock scale-mock.json`; `--key` or `--pair`). It builds the
  C# wrapper source ZIP extracted to `sdk/dotnet/` and copies the core from `sdk/core/` (a core ZIP).
- Swift: `swift/ScaleApp.swift`, a complete SwiftUI App source for Xcode. For `Demo without hardware`
  add `scale-mock.json` to the app target.
- Kotlin/Java: `android/` has two minimal Android applications. For `Demo without hardware` copy
  `scale-mock.json` to `app-kotlin/src/main/assets/` (or `app-java/src/main/assets/`).
- Flutter: `flutter/prepare.py` creates standard host projects with permissions;
  `flutter/lib/main.dart` uses SDK BLE/desktop USB adapters and the bundled synthetic demo.
- C/C++: public header/library consumers built by the root `CMakeLists.txt`
  (`fisica-c sample-frame.hex [sdk-key.fse]`). They answer the core with the replies of
  `scale-mock.json` on a simulated clock; the license line has the reason name only (the
  wrappers add the explanation).

The mock data is synthetic and is not evidence of hardware, firmware or clinical
accuracy. Without an SDK key the samples run in basic mode: measurement, weight and
foot size work, frames arrive at most once a second without raw data (the mock sends its
12 frames at once, so `Frames: 1`). Pass an SDK key from the developer portal (activated for
the app or PC) for full mode.

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
