# Fisica SDK - Python 인터페이스

[![Python Version](https://img.shields.io/badge/python-3.8+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20|%20macOS%20-lightgrey.svg)](https://github.com/fisica/fisica_sdk)

이 SDK는 족저압 분석을 위한 Fisica Scale 장치와 통신할 수 있는 Python 인터페이스를 제공합니다.


## 🚀 설치 방법

### 시스템 요구사항

- Python 3.8 이상
- 운영체제: Windows 10/11 또는 macOS 10.15+

### pip로 설치

```bash
  pip install fisica
```

### 소스코드에서 설치

```bash
    git clone https://github.com/fisica/fisica_sdk.git
    cd fisica_sdk
    pip install -e .
```

### 의존성 패키지

SDK는 다음 의존성 패키지들을 자동으로 설치합니다:

```bash
# 핵심 의존성
  'numpy>=1.21',
  'opencv-python>=4.5',
  'requests>=2.25',
  'packaging>=21.0',
  'Pillow>=8.0',
  'pyserial>=3.5',
  'importlib-metadata>=4.0; python_version<"3.8"',  # 하위 호환
  'bleak>=0.22.3' # For Bluetooth support

  # GUI용 의존성  
  'PyQt5>=5.15',
```

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

### 실시간 데이터 모니터링

```python
# examples/realtime_monitoring.py
from fisica_sdk import FisicaSDK, Visualizer, VisualOptions

# Define a function to visualize incoming data
def visualize(data):
   image = sdk.render(frame=data, mode=VisualOptions.BLUR, bbox=True, scale=2.0)  # Render image from incoming data
   vis.update(image)         # Update visualizer with new image

# Initialize the SDK
sdk = FisicaSDK()
# Find available devices.
devices = sdk.scan_devices()

if devices:
   # Connect to the first device from the list.
   sdk.connect(devices[0])

   # Register callback function to visualize data
   sdk.on_data(visualize)

   # Start measurement.
   sdk.start_measurement()

   # Sleep without blocking the main thread.
   sdk.sleep(15)

   # Stop measurement.
   sdk.stop_measurement()

   # Create visualizer instance
   vis = Visualizer()

   # Launch visualizer with scaling
   vis.run(scale=2.0)

```

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

SDK는 종합적인 족저압 분석을 제공합니다:

- **압력 분포**: 히트맵 분석
- **압력 중심점(COP)**: 움직임 추적
- **발 크기**: 길이 및 폭 측정
- **체중 분포**: 좌우 발 균형
- **시간별 분석**: 시간에 따른 압력 변화

### 시각화 옵션

```python
# 다양한 렌더링 모드
modes = ["PIXEL", "BLUR", "BINARY", "BINARY_NONZERO", "BBOX", "CONTOUR"]

for mode in modes:
    image = sdk.render(frame, mode=mode, scale=2.0)
    # 이미지 저장 또는 표시
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

### GUI 애플리케이션

```python
# examples/gui_app.py
import sys
import numpy as np
import cv2
import fisica_sdk as fisica
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QPushButton, QLabel, QTextEdit
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QPixmap, QImage

class MeasurementThread(QThread):
   frame_received = pyqtSignal(object)

   def __init__(self, sdk):
      super().__init__()
      self.sdk = sdk
      self.sdk.on_data(self.on_frame)

   def on_frame(self, frame):
      self.frame_received.emit(frame)

   def run(self):
      self.sdk.start_measurement(duration=10)
      self.sdk.wait()

class FisicaGUI(QMainWindow):
   def __init__(self):
      super().__init__()
      self.sdk = fisica.FisicaSDK()
      self.devices = []
      self.measurement_thread = None
      self.init_ui()

   def init_ui(self):
      self.setWindowTitle('Fisica SDK GUI')
      self.setGeometry(100, 100, 800, 600)

      # Central widget
      central_widget = QWidget()
      self.setCentralWidget(central_widget)
      layout = QVBoxLayout(central_widget)

      # Status label
      self.status_label = QLabel('Ready')
      layout.addWidget(self.status_label)

      # Buttons
      self.scan_btn = QPushButton('Scan Devices')
      self.scan_btn.clicked.connect(self.scan_devices)
      layout.addWidget(self.scan_btn)

      self.connect_btn = QPushButton('Connect')
      self.connect_btn.clicked.connect(self.connect_device)
      self.connect_btn.setEnabled(False)
      layout.addWidget(self.connect_btn)

      self.start_btn = QPushButton('Start Measurement')
      self.start_btn.clicked.connect(self.start_measurement)
      self.start_btn.setEnabled(False)
      layout.addWidget(self.start_btn)

      # Device list
      self.device_text = QTextEdit()
      self.device_text.setMaximumHeight(100)
      layout.addWidget(self.device_text)

      # Visualization area
      self.image_label = QLabel('Pressure visualization will appear here')
      self.image_label.setMinimumHeight(400)
      self.image_label.setStyleSheet("border: 1px solid black;")
      self.image_label.setAlignment(Qt.AlignCenter)
      layout.addWidget(self.image_label)

   def scan_devices(self):
      self.status_label.setText('Scanning devices...')
      self.devices = self.sdk.scan_devices()

      if self.devices:
         device_info = '\n'.join([f"{i}: [{d['type']}] {d['name']} - {d['id']}"
                                  for i, d in enumerate(self.devices)])
         self.device_text.setText(device_info)
         self.connect_btn.setEnabled(True)
         self.status_label.setText(f'Found {len(self.devices)} devices')
      else:
         self.device_text.setText('No devices found')
         self.status_label.setText('No devices found')

   def connect_device(self):
      if self.devices:
         try:
            self.sdk.connect(self.devices[0])  # Connect to first device
            self.status_label.setText(f'Connected to {self.devices[0]["name"]}')
            self.start_btn.setEnabled(True)
            self.connect_btn.setEnabled(False)
         except Exception as e:
            self.status_label.setText(f'Connection failed: {e}')

   def start_measurement(self):
      if self.measurement_thread and self.measurement_thread.isRunning():
         return

      self.status_label.setText('Starting measurement...')
      self.start_btn.setEnabled(False)

      self.measurement_thread = MeasurementThread(self.sdk)
      self.measurement_thread.frame_received.connect(self.update_visualization)
      self.measurement_thread.finished.connect(self.measurement_finished)
      self.measurement_thread.start()

   def set_image(self, image: np.ndarray):
      """Improved image display method based on the provided code"""
      try:
         if len(image.shape) == 2:
            # Grayscale image
            h, w = image.shape
            qimage = QImage(image.data, w, h, w, QImage.Format_Grayscale8)
         elif len(image.shape) == 3:
            if image.shape[2] == 3:
               # RGB/BGR image
               h, w, ch = image.shape
               # Convert BGR to RGB if needed
               if image.dtype == np.uint8:
                  image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
               else:
                  image_rgb = image
               qimage = QImage(image_rgb.data, w, h, ch * w, QImage.Format_RGB888)
            elif image.shape[2] == 4:
               # RGBA image
               image = cv2.cvtColor(image, cv2.COLOR_BGRA2RGBA)
               h, w, ch = image.shape
               qimage = QImage(image.data, w, h, ch * w, QImage.Format_RGBA8888)
            else:
               raise ValueError("Unsupported image format.")
         else:
            raise ValueError("Unsupported image format.")

         pixmap = QPixmap.fromImage(qimage)
         # Scale to fit the label while maintaining aspect ratio
         scaled_pixmap = pixmap.scaled(self.image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
         self.image_label.setPixmap(scaled_pixmap)

      except Exception as e:
         print(f"Image display error: {e}")
         self.status_label.setText(f'Image display error: {e}')

   def update_visualization(self, frame):
      try:
         # Render the frame
         image_array = self.sdk.render(frame, mode="BLUR", scale=2.0)

         # Ensure the image is in the correct format
         if image_array.dtype != np.uint8:
            # Normalize to 0-255 range if needed
            if image_array.max() <= 1.0:
               image_array = (image_array * 255).astype(np.uint8)
            else:
               image_array = np.clip(image_array, 0, 255).astype(np.uint8)

         # Use the improved image display method
         self.set_image(image_array)

      except Exception as e:
         print(f"Visualization error: {e}")
         self.status_label.setText(f'Visualization error: {e}')

   def measurement_finished(self):
      self.status_label.setText('Measurement completed')
      self.start_btn.setEnabled(True)

      # Analyze results
      try:
         report = self.sdk.analyze()
         print("Analysis completed:", report)
      except Exception as e:
         print(f"Analysis error: {e}")

def main():
   app = QApplication(sys.argv)
   window = FisicaGUI()
   window.show()
   sys.exit(app.exec_())

if __name__ == '__main__':
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

### `render(frame: Optional[MeasurementFrame], grid_data: Optional[np.ndarray], mode: Optional[str], scale: Optional[float])`

**Description**:  
프레임 또는 센서 그리드 데이터를 이미지로 렌더링합니다.  
지정한 시각화 모드와 크기 배율에 따라 이미지가 데이터가 생성됩니다.
`frame`은 `on_data`에 콜백함수를 등록했을 때 인자로 전달되는 변수입니다.

**Parameters**:
- `frame` (MeasurementFrame, optional): 센서 매트릭스를 포함하는 데이터 클래스 인스턴스입니다.
- `grid_data` (np.ndarray, optional): 2차원 NumPy 배열 형태의 센서 그리드 데이터입니다.
- `mode` (str, optional): 시각화 스타일(예: "BLUR")입니다. 자세한 내용은 아래 VisualOption 섹션을 참조하세요. `기본:VisualOptions.BLUR`
- `scale` (float, optional): 이미지 크기 배율입니다. `기본 1.0`

**Returns**:
- `np.ndarray`: 렌더링된 이미지가 담긴 NumPy 배열입니다.

---

**🖼️ VisualOptions**

VisualOptions 클래스는 FisicaSDK.render()에서 사용할 수 있는 렌더링 모드를 정의합니다.

렌더링 모드:
- PIXEL: 픽셀 히트맵 렌더링
- BLUR: 부드러운 히트맵 렌더링
- BINARY: 바이너리 그리드 렌더링
- BINARY_NONZERO: 0 값을 제외한 바이너리 그리드 렌더링
- BBOX: BLUR 및 주성분 분석(PCA) 기반 바운딩 박스 렌더링
- CONTOUR: 픽셀 기반 윤곽선 렌더링
- ALL: 모든 렌더링 결과를 리스트 형태로 반환

---

### `run(scale: Optional[float])`

**Description**:  
실시간 시각화를 위한 GUI 뷰어를 지정한 배율로 실행합니다.

**Parameters**:
- `scale` (float, optional): GUI 창의 크기 배율입니다. `기본 1.0`

**Returns**:  
없음

---

### `update(image: np.ndarray)`

**Description**:  
렌더링된 이미지를 GUI 뷰어에 업데이트합니다.

**Parameters**:
- `image` (np.ndarray): 표시할 렌더링 이미지입니다.

**Returns**:  
없음

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

이 프로젝트는 MIT 라이선스 하에 라이선스가 부여됩니다. 자세한 내용은 [LICENSE](LICENSE) 파일을 참조하세요.

## 📞 지원

- **Issues**: [GitHub Issues](https://github.com/care-co/fisica_sdk/issues)
- **Email**: carencoinc@carenco.kr
- **Website**: [https://carenco.kr](https://carenco.kr)
-
## 🔄 변경 로그

### v1.0.0 (최신)
- 초기 릴리스
- 시리얼 및 블루투스 기기 지원
- 실시간 데이터 시각화
- 종합적인 압력 분석

---
