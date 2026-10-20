# 다중 플랫폼·언어 SDK 지원

2026-10-17 구현 기준: SDK/Python 3.0.0, core 2.0.0, C ABI 2.0(라이선스: 기본·전체 모드),
Dart/Flutter core 0.6.0, Flutter transport 0.1.0, Swift/Kotlin/C# 각각 0.3.0. 펌웨어 변경 없음.
아래 "검증과 한계"의 로컬 결과는 2026-10-14 실행 기록이다(이후 확인한 항목은 날짜를 적었다).
코어 소스는 비공개이며 모든 플랫폼이 같은 C++17 코어를 C ABI로 사용한다.
회사가 달라도 플랫폼·아키텍처·코어 버전이 같으면 바이너리가 같다([라이선스와 SDK 키](entitlement.md)).

## 지원 구성

| 플랫폼 | 배포물 | 언어 | 연결 범위 |
|---|---|---|---|
| Windows x64 | Python wheel, DLL+C header(core ZIP), C# 소스 ZIP, Flutter DLL | Python, C#, Dart, C/C++ | Python/C#/Flutter BLE·USB, C/C++는 앱 Transport 구현 |
| Windows ARM64 | 별도 ARM64 wheel/DLL, C# 소스 ZIP, Flutter DLL | Python, C#, Dart, C/C++ | 동일 계약, 실제 ARM64 드라이버/연결 검증 필요 |
| macOS 11+ Intel·Apple Silicon (Flutter 12+) | universal2 wheel/dylib, SwiftPM/XCFramework, C# 소스 ZIP, Flutter | Python, Swift, C#, Dart, C/C++ | 기존 Python BLE/USB, Swift CoreBluetooth 어댑터 |

C#은 당분간 래퍼 소스 ZIP(`fisica-dotnet-<버전>.zip`)과 플랫폼별 core ZIP으로 제공하고, 앱이 래퍼 프로젝트를
참조한다([타사 앱 적용 가이드](partner-quickstart.md)). 코어가 들어 있는 NuGet 패키지는 Windows에서 빌드·검증할
러너가 생긴 뒤 만든다.
| Android API 23+ | arm64-v8a/armeabi-v7a/x86_64 AAR, Flutter .so (16KB 페이지 정렬) | Kotlin/Java, Dart, C/C++ | Kotlin GATT 및 Flutter BLE 어댑터, 앱 권한/기기 선택 필요 |
| iOS 13+ (Flutter 15+) | arm64 기기, arm64/x86_64 simulator XCFramework | Swift, Dart, C/C++ | Swift CoreBluetooth 및 Flutter BLE 어댑터, 앱 권한/기기 선택 필요 |

Flutter 앱은 macOS 12.0, iOS 15.0 이상을 대상으로 한다. Xcode 27은 macOS 12.0, iOS 15.0 미만
배포 대상을 빌드 오류로 거절하고, CocoaPods pod target은 podspec이 선언한 최소 버전을 그대로 쓰기
때문이다. 따라서 Flutter 앱은 iOS 13/14 기기를 더 이상 지원하지 않는다. Swift package
(`.macOS(.v11)`, `.iOS(.v13)`)는 Xcode가 지원 범위로 올려 빌드하므로 Xcode 27에서도 그대로 빌드되지만,
Xcode 27로 빌드하는 iOS 앱 자체는 15.0 이상만 대상으로 할 수 있다.
.NET은 .NET 8 자체의 OS 지원 조건도 충족해야 한다. Python 시각화 extra의
OpenCV·Pillow 등 플랫폼별 바이너리 의존성은 별도 확인 대상이다. Windows ARM64 측정
SDK wheel과 시각화 extra의 설치 가능성을 같은 지원 수준으로 표시하지 않는다.
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
연결 유실 보고를 제공하고 모든 실패를 예외 대신 이벤트로 알린다. macOS·Linux에서는 컴파일만 확인했고
2026-10-17까지 GitHub Windows runner에서 Bluetooth 장치 없이 실패 경로만 실행했다(지금은 Windows CI 중단).
실제 Scale과의 연결은 검증하지 않았다.

Flutter는 플랫폼 바이너리와 FFI, 고수준 클라이언트 및 별도 `fisica_flutter` BLE/desktop USB
어댑터를 제공한다. 앱은 어댑터 대신 OS 권한 요청과 기기 선택을 구현한다. 최소 앱과 준비 스크립트는
[타사 앱 적용 가이드](partner-quickstart.md)에 있다. 통신 플러그인의 별도 라이선스 조건도 함께 안내한다.

