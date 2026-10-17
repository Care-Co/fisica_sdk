# 라이선스와 SDK 키(entitlement)

2026-10-17 정책 기준. 구현은 SDK/Python 3.0.0, core 2.0.0(C ABI 2.0), Swift·Kotlin·C# 0.3.0,
Dart 0.6.0이며 서버는 개발자 포털 https://developer.fisicahealthcare.com 이다(별도 저장소 Care-Co/developer-portal).
서버 계약은 [entitlement-api.yaml](entitlement-api.yaml), 약관은 [LICENSE](../LICENSE) 2~4·8~10조,
설계와 결정 내역은 [licensing-plan.md](licensing-plan.md), 바뀐 정책은 [policy-history.md](policy-history.md)에 있다.

## 원칙

- 플랫폼·아키텍처·코어 버전이 같으면 모든 회사가 **동일한 바이너리**를 쓴다.
  회사·계약·배포 ID는 빌드에 들어가지 않는다. 코어에 들어가는 것은 서버 공개키뿐이다.
- SDK는 **기본 모드**와 **전체 모드**로 동작한다. 판정과 기능 제한은 비공개 코어가 한다.
  래퍼는 저장·네트워크·시계만 맡고, 래퍼를 고쳐도 모드는 바뀌지 않는다.
- **SDK 키 = 좌석.** 키는 처음 쓸 때 이 앱의 ID 또는 이 PC의 해시(대상)에 묶이고, 그 대상에서만 전체 모드가 된다.
  대상은 코어가 직접 읽는다.
- 키는 장비 진위, 코드 출처, 계약 준수를 증명하지 않는다. 장비 인증은 별도 과제(W7)다.
- **SDK 사용을 추적하지 않는다.** 아래 "사용 추적 없음" 절 참조.

## 기본 모드와 전체 모드

| 기능 | 기본 모드 | 전체 모드 |
|---|---|---|
| 검색·연결, 측정, 배터리, 기기 설정·영점·보정 | O | O |
| 체중, 발 길이·폭(세션이 받은 모든 패킷으로 분석) | O | O |
| 측정 프레임(표시용 29×29 격자) | 초당 1장 | 매 프레임 |
| 원본 패킷·원본 격자(29×22)·가공 격자, `decode_packet` 등 격자 함수 | — (`FEATURE_NOT_LICENSED`) | O |
| 보고서 내보내기 | 요약(체중·발 길이·폭)만 | 프레임 포함 |
| 고급 분석(예정) | — | O |

- 기본 모드는 정품 장비 구매에 포함된 사용권이다. 상업 사용도 등록·키 없이 쓸 수 있고 기간 제한이 없다.
- 기기 없는 Mock 데이터도 같은 규칙이다(세션을 거치므로 자동으로 같다).
- 키의 `features`가 비어 있으면 원본 데이터·내보내기·고급 분석이 모두 열린다. 값이 있으면 그 기능만 연다.
  매 프레임(`full_rate`)은 전체 모드면 항상 열린다.

## 판정 순서 (코어)

| 순서 | 조건 | 결과(이유) |
|---|---|---|
| 1 | 키 없음 | 기본(`no_key`) |
| 2 | 좌석 키가 아닌 이전 계약 확인 토큰 | 기본(`legacy_token`) |
| 3 | 아직 활성화되지 않은 키 | 기본(`not_activated`) |
| 4 | 코어가 이 앱·PC를 읽지 못함 | 기본(`no_target`) |
| 5 | 다른 앱·PC에 활성화된 키(상태 파일을 복사한 경우 등) | 기본(`other_target`) |
| 6 | 서버가 서명한 해제·폐기(`revoked_at`) | 기본(`revoked`) |
| 7 | 시계가 지금까지 본 가장 늦은 시각이나 키의 `verified_at`보다 1시간 넘게 이름 | 기본(`clock_rollback`) |
| 8 | 키 기한(`key_until`)이 지남 | 기본(`key_expired`) |
| 9 | 계약 시작 전 | 기본(`not_started`) |
| 10 | 전체 모드 기간(`full_until`)이 없거나 지남 | 기본(`full_ended`) |
| — | 위에 해당 없음 | 전체 모드. 기한은 `key_until`과 `full_until` 중 이른 쪽 |

