# 다중 플랫폼·언어 SDK 지원

2026-10-13 구현 기준: SDK/Python 2.4.0, core 1.4.0, ABI 1.4,
Dart/Flutter core 0.5.0, Flutter transport 0.1.0, Swift/Kotlin/C# 각각 0.2.0. 펌웨어 변경 없음.
코어 소스는 비공개이며 모든 플랫폼이 같은 C++17 코어를 C ABI로 사용한다.
회사가 달라도 플랫폼·아키텍처·코어 버전이 같으면 바이너리가 같다([확인 정보](entitlement.md)).

## 지원 구성

| 플랫폼 | 배포물 | 언어 | 연결 범위 |
|---|---|---|---|
| Windows x64 | Python wheel, DLL+C header, NuGet, Flutter DLL | Python, C#, Dart, C/C++ | Python/C#/Flutter BLE·USB, C/C++는 앱 Transport 구현 |
| Windows ARM64 | 별도 ARM64 wheel/DLL, NuGet, Flutter DLL | Python, C#, Dart, C/C++ | 동일 계약, 실제 ARM64 드라이버/연결 검증 필요 |
| macOS 11+ Intel·Apple Silicon (Flutter 12+) | universal2 wheel/dylib, SwiftPM/XCFramework, NuGet, Flutter | Python, Swift, C#, Dart, C/C++ | 기존 Python BLE/USB, Swift CoreBluetooth 어댑터 |
| Android API 23+ | arm64-v8a/armeabi-v7a/x86_64 AAR, Flutter .so (16KB 페이지 정렬) | Kotlin/Java, Dart, C/C++ | Kotlin GATT 및 Flutter BLE 어댑터, 앱 권한/기기 선택 필요 |
| iOS 13+ (Flutter 15+) | arm64 기기, arm64/x86_64 simulator XCFramework | Swift, Dart, C/C++ | Swift CoreBluetooth 및 Flutter BLE 어댑터, 앱 권한/기기 선택 필요 |

Flutter 앱은 macOS 12.0, iOS 15.0 이상을 대상으로 한다. Xcode 27은 macOS 12.0, iOS 15.0 미만
배포 대상을 빌드 오류로 거절하고, CocoaPods pod target은 podspec이 선언한 최소 버전을 그대로 쓰기
때문이다. 따라서 Flutter 앱은 iOS 13/14 기기를 더 이상 지원하지 않는다. Swift package
(`.macOS(.v11)`, `.iOS(.v13)`)는 Xcode가 지원 범위로 올려 빌드하므로 Xcode 27에서도 그대로 빌드되지만,
Xcode 27로 빌드하는 iOS 앱 자체는 15.0 이상만 대상으로 할 수 있다.
.NET은 .NET 8 자체의 OS 지원 조건도 충족해야 한다. Python GUI extra의 PyQt5/
OpenCV 등 플랫폼별 바이너리 의존성은 별도 확인 대상이다. Windows ARM64 측정
SDK wheel과 GUI extra의 설치 가능성을 같은 지원 수준으로 표시하지 않는다.
모바일 USB, Wi-Fi, JS/Web, MCU 포팅은 이번 범위에 포함하지 않았다.

## 언어와 확장 방식

플랫폼과 언어는 서로 다른 축이다. 분석 알고리즘을 언어별로 다시 만들지 않는다.

| 언어 | 고수준 API | Transport | 비고 |
|---|---|---|---|
| Python | `FisicaSDK` (기존 API 유지) | BLE, USB Serial, `ByteTransport`/`MockTransport` 주입 | GUI·보고서 내보내기 포함 |
| Swift | `FisicaClient` | `FisicaTransport`, `BLETransport`(CoreBluetooth), `MockTransport` | completion handler |
| Kotlin/Java | `FisicaClient` | `FisicaTransport`, `BleTransport`(Android GATT), `MockTransport` | 콜백 + Executor |
| C# | `FisicaClient` | `IFisicaTransport`, `SerialTransport`(USB), `BleTransport`(Windows WinRT), `MockTransport` | async Task |
| Dart/Flutter | `FisicaClient` | `fisica_flutter` BLE/desktop USB 또는 `FisicaTransport` 주입 | Future/Stream |
| C/C++ | 공개 C ABI | 호스트 구현 | 동적/정적 바이너리 |

- 고수준 클라이언트는 연결 → 측정 시작 → 프레임 수집 → 종료 → 보고서 → 해제를 제공한다.
  tick, drain, TRANSMIT 전달, 취소, 자원 정리는 클라이언트 안에서 처리한다.
