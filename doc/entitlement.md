# 런타임 확인 정보(entitlement)

2026-10-14 정책 기준. 구현은 SDK/Python 2.4.0, core 1.4.0, C ABI 1.4이며 서버는 아직 없다.
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
- 신뢰하는 공개키는 `native/entitlement-keys.json`에 `kid`별로 둔다. 현재는 **테스트 키 하나뿐**이며
  비밀키가 저장소(`tests/fixtures/entitlement-test-key.json`)에 있다.
- 테스트 키로 확인된 상태에는 `key_environment=test`와 `test_key` 경고가 붙는다.
- 고객용 패키징 도구는 테스트 키가 있으면 실패한다. QA용은 `--validation`을 명시한다.
  운영 키(공개키와 `kid`)는 서버 쪽에서 생성해 전달해야 한다.

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
sdk.configure_entitlement(token=token, endpoint='https://<server>')  # 온라인 갱신 포함
print(sdk.entitlement_status())
```

- `endpoint`를 주지 않으면 네트워크를 전혀 쓰지 않는다. `online=False`로도 끌 수 있다.
- `endpoint`가 있으면 백그라운드에서 확인 정보를 갱신한다(최대 시간당 1회).
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

1. 운영 Ed25519 키 쌍 생성, 공개키와 `kid` 전달. 키 교체는 `kid` 추가로 처리한다.
2. 회사·앱·서비스 등록과 기본 약관 수락 → `kind=basic` 계약 참조번호와 최초 토큰(파일) 발급.
3. 별도 계약(OEM·리브랜딩·재판매 등) → `kind=special`, 기간·`grants`·폐기 상태 관리.
4. `/sdk/v1/entitlement/refresh` 구현. 규칙은 API 명세의 설명 그대로다. 사용 보고 API는 없다.
5. 갱신 요청 기록을 SDK 사용 이력이나 계약 준수 판단 자료로 쓰지 않는다.
6. 기존 조직·계약·결제·장비 기능을 재사용할 수 있는지 먼저 확인한다. 계약 종료를 구독·결제
   NONE 상태나 장비 REVOKED로 표현하지 않는다.

## 구현 상태 (2026-10-14): 코드에 사용 기록 기능이 남아 있다

위 정책은 2026-10-14에 확정됐다. 현재 코드는 2026-10-13 정책으로 작성돼 **사용 기록 누적, 로컬 저장,
재시도용 보고 고정, `/sdk/v1/usage` 전송과 그 테스트를 아직 포함한다.** `endpoint`를 지정하면 실제로 전송을
시도한다. 문서만 바뀐 상태이므로 코드에서 제거하기 전에는 이 브랜치를 고객에게 제공하지 않는다.

제거 대상(토큰 저장, 온라인 갱신, 서명 검증, 파트너의 측정 보고서 저장은 대상이 아니다):

| 위치 | 제거할 것 |
|---|---|
| `native/include/fisica_core.h` | `fisica_core_entitlement_mark_used`, `_report`, `_report_done` 선언과 설명 |
| `native/src/fisica_entitlement.cpp` | `Usage`·`Report` 구조와 위 세 함수, 상태 직렬화의 `U`·`R` 줄(토큰 `C` 줄만 유지) |
| `fisica_sdk/native.py`, `entitlement.py`, `fisica_sdk.py` | `mark_used`/`report`/`report_done` 바인딩, 갱신 뒤의 보고 루프, `_note_use`의 사용 표시 |
| `wrappers/swift` `Entitlement.swift`, `Client.swift` | `markUsed`, `pendingReport`, `reportDone`, 보고 루프, 클라이언트의 `markUsed` 호출 |
| `wrappers/kotlin` `Entitlement.kt`, `Client.kt`, `Core.kt`, `bridge/fisica_jni.cpp` | 같은 API와 JNI 함수 3개 |
| `wrappers/dotnet` `Entitlement.cs`, `Client.cs`, `Core.cs` | 같은 API와 P/Invoke 3개 |
| `wrappers/flutter` `entitlement.dart`, `client.dart`, podspec 2개 | 같은 API. podspec 심볼 목록은 재생성 |
| `tools/entitlement_dev.py` | Mock 서버의 `/sdk/v1/usage`, `usage`, `report_ids`. 갱신 요청은 빈 body만 받도록 명세에 맞춤 |
| `tools/verify_bindings.py` | Mock 서버가 사용 보고를 받았는지 확인하는 부분 |
| `tests/test_entitlement.py`, `native_entitlement.cpp`, `kotlin_client.kt`, `dotnet/Program.cs`, Swift `ContractTests.swift`, Dart `client_test.dart` | 사용 기록·보고 테스트. 갱신·저장·상태 테스트는 유지 |

함께 정할 것:

- 실제 사용 시점에 하던 일 중 유지할 것은 백그라운드 갱신 시도와 경고 갱신뿐이다. 호출 지점은 남기되 기록은 하지 않는다.
- ABI 1.4는 아직 배포한 적이 없으므로 세 함수를 그대로 삭제한다. 배포 후였다면 major 변경이 필요했다.
- 이미 저장된 상태 파일의 `U`·`R` 줄은 복원 시 무시한다.