`full_until`은 서버가 정한다: 체험은 승인일+체험 기간, 연장은 승인 기간 끝, 상업 계약은 종료일+유예(30일).

경고 비트: `renew_due`(갱신할 때가 됨: `refresh_by` 지남 또는 키 기한 7일·기간의 1/3 전),
`grace`(계약 종료 후 유예 중), `full_ending`(전체 모드가 7일 안에 끝남), `import_rejected`, `test_key`.

- 다른 대상에 활성화된 키, 서명 실패, 형식 오류, 모르는 서명 키, 같은 키의 더 오래된 파일은 가져와도
  지금 가진 키를 바꾸지 않는다(`rejected_target`, `rejected_*`, `ignored_older`).
- 디버그 빌드(`.debug`, `.dev`)는 같은 앱이다. 앱 ID 비교에서 접미사를 뗀다(포털도 같다).
- 통신 실패는 만료·해제·폐기가 아니다. 키 기한 안에서는 오프라인으로 계속 전체 모드다.
- 상태는 SDK 인스턴스(컨텍스트 핸들)마다 따로 관리한다.

## 대상(앱 ID·PC 해시)

| 플랫폼 | 대상 | 코어가 읽는 곳 |
|---|---|---|
| iOS 앱, macOS `.app` (Swift, Flutter, C#·Python으로 만든 앱 번들) | 번들 ID | `CFBundleGetMainBundle` |
| Android 앱 (Kotlin, Flutter) | 패키지명 | `/proc/self/cmdline` |
| Windows (C#, Flutter, Python) | PC 해시 | 레지스트리 `MachineGuid` |
| macOS의 스크립트·명령줄 프로그램(Python, `dotnet` 실행, XCTest 등) | PC 해시 | `gethostuuid()`(IOPlatformUUID와 같은 값) |
| Linux(향후) | PC 해시 | `/etc/machine-id` |

- PC 해시는 `SHA-256("fisica-pc-v1:" + 플랫폼 고유값)`의 16진수 64자다. 원래 값은 보내지 않는다.
  모든 계정에서 같은 값이라 "PC당 체험 1회"를 지킬 수 있다.
- macOS에서 Python 인터프리터(`org.python.python`)와 Apple 도구(`com.apple.*`)는 앱으로 보지 않는다.
- 모바일 앱의 좌석은 앱 하나다. 같은 앱을 설치한 최종 사용자 기기 수는 세지 않는다.

## 키와 서명

- 형식: `FSE1.<base64url(payload)>.<base64url(Ed25519 서명)>`. 키 파일(`.fse`)의 내용도 같은 문자열이다.
- payload 필드와 규칙은 [entitlement-api.yaml](entitlement-api.yaml)의 `Token` 참조.
- 신뢰하는 공개키는 `native/entitlement-keys.json`에 `kid`별로 둔다.
  - `prod-2026-10-15`(production): 개발자 포털의 운영 서명 키. 비밀키는 포털 서버에만 있다.
  - `test-2026-10`(test): 비밀키가 저장소(`tests/fixtures/entitlement-test-key.json`)에 있다.
- 고객용 빌드(`--validation` 없음)는 production 키만 넣는다. `--validation` QA 빌드와 개발용
  `tools/native_build.py`는 두 키를 모두 넣는다(`FISICA_ENTITLEMENT_KEYS=release`면 production만).
- 테스트 키로 서명된 키에는 `key_environment=test`와 `test_key` 경고가 붙는다.
- 운영 키를 교체할 때는 새 `kid`를 이 목록에 추가해 SDK를 먼저 배포한 뒤 포털의 서명 키를 바꾼다.

서명 검증에는 외부 감사를 받은 Monocypher 4.0.2를 비공개 코어에 포함해 쓴다
(CC0-1.0 / BSD-2-Clause, `native/src/third_party/monocypher/`). SHA-256은 코어에 직접 구현했다.

## 활성화·갱신·해제

| 작업 | 온라인 | 오프라인 |
|---|---|---|
| 활성화 | SDK가 `POST /sdk/v1/entitlement/activate`에 키와 대상(코어가 만든 본문)을 보내 묶인 키를 받는다 | SDK가 보여주는 활성화 코드(`FAC1.…`)를 포털의 그 키에 입력해 받은 키 파일을 가져온다 |
| 갱신 | `renew_due`이거나 키 기한·기간이 지났으면 `POST /refresh`(빈 본문)로 같은 키를 다시 받는다 | 포털에서 같은 키의 갱신 파일을 받아 가져온다 |
| 해제 | 포털에서 해제를 요청하면 다음 갱신 응답이 `revoked_at`을 담고, SDK는 저장한 키를 지운다 | SDK의 해제 기능이 키를 지우고 해제 코드(`FRC1.…`)를 보여준다. 포털에 입력하면 좌석이 빈다 |

- 활성화 코드·해제 코드의 형식은 [licensing-plan.md](licensing-plan.md) 5절과 같고, 포털의 해석기로
  교차 검증한다(포털 `tests/test_sdk_interop.py`).
- 코어의 상태 파일에는 서명된 키와 지금까지 본 가장 늦은 시각만 들어간다(`FSS1`, `C`·`T` 줄).
  같은 회사·계약의 원래 키 파일을 다시 가져와도 저장해 둔 활성화된 키가 이긴다.
- 같은 키 파일을 다시 가져와도 시계 되돌림 기록은 지워지지 않는다. 서버가 새로 서명한 키만 기록을 새로 맞춘다.

## 키 없이 연결(기기 연결)

키를 앱에 넣지 않고, 기기가 보여주는 코드를 포털에서 승인해 키를 받는다(RFC 8628 장치 인증 방식,
[licensing-plan.md](licensing-plan.md) 4.7).

1. SDK가 `POST /sdk/v1/pairing`에 코어의 활성화 요청 본문(대상만)을 보내고 연결 코드(`XXXX-XXXX`)와 승인 주소를 받는다.
2. 사람이 포털 `/activate`에 로그인해 코드를 넣는다. 화면에는 앱 ID 또는 PC 해시 앞 8자리와 요청 시각이 나온다.
3. 조직 소유자·관리자가 체험·계약과 키(새 키 또는 미활성 키)를 골라 승인한다. 좌석·이동 상한·체험 규칙은 활성화와 같다.
   거부할 수도 있다. 코드는 10분, 한 번만 쓸 수 있다.
4. SDK가 `POST /sdk/v1/pairing/token`을 5초마다 묻다가 이 대상에 활성화된 키를 받아 저장한다.
5. 그 뒤로는 키 없이 시작해도 저장 위치에서 이 앱·PC의 키를 찾아 쓴다. 상태 파일 이름이 코어가 이 대상으로 다시
   만든 이름과 같은 파일만 쓰며, 여러 개면 전체 모드인 키, 그다음 가장 최근에 확인된 키를 쓴다.

- 저장된 키가 있으면 원래 키 파일을 다시 가져와도 바뀌지 않는다(`ignored_older`). 저장된 키가 더 새롭다.
- 다른 사람이 보내 준 코드를 승인하면 그 사람의 기기가 이 조직의 키를 받는다. 포털 화면이 이를 경고한다.
- PC의 Python 스크립트·C# 프로그램·Flutter 데스크톱 앱은 대상이 같은 PC 해시라 한 번 연결하면 함께 쓴다.
  앱 ID가 있는 앱(iOS·Android·macOS `.app`)은 앱 안에서 `pair`를 불러 연결한다.

## 세션과 함수

- 세션은 라이선스 판정을 복사해 쓴다(`fisica_core_session_license`). 시작 전, 키가 바뀔 때, 30초마다
  래퍼가 다시 복사한다. 전체 모드는 키·기간이 끝나는 시각까지 세션의 단조 시계로 센다.
- MEASUREMENT 이벤트: `[체중, 29×29 표시 격자]` double 842개. 원본 데이터가 열리면 원본 격자 double 638개와
  645바이트 패킷이 뒤에 붙는다. 기본 모드는 1000ms에 1개까지만 내보낸다.
- `fisica_core_session_analyze`: 시작 이후 받은 모든 패킷으로 체중·발 길이·폭. 모든 모드에서 동작한다.
- `decode_packet`·`preprocess`·`display_grid`는 라이선스 컨텍스트를 받으며 원본 데이터가 열린 경우에만 동작한다.
  `analyze`·`analyze_packets`·`dimensions`·`cop`은 라이선스가 필요 없다.
- 한계: 블루투스·USB 바이트는 앱을 거쳐 코어로 들어오므로 프로토콜을 직접 해석하는 것은 막지 못한다
  ([licensing-plan.md](licensing-plan.md) 7절).

## 사용 추적 없음

- SDK는 Care&Co의 사용 현황 추적을 위한 활동 기록을 수집·보관·보고하지 않는다.
- 측정 데이터, 측정 횟수, API 호출 횟수, 사용 여부·사용 시각, 기간 종료 후 사용 여부를 기록하거나 보내지 않는다.
- 서버로 가는 것: 활성화 때 키와 대상(앱 ID 또는 PC 해시), 갱신 때 키, 기기 연결 때 대상과 연결 확인용 장치 코드뿐이다.
- 서버가 저장하는 것: 키마다 묶인 대상과 활성화·갱신·해제 시각. 좌석·체험 관리용이며 사용 이력이 아니다.
  오프라인 사용이나 갱신 없이 쓰는 사용은 서버에 보이지 않는다.
- 기기에 저장하는 것: 서명된 키와 지금까지 본 가장 늦은 시각. 오프라인 판정과 시계 되돌림 감지용이다.
- 파트너 앱이 자체 서비스를 위해 측정 데이터와 보고서를 저장하는 것은 이와 별개이며 제한하지 않는다.

## Python 사용

```python
sdk = FisicaSDK()
sdk.configure_entitlement(file='sdk-key.fse', endpoint='https://developer.fisicahealthcare.com')
print(sdk.entitlement_status())   # mode, reason, features, key, track, 날짜, warnings, activation_code
sdk.activation_code()             # 인터넷이 없는 PC: 포털에 입력할 활성화 코드
sdk.release_key()                 # 이 앱·PC의 키 해제. 포털에 입력할 해제 코드를 돌려준다
sdk.pair_device()                 # 키 없이 연결: 코드를 보여주고 포털 승인을 기다린다
```

터미널에서 이 PC를 연결하고 관리하는 명령:

```sh
python -m fisica_sdk license pair      # 코드 표시 + 승인 페이지 열기, 승인되면 키 저장
python -m fisica_sdk license status    # 이 PC에 저장된 키와 모드 (--json)
python -m fisica_sdk license import sdk-key.fse   # 키·키 파일 가져오기와 온라인 활성화 (--offline)
python -m fisica_sdk license code      # 미활성 키의 활성화 코드(오프라인 활성화)
python -m fisica_sdk license release   # 키 해제와 해제 코드
```

- `configure_entitlement()`를 부르지 않거나 키 없이 부르면 이 앱·PC에 저장된 키(연결했거나 가져온 키)를 쓴다.
  저장된 키를 찾아 쓸 때와 `pair_device()`·`license` 명령은 서버 주소 기본값 https://developer.fisicahealthcare.com 을
  쓴다(`--endpoint`로 바꿀 수 있다).

- `endpoint`를 주지 않으면 네트워크를 전혀 쓰지 않는다. `online=False`로도 끌 수 있다.
- `endpoint`가 있으면 새 키를 가져오는 즉시 백그라운드에서 활성화하고, 기능 사용 시점에 갱신할 때가 됐는지 본다.
  통신 실패는 `last_sync_error`에만 남고 라이선스를 바꾸지 않는다.
- 키는 기본으로 사용자 앱 데이터 폴더에 회사+계약별 파일로 저장한다. `storage=경로` 또는 `None` 지정 가능.
- 기본 모드가 되는 이유와 경고는 `FisicaEntitlementWarning`으로 한 번씩 알린다. 원본 데이터를 요청하면
  `FisicaLicenseError`(코드 13)가 난다. 그 밖의 기술 오류는 기존 `FisicaError` 계열이다.

## Swift·Kotlin·C#·Dart

`FisicaEntitlement`가 Python과 같은 동작을 제공한다: 키·파일 가져오기, 상태, 자동 활성화·갱신, 활성화 코드,
해제, 저장·복원, 키 없이 연결. 저장 위치가 있으면 만들 때 이 앱·PC의 저장된 키를 바로 읽는다.
각 고수준 클라이언트가 세션에 라이선스를 적용하고 기본 모드의 체중·발 크기는 코어 분석을 쓴다.

| 언어 | 온라인 | 키 없이 연결 | 저장 위치 인자 | 기본 저장 위치 | 라이선스 오류 |
|---|---|---|---|---|---|
| Swift | `endpoint: URL`, `syncNow()`/`sync()` | `pair(onCode:)` (async), `pairNow` | `storage: URL?` | Application Support/Fisica/sdk | `FisicaLicenseError` |
| Kotlin | `endpoint: String`, `syncNow()`/`sync()` | `pair(onCode)` (백그라운드 스레드) | `storage: File?` | 없음. 앱이 `filesDir` 등을 전달 | `FisicaLicenseException` |
| C# | `endpoint`, `SyncAsync()`/`Sync()` | `PairAsync(onCode)` | `storage` | LocalApplicationData/Fisica/sdk | `FisicaLicenseException` |
| Dart | `endpoint`, `sync()` | `pair(onCode)` | `storageDirectory` | 없음. 앱이 디렉터리를 전달 | `FisicaLicenseError` |

- 모든 언어에서 `endpoint`가 없으면 네트워크를 쓰지 않는다. 연결에 쓸 포털 주소는 상수로 제공한다
  (`DefaultEndpoint`, `defaultEndpoint`); 앱이 넘겨야 쓰인다.
- 연결 결과는 승인·거부·만료·취소 네 가지다. 시작 요청이 실패하거나 endpoint·대상이 없으면 예외가 난다.
- Android 앱은 온라인 활성화·갱신·연결에 INTERNET 권한이 필요하다(AAR은 선언하지 않는다).
- 네 언어 모두 같은 Mock 서버를 상대로 이 PC 활성화, 갱신, 통신 실패, 저장·복원, 키 없이 연결을 검증한다
  (`python tools/verify_bindings.py native swift kotlin dotnet dart`). Mock 서버는 이 PC의 활성화·연결 본문,
  연결 확인용 장치 코드, 빈 갱신 요청 외의 요청이 오면 실패한다.

## 개발용 도구

```sh
python tools/entitlement_dev.py serve --port 8765           # Mock 서버
python tools/entitlement_dev.py issue --company acme --contract ctr-1 --out legacy.fse   # 이전 형식 토큰
python -m pytest tests/test_entitlement.py -q
```

`tools/entitlement_dev.py`의 `issue_token(..., key=, bind_type=, bind=, key_until=, full_until=, features=, track=)`과
`host_pc_vectors(now)`로 이 PC에 활성화된 테스트 키를 만든다. Mock 서버와 테스트 서명기는 테스트 키만 쓴다.
서명기는 RFC 8032 참조 알고리즘의 순수 Python 구현으로 테스트·개발 전용이다. 운영 서버는 검증된 라이브러리로 서명한다.

## 서버(개발자 포털)

1. 운영 Ed25519 키 쌍. 공개키와 `kid`를 SDK에 넣는다. 키 교체는 `kid` 추가로 처리한다.
2. 체험·연장·계약 신청과 관리자 승인, 계약별 좌석·이동 상한·최대 키 기한·기능·유예.
3. 키 만들기(유효기간 선택)·상태(미활성·사용 중·해제 대기·만료됨)·삭제, 활성화 파일·갱신 파일·해제 코드.
4. `/sdk/v1/entitlement/activate`와 `/refresh`, 키 없는 연결 `/sdk/v1/pairing`·`/pairing/token`과 승인 페이지
   `/activate`. 규칙은 API 명세의 설명 그대로다. 사용 보고 API는 없다.
5. 활성화·갱신 기록을 SDK 사용 이력이나 계약 준수 판단 자료로 쓰지 않는다.

## 구현 상태 (2026-10-17)

- 코어 2.0.0(C ABI 2.0): 모드 판정, 대상 읽기, 기능 제한, 활성화 요청·코드, 해제 코드, 시계 되돌림 감지.
  공개 C ABI는 UBSan 아래 계약 테스트(`tests/native_entitlement.cpp`)로 검증한다.
- 래퍼 5종과 파트너 샘플이 새 ABI로 옮겨졌다. 실기기(BLE, Android·iOS 기기, Windows 실행)에서는 아직 확인하지 않았다.
- 2026-10-13 정책의 사용 기록 기능은 2026-10-14에 제거했고 다시 넣지 않았다. 이전 개발 빌드가 쓴 사용 기록 줄은
  복원 시 무시한다.
- 2026-10-14의 "어떤 상태도 기능을 차단하지 않는다" 정책은 2026-10-17에 기본·전체 모드로 대체됐다
  ([policy-history.md](policy-history.md)).
