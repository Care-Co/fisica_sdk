# 런타임 확인 정보(entitlement)

2026-10-14 정책 기준. 구현은 SDK/Python 2.4.0, core 1.4.0, C ABI 1.4이며 서버는 개발자 포털
https://developer.fisicahealthcare.com 이다(별도 저장소 Care-Co/developer-portal).
서버 계약은 [entitlement-api.yaml](entitlement-api.yaml), 약관은 [LICENSE](../LICENSE) 8·9조,
약관 검토 자료는 [license-review.md](license-review.md), 바뀐 정책은 [policy-history.md](policy-history.md)에 있다.

## 원칙

- 플랫폼·아키텍처·코어 버전이 같으면 모든 회사가 **동일한 바이너리**를 쓴다.
  회사·계약·배포 ID는 빌드에 들어가지 않는다. 코어에 들어가는 것은 서버 공개키뿐이다.
- 회사·계약 정보는 서버가 서명한 토큰(온라인 응답 또는 오프라인 파일)으로 런타임에 주입한다.
- **어떤 상태도 로컬 SDK 기능을 차단하지 않는다.** 결과는 상태값과 경고뿐이며 오류 코드로
  반환하지 않는다. 측정·통신·입력·버전 호환성 오류는 기존대로 별도의 오류다.
- 토큰은 장비 진위, 코드 출처, 계약 준수를 증명하지 않는다. 장비 인증·잠금은 없다.
- **SDK 사용을 추적하지 않는다.** 아래 "사용 추적 없음" 절 참조.

## 서로 독립인 세 가지 상태

| 상태 | 값 | 판정 근거 |
|---|---|---|
| ① 기본 SDK 사용권 | 항상 `granted` | 정품 장비 구매에 포함. 날짜 기반 만료 없음 |
| ② 확인 정보의 최신성 | `none` / `fresh` / `refresh_due` | 서버가 서명한 `verified_at`·`refresh_by`. 최대 30일 |
| ③ 계약 | `unverified` / `active` / `not_started` / `ended` / `revoked` | 서버가 서명한 계약 시작·종료·폐기 시각 |

- 30일 초과는 `refresh_due`(확인 정보 갱신 필요)이며 사용권 만료가 아니다.
- 확인 기한과 계약 종료일은 서로 다른 필드다. 하나의 `expires_at`으로 합치지 않는다.
- `revoked`는 서버가 서명한 폐기 정보를 받았을 때만 표시한다. 통신 장애, 오래된 정보,
  서명 실패는 폐기가 아니다.
- 서명 실패·형식 오류·모르는 키·정보 없음은 `unverified`(계약 미확인)다. 입력 문자열이나
  다른 인스턴스의 정보로 회사·계약을 추정하지 않는다.
- 거절되거나 더 오래된 토큰을 가져와도 이미 확인한 정보와 확인 시각은 그대로 남는다.
- 파일을 다시 가져오거나 로컬에서 서명을 다시 검증해도 30일 기간은 늘어나지 않는다.
  기간은 서명된 시각에서만 계산하고, 서버가 30일보다 길게 써도 코어가 30일로 자른다.
- 상태는 SDK 인스턴스(컨텍스트 핸들)마다 따로 관리한다.

경고 비트: `unverified`, `refresh_due`, `contract_ended`, `contract_revoked`,
`contract_not_started`, `import_rejected`, `test_key`.

## 책임 분담

| 계층 | 담당 |
|---|---|
| 코어 (C ABI 1.4, I/O 없음) | 토큰 파싱, Ed25519 서명 검증, 세 상태 판정, 저장용 상태(토큰) 직렬화·복원 |
| 래퍼 | 파일·네트워크·시계·난수 제공, 상태를 언어 자료형과 경고 이벤트로 전달 |
| 서버 | 회사 등록, 약관 수락, 계약 관리, 최초 토큰 발급, 갱신 토큰 서명, 폐기 정보 서명. 서명 비밀키는 서버에만 둔다 |

서명 검증에는 외부 감사를 받은 Monocypher 4.0.2를 비공개 코어에 포함해 사용한다
(CC0-1.0 / BSD-2-Clause, `native/src/third_party/monocypher/`). 배포물에는 소스가 들어가지 않는다.

