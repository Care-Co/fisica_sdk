# 타사 앱 적용 검증

2026-10-14, `sdk-runtime-entitlement`에서 최소 앱·설치 문서와 Flutter transport를
추가한 작업의 결과다(당시 SDK 2.4.0, Flutter core 0.5.0). 아래 표는 그때의 기록이고, 패키지와 재현 절차는
현재 버전(SDK 3.0.0) 기준이다. 서버·펌웨어·코어 측정/프로토콜 소스는 변경하지 않았다.
기본 사용에 계약 토큰이나 인증 서버가 필요하지 않은 기존 동작을 유지했다.

## 실행한 검증

| 항목 | 결과 | 범위 |
|---|---|---|
| Python 전체 테스트 | 179 passed, 12 warnings | 기존 계약·프로토콜·분석 + 공개 샘플 배포 감사 |
| Python 샘플 | 설치 wheel에서 합성 70.5kg·12프레임 | 별도 venv, 저장소 코어 소스를 import 경로에 넣지 않음 |
| C# 샘플 | NuGet 복원, 두 TFM 빌드, Mock 70.5kg·12프레임 | macOS .NET 8. Windows BLE는 컴파일만. 2026-10-17부터 NuGet 대신 C# 소스 ZIP + core ZIP으로 두 TFM 빌드·Mock 실행(기본 모드 70.5kg) 확인 |
| SwiftUI 샘플 | Swift 모듈 빌드와 앱 소스 typecheck 통과 | macOS Swift 6.4. Xcode 프로젝트의 설치/서명/기기 BLE는 별도 |
| Kotlin/Java 샘플 | 제공 AAR classes.jar·Kotlin stdlib·android-35로 두 앱 소스 컴파일 통과 | APK 전체 빌드/기기 실행은 별도 |
| C/C++ 샘플 | 공개 core ZIP으로 CMake 빌드·실행, 두 예제 70.5kg·12프레임 | macOS, 합성 응답. private core를 샘플에서 빌드하지 않음 |
| Flutter 어댑터 | 11 tests passed, analyze 통과 | 전원 꺼짐·UUID 누락·연결 timeout·동시 연결 거절·MTU 실패/512-byte 상한·분할/순서·연결 유실·송신 실패·실제 공통 코어로 측정/분석 |
| Flutter 샘플 | pub get/analyze/Dart bundle 통과 | Flutter 3.47.7/Dart 3.13, native Flutter |
| Flutter macOS 소비 앱 | 공개 ZIP을 별도 폴더에 풀고 prepare → pub get → 앱 빌드 → integration smoke 1 passed | 네이티브 USB 포트 열거 및 합성 데이터 전체 측정·분석. 링크된 FisicaCore.platform() 사용 |
| 배포 감사 | core/래퍼/모바일/새 transport·샘플 ZIP 모두 통과 | 비공개 소스·디버그 파일 제외. 공개 C/C++ 샘플만 명시적 허용 목록으로 구분 |

Flutter 실행 테스트에서 앱 foreground 전환 경고가 있었지만 앱은 실행됐고 두 동작의
assertion과 테스트 종료는 정상 통과했다. 사용자가 눈으로 UI를 확인한 결과는 아니다.
외부 libserialport C 빌드의 deprecated/정수 변환 warning도 있었다. dependency 소스를
수정하거나 private core에 혼합하지 않았다.

## 패키지와 재현

`tools/package_platforms.py`가 `--output/<sdk-version>/`(기본 `.build/multiplatform-releases/3.0.0/`)을 비우고
다음을 만든다. 버전은 `sdk-release.json`과 `wrappers/*/wrapper.json`에서 온다.

- `fisica-flutter-0.6.0-platforms.zip`: 공개 Dart core 래퍼 + OS별 컴파일된 Fisica 코어.
- `fisica-flutter-transports-0.1.0.zip`: Flutter BLE/desktop USB 어댑터와 외부 라이선스 고지.
- `fisica-partner-samples-3.0.0.zip`: 명시적 공개 샘플만 포함. `INSTALL.md`에 설치·실행 절차.
- `fisica-swift-0.3.0.zip`, `fisica-scale-0.3.0.aar`, `fisica-kotlin-0.3.0.zip`, `fisica-dotnet-0.3.0.zip`,
  `fisica-core-2.0.0-<platform>.zip`, 그리고 해시 목록 `platform-manifest.json`.

입력은 `tools/build_platforms.py`로 다섯 대상(`apple`, `android`, `macosx_11_0_universal2`, `win_arm64`,
`win_amd64`)을 모두 빌드한 `.build/platforms/`다. QA 패키지는 모든 명령에 `--validation`을 붙인다.

```sh
python tools/package_platforms.py --validation
python tools/audit_artifacts.py --validation .build/multiplatform-releases/3.0.0/*.zip .build/multiplatform-releases/3.0.0/*.aar
python -m pytest tests -q
cd wrappers/flutter_transport
flutter pub get
FISICA_CORE_TEST_LIBRARY=/absolute/path/libfisica_core.dylib flutter test
flutter analyze
```

`--validation`은 운영 키와 함께 테스트 서명 키도 신뢰하는 QA 빌드를 다루는 옵션이다. 파트너별 키나
온라인 인증을 필수로 만드는 옵션이 아니다. 공식 공개나 버전 태그는 만들지 않았다.

QA 실행 로그는 `.build/partner-*.log`, 소비 앱의 임시 프로젝트는
`.build/partner-consumer/`에 있다. 이 자료는 커밋·고객 아카이브에 들어가지 않는다.

## 남은 확인

- 실제 Scale의 Flutter USB/BLE 입출력과 BLE UUID·MTU·연속 측정 허용 여부.
- 양산 펌웨어 2.0.1 및 임시 2.0.2의 실기기 비교. `wrappers/ble-profile.json`의 `verified=false` 유지.
- 실제 Windows x64/ARM64 앱 실행, USB 드라이버, BLE 플러그인/COM 포트.
- Android APK 전체 빌드·기기 권한/연결, iOS 앱 링크·서명·기기 측정.
- 새 Flutter CI job의 원격 실행. 워크플로는 추가했으며 이 작업에서 push/PR/릴리스는 하지 않았다.

## 통신 의존성

BLE의 BSD 릴리스는 정확한 버전으로 고정했다. USB의 LGPL C 라이브러리는 별도
dependency로 유지하고 BSD/MIT/LGPL/GPL 라이선스 본문을 transport ZIP에 동봉했다.
재배포 조건은 `wrappers/flutter_transport/THIRD_PARTY_NOTICES.md`와 설치 가이드에 있다.
Fisica의 내부 계산/분석 소스는 두 새 패키지에도 들어가지 않는다.
