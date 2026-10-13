# 공통 세션 코어 변경·검증 기록

검증일: 2026-10-13. SDK/Python `2.2.0`, 코어 `1.2.0`, C ABI `1.2`,
Flutter 래퍼 `0.3.0`. 서버와 기기 펌웨어는 변경하지 않았다.

## 변경 내용

- `native/src/fisica_session.cpp`의 세션별 상태가 연결, 모드 설정, 시작, 종료,
  모드 해제, 오류, 연결 복구를 관리한다. 프로토콜과 분석 함수는 기존 코어를 사용한다.
- 코어가 바이트와 연결 이벤트, 호스트의 monotonic 시간을 받아 전송·상태·결과·
  측정·재연결 이벤트를 반환한다. IO, 스레드와 OS 라이브러리는 코어에 없다.
- 모드 ACK 이후 시작 명령을 보내고 시작 ACK 이후에만 측정 상태가 된다.
  종료·해제 ACK도 각각 기다린다. 측정 중 disconnect는 종료 후 해제를 수행한다.
- ACK 대기 시간은 코어의 2000ms다. 시작 ACK 시점부터 측정 기간을 계산한다.
  상태 변경 명령의 실패/timeout을 성공으로 처리하거나 자동 재전송하지 않는다.
- SDK 오류 코드와 기기의 16-bit 거절 코드를 구분한다. 비동기 시작/종료/해제
  오류는 기존 `wait()`에서 확인하며 영점/보정 호출은 직접 오류를 반환한다.
- 펌웨어 조회 실패는 기존 경고·사용 지속 정책을 유지한다. 물리 연결 복구와
  원래 기기 검색은 호스트가 수행하고 재측정 여부와 남은 기간은 코어가 판단한다.
- Python `NativeSession`, Dart `FisicaSession`이 같은 C ABI를 사용한다. 기존
  `FisicaSDK` 메서드와 stateless 코어 ABI는 유지한다. 콜백 지연과 무관하게
  수신 프레임을 먼저 기록해 보고서의 프레임 누락을 방지한다.

## 검증 결과

| 항목 | 결과 |
|---|---|
| Python 전체 테스트 | 133 passed |
| Dart 테스트 / 정적 분석 | 10 passed / No issues found |
| 공통 세션 벡터 | Python·Dart·Windows Python에서 동일한 7개 JSON 시나리오 통과 |
| 네이티브 계약 | CMake 정적 라이브러리 링크 및 버퍼 canary 테스트 통과 |
| UBSan | 공개 C ABI 계약 실행 통과 |
| Windows x64 | LLVM-MinGW DLL·휠 빌드, PE x64 및 공개 C 함수 21개 확인 |
| Windows 실행 검증 | Wine / Windows Python에서 설치 휠의 Mock 측정·분석·원본·deprecated 및 코어 기간 종료 통과 |
| macOS 설치 휠 | 별도 가상환경에서 offline Mock 측정·분석·원본·deprecated 및 OpenCV·Qt offscreen 통과 |
| 배포 감사 | macOS/Windows 휠·코어 ZIP·Flutter ZIP 총 6개 소스/디버그 제외 감사 통과 |
| 제공 대장 | 6개 아티팩트 SHA-256 및 패키지 내 파트너/배포 ID 일치 확인 |

부정 테스트는 누락·늦은·무관한 ACK, 기기 오류 1~9 및 16-bit 코드 보존,
시작 도중 취소, 종료/해제 실패, 연결 유실 중 종료의 불확실성, 재연결 시 기간 유지,
서로 다른 세션 핸들, 버퍼 용량 오류, 0ms 시간/기간과 BLE 프로파일,
느린 앱 콜백과 공개 API 오류 전달을 포함한다.

## 연결된 기기

macOS USB `/dev/cu.usbserial-0001`, 조회 펌웨어 `2.0.2-RC1`에서 수행했다.
사용자가 Scale에 하중을 올렸다고 확인한 뒤 최종 검증했다.

| 시나리오 | 결과 |
|---|---|
| 3초 기간 지정 측정 | 시작/종료 ACK, 9프레임, 종료 후 IDLE |
| 같은 연결에서 수동 측정·종료 | 시작/종료 ACK, 8프레임, 종료 후 IDLE |
| 측정 중 disconnect | 9프레임 보존, 종료 ACK 후 모드 해제 ACK, 호스트 연결 정리 |
| 데이터 | 645 bytes, raw 29×22, display 29×29, battery_valid=false |
| 펌웨어 경고 | 미검증 `2.0.2-RC1` 경고 후 사용 지속 |

영점·보정·OTA·Wi-Fi 설정·측정 횟수/간격 설정은 실행하지 않았다. 하중 확인 전 1.2초 및 일부 3초 실행에서는
명령 ACK만 수신되고 측정 패킷은 없었다. 하중 확인 후 최종 3초 실행에서
9/8/9프레임과 모든 ACK를 확인했다. 데이터 미수신의 원인은 확정하지 않았다. 이 결과는 프레임 도착
주기나 양산 펌웨어의 품질 보증이 아니다.

## 남은 검증·프로토콜 한계

- 실제 Windows 10/11 MSVC 빌드 및 USB 드라이버·BLE 실기기 실행은 아직 하지 않았다.
  Wine 검증은 이를 대체하지 않는다. 기존 Windows MSVC CI와 Python 설치 matrix는 유지했다.
- 양산 예정 `2.0.1`, 실물 BLE, 물리 케이블 끊김/복구, Android/iOS는 미검증이다.
  Flutter에서는 공통 FFI와 상태 벡터를 검증했고 모바일 Transport/패키징은 후속 범위다.
- 요청 번호가 없는 기존 프로토콜에서는 같은 header/type의 오래된 ACK를 완전히
  식별할 수 없다. 실제 SET ACK의 DATA는 zero이므로 요청 값의 echo로 매칭할 수도 없다.
  정확히 한 번 실행이나 안전한 재시도를 보장하지 않는다. timeout 후에는 결과가
  불확실하며 FAULT에서 disconnect하더라도 기기 모드 해제를 확인했다는 뜻이 아니다.
- Windows 검증 휠은 교차 컴파일한 바이너리다. Wine 환경 제약 때문에 사용한 Windows
  NumPy 환경과 실제 Windows의 최신 의존성/GUI 조합에 대한 검증 범위를 구분한다.

## 로컬 증거·아티팩트

`.build/session-verification/`은 공개 배포에서 제외되는 로컬 QA 자료다.

- `usb.json`, `usb.log`: 최종 USB 시나리오, 명령/응답·상태 이벤트와 수신 개수.
- `windows-wine.log`, `macos-wheel.log`: 소스 checkout 밖의 설치 휠 실행 기록.
- `artifact-audit.json`, `windows-exports.txt`: 배포 감사·해시 및 공개 export 목록.
- `native-contract`, `native-ubsan`: 네이티브 계약 실행 파일.

아티팩트는 `.build/session-releases/2.2.0/`의 `macos-session-validation`과
`windows-session-validation`에 있다. 둘 다 QA용 `validation` 파트너 ID다.
`Fisica Scale SDK License`를 유지하며 공개 래퍼 소스와 컴파일된 코어만 포함한다.
실제 고객 배포에는 고객/제공 건의 ID로 다시 빌드해야 한다. PyPI/GitHub로 발행하지 않았다.
