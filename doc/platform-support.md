# 다중 플랫폼·언어 SDK 지원

2026-10-13 구현 기준: SDK/Python 2.3.0, core 1.3.0, ABI 1.3,
Dart/Flutter 0.4.0, Swift/Kotlin/C# 각각 0.1.0. 서버·펌웨어 변경 없음.
코어 소스는 비공개이며 모든 플랫폼이 같은 C++17 코어를 C ABI로 사용한다.

## 지원 구성

| 플랫폼 | 배포물 | 언어 | 연결 범위 |
|---|---|---|---|
| Windows x64 | Python wheel, DLL+C header, NuGet, Flutter DLL | Python, C#, Dart, C/C++ | 기존 Python BLE/USB, 다른 언어는 앱 Transport 주입 |
| Windows ARM64 | 별도 ARM64 wheel/DLL, NuGet, Flutter DLL | Python, C#, Dart, C/C++ | 동일 계약, 실제 ARM64 드라이버/연결 검증 필요 |
| macOS Intel·Apple Silicon | universal2 wheel/dylib, SwiftPM/XCFramework, NuGet, Flutter | Python, Swift, C#, Dart, C/C++ | 기존 Python BLE/USB, Swift CoreBluetooth 어댑터 |
| Android API 23+ | arm64-v8a/armeabi-v7a/x86_64 AAR, Flutter .so | Kotlin/Java, Dart, C/C++ | Kotlin GATT BLE 어댑터, 앱 권한/기기 선택 필요 |
| iOS 13+ | arm64 기기, arm64/x86_64 simulator XCFramework | Swift, Dart, C/C++ | Swift CoreBluetooth 어댑터, 앱 권한/기기 선택 필요 |

.NET은 .NET 8 자체의 OS 지원 조건도 충족해야 한다. Python GUI extra의 PyQt5/
OpenCV 등 플랫폼별 바이너리 의존성은 별도 확인 대상이다. Windows ARM64 측정
SDK wheel과 GUI extra의 설치 가능성을 같은 지원 수준으로 표시하지 않는다.
모바일 USB, Wi-Fi, JS/Web, MCU 포팅은 이번 범위에 포함하지 않았다.

## 언어와 확장 방식

플랫폼과 언어는 서로 다른 축이다. 분석 알고리즘을 언어별로 다시 만들지 않는다.
Python·Dart는 기존 API를 유지한다. Swift/Kotlin/C#의 첫 버전은 공통 Decode,
Analyze, Command, Session API와 해당 언어의 자료형·메모리 소유권을 제공한다.
C 헤더의 나머지 수치 유틸리티, Python 전용 GUI/보고서 편의 API는 각 언어의
앱 요구에 따라 공개 래퍼에서 확장할 수 있다. UI·파일 저장·보고서 표시 등은
새 언어에서 Python API와 동일한 고수준 형태로 포팅한 상태가 아니다.

- Swift: 공개 소스 + 비공개 XCFramework, 명시적 close/deinit와 배열.
- Kotlin/Java: 공개 Kotlin 및 JNI marshaler 소스 + AAR, synchronized/AutoCloseable.
- C#: 공개 C# + NuGet RID 자산, SafeHandle/IDisposable.
- Dart/Flutter: 기존 FFI + FisicaClient의 Future/Stream, FisicaTransport 주입.
- C/C++: 공개 C ABI 헤더, 동적/정적 바이너리.

새 래퍼는 자체 wrapper.json/버전/지원 기간을 가진다. 생성한 Release 상수와
패키지 버전을 일치시킨다. ABI 1.3의 check_wrapper가 지원 범위와 SDK/래퍼의
deprecated 여부를 공통 코어에서 계산하므로 새 바인딩에 판정 알고리즘을 복제하지
않는다. 경고 bit 1은 SDK, bit 2는 래퍼 deprecated다. 지원하지 않는 버전은 코드 12.
새 API deprecated 정책은 wrapper.json의 deprecated_apis를 확장해 운영한다.

## 통신 책임

코어: 바이트 프레이밍·명령/ACK·상태·기간/timeout·재개 판단·측정/분석.
래퍼: 언어별 객체·메모리·비동기 이벤트, OS BLE/USB, monotonic 시간 주입.
앱: 권한 요청·기기 선택·서비스/characteristic UUID 설정·경고/오류 UI.
Swift/Kotlin BLE 어댑터는 알림 준비 후 onReady, 수신 onBytes, 연결 유실 onLost를
제공한다. GATT write 완료를 명령 ACK로 취급하지 않는다. CoreBluetooth/GATT는
앱에서 확인된 Scale UUID를 전달해야 하며 새 기기 UUID를 임의로 추측하지 않는다.

