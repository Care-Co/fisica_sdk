# Fisica Scale C# / .NET wrapper

Use `CareCo.Fisica.Scale` NuGet (net8.0+) with native assets for win-x64, win-arm64,
osx-x64 and osx-arm64. The source wrapper ZIP also includes the SDK license and
project. The SDK does not include a UI framework or a Windows BLE/Serial driver.
Python supplies the existing desktop transport implementation; C# hosts may inject
their application's byte transport through the session's feed/transmit contract.

`new FisicaCore()` loads the packaged core; an explicit trusted absolute path can be
supplied for controlled integration/testing. One core binary is loaded per process,
with separate SafeHandle-owned sessions for different devices. Core performs offline
SDK/wrapper/core/ABI validation and returns visible deprecation notifications through
onDeprecation (default Trace warning). Dispose sessions; await CLOSE RESULT before
closing actual IO. Use Stopwatch.ElapsedMilliseconds for the monotonic clock.

Decode/Analyze/Command and Session Action/Feed/Tick/Poll share the C ABI. RESULT has
Status and DeviceError; Payload retains the wire bytes for partner analysis. UI,
reports, transport scheduling and warning presentation belong to the host app.
Windows ARM64 execution still needs an ARM64 host; PE/packaging was verified locally.
