# 타사 앱 적용 가이드

SDK/Python 2.4.0, core 1.4.0, Flutter core 0.5.0, Flutter transport 0.1.0,
Swift/Kotlin/C# 0.2.0 기준. 설치는 회사가 제공한 **바이너리 패키지**로 한다.
현재 패키지를 공개 레지스트리에서 내려받는 명령은 제공하지 않는다.

## 공통 설치 원칙

- 정품 Scale의 기본 사용에 계약 토큰·온라인 인증 설정은 필요 없다.
  계약 확인 정보는 선택 사항이고, 서버가 없어도 측정·분석이 동작한다.
- 코어는 컴파일된 DLL/dylib/so/정적 라이브러리로 제공한다. 고객 앱은 코어 소스를
  받거나 컴파일하지 않는다. 공개 래퍼·샘플은 수정할 수 있다.
- Windows x64와 ARM64 파일을 구분한다. macOS는 Intel/Apple Silicon universal2다.
- 설치/빌드 도구와 외부 의존성을 처음 받는 단계에는 인터넷이 필요할 수 있다.
  설치된 앱의 기본 측정·분석에는 인터넷이 필요 없다.
- BLE 기본 UUID와 양산 2.0.1의 측정 경로는 실기기 확인 전이다. `verified=false`를
  유지한다. 샘플 성공이나 Mock 결과를 펌웨어 검증으로 취급하지 않는다.
- 모바일은 BLE 어댑터만 제공한다. USB는 Windows/macOS 가상 Serial 포트다.
  Wi-Fi, Web, Linux/MCU 전용 패키지는 제공하지 않는다.

## 언어별 진입점

| 앱 언어 | 패키지 | 불러오기 | 샘플 |
|---|---|---|---|
| Python 3.9+ | OS/CPU에 맞는 wheel | `from fisica_sdk import FisicaSDK` | `python/main.py` |
| C#/.NET 8 | `CareCo.Fisica.Scale.0.2.0.nupkg` | `using CareCo.Fisica;` | `dotnet/Quickstart.csproj` |
| Swift | 공개 소스 + XCFramework가 포함된 Swift ZIP | `import FisicaScale` | `swift/ScaleApp.swift` |
| Kotlin/Java | `fisica-scale-0.2.0.aar` | `com.carenco.fisica` | `android/app-kotlin`, `android/app-java` |
| Dart/Flutter | core 플랫폼 ZIP + transport ZIP | `import 'package:fisica_flutter/fisica_flutter.dart';` | `flutter/` |
| C/C++ | core ZIP의 공개 헤더 + 라이브러리 | `#include "fisica_core.h"` | `c/main.c`, `cpp/main.cpp` |

샘플 경로는 `fisica-partner-samples-2.4.0.zip`을 푼 위치를 기준으로 한다.
저장소에서는 `examples/partner/`에 있다. 샘플은 UI/연결 방법만 구현하고 측정·분석은
공통 코어를 호출한다. 결과가 없으면 하중과 수신 프레임을 확인한다.

## Python: Windows/macOS

