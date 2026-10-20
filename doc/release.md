# SDK 릴리스: CDN 배포

고객용 패키지는 `https://cdn.carencoinc.com/public/sdk/<버전>/`에서 받는다. 개발자 포털의 다운로드 페이지
(`/docs/downloads`)는 `latest.json`이 가리키는 릴리스의 파일 목록을 보여준다. 새 릴리스를 올리면 포털을 다시 배포하지
않아도 링크가 바뀐다.

## 주소와 파일

S3 `carenco-prod` 버킷(서울)의 `public/sdk/`를 CloudFront(`cdn.carencoinc.com`, 배포 `ETFICHEKAL1F8`)가 공개한다.

| 경로 | 내용 | 캐시 |
|---|---|---|
| `public/sdk/latest.json` | 최신 릴리스 번호와 그 manifest 주소 | 5분 |
| `public/sdk/<버전>/manifest.json` | 파일마다 분류·이름·주소·크기·SHA-256·플랫폼, 커밋, 게시 시각 | 1년, 바뀌지 않음 |
| `public/sdk/<버전>/python/` | `fisica-<버전>-py3-none-<플랫폼>.whl` (macOS universal2, Windows x64, Windows ARM64) | 1년, 바뀌지 않음 |
| `public/sdk/<버전>/core/` | `fisica-core-<코어>-<플랫폼>.zip` (C/C++) | 〃 |
| `public/sdk/<버전>/swift/` | `fisica-swift-<v>.zip` | 〃 |
| `public/sdk/<버전>/android/` | `fisica-scale-<v>.aar`, `fisica-kotlin-<v>.zip` | 〃 |
| `public/sdk/<버전>/dotnet/` | `fisica-dotnet-<v>.zip` | 〃 |
| `public/sdk/<버전>/flutter/` | `fisica-flutter-<v>-platforms.zip`, `fisica-flutter-transports-<v>.zip` | 〃 |
| `public/sdk/<버전>/samples/` | `fisica-partner-samples-<버전>.zip` | 〃 |

`<버전>`은 SDK 버전(`sdk_version`, 지금 3.0.0)이다. 모든 고객에게 같은 파일이다. 전체 모드는 개발자 포털의 SDK 키로 연다.

규칙:

- 고객용(운영 키만 믿는) 패키지만 올린다. QA 빌드(`-validation`, `+validation`)는 업로드 도구가 거부한다.
- 한 번 올린 파일은 바꾸지 않는다. 같은 내용이면 건너뛰고(재실행), 다르면 실패한다. 고칠 것이 있으면 버전을 올려 새 릴리스로
  낸다. 버킷은 버전 관리(지난 버전 30일 보관)가 켜져 있어, 덮어쓰면 저장 비용도 늘어난다.
- 바뀌는 파일은 `latest.json` 하나다.
- 이 버킷의 파일 생성 이벤트는 storage 워커로 가지만, 관리 경로가 아닌 `public/sdk/`는 무시된다.

## 실행

GitHub Actions의 **Release SDK packages to the CDN**(`.github/workflows/release.yml`)을 수동으로 실행한다. 커밋이나
푸시로는 실행되지 않는다.

1. `publish`를 끈 채 실행한다(시험 실행). 네 플랫폼에서 고객용으로 빌드하고 패키지를 만든 뒤 감사하고, 올릴 파일과 주소를
   출력한다. 아무것도 올리지 않는다.
2. 결과가 맞으면 `publish`를 켜고 main에서 다시 실행한다.

작업 구성:

| 작업 | 러너 | 하는 일 |
|---|---|---|
| `apple` | `self-hosted, macOS` (Company-Mac) | XCFramework·Swift ZIP, macOS universal2 코어, universal2 휠. 주말이나 평일 20시(KST) 이후가 아니면 멈춘다 |
| `android` | `self-hosted, carenco, Linux` (컨테이너) | Android 코어·AAR·Kotlin ZIP |
| `windows` | `self-hosted, Windows` | Windows x64·ARM64 코어와 휠 |
| `publish` | `self-hosted, carenco, Linux` (컨테이너) | `package_platforms.py`로 언어별 패키지를 만들고 `publish_packages.py`로 감사·업로드 |

플랫폼 사이의 중간 결과는 하루만 보관하는 GitHub 아티팩트로 넘긴다. 저장소가 공개 상태이면 모든 SDK 워크플로가 실행되지
않는다(`if: github.event.repository.private`).

로컬에서 확인할 때(업로드 없음):

```sh
python tools/publish_packages.py --release 3.0.0 --packages .build/multiplatform-releases/3.0.0 --wheels <휠 폴더>...
```

## 업로드 권한 (한 번 설정)

`public/sdk/`에만 쓸 수 있는 전용 IAM 사용자를 만들고, 액세스 키를 fisica_sdk 저장소의 Actions Secret으로 넣는다.

- Secret 이름: `SDK_RELEASE_AWS_ACCESS_KEY_ID`, `SDK_RELEASE_AWS_SECRET_ACCESS_KEY`
- 리전은 워크플로에 `ap-northeast-2`로 고정돼 있다.
- 삭제 권한은 주지 않는다. `ListBucket`은 없는 파일을 403이 아닌 404로 알려 주기 위해서만 필요하다.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "SeeWhatIsPublished",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::carenco-prod",
      "Condition": {"StringLike": {"s3:prefix": ["public/sdk/*"]}}
    },
    {
      "Sid": "PublishSdkPackages",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": "arn:aws:s3:::carenco-prod/public/sdk/*"
    }
  ]
}
```

## 비용

패키지 한 벌은 수십 MB이고 릴리스할 때만 올라간다. 다운로드는 CloudFront를 거쳐 S3 요청이 거의 생기지 않는다(파일은 1년
캐시). 커밋마다 올리는 일은 없다.
