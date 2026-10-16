> v2 네이티브 SDK의 설치·버전·배포 정책은 [Readme.md](../Readme.md)와 [native-sdk.md](native-sdk.md)를 참조하세요. 아래는 기존 측정 API 사용 가이드입니다.

# Fisica SDK - Python 인터페이스

[![Python Version](https://img.shields.io/badge/python-3.9+-blue.svg)](https://python.org)
[Fisica Scale SDK License](../LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20|%20macOS%20-lightgrey.svg)](https://github.com/fisica/fisica_sdk)

이 SDK는 족저압 분석을 위한 Fisica Scale 장치와 통신할 수 있는 Python 인터페이스를 제공합니다.


## 🚀 설치 방법

### 시스템 요구사항

- Python 3.9 이상, 64비트
- Windows 10/11 (x64·ARM64) 또는 macOS 11 이상

### wheel로 설치

Care&Co가 제공한 바이너리 wheel을 설치합니다. 공개 레지스트리 배포와 소스 설치는 제공하지 않습니다.

```bash
python -m pip install /path/to/fisica-3.0.0-py3-none-PLATFORM.whl
# 이미지 렌더링(sdk.render)을 쓸 때: OpenCV·Pillow 포함
python -m pip install '/path/to/fisica-3.0.0-py3-none-PLATFORM.whl[visualization]'
```

필수 의존성은 numpy, pyserial, bleak, packaging이며 wheel 설치 때 함께 설치됩니다.
SDK 자체 GUI는 3.0.0부터 없습니다. 렌더링한 이미지는 앱에서 표시합니다.

## 🔧 빠른 시작

### 기본 사용법

```python
# examples/basic_use.py
import fisica_sdk as fisica

# Initialize SDK
sdk = fisica.FisicaSDK()

# Scan for devices
devices = sdk.scan_devices()
print("Available devices:", devices)

# Connect to the first device
if devices:
   sdk.connect(devices[0])

   # Set metadata
   sdk.set_metadata(name="John Doe")

   # Start measurement for 10 seconds
   sdk.start_measurement(duration=10)

   # Wait for completion
   sdk.wait()

   # Analyze results
   report = sdk.analyze()
   print("Analysis complete:", report)

   # Disconnect
   sdk.disconnect()

   # Wait for completion
   sdk.wait()
```

### 실시간 데이터 받기

```python
from fisica_sdk import FisicaSDK

sdk = FisicaSDK()
sdk.connect(sdk.scan_devices()[0])         # 사용자가 고른 기기

def on_frame(frame):
    print(frame.weight, 'kg')               # frame.sensor_matrix: 29x29 표시용 격자
    image = sdk.render(frame, mode='BLUR')  # [visualization] extra: BGR 이미지(numpy). 표시는 앱에서
                                            # 전체 모드일 때만 frame.raw_data(645바이트)·frame.raw_grid가 있음

sdk.on_data(on_frame)
sdk.start_measurement(duration=5)
sdk.wait()
sdk.disconnect()
sdk.wait()
```

기본 모드(SDK 키 없음 등)에서는 프레임이 초당 1장까지만 오고 원본 데이터가 없습니다. 체중·발 크기 분석은
두 모드에서 같습니다. 자세한 내용은 아래 "라이선스와 SDK 키"를 참고하세요.

### 배치 처리

```python
# examples/batch_processing.py
import fisica_sdk as fisica

sdk = fisica.FisicaSDK()
devices = sdk.scan_devices()

if devices:
   sdk.connect(devices[0])

   # Multiple measurements
   for i in range(5):
      sdk.set_metadata(id=i, name=f"Test_{i}")
      sdk.start_measurement(duration=5)
      sdk.wait()
      sdk.analyze()
      # Get session data
      frames = sdk.get_session_frames()
      print(f"Session {i}: {len(frames)} frames captured")

      # Reset for next measurement
      sdk.reset_session()

   # Export all reports
   reports = sdk.get_all_reports()
   sdk.export_report(reports, output="measurement_results.json")
   sdk.disconnect()
```

## 📱 기기 연결

### 시리얼 연결
SDK는 USB로 연결된 Fisica 기기를 자동으로 감지합니다.

### 블루투스 연결
블루투스(BLE) 기기의 경우:

1. **Windows**: 블루투스가 활성화되어 있는지 확인
2. **macOS**: 메시지가 나타나면 블루투스 권한 허용
   ```

## 📊 데이터 분석

### 사용 가능한 지표

- **체중**: 안정 구간 평균(kg)
- **발 길이·폭**: 좌우 각각, 공칭 1cm 셀 기준(임상 보정 아님)
- **압력 분포**: 프레임마다 29x29 표시용 격자(`sensor_matrix`, 상대값이며 kPa가 아님)
- **압력 중심점(COP)**: `fisica_sdk.analyzer.compute_cop(grid)`로 프레임별 계산

### 시각화 옵션

```python
modes = ["PIXEL", "BLUR", "BINARY", "BINARY_NONZERO", "CONTOUR"]
for mode in modes:
    image = sdk.render(frame, mode=mode, scale=2.0, bbox=True, vmax=255)  # vmax: 프레임끼리 같은 색 기준
```

## 🎯 예제

### 명령줄 인터페이스

```python
# examples/cli_measurement.py
import fisica_sdk as fisica
import argparse

def main():
   parser = argparse.ArgumentParser(description='Fisica measurement CLI')
   parser.add_argument('--duration', type=int, default=10, help='Measurement duration')
   parser.add_argument('--output', type=str, default='results.json', help='Output file')
   args = parser.parse_args()

   sdk = fisica.FisicaSDK()
   devices = sdk.scan_devices()

   if not devices:
      print("No devices found")
      return

   sdk.connect(devices[0])
   sdk.start_measurement(duration=args.duration)
   sdk.wait()

   report = sdk.analyze()
   print(report)

   sdk.export_report(output=args.output)

if __name__ == "__main__":
   main()
```

## 🔧 고급 설정

### 커스텀 콜백 함수

```python
def advanced_callback(frame):
    """고급 데이터 처리"""
    # 커스텀 분석
    pressure_sum = frame.grid.sum()
    max_pressure = frame.grid.max()
    
    # 파일에 로그 기록
    with open("pressure_log.txt", "a") as f:
        f.write(f"{frame.timestamp},{pressure_sum},{max_pressure}\n")

sdk.on_data(advanced_callback)
```

### 비동기 작업

```python
# 논블로킹 측정
sdk.start_measurement(duration=30)

# 측정하는 동안 다른 작업 수행
for i in range(10):
    sdk.sleep(2, after=lambda: print(f"체크포인트 {i}"))

# 완료 대기
sdk.wait()
```

## 🐛 문제 해결

### 일반적인 문제

1. **기기를 찾을 수 없음**
   ```python
   devices = sdk.scan_devices()
   if not devices:
       print("기기를 찾을 수 없습니다. 연결과 권한을 확인하세요.")
   ```

2**블루투스 권한 (macOS)**
- 메시지가 나타나면 블루투스 액세스 허용

### 디버깅

디버그 로그 활성화:

```python
import logging

sdk = fisica.FisicaSDK(debug=logging.DEBUG)
```

## 📚 API 레퍼런스

---

### `set_metadata(**kwargs)`

**Description**:  
현재 측정 세션에 대한 메타데이터를 설정합니다.

**Parameters**:
- `id` (Any, optional): 사용자 또는 세션 식별자입니다.
- `name` (str, optional): 사용자 또는 세션 식별자입니다.

**Returns**:  
없음

---

### `scan_devices()`

**Description**:  
시리얼 또는 블루투스 연결을 통해 사용 가능한 스케일 장치를 스캔합니다.  
유선 장치가 연결되어 있으면 즉시 유선 장치 정보를 반환하고,  
그렇지 않으면 블루투스를 통해 전원이 켜진 스케일 장치를 검색합니다.

**Parameters**:  
없음

**Returns**:
- `List[Dict]`: 연결 세부 정보를 포함하는 장치들의 리스트 입니다.

---

### `connect(device: Dict)`

**Description**:  
`scan_devices()`에서 반환된 선택한 장치에 연결을 설정합니다.

**Parameters**:
- `device` (Dict): 장치 ID와 유형이 포함된 Dict 형식의 변수 입니다.

**Returns**:  
없음

---

### `disconnect()`

**Description**:  
연결된 Scale 연결을 종료합니다. 측정 작업이 완료될 때까지 자동으로 대기합니다.

**Parameters**:  
없음

**Returns**:  
없음

---

### `start_measurement(duration: Optional[float])`

**Description**:  
측정 데이터 수집을 시작합니다. `duration`이 주어지면 해당 시간(초) 후에 자동으로 측정을 중지합니다.  
`duration`이 지정되지 않으면 자동 중지가 되지 않으며, 이는 권장되지 않습니다.  
측정을 일정 시간 동안 수행하려면 `duration`을 설정하거나, SDK 내장 `sleep()` 함수 또는 `time` 모듈의 `sleep()` 함수를 사용해 일정 시간 대기한 후 `stop_measurement()`를 호출하세요.  
SDK 내장 `sleep()`은 메인 스레드 실행을 차단하지 않습니다.  
또는 GUI 기반 애플리케이션을 개발하여 `duration` 경과 후 자동으로 측정을 종료하도록 하거나, `stop_measurement()` 트리거를 설정하는 방식을 사용할 수 있습니다.

**Parameters**:
- `duration` (float, optional): 측정 시간(초)입니다.

**Returns**:  
없음

---

### `stop_measurement()`

**Description**:  
현재 측정을 중지합니다.

**Parameters**:  
없음

**Returns**:  
없음

---

### `on_data(callback)`

**Description**:  
측정 프레임이 실시간으로 수신될 때 호출되는 콜백 함수를 등록합니다.

**Parameters**:
- `callback` (callable): `MeasurementFrame` 타입의 단일 인자를 받는 함수를 등록할 수 있습니다.

**Returns**:  
없음

---

### `sleep(seconds: float, after: Optional[callable])`

**Description**:  
메인 스레드를 차단하지 않고 비동기적으로 지정한 시간 동안 대기합니다.  
대기 후 실행할 선택적 콜백 함수를 지정할 수 있습니다.

**Parameters**:
- `seconds` (float): 대기 시간(초)입니다.
- `after` (callable, optional): 대기 후 실행할 함수입니다.

**Returns**:  
없음

---

### `wait()`

**Description**:  
`start_measurement()`, `stop_measurement()`, `sleep()`, `disconnect()` 등 모든 비동기 작업이 완료될 때까지 대기합니다.

이 함수는 백그라운드에서 실행 중인 모든 SDK 작업이 완전히 종료된 후 다음 단계(예: 데이터 처리 또는 시각화)로 진행할 수 있도록 보장합니다.

이 함수는 **메인 스레드의 실행을 차단**합니다.

**Parameters**:  
없음

**Returns**:  
없음

---

### `analyze()`

**Description**:  
현재 세션에서 수집한 프레임을 분석하여 발 크기, 압력 분포 등 관련 지표를 계산합니다.

이 함수는 측정 직후에만 유효한 값을 반환하며, 이후에는 정상 동작하지 않을 수 있습니다.  
가장 최신 세션 레포트를 얻으려면 `get_current_report()` 호출을 권장합니다.  
레포트를 불러오거나, 내보내기 전에 반드시 `analyze()`를 호출하시기 바랍니다.

**Parameters**:  
없음

**Returns**:
- `Dict`: 메타데이터, 센서 그리드, 체중 등 분석 결과가 포함된 Dictionary 데이터 입니다.

---

### `get_session_frames()`

**Description**:  
현재 세션의 프레임 데이터를 직렬화된 형식으로 반환합니다.

**Parameters**:  
없음

**Returns**:
- `List[Dict]`: 현재 세션의 직렬화된 프레임 목록입니다.

---

### `get_report()`

**Description**:  
가장 최근에 분석된 세션 레포트를 반환합니다.

**Parameters**:  
없음

**Returns**:
- `Dict`: 메타데이터, 센서 그리드, 계산된 결과가 포함된 Dictionary 데이터 입니다.

---

### `get_all_reports()`

**Description**:  
완료된 모든 세션의 분석 레포트를 가져옵니다.

**Parameters**:  
없음

**Returns**:
- `List[Dict]`: 세션 레포트 목록입니다.

---

### `export_report(report: Optional[Union['SessionReport', list]], output: Optional[str])`

**Description**:  
지정된 출력 경로, 파일명 또는 디렉토리에 모든 보고서를 JSON 파일로 내보냅니다.  
누락된 디렉토리를 자동으로 생성하고, .json 확장자가 제공되지 않은 경우 자동으로 추가합니다.  
대상이 기존 디렉토리인 경우, 미리 정의된 명명 규칙을 사용하여 파일명을 생성합니다.  
예: result 폴더가 존재하지 않는 경우 "output/sample/result"는 "output/sample/result.json"이 됩니다.

report에 인자가 없는 경우, 가장 최근에 `analyze()`된 SessionReport의 정보를 내보냅니다.  
`analyze()`를 수행하지 않고 이 메소드를 호출하는 경우 오류가 발생할 수 있습니다.  


**Parameters**:

- `report` (SessionReport, optional): `get_all_reports()` 혹은 `get_all_reports()`를 통해 얻은 데이터. 제공되지 않는 경우 가장 최근의 SessionReport를 기준으로 출력됨.
- `output` (str): 내보낼 파일 경로나 디렉토리입니다.

**Returns**:  
없음

---

### `reset_session()`

**Description**:  
현재 세션의 프레임과 메타데이터를 초기화합니다.

**Parameters**:  
없음

**Returns**:  
없음

---

### `reset_reports()`

**Description**:  
메모리에 저장된 모든 분석 레포트를 초기화합니다.

**Parameters**:  
없음

**Returns**:  
없음

---

### `render(frame=None, grid_data=None, mode='BLUR', scale=1.0, bbox=False, vmax=None)`

**Description**:  
프레임의 센서 격자(`sensor_matrix`) 또는 직접 넘긴 격자를 이미지로 그립니다. `[visualization]` extra가 필요합니다.
모든 라이선스 모드에서 동작하며, 표시는 앱이 합니다.

**Parameters**:
- `frame` (MeasurementFrame, optional): `on_data` 콜백으로 받은 프레임입니다.
- `grid_data` (np.ndarray, optional): 프레임 대신 그릴 2차원 격자입니다.
- `mode` (str, optional): 아래 VisualOptions 중 하나. `기본 BLUR`
- `scale` (float, optional): 이미지는 512x512 픽셀 × scale입니다. `기본 1.0`
- `bbox` (bool, optional): 발마다 방향 상자를 그립니다(왼발 초록, 오른발 자홍).
- `vmax` (float, optional): 색 지도의 최댓값. 주면 프레임끼리 색을 비교할 수 있고, 없으면 프레임마다 최댓값 기준입니다.

**Returns**:
- `np.ndarray`: BGR 이미지(uint8). `ALL`이면 이미지 6개의 리스트입니다.

---

**🖼️ VisualOptions**

- PIXEL: 셀 단위 히트맵
- BLUR: 부드러운 히트맵(압력이 없는 곳은 검은 배경)
- BINARY: 셀 값을 숫자로 표시
- BINARY_NONZERO: 0인 셀을 비운 숫자 표시
- CONTOUR: 셀 히트맵과 발 윤곽선
- ALL: [PIXEL, BLUR, BINARY, BINARY_NONZERO, 발 상자를 그린 PIXEL, CONTOUR]

---

## 📚 API 참조 - 추가 메서드


### `set_zero()`

**설명**:  
현재 측정값을 무게 측정의 영점 기준점으로 설정하여 장치의 영점을 보정합니다.

**매개변수**:  
없음

**반환값**:  
없음

**사용 예시**:
```python
# 먼저 장치에 연결
devices = sdk.scan_devices()
if devices:
   sdk.connect(devices[0])
   
   # 플랫폼이 비어있는지 확인한 후 영점 설정
   sdk.set_zero()  # 저울 영점 조정
   
   # 이제 정확한 측정 준비 완료
   sdk.start_measurement(duration=10)
   
   # 측정 종료 대기
   sdk.wait()  
```
**참고사항**:
- 이 메서드를 호출하기 전에 장치 연결이 필요합니다
- 장치 연결을 설정하려면 먼저 `connect()`를 호출하세요
- 저울 플랫폼이 비어있을 때 이 기능을 사용하세요
- 이는 모든 후속 무게 측정의 기준선을 설정합니다

---

### `set_scale(value)`

**설명**:  
장치의 스케일 보정 매개변수를 설정합니다. 이 함수는 정확한 측정값을 보장하기 위해 장치의 무게 측정 시스템에서 사용하는 내부 보정값을 조정합니다.

**매개변수**:
- `value` (int): 스케일 보정 매개변수, -32768부터 32767까지의 범위 내에 있어야 함

**반환값**:  
없음

**사용 예시**:
```python
# 먼저 장치에 연결
devices = sdk.scan_devices()
if devices:
    sdk.connect(devices[0])
    
    # 먼저 영점 설정
    sdk.set_zero()
    
    # 스케일 보정 매개변수 설정
    sdk.set_scale(12600)  # 보정값을 12600으로 설정
    
    # 보정된 설정으로 측정 시작
    sdk.start_measurement(duration=10)
    
    # 측정 종료 대기
    sdk.wait()
```

**참고사항**:
- 이 메서드를 호출하기 전에 장치 연결이 필요합니다
- 장치 연결을 설정하려면 먼저 `connect()`를 호출하세요
- 값은 -32768부터 32767까지 범위의 정수여야 합니다
- 최적의 보정을 위해 `set_zero()` 후에 사용하세요
- 적절한 보정값은 장치 문서를 참조하세요

---

## 🔧 장치 보정 워크플로우

최적의 측정 정확도를 위해 다음 보정 순서를 따르세요:

```python
# examples/calibration.py
import fisica_sdk as fisica

# 초기화 및 연결
sdk = fisica.FisicaSDK()
devices = sdk.scan_devices()

if devices:
    sdk.connect(devices[0])
    
    # 1단계: 영점 설정 (빈 플랫폼)
    print("플랫폼이 비어있는지 확인해주세요...")
    input("준비되면 Enter를 누르세요...")
    sdk.set_zero()
    print("영점이 보정되었습니다.")
    
    # 2단계: 스케일 보정 매개변수 설정
    # 장치에 적합한 보정값 사용
    # (권장값은 장치 문서 참조)
    calibration_value = 12600  # -32768부터 32767 범위의 예시값
    sdk.set_scale(calibration_value)
    print(f"스케일 보정이 다음 값으로 설정되었습니다: {calibration_value}")
    
    # 3단계: 알려진 기준 무게로 보정 확인
    print("플랫폼에 알려진 기준 무게를 올려주세요...")
    reference_weight = float(input("기준 무게를 입력하세요 (kg): "))
    
    # 정확도 확인을 위한 측정
    sdk.start_measurement(duration=10)
    sdk.wait()
    report = sdk.analyze()
    measured_weight = report.weight
    
    print(f"기준값: {reference_weight}kg, 측정값: {measured_weight}kg")
    print(f"정확도: {abs(measured_weight - reference_weight):.3f}kg 차이")
    
    # 4단계: 정확한 측정 준비 완료
    print("보정이 완료되었습니다. 측정 준비가 되었습니다.")
    sdk.disconnect()
```

## 🤝 Contributing

기여를 환영합니다! Pull Request를 자유롭게 제출해 주세요.

1. 저장소 포크
2. 브랜치 생성 (`git checkout -b feature/amazing-feature`)
3. 변경사항 커밋 (`git commit -m 'Add amazing feature'`)
4. 브랜치에 푸시 (`git push origin feature/amazing-feature`)
5. Pull Request 열기

## 📄 License

래퍼와 비공개 코어에는 [Fisica Scale SDK License](../LICENSE)를 적용합니다.
현재 LICENSE는 2026-10-17 정책(기본·전체 모드, SDK 키)에 맞춰 다시 쓴 Version 2.0 검토용 초안이며 법무 검토 전입니다
([검토 자료](license-review.md), [정책 이력](policy-history.md), [Version 1.0](LICENSE-1.0.txt)). 기존 계약, 이미 제공한 배포본,
과거 MIT 등으로 제공한 버전의 권리는 자동으로 바뀌지 않습니다.

정책 요약: 정품 장비 구매에 포함된 기본 사용권은 **기본 모드**이며 날짜 기반 만료가 없고, 상업 사용도 등록 없이
쓸 수 있습니다. 원본 데이터·매 프레임·전체 보고서 내보내기 같은 **전체 모드**는 개발자 포털에서 받은 SDK 키를
앱이나 PC에 활성화해야 쓸 수 있습니다(14일 체험, 비상업 무료 연장, 상업 계약). OEM·리브랜딩·SDK 재판매 같은 예외는
별도 계약이 필요합니다. 래퍼 수정과 자체 앱에 포함한 배포는 허용하며 코어 소스는 배포하지 않고 역설계를 금지합니다.
이전 버전과 제3자 소프트웨어에 적용된 별도 이용 조건은 유지됩니다.

### 라이선스와 SDK 키

```python
sdk.configure_entitlement(file='sdk-key.fse', endpoint='https://developer.fisicahealthcare.com')
print(sdk.entitlement_status())   # mode, reason, features, 키·체험·계약 날짜, 경고
sdk.activation_code()             # 인터넷이 없는 PC: 포털에 입력할 활성화 코드
sdk.release_key()                 # 이 앱·PC의 키 해제, 포털에 입력할 해제 코드 반환
```

키가 없거나, 아직 활성화되지 않았거나, 다른 앱·PC에 활성화됐거나, 기한·체험·계약이 끝났거나, 시계를 1시간 넘게
되돌리면 **기본 모드**로 동작합니다. 연결·측정·설정·체중·발 길이와 폭은 그대로 쓰고, 프레임은 초당 1장까지 원본 데이터
없이 오며, 보고서 내보내기는 요약만 담깁니다. 원본 격자를 직접 해석하면 `FisicaLicenseError`가 납니다.
`endpoint`가 있으면 새 키를 이 앱·PC로 자동 활성화하고 기한 전에 갱신합니다. SDK는 측정 데이터나 사용 여부·사용 시각·
호출 횟수 같은 사용 기록을 보내지 않습니다. 서버로 가는 것은 활성화 때의 키와 대상(앱 ID 또는 PC 해시), 갱신 때의 키뿐입니다.
자세한 내용은 [라이선스와 SDK 키](entitlement.md)를 참고하세요.

## 📞 지원

- **Issues**: [GitHub Issues](https://github.com/care-co/fisica_sdk/issues)
- **Email**: carencoinc@carenco.kr
- **Website**: [https://carenco.kr](https://carenco.kr)
-
## 🔄 변경 로그

### v1.0.0 (과거 릴리스)
- 초기 릴리스
- 시리얼 및 블루투스 기기 지원
- 실시간 데이터 시각화
- 종합적인 압력 분석

---