새 가상환경을 만들고 회사에서 받은 OS별 wheel을 설치한다. 아래 파일명의 PLATFORM은
Windows x64의 `win_amd64`, ARM64의 `win_arm64`, macOS의 `macosx_11_0_universal2` 등
실제로 받은 파일명으로 바꾼다. 코어 소스가 있는 저장소를 설치하지 않는다.

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate / macOS: source .venv/bin/activate
python -m pip install /path/to/fisica-2.4.0-py3-none-PLATFORM.whl
python python/main.py --mock scale-mock.json
python python/main.py --list
python python/main.py --usb COM7
# macOS: --usb /dev/cu.usbserial-... / BLE: --ble "--list에 표시된 id"
```

실기기는 `scan_devices()`가 반환한 항목 중 사용자가 고른 ID로 연결한다. USB가 보이지
않으면 CP210x 드라이버와 케이블을 확인한다. macOS BLE는 실행하는 터미널/앱에
Bluetooth 권한을 허용한다. 기본 설치에는 GUI가 필요 없다.

## C#: Windows 우선

받은 nupkg를 `sdk/nuget/`에 넣는다. 해당 폴더를 로컬 패키지 소스로 등록하고 복원한다.
USB는 COM 포트를 사용자가 선택한다. SDK가 VID로 자동 선택하지 않는다.

```sh
dotnet nuget add source /absolute/path/sdk/nuget --name fisica-local
dotnet restore dotnet/Quickstart.csproj
dotnet run --project dotnet/Quickstart.csproj -f net8.0 -- mock scale-mock.json
dotnet run --project dotnet/Quickstart.csproj -f net8.0 -- list-usb
dotnet run --project dotnet/Quickstart.csproj -f net8.0 -- usb COM7
```

Windows BLE를 사용하는 앱은 Windows 전용 대상 프레임워크가 필요하다.

```sh
dotnet run --project dotnet/Quickstart.csproj -f net8.0-windows10.0.19041.0 -- scan-ble
dotnet run --project dotnet/Quickstart.csproj -f net8.0-windows10.0.19041.0 -- ble DECIMAL_BLUETOOTH_ADDRESS
```

주소는 `scan-ble` 출력에서 선택한다. 이 최소 콘솔 샘플은
선택한 주소를 인자로 받는다. macOS에서는 `net8.0`의 USB/Mock을 사용하고 Windows BLE
클래스는 사용하지 않는다. GUI 앱에서는 SDK 이벤트를 UI 스레드로 전달한다.

## Swift: iOS/macOS

1. 받은 `fisica-swift-0.2.0.zip`을 푼다. `Package.swift` 옆에 `CFisica.xcframework`가 있어야 한다.
2. Xcode에서 SwiftUI App 프로젝트를 만들고 **Add Local Package**로 이 폴더를 추가한다.
   앱 대상에 `FisicaScale` 제품을 연결한다. 저장소의 래퍼 소스만 복사하지 않는다.
3. 기본 App/ContentView 파일을 제거하고 `swift/ScaleApp.swift`를 앱 대상에 추가한다.
4. Info.plist에 `NSBluetoothAlwaysUsageDescription`, `NSBluetoothPeripheralUsageDescription`을
   넣고 사용자에게 Scale 연결 목적을 설명한다. sandboxed macOS 앱에는 Bluetooth capability를 추가한다.
5. 지원되는 iOS/macOS 대상으로 실행하고 권한을 허용한 후 목록에서 Scale을 선택한다.

이 샘플은 BLE 연결 → 5초 측정 → 종료 → 결과 표시 → 연결 해제를 수행한다.
기기용 iOS 앱의 서명과 실제 BLE 측정은 별도 확인이 필요하다. Swift USB 어댑터는 없다.

## Kotlin/Java: Android

1. 샘플 `android/`를 Android Studio에서 연다. JDK 17 또는 21, Gradle 8.7,
   Android SDK 35를 준비한다. 프로젝트의 AGP는 8.6.1이다.
2. 받은 AAR를 `android/sdk/fisica-scale-0.2.0.aar`로 넣는다. AAR 안에 공개 클래스와
   세 가지 CPU의 core/JNI 바이너리가 포함돼 있다. Kotlin stdlib 의존성은 샘플에 선언돼 있다.
3. `app-kotlin` 또는 `app-java` 실행 구성을 선택한다. SDK 사용 과정은 같고 호출 문법만 다르다.
4. 앱에서 **Scan BLE**를 누르고 권한을 허용한 뒤 기기 목록에서 Scale을 선택한다.

AAR의 네이티브 라이브러리는 16KB 페이지 기기(Android 15+, Google Play 요구사항)용으로
빌드돼 있다: LOAD 세그먼트 16KB 정렬, RELRO 끝 16KB 경계. 앱은 AGP 8.5.1 이상으로 빌드해야
APK/AAB 안의 비압축 라이브러리도 16KB zip 정렬이 된다(샘플은 8.6.1). 출시 전 APK를
`zipalign -c -P 16 -v 4 app.apk`로 확인한다.

Manifest의 BLE 권한은 AAR에서 합쳐진다. 앱은 Android 12+의 Scan/Connect 권한 또는
이전 Android의 위치 권한을 런타임에 요청해야 한다. 이 샘플은 이 절차를 포함한다.
에뮬레이터에서 UI를 볼 수 있어도 Scale BLE 검증을 대신하지 못한다.

## Flutter: BLE + 데스크톱 USB

Flutter 3.35+/Dart 3.9+를 사용한다. 두 ZIP을 다음과 같이 푼다.
Windows는 Flutter용 Visual Studio C++ 도구/Windows SDK, Apple은 Xcode/CocoaPods가 필요하다.
macOS의 USB 의존성을 처음 빌드할 때 `autoconf`, `automake`, `libtool`도 필요하다
(`brew install autoconf automake libtool`). 비공개 Fisica 코어의 빌드 도구는 필요 없다.

```text
sdk/
  flutter/             # fisica-flutter-0.5.0-platforms.zip
  flutter_transport/   # fisica-flutter-transports-0.1.0.zip