## 토큰과 키

- 형식: `FSE1.<base64url(payload)>.<base64url(Ed25519 서명)>`. 오프라인 파일의 내용도 같은 문자열이다.
- payload 필드와 규칙은 [entitlement-api.yaml](entitlement-api.yaml)의 `Token` 참조.
- 신뢰하는 공개키는 `native/entitlement-keys.json`에 `kid`별로 둔다.
  - `prod-2026-10-15`(production): 개발자 포털의 운영 서명 키. 비밀키는 포털 서버에만 있다.
  - `test-2026-10`(test): 비밀키가 저장소(`tests/fixtures/entitlement-test-key.json`)에 있다.
- 고객용 빌드(`--validation` 없음)는 production 키만 넣는다. 테스트 키는 넣지 않는다.
  `--validation` QA 빌드와 개발용 `tools/native_build.py`는 두 키를 모두 넣는다.
  선택은 `FISICA_ENTITLEMENT_KEYS=release`(production만)로 전달되며 릴리스 도구가 설정한다.
- 테스트 키로 확인된 상태에는 `key_environment=test`와 `test_key` 경고가 붙는다.
- 운영 키를 교체할 때는 새 `kid`를 이 목록에 추가해 SDK를 먼저 배포한 뒤 포털의 서명 키를 바꾼다.

## 사용 추적 없음

- SDK는 Care&Co의 사용 현황 추적을 위한 활동 기록을 수집·보관·보고하지 않는다.
- 사용 여부, 마지막 실제 사용일시, 계약 만료·폐기 후 사용 여부, 측정 횟수, API 호출 횟수를 기록하거나
  보고하지 않는다. 앱·설치·기기별 추적도 하지 않는다.
- 기기에 저장하는 것은 서명 토큰과 그 안의 확인 시각뿐이다. 오프라인 검증을 위한 것이며 사용 이력이 아니다.
- 온라인 갱신은 토큰을 서버에 보내 새로 서명된 토큰을 받는 일이다. 측정 데이터나 활동 기록을 싣지 않는다.
- 마지막 서버 확인 시각(`verified_at`)은 서버가 계약을 확인한 시각이다. 마지막 실제 SDK 사용일시가 아니다.
- 서버의 갱신 기록만으로는 SDK를 실제로 썼는지, 계약을 지켰는지 알 수 없다. 오프라인 사용, 갱신 없이 쓰는
  사용은 서버에 보이지 않는다.
- 파트너 앱이 자체 서비스를 위해 측정 데이터와 보고서를 저장하는 것은 이와 별개이며 제한하지 않는다
  (예: Python의 `export_report`).

## Python 사용

```python
sdk = FisicaSDK()
sdk.configure_entitlement(file='fisica-entitlement.fse')            # 인터넷이 없는 PC
sdk.configure_entitlement(token=token, endpoint='https://developer.fisicahealthcare.com')  # 온라인 갱신 포함
print(sdk.entitlement_status())
```

- `endpoint`를 주지 않으면 네트워크를 전혀 쓰지 않는다. `online=False`로도 끌 수 있다.
- `endpoint`가 있으면 가져오기 직후와 기능 사용 시점에 백그라운드에서 확인 정보를 갱신한다(최대 시간당 1회).
  통신 실패는 `last_sync_error`에만 남고 상태를 바꾸지 않는다. 측정 루프·종료·자원 정리를 막지 않는다.
- 토큰은 기본으로 사용자 앱 데이터 폴더에 회사+계약별 파일로 저장한다. `storage=경로` 또는 `None` 지정 가능.
- 확인 정보를 설정하지 않으면 첫 실제 사용 때 `FisicaEntitlementWarning`이 한 번 나온다.
- 경고는 `FisicaEntitlementWarning`, 기술 오류는 기존 `FisicaError` 계열이다.

## Swift·Kotlin·C#·Dart

`FisicaEntitlement`가 Python과 같은 동작을 제공한다: 토큰/파일 가져오기, 상태, 경고 콜백,
토큰 저장·복원, 온라인 갱신.