BLE 측정 경로: 프로토콜 문서는 연속 측정(0x51)이 USB 전용이고 BLE는 단일 측정(0x50)이라고 한다.
공개된 1.0.1 SDK와 현재 코어는 BLE에서도 연속 측정 명령을 보낸다. 어느 쪽이 맞는지는 실기기로만 정할 수
있어 코어의 BLE 경로는 바꾸지 않았다. 대조 내용, 확인 도구(`tools/ble_probe.py`), 결과별 코어 변경 계획은
[BLE 측정 경로](ble-measurement-path.md)에 있다.

## 검증과 한계

로컬은 2026-10-14 실행 결과(모바일 보강 후 다시 실행)다. "CI" 열은 지금 그 검사를 실행하는 작업이다.
CI는 요청할 때만 수동으로 돌린다. 자체 호스팅 러너로 옮긴 뒤 2026-10-17과 2026-10-18(main a3a9c2b)에 `linux`, `android`, `apple`이 모두 통과했다.
그 전 GitHub 호스팅 러너(`windows-2022`, `macos-15`)에서의 결과는 기록으로만 적는다.

| 항목 | 결과 | 확인한 곳 |
|---|---|---|
| Python 테스트 | 전체 통과(테스트 수는 `python -m pytest tests -q` 출력) | 로컬 macOS, Mock. CI `linux` |
| 네이티브 C ABI 계약 | UBSan 통과. 토큰 1글자 변조 3144건·무작위 입력 모두 거절 | 로컬 macOS. CI `linux`(UBSan) |
| Swift `swift test` | 10 passed (공통 벡터 2, 클라이언트·연결 종류·프레임 규칙·확인 정보 7, 온라인 갱신·저장 1) | 로컬 macOS XCTest. CI `apple` |
| Kotlin/JNI | 공통 벡터, 클라이언트·연결 종류 검증·프레임 규칙·확인 정보·온라인 갱신·저장·MTU 분할 규칙 통과 | 로컬 JVM. `BleTransport`는 android-35로 컴파일만. CI `android` |
| Android 빌드 | 3개 ABI의 core/JNI `.so` 전부 LOAD 정렬 0x4000, RELRO 끝 16KB 경계, AAR 감사 통과. Java·Kotlin 샘플이 새 AAR로 컴파일 | 로컬 NDK r26. CI `android`. APK 빌드·zipalign·16KB 기기 실행은 안 함 |
| C# | 공통 벡터, 클라이언트·연결 종류 검증·프레임 규칙·Serial 오류 처리·확인 정보·온라인 갱신·저장 통과. Windows BLE는 컴파일 + 장치 없는 실패 경로 | 로컬 macOS .NET 8. CI `linux`(Ubuntu .NET 8: net8.0 실행, Windows 대상은 컴파일만). 실패 경로 실행은 2026-10-17까지 GitHub Windows CI |
| Dart | analyze 문제 없음, 22 tests passed (재연결·transport 교체·유실 후 close·연결 종류·프레임 규칙·온라인 갱신 포함) | 로컬 macOS Dart 3.11. CI `linux` |
| Flutter 어댑터 | analyze 문제 없음, 15 tests passed (같은 어댑터 재연결, 연결 중 재연결, 실패 후 재시도, 실제 코어로 유실 후 측정 재개) | 로컬 Flutter 3.41.9. CI `linux`(Flutter 3.47.7) |
| 온라인 갱신 | 네 언어와 Python이 같은 Mock 서버에서 확인 정보를 갱신. 빈 갱신 요청 외에는 아무것도 보내지 않음을 서버 쪽에서 확인 | 로컬, CI. 실제 서버 아님 |
| 휠과 샘플 | 설치한 휠에서 release(운영 키만)는 테스트 서명 키를 거절, `--validation`은 같은 키로 전체 모드. Python·C·C++ 샘플과 C# 샘플(소스 ZIP + core ZIP)이 Mock으로 70.5kg | 로컬 macOS(2026-10-17). CI `linux`(Linux 휠·코어는 검사용이며 배포하지 않음) |
| Windows x64 MSVC | 코어 빌드, 전체 테스트, 휠 감사, Python 3.9/3.11/3.13/3.14 설치 검증 | 2026-10-17까지 GitHub `windows-2022`. 지금은 실행하는 곳 없음(Windows 러너 대기) |
| Windows ARM64 MSVC | 코어·휠 교차 빌드와 감사 | 2026-10-17까지 GitHub Actions. ARM64 실행은 안 함. 지금은 실행하는 곳 없음 |
| Apple | 코어 빌드, 테스트, XCFramework, universal2. 고객용 universal2 휠·core ZIP의 코어에 arm64·x86_64가 모두 있음(감사와 `lipo -archs`), 설치 검증은 arm64로 실행 | 로컬 macOS(2026-10-18, x86_64 조각은 Rosetta로 불러오기만 함). CI `apple`(자체 호스팅 Mac, x86_64는 실행 안 함). 2026-10-17까지 GitHub `macos-15` |