Flutter는 플랫폼 바이너리와 FFI 및 Transport 주입까지 제공한다. 기본 BLE 플러그인
또는 Swift/Kotlin 어댑터를 Dart에 연결하는 method channel은 앱이 구현해야 한다.
Android/iOS 원클릭 연결 SDK나 Python 고수준 API의 전체 모바일 포팅으로 표시하지 않는다.

문서에는 연속 측정이 USB 전용이라고 적혀 있다. 기존 BLE 연속 명령은 호환성을
위해 유지했지만 모바일에서 실제로 허용되는지 검증하지 않았다. 지원 가능한 BLE
측정 방식은 실제 2.0.1 펌웨어로 확정해야 한다. 펌웨어 변경이나 미검증 단일 측정
형식 변경으로 이 차이를 숨기지 않았다. 실제 모바일 연결 완료를 주장하지 않는다.

## 검증과 한계

- Python 143 tests, Dart 13 tests/analysis, Swift·Kotlin/JNI·C#에서 공통 7개 세션
  벡터 및 측정 수치/배열 변환을 검증했다. 새 바인딩의 분석/명령 경로도 C ABI를 호출한다.
- macOS universal2, Windows x64/ARM64 코어 및 ARM64 wheel 빌드.
- Apple 기기/시뮬레이터 정적 코어, SwiftPM macOS 소스 및 iOS Swift 모듈 컴파일.
- Android 3개 ABI core/JNI ELF, Kotlin BLE 소스 컴파일 및 AAR 패키징.
- NuGet에 win-x64/win-arm64/osx-x64/osx-arm64 바이너리 포함, C# 실제 로컬 호출.
- Swift의 XCTest는 이 호스트 CLT에서 모듈을 찾지 못해 실행하지 않았다. 동일
  소스와 코어로 standalone contract 실행을 대신했다. xcodebuild는 설치된 Apple
  라이브러리 symbol 오류로 실행 불가여서 Apple SDK 직접 컴파일/패키징을 사용했다.
- 실제 Android/iOS BLE, iOS 앱 링크/서명·스토어 제출, Windows ARM64 실행·드라이버,
  양산 2.0.1은 검증하지 않았다. CI는 구성만 추가했고 원격 실행/배포하지 않았다.

로컬 아티팩트: `.build/multiplatform-releases/2.3.0/`.
QA 자료: `.build/multiplatform-verification/`, 각 플랫폼 빌드 로그.
고객에게는 core 소스·빌드 도구·debug 자료를 주지 않는다. Kotlin JNI .cpp는
공개 marshaling 래퍼여서 소스 ZIP에 허용했고 core .cpp와 명확히 구분했다.
공개 래퍼와 비공개 코어 모두 Fisica Scale SDK License를 유지한다.

## 빌드

```
python tools/sync_flutter_metadata.py
python tools/sync_binding_metadata.py
python tools/build_platforms.py --target apple
python tools/build_platforms.py --target android --ndk /trusted/android-ndk
python tools/build_platforms.py --target macosx_11_0_universal2
FISICA_BUILD_PLATFORM=win_arm64 CXX=/trusted/arm64-compiler python tools/build_release.py
python tools/package_platforms.py
```

Apple SDK 경로는 FISICA_XCODE_DEVELOPER, Android SDK는 ANDROID_SDK_ROOT,
Kotlin compiler runtime은 FISICA_GRADLE_LIB로 설정한다. 소비 앱은 private 코어를
컴파일하지 않는다. 제공 대장·파트너 식별자는 기존 trusted build 환경변수로 생성한다.

배포/권한 기준: [Apple binary Swift packages](https://developer.apple.com/documentation/xcode/distributing-binary-frameworks-as-swift-packages),
[Android NDK prebuilt libraries](https://developer.android.com/ndk/guides/cmake),
[Android Bluetooth permissions](https://developer.android.com/develop/connectivity/bluetooth/bt-permissions),
[Apple Bluetooth usage description](https://developer.apple.com/documentation/BundleResources/Information-Property-List/NSBluetoothAlwaysUsageDescription),
[.NET ABI interoperability](https://learn.microsoft.com/dotnet/standard/native-interop/abi-support).