| 언어 | 온라인 | 저장 위치 인자 | 기본 저장 위치 |
|---|---|---|---|
| Swift | `endpoint: URL`, `syncNow()`/`sync()` (URLSession) | `storage: URL?` | Application Support/Fisica/sdk |
| Kotlin | `endpoint: String`, `syncNow()`/`sync()` (HttpURLConnection) | `storage: File?` | 없음. 앱이 `context.filesDir` 등을 전달 |
| C# | `endpoint`, `SyncAsync()`/`Sync()` (HttpClient) | `storage` | LocalApplicationData/Fisica/sdk |
| Dart | `endpoint`, `sync()` (dart:io HttpClient) | `storageDirectory` | 없음. 앱이 디렉터리를 전달 |

- 모든 언어에서 `endpoint`가 없으면 네트워크를 쓰지 않는다. 서버 주소 기본값은 없다.
- `endpoint`가 있으면 백그라운드에서 확인 정보를 갱신한다(최대 시간당 1회). 한 번에 하나의 교환만 실행한다.
- 통신 실패는 `lastSyncError`에만 남는다. 예외를 던지지 않고 상태를 바꾸지 않는다.
- Android 앱은 온라인 갱신에 INTERNET 권한이 필요하다(AAR은 선언하지 않는다).
- 네 언어 모두 같은 Mock 서버를 상대로 갱신, 통신 실패, 저장·복원을 검증한다
  (`python tools/verify_bindings.py swift kotlin dotnet dart`).

## 개발용 도구

```sh
python tools/entitlement_dev.py serve --port 8765           # Mock 서버
python tools/entitlement_dev.py issue --company acme --contract ctr-1 --out fisica-entitlement.fse
python -m pytest tests/test_entitlement.py -q
```

Mock 서버와 테스트 서명기는 테스트 키만 쓴다. 서명기는 RFC 8032 참조 알고리즘의 순수 Python
구현으로 테스트·개발 전용이다. 운영 서버는 검증된 라이브러리로 서명한다.

## 서버 쪽에 필요한 것

개발자 포털(Care-Co/developer-portal)이 아래 항목을 구현해 운영 중이다.

1. 운영 Ed25519 키 쌍 생성, 공개키와 `kid` 전달. 키 교체는 `kid` 추가로 처리한다.
2. 회사·앱·서비스 등록과 기본 약관 수락 → `kind=basic` 계약 참조번호와 최초 토큰(파일) 발급.
3. 별도 계약(OEM·리브랜딩·재판매 등) → `kind=special`, 기간·`grants`·폐기 상태 관리.
4. `/sdk/v1/entitlement/refresh` 구현. 규칙은 API 명세의 설명 그대로다. 사용 보고 API는 없다.
5. 갱신 요청 기록을 SDK 사용 이력이나 계약 준수 판단 자료로 쓰지 않는다.
6. 기존 조직·계약·결제·장비 기능을 재사용할 수 있는지 먼저 확인한다. 계약 종료를 구독·결제
   NONE 상태나 장비 REVOKED로 표현하지 않는다.

## 구현 상태 (2026-10-14)

2026-10-13 정책으로 만들었던 사용 기록 기능은 코드에서 제거했다.

- 코어: `fisica_core_entitlement_mark_used`, `_report`, `_report_done` 삭제. 컨텍스트의 상태는 마지막으로
  확인한 토큰뿐이고, 저장용 상태도 토큰 한 줄이다. 이전 개발 빌드가 쓴 사용 기록 줄은 복원 시 무시한다.
- 래퍼(Python·Swift·Kotlin·C#·Dart): 사용 표시, 대기 보고 조회·완료, 보고 전송 삭제.
  기능 사용 시점에는 `check_status()`/`checkStatus()`가 경고를 다시 계산하고 백그라운드 갱신을 시도할 뿐 아무것도 기록하지 않는다.
- Mock 서버: `/sdk/v1/usage` 삭제(404). 갱신 요청은 빈 body만 받는다.
- 검증: 헤더와 바이너리에 해당 심볼이 없음, 저장 상태가 토큰뿐임, 각 언어가 서버에 **빈 갱신 요청 외에는 아무것도
  보내지 않음**을 Mock 서버 쪽에서 확인한다(`tests/test_entitlement.py`, `python tools/verify_bindings.py ...`).
- ABI 1.4는 배포한 적이 없어 함수 삭제에 major 변경을 하지 않았다.