```

코어 ZIP에는 Android so, Apple XCFramework, Windows x64/ARM64 DLL이 포함돼 있다.
transport 패키지는 이 코어 패키지를 `../flutter`에서 찾는다. 고객이 비공개 코어를
컴파일하는 단계는 없다. 샘플 폴더에서 공개 준비 스크립트를 실행한다.

```sh
cd flutter
python prepare.py --sdk /absolute/path/sdk
flutter pub get
flutter run -d windows      # Windows에서 실행
# macOS: flutter run -d macos / 모바일: flutter devices로 고른 기기 ID 사용
```

`prepare.py`는 Flutter 기본 host 프로젝트를 만들고 Bluetooth 설명/권한, macOS Serial
권한 및 최소 OS 설정을 추가한다. macOS 최소 버전은 12.0이다(Xcode 27은 12.0 미만 배포 대상을
빌드 오류로 거절한다). 이를 위해 `macos/Podfile`을 만들어 `platform :osx, '12.0'`으로 하고,
더 낮은 버전을 선언한 플러그인 pod(`libserialport` 등)를 `post_install`에서 12.0으로 올린다. Android 런타임 권한 요청은 샘플 Dart 코드에 있다.
**Demo without hardware**로 합성 데이터의 70.5kg·12프레임을 확인한 뒤 실제 연결을 시도한다.

기존 앱에는 다음 의존성을 추가한다.

```yaml
dependencies:
  fisica_flutter:
    path: /absolute/path/sdk/flutter_transport
```

```dart
import 'package:fisica_flutter/fisica_flutter.dart';

