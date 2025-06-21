# Fisica SDK - Python 인터페이스

이 SDK는 족저압 분석을 위한 Fisica Scale 장치와 통신할 수 있는 Python 인터페이스를 제공합니다.

## 📦 주요 기능

---

### `set_metadata(**kwargs)`

**Description**:  
현재 측정 세션에 대한 메타데이터를 설정합니다.

**Parameters**:
- `id` (Any, Optional): 사용자 또는 세션 식별자입니다.
- `name` (str, Optional): 사용자 또는 세션 식별자입니다.

**Returns**:  
없음

---

### `scan_devices()`

**Description**:  
시리얼 또는 블루투스 연결을 통해 사용 가능한 체중계 장치를 검색합니다.

**Parameters**:  
없음

**Returns**:
- `List[Dict]`: 연결 세부 정보를 포함하는 장치들의 리스트 입니다.

---

### `connect(device: Dict)`

**Description**:  
`scan_devices()`에서 반환된 선택한 장치에 연결을 설정합니다.

**Parameters**:
- `device` (Dict): 장치 ID와 유형이 포함된 변수 입니다.

**Returns**:  
없음

---

### `disconnect()`

**Description**:  
연결된 Scale 연결을 종료합니다. 측정 작업이 완료될 때까지 자동으로 대기합니다.  
이 메소드는 메인 스레드 실행을 차단합니다.

**Parameters**:  
없음

**Returns**:  
없음

---

### `start_measurement(duration: int = None)`

**Description**:  
측정 데이터 수집을 시작합니다. `duration`이 주어지면 해당 시간(초) 후에 자동으로 측정을 중지합니다.  
`duration`이 지정되지 않으면 자동 중지가 되지 않으며, 이는 권장되지 않습니다.  
측정을 일정 시간 동안 수행하려면 `duration`을 설정하거나, SDK 내장 `sleep()` 함수 또는 `time` 모듈의 `sleep()` 함수를 사용해 일정 시간 대기한 후 `stop_measurement()`를 호출하세요.  
SDK 내장 `sleep()`은 메인 스레드 실행을 차단하지 않습니다.  
또는 GUI 기반 애플리케이션을 개발하여 `duration` 경과 후 자동으로 측정을 종료하도록 하거나, `stop_measurement()` 트리거를 설정하는 방식을 사용할 수 있습니다.

**Parameters**:
- `duration` (int, Optional): 측정 시간(초)입니다.

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

### `sleep(seconds: float, after: Optional[callable] = None)`

**Description**:  
메인 스레드를 차단하지 않고 비동기적으로 지정한 시간 동안 대기합니다.  
대기 후 실행할 선택적 콜백 함수를 지정할 수 있습니다.

**Parameters**:
- `seconds` (float): 대기 시간(초)입니다.
- `after` (callable, Optional): 대기 후 실행할 함수입니다.

**Returns**:  
없음

---

### `wait()`

**Description**:  
`start_measurement()`, `stop_measurement()`, `sleep()`, `disconnect()` 등 모든 비동기 작업이 완료될 때까지 대기합니다.

이 함수는 백그라운드에서 실행 중인 모든 SDK 작업이 완전히 종료된 후 다음 단계(예: 데이터 처리 또는 시각화)로 진행할 수 있도록 보장합니다.

이 함수는 **메인 스레드를 차단**합니다.

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

### `export_report(output: str)`

**Description**:  
모든 레포트를 JSON 파일로 내보냅니다. 경로에 파일명 또는 디렉토리를 포함할 수 있으며, 확장자가 없는 파일명을 입력하면 자동으로 `.json`이 추가됩니다.

**Parameters**:
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

### `render(frame: Optional[MeasurementFrame] = None, grid_data: Optional[np.ndarray] = None, mode: str = "BLUR", scale: float = 1.0)`

**Description**:  
프레임 또는 센서 그리드 데이터를 이미지로 렌더링합니다.  
지정한 시각화 모드와 크기 배율에 따라 이미지가 데이터가 생성됩니다.
`frame`은 `on_data`에 콜백함수를 등록했을 때 인자로 전달되는 변수입니다.

**Parameters**:
- `frame` (MeasurementFrame, Optional): 센서 매트릭스를 포함하는 데이터 클래스 인스턴스입니다.
- `grid_data` (np.ndarray, Optional): 2차원 NumPy 배열 형태의 센서 그리드 데이터입니다.
- `mode` (str): 시각화 스타일(예: "BLUR")입니다. 자세한 내용은 아래 VisualOption 섹션을 참조하세요.
- `scale` (float): 이미지 크기 배율입니다.

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

### `run(scale: float = 1.0)`

**Description**:  
실시간 시각화를 위한 GUI 뷰어를 지정한 배율로 실행합니다.

**Parameters**:
- `scale` (float): GUI 창의 크기 배율입니다.

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
