# 런타임 확인 정보(entitlement)와 사용 기록

2026-10-13 기준: SDK/Python 2.4.0, core 1.4.0, C ABI 1.4. 서버는 아직 구현되지 않았다.
서버 계약은 [entitlement-api.yaml](entitlement-api.yaml), 약관 초안은
[license-v2-draft.md](license-v2-draft.md)에 있다.

## 원칙

- 플랫폼·아키텍처·코어 버전이 같으면 모든 회사가 **동일한 바이너리**를 쓴다.
  회사·계약·배포 ID는 빌드에 들어가지 않는다. 코어에 들어가는 것은 서버 공개키뿐이다.
- 회사·계약 정보는 서버가 서명한 토큰(온라인 응답 또는 오프라인 파일)으로 런타임에 주입한다.
- **어떤 상태도 로컬 SDK 기능을 차단하지 않는다.** 결과는 상태값과 경고뿐이며 오류 코드로
  반환하지 않는다. 측정·통신·입력·버전 호환성 오류는 기존대로 별도의 오류다.
- 토큰은 장비 진위, 코드 출처, 계약 준수를 증명하지 않는다. 장비 인증·잠금은 없다.

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
| 코어 (C ABI 1.4, I/O 없음) | 토큰 파싱, Ed25519 서명 검증, 세 상태 판정, 사용 기록 갱신, 보고 내용 생성, 저장용 상태 직렬화·복원 |
| 래퍼 | 파일·네트워크·시계·난수 제공, 상태를 언어 자료형과 경고 이벤트로 전달 |
| 서버 | 회사·계약 관리, 토큰 서명, 폐기 정보 서명, 사용 보고 수신. 서명 비밀키는 서버에만 둔다 |

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

## 사용 기록

수집하는 것은 회사+계약별 세 가지뿐이다.

- 사용 여부(기록이 있음)
- 마지막 실제 사용일시(기기 시계)
- 계약 만료·폐기 상태에서 사용이 관측됐는지

규칙:

- 연결·측정·분석 같은 실제 기능 사용 때만 갱신한다. import, 인스턴스 생성, 확인 정보 갱신은 사용이 아니다.
- 서명이 확인된 회사·계약이 있을 때만 기록한다. 미확인 사용은 어디에도 귀속하지 않는다.
- 회사·계약이 바뀌어도 이전 기록은 이전 계약에 남는다.
- 측정 횟수, 호출 횟수, 실행 로그, 측정 원본, 체중, 건강·개인 정보, 하드웨어 식별자는 수집·전송하지 않는다.
  서버도 세 필드 외의 항목이 있으면 요청을 거절한다.
- `observed_after_contract_end`는 "계약 종료 상태와 SDK 사용이 함께 관측됐다"는 뜻이다.
  특별 권한 행사나 약관 위반의 증거가 아니며, 별도 계약이 끝난 뒤 기본 SDK를 쓰는 것은 허용된다.
- 30일 초과(`refresh_due`)만으로는 이 값이 켜지지 않는다.
- 보고는 임시 `report_id`로 중복 제거한다. 재시도는 같은 내용을 같은 id로 보낸다.
  id는 보고 단위마다 새로 만들며 설치·기기 식별자로 쓰지 않는다.
- 서버가 성공을 확인한 뒤에만 로컬 기록을 지운다. 실패하면 기록을 보존하고 SDK 기능은 계속 동작한다.
- 서버는 `client_last_used_at`의 최댓값을 유지하므로 늦게 온 보고가 시각을 되돌리지 않는다.
  이 시각은 기기 시계이며 서버가 검증한 시각이 아니다. 서버 수신 시각은 따로 기록한다.
- 보고가 오래됐다고 미사용이라고 볼 수 없다. 이 기록은 과금·감사 증빙이 아니며,
  보고 코드를 제거한 사용이나 오프라인 사용을 탐지·강제하지 못한다.

## Python 사용

```python
sdk = FisicaSDK()
sdk.configure_entitlement(file='fisica-entitlement.fse')            # 인터넷이 없는 PC
sdk.configure_entitlement(token=token, endpoint='https://<server>')  # 온라인 갱신 포함
print(sdk.entitlement_status())
```

- `endpoint`를 주지 않으면 네트워크를 전혀 쓰지 않는다. `online=False`로도 끌 수 있다.
- `endpoint`가 있으면 가져오기 직후와 실제 사용 시 백그라운드에서 갱신·보고한다(최대 시간당 1회).
  통신 실패는 `last_sync_error`에만 남고 상태를 바꾸지 않는다. 측정 루프·종료·자원 정리를 막지 않는다.
- 상태 파일은 기본으로 사용자 앱 데이터 폴더에 회사+계약별로 저장한다. `storage=경로` 또는 `None` 지정 가능.
- 확인 정보를 설정하지 않으면 첫 실제 사용 때 `FisicaEntitlementWarning`이 한 번 나온다.
- 경고는 `FisicaEntitlementWarning`, 기술 오류는 기존 `FisicaError` 계열이다.

Swift·Kotlin·C#·Dart는 `FisicaEntitlement`로 토큰/파일 가져오기, 상태, 경고 콜백, 사용 표시,
대기 중 보고 조회·완료 처리, 상태 내보내기·복원을 제공한다. 이 언어들의 HTTP 전송과 파일 저장은
서버 API가 확정된 뒤 추가한다. 그 전까지는 앱이 `pendingReport`/`exportState` 결과를 직접 전송·저장한다.

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
2. 회사·앱·서비스 등록과 기본 약관 수락 → `kind=basic` 계약 참조번호와 토큰 파일 발급.
3. 별도 계약(OEM·리브랜딩·재판매 등) → `kind=special`, 기간·`grants`·폐기 상태 관리.
4. `/sdk/v1/entitlement/refresh`, `/sdk/v1/usage` 구현. 규칙은 API 명세의 설명 그대로다.
5. 기존 조직·계약·결제·장비 기능을 재사용할 수 있는지 먼저 확인한다. 계약 종료를 구독·결제
   NONE 상태나 장비 REVOKED로 표현하지 않는다.