- 명령 순서, ACK 대기, timeout, 재개 판단, 측정·분석 알고리즘은 코어에 있다.
- action/event/state/error와 확인 정보 코드는 공개 C 헤더에서 언어별로 생성한다
  (`tools/sync_binding_metadata.py`). 숫자 코드를 직접 쓰지 않아도 된다.
- 프레임은 원본 645-byte 패킷, 29×22 raw grid, 29×29 display grid, 체중을 담는다.
- 공통 세션 규칙: 측정 시작이 수락되면 이전 측정의 프레임을 비운다. 연결 유실 후 같은 클라이언트에서
  다시 연결하면 코어가 측정을 이어가고 프레임을 유지한다(Dart는 transport 교체도 가능). 클라이언트는 최근
  `maxFrames`개(최대 `FisicaLimit` 분석 상한 19,949)만 보관하고 넘친 수를 `droppedFrames`로 알린다.
- 연결 종류는 헤더에서 생성한 `FisicaLink` 상수로 지정한다. SDK transport는 자기 연결 종류를 알려 주며
  다른 값은 송신 전에 인자 오류로 거절한다. 규칙과 예시는 [타사 앱 적용 가이드](partner-quickstart.md).
- 보고서 분석은 Python과 같이 29×29 display grid와 프레임별 체중을 쓴다. 29×22 raw grid는 분석에
  쓰지 않는다. 다른 언어는 수집한 패킷을 `analyze_packets`로 코어에 넘기며 결과가 Python 경로와 같음을
  테스트로 확인한다. Python `SessionReport`는 표시용으로 소수 2자리 반올림하고 다른 언어는 코어 값을 그대로 준다.
- 배터리: 측정 패킷에는 배터리 값이 없어 프레임은 고정값 99와 `battery_valid=false`다. 실제 잔량은
  `GET_BATTERY_LEVEL` 조회로 읽는다(대기·측정 중 모두 가능, 측정을 끊지 않음). Python은 SDK가 연 USB/BLE
  연결에서 연결 직후와 측정 시작 전에 자동으로 읽고 `get_battery()`로도 읽는다. Swift·Kotlin·C#·Dart는
  `readBattery`/`ReadBatteryAsync`로 읽는다(측정 시작 등 다른 요청과 겹치지 않게 자동 조회는 하지 않음).
  보고서는 마지막 조회값을 `battery`, `battery_valid=true`로 담는다. GET 조회가 거부·무응답이면 그 호출만 실패하고 세션은 유지된다.
- UI·파일 저장·보고서 내보내기는 Python에만 있다. 다른 언어에서는 앱이 구현한다.
- 새 래퍼는 자체 wrapper.json/버전/지원 기간을 가진다. ABI 1.3의 check_wrapper가 지원 범위와 deprecated
  여부를 코어에서 계산한다. 경고 bit 1은 SDK, bit 2는 래퍼 deprecated다. 지원하지 않는 버전은 코드 12.

## 통신 책임

코어: 바이트 프레이밍·명령/ACK·상태·기간/timeout·재개 판단·측정/분석.
래퍼: 언어별 객체·메모리·비동기 이벤트, OS BLE/USB, monotonic 시간 주입.
앱: 권한 요청·기기 선택·경고/오류 UI.

BLE 프로필:

- 기본 프로필은 `wrappers/ble-profile.json`에서 생성한 상수다(`FisicaBleProfile.scale`, `nordicUart`,
  이름 필터 `Fisica Scale`). 기존 Python SDK의 characteristic 선택과 스캔 필터에서 가져온 값이며
  **양산 2.0.1 펌웨어에서 확인하지 않았다**(`verified=false`).
- 사용자 정의 프로필은 `FisicaBleProfile(write, notify)`로 주입한다.
- Swift: UUID를 찾지 못하면 오류를 내고 연결을 끊는다. 준비 timeout(기본 15초)이 있다.
- Kotlin: Bluetooth가 꺼져 있거나 권한이 없으면 예외 대신 오류 콜백을 낸다. 연결 후 MTU 517을
  요청하고 실제 협상값 기준으로 분할 송신한다. 요청이 거절되면 기본 23으로 동작하고 재연결 때 초기화한다.
- GATT write 완료를 명령 ACK로 취급하지 않는다.

