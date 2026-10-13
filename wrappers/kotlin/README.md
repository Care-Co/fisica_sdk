# Fisica Scale Kotlin / Java wrapper

Add the built AAR to the app and include Kotlin stdlib (2.0.21-compatible). Android
API 23+, arm64-v8a/armeabi-v7a/x86_64. It contains public Kotlin classes and compiled
libfisica_core.so + libfisica_jni.so. Runtime uses System.loadLibrary, with no source
compilation or download. Do not strip/rename the `com.carenco.fisica.Bridge` native
methods or FisicaException constructor in R8; preserve them with consumer rules.

`FisicaCore`, `FisicaSession`, `Measurement` and `SessionEvent` marshal the common C
ABI. Java can call the same generated classes with explicit parameters. Commands,
analysis and state policy live in the native core. Close the native handle explicitly.
The public JNI C++ marshaler is in the wrapper source ZIP, alongside the C header;
private algorithm/state sources are absent. Partners may rebuild the JNI bridge
against the vendor binary without owning core sources.

`BleTransport` uses Android GATT, notification setup, queued writes and selected
characteristic UUIDs. The host grants runtime permissions, selects a device and
wires callbacks to the session. Grant BLUETOOTH_SCAN/CONNECT on Android 12+, and
legacy location permissions as required. Choose location/neverForLocation policy
in the app. Tick with SystemClock.elapsedRealtime and drain events; GATT write completion
is not a command ACK. Display PROBE firmware warnings using native policy and decoded
responses, and check RESULT status/deviceError. Keep UI work out of IO callbacks.

Actual Android/BLE measurements are unverified. Documented continuous mode is USB-only;
the existing BLE command profile is retained pending firmware verification. Android USB
and Wi-Fi adapters are not supplied in this release.