재현: `python tools/verify_bindings.py native swift kotlin dotnet dart`,
`python tools/build_platforms.py --validation --target android`,
`python tools/build_release.py`와
`python tools/verify_wheel.py .build/releases/3.0.0/fisica-3.0.0-py3-none-PLATFORM.whl`
(QA: `python tools/build_release.py --validation`과
`python tools/verify_wheel.py --validation .build/releases-validation/3.0.0/fisica-3.0.0+validation-py3-none-PLATFORM.whl`).

실기기 USB 확인(2026-10-20, Mac, `/dev/cu.usbserial-0001`, 펌웨어 `2.0.2-RC1`, Scale 위에 책 3.6kg):

- Python 샘플(`--usb`): 연결, 펌웨어 확인, 배터리 100%, 5초 측정, 정지, 연결 해제. 기본 모드 프레임 5장(초당 1장).
- Python 전체 모드(테스트 키, 개발 코어): 원본 패킷 645바이트, 내보내기에 전체 프레임 포함. 프레임 간격 345~390ms(평균
  약 367ms, 초당 약 2.7장)로 고르다. SDK는 10ms마다 읽으므로 이 값이 이 펌웨어의 전송 속도다.
- C# 샘플(`usb /dev/cu.usbserial-0001`, macOS .NET 8, PR #27의 `SerialTransport`): 연결, 펌웨어 확인, 배터리, 측정, 정지,
  닫기를 세 번 연속 오류 없이 반복(포트를 다시 열 수 있음).