final core = FisicaCore.platform();
final devices = await FisicaBleTransport.scan(); // 권한 허용 후, 사용자가 기기 선택
final transport = FisicaBleTransport(selectedDeviceId);
// USB 대안: FisicaUsbTransport(selectedPortName), connect(link: FisicaLink.usb)
final client = FisicaClient(core, transport);
final subscription = client.events.listen((event) {}, onError: (Object error) {
  // 앱에 오류 표시
});
try {
  await client.connect(link: FisicaLink.ble);
  await client.start();
  await Future<void>.delayed(const Duration(seconds: 5));
  await client.stop();
  final result = client.report();
} finally {
  try { await client.close(); } finally { await subscription.cancel(); }
}
```

`FisicaUsbTransport.ports()`로 Windows/macOS 포트 목록을 얻는다. BLE 어댑터는
UUID 미발견/권한·전원 문제를 오류로 알리고 준비 작업에 timeout을 둔다. MTU 요청에
실패하면 23으로 돌아가며, 송신은 실제 MTU-3 크기로 나눈다. GATT write 완료를 기기
명령 ACK로 간주하지 않는다. 프로토콜과 측정 계산은 transport에 구현하지 않는다.

기존 앱은 Android Manifest/런타임 권한, Apple Info.plist와 capabilities를 샘플과 같이
설정해야 한다. macOS 앱은 Runner의 `MACOSX_DEPLOYMENT_TARGET`과 `macos/Podfile`의
`platform :osx`를 12.0 이상으로 하고, 샘플 Podfile과 같이 `post_install`에서 pod 대상을 올린다. Windows 패키징 시에는 사용하는 BLE 플러그인의 `bluetooth`, `radios`
capabilities 조건도 확인한다. Web와 모바일 USB는 지원하지 않는다.

## 반복 측정·재연결 공통 규칙

Python, Swift, Kotlin/Java, C#, Dart 클라이언트가 같은 규칙을 따른다.

- 연결 종류는 이름 있는 상수로 명시한다: `FisicaLink.BLE/USB`(Kotlin), `.ble/.usb`(Swift),
  `FisicaLink.Ble/Usb`(C#), `FisicaLink.ble/usb`(Dart). SDK가 제공하는 transport는 자기
  연결 종류를 알고 있어, 다른 값을 주면 기기에 아무것도 보내기 전에 인자 오류로 실패한다.
  생략 시 기본값은 transport의 연결 종류이고, 모르면 Kotlin·Dart는 BLE, C#은 USB다.
  Swift는 항상 명시한다.
- 한 Scale로 여러 사용자를 연속 측정할 때 클라이언트를 다시 만들 필요가 없다.
  측정 시작(`start`)이 코어에 수락되면 이전 측정의 프레임을 비운다. `clearFrames()`는
  필요할 때만 쓴다. 결과는 `stop` 후 다음 `start` 전에 `report()`로 읽는다.
- 연결이 끊기면 세션은 RECOVERING이 되고 재연결 이벤트(`onLinkLost`/`LinkLost`/`reconnect`)가
  온다. 같은 클라이언트에서 `connect`를 다시 호출하면 코어가 측정을 이어가며 원래 측정 기간과
  수집한 프레임을 유지한다. Dart는 `connect(transport: ...)`로 새 transport로 바꿀 수 있다.
- 클라이언트는 최근 `maxFrames`개(최대 19,949 = 분석 한 번의 상한)만 보관한다. 넘친 앞쪽
  프레임 수는 `droppedFrames`로 알 수 있다. 장시간 연속 측정에서도 보고서 생성이 실패하지 않고
  메모리가 제한된다. Python `analyze()`는 이 상한을 넘으면 최근 프레임으로 분석하고 경고를 남긴다.

## C/C++: 공개 C ABI

코어 ZIP을 `sdk/core/`에 풀고 샘플의 `sample-frame.hex`를 사용한다.

```sh
cmake -S . -B build -DFISICA_SDK_DIR=/absolute/path/sdk/core
cmake --build build --config Release
./build/fisica-c sample-frame.hex
./build/fisica-cpp sample-frame.hex
# Windows: build/Release/fisica-c.exe sample-frame.hex
```

Windows는 x64/ARM64에 맞는 MSVC developer environment와 core DLL을 사용한다.
샘플 CMake는 공개 export 목록으로 import library를 만들고 DLL을 실행 파일 옆에 복사한다.
이 샘플은 합성 응답으로 세션/측정/분석을 확인한다. 실제 USB/BLE는 앱이 OS 통신을 구현해
TRANSMIT 바이트를 보내고 입력을 `session_feed`에 넘기며 monotonic 시간으로 tick해야 한다.
분석 알고리즘·상태 머신·ACK 정책을 C/C++ 앱에서 다시 만들지 않는다.

## 통신 의존성의 배포 조건

Flutter BLE는 BSD-3-Clause 릴리스 `universal_ble 0.21.1`을 **정확히 고정**한다.
이후 major 버전은 적용 조건이 달라 자동 업그레이드하지 않는다.
USB는 MIT `flutter_libserialport 0.6.0`과 그 하위의 LGPL-3.0-or-later `libserialport`
C 라이브러리를 사용한다. 해당 C 라이브러리는 Fisica 코어와 별개다.
재배포 시 저작권/라이선스 고지 및 LGPL 라이브러리의 소스 제공·교체 가능 조건을 지켜야 한다.
Fisica의 비공개 코어 소스를 이 외부 라이브러리 소스와 혼합하지 않는다.
자세한 버전·출처와 라이선스 본문은 transport 패키지의 `THIRD_PARTY_NOTICES.md`, `licenses/`에 있다.

## 확인 범위

Mock 시나리오는 사용법과 공통 코어를 확인한다. Android/iOS 기기 실행, Windows USB/BLE,
Windows ARM64 드라이버/플러그인 실행, 양산 2.0.1의 BLE 경로는 별도 검증 대상이다.
현재 검증 결과는 `doc/platform-support.md`와 `doc/partner-integration-verification.md`를 참고한다.
