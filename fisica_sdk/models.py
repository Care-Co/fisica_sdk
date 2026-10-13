from dataclasses import dataclass
from typing import List, Dict, Any, Optional

@dataclass
class DecodedMeasurement:
    raw_grid: List[List[float]]
    sensor_matrix: List[List[float]]
    weight: float
    battery: float
    raw_data: List[int]
    core_version: str
    schema_version: int = 1
    battery_valid: bool = False

@dataclass
class MeasurementFrame:
    timestamp: str
    sensor_matrix: List[List[float]]          # processed relative values, not calibrated kPa
    weight: float                             # 측정 시점 체중
    battery: float
    raw_data: Optional[List[int]] = None       # 원본 645-byte rawData
    raw_grid: Optional[List[List[float]]] = None
    battery_valid: bool = False

@dataclass
class MeasurementSession:
    metadata: Dict[str, Any]
    frames: List[MeasurementFrame]

@dataclass
class SessionReport:
    session_id: str
    name: str
    foot_length: Dict[str, float]
    foot_width: Dict[str, float]
    total_frame: List[MeasurementFrame]
    frame_count: int
    duration_sec: float
    weight: Optional[float] = 0.0
    battery: Optional[float] = 0.0
    battery_valid: bool = False
