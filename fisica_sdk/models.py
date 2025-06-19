from dataclasses import dataclass
from typing import List, Dict, Any, Optional

@dataclass
class MeasurementFrame:
    timestamp: str
    sensor_matrix: List[List[float]]          # raw or kPa 센서값 (2D)
    weight: float                             # 측정 시점 체중
    battery: float
    # 측정 시점 배터리 (%)

@dataclass
class MeasurementSession:
    metadata: Dict[str, Any]
    frames: List[MeasurementFrame]

@dataclass
class SessionReport:
    session_id: str
    name: str
    foot_length: Dict[str, float]   # e.g., {'left': 23.1, 'right': 24.5}
    foot_width: Dict[str, float]    # e.g., {'left': 9.8, 'right': 10.2}
    total_frame: List[MeasurementFrame]
    frame_count: int
    duration_sec: float
    weight: Optional[float] = None
    battery: Optional[float] = None