- 압력 격자가 비어 있으면(책이 압력 센서에 닿지 않을 때, 무게만 3.6kg) 분석 결과는 없다(`None`, 샘플은 "No stable
  result"). 발 압력이 있는 측정의 분석은 사람이 올라서야 확인할 수 있다.

검증하지 않은 것:

- 사람이 올라선 실측 분석(발 길이·너비), USB 연결이 끊겼다가 다시 이어지는 경우, Windows COM 포트.
- BLE 실기기: GATT UUID, MTU, BLE 연속 측정 허용 여부, 양산 2.0.1.
- Android·iOS 기기 실행, 권한 흐름, iOS 앱 링크·서명.
- 실제 Windows의 USB 드라이버·COM 포트·BLE, C# `SerialTransport`·`BleTransport`의 실제 기기 입출력.
- Windows ARM64 실행.
- 운영 개발자 포털(https://developer.fisicahealthcare.com, 운영 중)과의 활성화·갱신·페어링 통신과 운영 서명 키로
  발급한 SDK 키: 위 표의 온라인 검증은 모두 Mock 서버다.

고객에게는 core 소스·빌드 도구·debug 자료를 주지 않는다. Kotlin JNI .cpp는 공개 marshaling 래퍼여서
소스 ZIP에 허용했고 core .cpp와 구분한다.

## 빌드

고객용 빌드(플래그 없음)는 운영 서명 키만 신뢰한다. QA용은 모든 명령에 `--validation`을 붙인다(테스트 서명 키도 신뢰).
테스트 키의 비밀키가 이 저장소에 있으므로 QA 패키지는 고객용과 섞이지 않게 구분한다.

- 기본 출력 위치가 따로 있다: `.build/releases-validation`, `.build/platforms-validation`,
  `.build/multiplatform-releases-validation`.
- 파일 이름이 QA임을 밝힌다. ZIP·AAR은 확장자 앞에 `-validation`(`fisica-swift-0.3.0-validation.zip`,
  `fisica-core-2.0.0-win_amd64-validation.zip`, `fisica-scale-0.3.0-validation.aar`). 휠은 이름 형식이
  정해져 있어 로컬 버전 `+validation`을 쓴다(`fisica-3.0.0+validation-py3-none-macosx_11_0_universal2.whl`).
  pip은 설치하고 `pip list`에 `3.0.0+validation`으로 보이며, PyPI는 로컬 버전을 받지 않는다.
- `release.json`(코어 정책에도 컴파일됨)과 `artifact-manifest.json`·`platform-manifest.json`에
  `"validation": true`가 들어간다. 고객용은 `false`.
- `tools/audit_artifacts.py`는 테스트 키를 신뢰하는데 이름에 표시가 없는 파일을 `--validation`이 있어도
  거절하고, `--validation` 없이는 이름에 `validation`이 들어간 파일을 내용과 관계없이 거절한다.

macOS에서 `tools/build_release.py`는 기본으로 universal2 휠과 core ZIP을 만든다(코어 하나에 arm64·x86_64).
한 아키텍처만 필요하면 `--platform macosx_11_0_arm64` 또는 `--platform macosx_11_0_x86_64`.

```
python tools/sync_flutter_metadata.py
python tools/sync_binding_metadata.py
python tools/build_platforms.py --target apple
python tools/build_platforms.py --target android --ndk /trusted/android-ndk
python tools/build_platforms.py --target macosx_11_0_universal2
CXX=/trusted/arm64-cross-compiler python tools/build_platforms.py --target win_arm64   # Windows MSVC shell: CXX 없이
CXX=/trusted/x64-cross-compiler python tools/build_platforms.py --target win_amd64
python tools/package_platforms.py
```

`build_platforms.py`는 한 번에 한 대상을 `.build/platforms/`(QA: `.build/platforms-validation/`)에 빌드하고,
시작할 때 그 대상의 출력을 비운다.
`package_platforms.py`는 다섯 대상의 출력이 모두 있어야 하며, 쓰기 전에 입력을 모두 확인한다: 빠진 입력은
한꺼번에 알리고, 각 입력의 `release.json`이 지금 체크아웃의 코어 빌드(코어·ABI 버전과 정책)와 다르거나
`--validation` 없이 테스트 키를 신뢰하거나 `--validation`인데 고객용 빌드이면 멈춘다. 그다음
`.build/multiplatform-releases/<sdk-version>/`(QA: `.build/multiplatform-releases-validation/<sdk-version>/`)을 비우고
정해진 패키지만 쓴다: 플랫폼별 core ZIP, Swift ZIP(XCFramework 포함), AAR과 Kotlin 소스 ZIP, C# 소스 ZIP,
Flutter 플랫폼 ZIP과 transport ZIP, 샘플 ZIP, 해시 목록 `platform-manifest.json`. 모든 패키지는
`tools/release_packages.py`의 한 함수가 만들고, 샘플 ZIP을 뺀 모든 패키지에 라이선스와 코어의 `release.json`이
들어간다. 공개 소스는 `git ls-files`가 추적하는 파일만 넣는다(로컬 도구 설정·lock 파일·`.DS_Store`·이전 빌드 결과 제외).

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
컴파일하지 않는다. `tools/audit_artifacts.py`는 패키지마다 필요한 바이너리(Swift ZIP의 XCFramework, AAR과 Flutter
ZIP의 ABI별 `.so`, Flutter의 Windows DLL)와 `release.json`이 없으면 실패하고, `--validation` 없이는 테스트 키를
신뢰하는 아카이브를 거절한다(`release.json`의 키 목록과 바이너리에 컴파일된 키 표를 모두 본다).

바이너리에 회사별 정보가 없으므로 Maven, SwiftPM, NuGet, pub 같은 레지스트리에 같은 패키지를 올리는 방식도
가능하다. 실제 등록·퍼블리시는 하지 않았고, C# NuGet 패키지는 아직 만들지 않는다(위 C# 참고).

배포/권한 기준: [Apple binary Swift packages](https://developer.apple.com/documentation/xcode/distributing-binary-frameworks-as-swift-packages),
[Android NDK prebuilt libraries](https://developer.android.com/ndk/guides/cmake),
[Android Bluetooth permissions](https://developer.android.com/develop/connectivity/bluetooth/bt-permissions),
[Apple Bluetooth usage description](https://developer.apple.com/documentation/BundleResources/Information-Property-List/NSBluetoothAlwaysUsageDescription),
[.NET ABI interoperability](https://learn.microsoft.com/dotnet/standard/native-interop/abi-support).

## CI (자체 호스팅 러너)

두 워크플로(`platform-builds.yml`, `apple.yml`)는 요청할 때만 수동으로 실행하고(workflow_dispatch),
GitHub 호스팅 러너를 쓰지 않는다. 포털(developer-portal)의 빌드·배포도 자체 호스팅 러너만 쓴다.

| 작업 | 러너 라벨 | 러너 | 준비 |
|---|---|---|---|
| `linux`, `android` | `self-hosted, carenco` | mac-host-runner1~3 (Linux X64, colima) | 없음. 매번 새 `ubuntu:24.04` 컨테이너에서 apt로 도구(CMake, make 포함)를 깔고, .NET 8 SDK는 Microsoft 8.0.425 tarball을 체크섬 확인 후 받아 두고, Flutter·Android SDK·NDK 26.3·Gradle 8.14·pip·pub 캐시는 Docker 볼륨 `fisica-sdk-ci-cache`에 둔다 |
| `apple` | `self-hosted, macOS` | Company-Mac (ARM64) | Xcode(iOS SDK 포함). 첫 단계에서 찾고 없으면 실패한다. 다른 직원의 Mac이라 주말과 평일 20시(KST) 이후에만 실행한다 |

- `linux`: 개발 빌드와 pytest, C ABI 계약(UBSan), C# 계약(net8.0), Linux 휠·core ZIP을 release와 `--validation`으로
  빌드해 설치 검증(release는 테스트 서명 키 거절, validation은 전체 모드), 설치한 release 휠로 Python 샘플,
  release core ZIP으로 C/C++ 샘플(CMake), Dart 계약, Flutter transport·샘플. 컨테이너의 빈 `/etc/machine-id`는
  매 실행 임의 값으로 채운다(PC에 묶인 테스트 키가 필요하다).
- `android`: `--validation` AAR·Kotlin ZIP 빌드와 감사, Kotlin 계약. Android command-line tools(SHA-1, Google이
  `repository2-3.xml`에 공개하는 값)와 Gradle 8.14(SHA-256)는 고정한 체크섬으로 받을 때와 캐시에서 쓸 때마다 확인하고
  매번 컨테이너에 새로 푼다.
- `apple`: `--validation` XCFramework·Swift ZIP·universal2 core ZIP 빌드와 감사, Swift 계약. 이어서 고객용
  (운영 키만) universal2 휠과 core ZIP을 `build_release.py`로 빌드해(임시 venv에 `build`만 설치) 감사하고,
  `lipo -archs`로 두 코어에 arm64·x86_64가 모두 있는지 확인한 뒤 `verify_wheel.py`로 설치 검증한다. 러너가
  ARM64라 실행 검증은 arm64 조각만이다(x86_64 실행에는 Rosetta가 필요하며 요구하지 않는다). 고객용 휠은
  올리지 않는다.
- 올리는 아티팩트는 QA 빌드라 이름이 `-validation`으로 끝난다(`fisica-android-validation`,
  `fisica-apple-binaries-validation`). 고객에게 주지 않는다.

Windows CI(x64 휠 빌드와 Python 3.9~3.14 설치 확인, ARM64 교차 빌드)는 자체 호스팅 Windows 러너가 없어
2026-10-17에 뺐다. 러너가 생기면 `git show a44754b:.github/workflows/windows-wheel.yml`과
`platform-builds.yml`의 `windows-arm64` 작업을 되살리고 `runs-on`을 `[self-hosted, Windows, X64, carenco]`로 바꾼다.
그 워크플로는 `build_release.py --validation`으로 빌드하므로 `verify_wheel.py`에 `--validation`을 붙이고,
아티팩트 이름에 `-validation`을 붙인다(`fisica-windows-x64-validation` 등). 휠과 core ZIP의 파일 이름도
`fisica-3.0.0+validation-py3-none-win_amd64.whl`, `fisica-core-2.0.0-win_amd64-validation.zip`이 되므로
업로드 경로의 glob을 그에 맞춘다(`--output`이 없으면 `.build/releases-validation/`). 러너에 필요한 것:

- Windows 10/11 또는 Server 2022, x64. Care-Co 조직에 러너로 등록하고 라벨 `carenco`를 붙인다. 서비스로 실행.
- Visual Studio 2022 Build Tools: "C++를 사용한 데스크톱 개발", MSVC v143 x64/x86 도구, MSVC v143 ARM64 도구,
  Windows 11 SDK (10.0.22621).
- .NET 8 SDK (`dotnet`이 PATH에 있어야 한다)와 Git for Windows.
- Python은 `actions/setup-python`이 러너 도구 캐시에 받는다.