USB 시리얼 transport는 115200 8N1, 흐름 제어 없음, DTR·RTS on으로 포트를 연다(Python `SerialUART`,
C# `SerialTransport`, Flutter `FisicaUsbTransport`). 실제 Scale(펌웨어 2.0.2-RC1, CP2102)에서 Python,
C#(macOS), Flutter(macOS)로 확인한 값이다. Windows COM 포트는 실기기에서 확인하지 않았다.

- 9600에서는 응답이 없다.
- Scale 안의 ESP32는 DTR/RTS 자동 리셋 회로에 연결돼 있다. DTR·RTS를 모두 끄면(.NET `SerialPort` 기본값)
  ESP32가 재부팅되고 부트 ROM 로그가 들어온다. RTS만 켜면 리셋 상태로 멈춘다. DTR을 켜야 동작한다.
- 직접 만든 transport도 DTR(권장: DTR·RTS 모두)을 켜야 한다.

C# `SerialTransport`는 이 설정으로 COM 포트를 연다. 포트 선택은 앱이 한다.
Windows에서 VID로 Scale 포트를 찾는 기능은 아직 없다.

C# `BleTransport`는 Windows WinRT BLE를 쓴다. 앱이 `net8.0-windows10.0.19041.0` 이상을 대상으로 할 때만
포함된다. 스캔, 주소·프로필 선택, 연결, UUID 미발견 오류, 준비 timeout, 실제 협상된 MTU 기준 분할 송신,
연결 유실 보고를 제공하고 모든 실패를 예외 대신 이벤트로 알린다. macOS에서는 컴파일만 확인했고
GitHub Windows runner에서는 Bluetooth 장치 없이 실패 경로만 실행했다. 실제 Scale과의 연결은 검증하지 않았다.

Flutter는 플랫폼 바이너리와 FFI, 고수준 클라이언트 및 별도 `fisica_flutter` BLE/desktop USB
어댑터를 제공한다. 앱은 어댑터 대신 OS 권한 요청과 기기 선택을 구현한다. 최소 앱과 준비 스크립트는
[타사 앱 적용 가이드](partner-quickstart.md)에 있다. 통신 플러그인의 별도 라이선스 조건도 함께 안내한다.

BLE 측정 경로: 프로토콜 문서는 연속 측정(0x51)이 USB 전용이고 BLE는 단일 측정(0x50)이라고 한다.
공개된 1.0.1 SDK와 현재 코어는 BLE에서도 연속 측정 명령을 보낸다. 어느 쪽이 맞는지는 실기기로만 정할 수
있어 코어의 BLE 경로는 바꾸지 않았다. 대조 내용, 확인 도구(`tools/ble_probe.py`), 결과별 코어 변경 계획은
[BLE 측정 경로](ble-measurement-path.md)에 있다.

## 검증과 한계

2026-10-14 실행 결과 (모바일 보강 후 다시 실행):

| 항목 | 결과 | 종류 |
|---|---|---|
| Python 테스트 | 182 passed | 로컬 macOS, Mock. Windows CI에서도 실행 |
| 네이티브 C ABI 계약 | UBSan 통과. 토큰 1글자 변조 3144건·무작위 입력 모두 거절 | 로컬 macOS, GitHub macOS CI(UBSan) |
| Swift `swift test` | 10 passed (공통 벡터 2, 클라이언트·연결 종류·프레임 규칙·확인 정보 7, 온라인 갱신·저장 1) | 로컬 macOS XCTest, GitHub macOS CI |
| Kotlin/JNI | 공통 벡터, 클라이언트·연결 종류 검증·프레임 규칙·확인 정보·온라인 갱신·저장·MTU 분할 규칙 통과 | 로컬 JVM. `BleTransport`는 android-35로 컴파일만. CI Android job 추가 |
| Android 빌드 | 3개 ABI의 core/JNI `.so` 전부 LOAD 정렬 0x4000, RELRO 끝 16KB 경계, AAR 감사 통과. Java·Kotlin 샘플이 새 AAR로 컴파일 | 로컬 NDK r26. CI Android job. APK 빌드·zipalign·16KB 기기 실행은 안 함 |
| C# | 공통 벡터, 클라이언트·연결 종류 검증·프레임 규칙·Serial 오류 처리·확인 정보·온라인 갱신·저장 통과. Windows BLE는 컴파일 + 장치 없는 실패 경로 | 로컬 macOS .NET 8, GitHub Windows CI |
| Dart | analyze 문제 없음, 22 tests passed (재연결·transport 교체·유실 후 close·연결 종류·프레임 규칙·온라인 갱신 포함) | 로컬 macOS Dart 3.11. CI flutter job에 추가 |
| Flutter 어댑터 | analyze 문제 없음, 15 tests passed (같은 어댑터 재연결, 연결 중 재연결, 실패 후 재시도, 실제 코어로 유실 후 측정 재개) | 로컬 Flutter 3.41, GitHub macOS CI |
| 온라인 갱신 | 네 언어와 Python이 같은 Mock 서버에서 확인 정보를 갱신. 빈 갱신 요청 외에는 아무것도 보내지 않음을 서버 쪽에서 확인 | 로컬, CI. 실제 서버 아님 |
| Windows x64 MSVC | 코어 빌드, 전체 테스트, 휠 감사, Python 3.9/3.11/3.13/3.14 설치 검증 | GitHub Actions `windows-2022` |
| Windows ARM64 MSVC | 코어·휠 교차 빌드와 감사 | GitHub Actions. ARM64 실행은 안 함 |
| Apple | 코어 빌드, 테스트, XCFramework, universal2 | GitHub Actions `macos-15` |

재현: `python tools/verify_bindings.py native swift kotlin dotnet dart`,
`python tools/build_platforms.py --validation --target android`.

검증하지 않은 것:

- 실제 Scale 연결 전부(이번 작업 중 USB·BLE 기기가 연결돼 있지 않았다). 직전 기록은
  [세션 코어 검증](session-core-verification.md)의 Mac USB `2.0.2-RC1`뿐이다.
- BLE 실기기: GATT UUID, MTU, BLE 연속 측정 허용 여부, 양산 2.0.1.
- Android·iOS 기기 실행, 권한 흐름, iOS 앱 링크·서명.
- 실제 Windows의 USB 드라이버·COM 포트·BLE, C# `SerialTransport`·`BleTransport`의 실제 기기 입출력.
- Windows ARM64 실행.
- 확인 정보 서버(미구현)와의 실제 통신, 운영 서명 키.

고객에게는 core 소스·빌드 도구·debug 자료를 주지 않는다. Kotlin JNI .cpp는 공개 marshaling 래퍼여서
소스 ZIP에 허용했고 core .cpp와 구분한다.

## 빌드

```
python tools/sync_flutter_metadata.py
python tools/sync_binding_metadata.py
python tools/build_platforms.py --validation --target apple
python tools/build_platforms.py --validation --target android --ndk /trusted/android-ndk
python tools/build_platforms.py --validation --target macosx_11_0_universal2
FISICA_BUILD_PLATFORM=win_arm64 CXX=/trusted/arm64-compiler python tools/build_release.py --validation
python tools/package_platforms.py --validation
```

Android 라이브러리는 `-Wl,-z,max-page-size=16384 -Wl,-z,common-page-size=16384`로 링크한다(NDK r27 이하에
필요, r28+는 기본값). 공식 기준 두 가지를 빌드와 `tools/audit_artifacts.py`가 AAR·Flutter ZIP 안의 모든
`.so`에 대해 검사하고 하나라도 어긋나면 실패한다.

- 모든 LOAD 세그먼트 정렬이 16KB(0x4000) 이상.
- GNU_RELRO 세그먼트가 있으면 `(VirtAddr + MemSiz) % 0x4000 == 0`. 로더는 RELRO가 걸친 페이지 전체를
  읽기 전용으로 만들기 때문에, 끝이 16KB 경계가 아니면 16KB 페이지 기기에서 뒤따르는 쓰기 영역이 막혀
  segmentation fault가 난다. LOAD 정렬만 맞고 이것이 어긋날 수 있다(`max-page-size`만 준 경우).

APK/AAB의 zip 정렬(`zipalign -c -P 16 -v 4`)은 앱 빌드 단계라 SDK가 대신 확인하지 못한다.
Android NDK 경로는 `--ndk` 또는 FISICA_ANDROID_NDK로 준다.

Apple SDK 경로는 FISICA_XCODE_DEVELOPER, Android SDK는 ANDROID_SDK_ROOT,
Kotlin compiler runtime은 FISICA_GRADLE_LIB로 설정한다. 소비 앱은 private 코어를
컴파일하지 않는다. 플래그 없이 빌드하면 고객용으로 운영 서명 키만 신뢰한다. `--validation`은 테스트 서명 키도 신뢰하는 QA용 빌드다.
`package_platforms.py`를 플래그 없이 실행하면 `--validation`으로 만든 코어가 섞였을 때 멈춘다.

바이너리에 회사별 정보가 없으므로 Maven, SwiftPM, NuGet, pub 같은 레지스트리에 같은 패키지를 올리는 방식도
가능하다. 실제 등록·퍼블리시는 하지 않았다.

배포/권한 기준: [Apple binary Swift packages](https://developer.apple.com/documentation/xcode/distributing-binary-frameworks-as-swift-packages),
[Android NDK prebuilt libraries](https://developer.android.com/ndk/guides/cmake),
[Android Bluetooth permissions](https://developer.android.com/develop/connectivity/bluetooth/bt-permissions),
[Apple Bluetooth usage description](https://developer.apple.com/documentation/BundleResources/Information-Property-List/NSBluetoothAlwaysUsageDescription),
[.NET ABI interoperability](https://learn.microsoft.com/dotnet/standard/native-interop/abi-support).
