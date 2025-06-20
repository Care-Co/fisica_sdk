import numpy as np
from typing import Optional, Any

from numpy import ndarray, dtype

from .models import MeasurementFrame
from datetime import datetime
from .utils import log


def _decode_hex(hex_data: str) -> tuple[ndarray[Any, dtype[Any]], str, str]:
    """
    Decode a hex string into sensor data (29x22 grid), weight, and battery level.
    Emulates the behavior of the original _decode_hex function.
    """
    try:
        data_int = [int(hex_data[i:i+2], 16) for i in range(0, len(hex_data), 2)]
    except ValueError:
        log.warning("Hex decoding failed due to invalid characters.")
        raise ValueError("Invalid hex string")

    rawData = np.array(data_int)

    if len(rawData) != 645:
        log.error(f"Invalid data length: expected 645, got {len(rawData)}")
        raise ValueError("Incorrect data length")

    # Extract grid data
    grid_data = rawData[:638].reshape((29, 22))

    # Extract weight in "a.b" format
    weight = f"{rawData[640]}.{rawData[641]}"

    # Extract battery as integer string
    battery = str(rawData[642])

    return grid_data, weight, battery


def _preprocess_grid_data(grid_data: np.ndarray) -> np.ndarray:
    """
    센서 데이터 전처리 수행
    """
    # Step 1: 각 열의 0이 아닌 값 개수로 보정
    non_zero_counts = np.count_nonzero(grid_data, axis=0)
    grid_data = np.where(grid_data != 0, grid_data * non_zero_counts, grid_data)

    # Step 2: 구간화
    non_zero_values = grid_data[grid_data != 0]
    if non_zero_values.size == 0:
        return grid_data  # 모두 0인 경우는 그대로 반환

    mean_value = np.mean(non_zero_values)
    bins = np.linspace(0, 2 * mean_value, 11)
    bin_midpoints = (bins[:-1] + bins[1:]) / 2

    def assign_to_nearest_bin(value):
        if value == 0:
            return 0
        bin_idx = min(np.digitize(value, bins) - 1, len(bin_midpoints) - 1)
        return bin_midpoints[bin_idx]

    grid_data = np.vectorize(assign_to_nearest_bin)(grid_data)

    # Step 3: 255로 스케일링
    max_value = np.max(grid_data)
    if max_value > 255:
        scale_factor = 255 / max_value
        grid_data = (grid_data * scale_factor).astype(int)

    return grid_data


def extract_frames_from_buffer(buffer: bytearray, index: int) -> Optional[str]:
    if index >= 643:
        start_index = index - 643
        frame = buffer[start_index : index + 2]
        if len(frame) == 645:
            return frame.hex()



def parse_frame_from_chunk(chunk: str) -> Optional[MeasurementFrame]:
    if (data := extract_data(chunk)) is None:
        return None
    matrix, weight, battery = data
    return MeasurementFrame(
        timestamp=datetime.now().replace(microsecond=0).isoformat(),
        sensor_matrix=matrix,
        weight=float(weight),
        battery=float(battery)
    )

def extract_data(chunk: str) -> Optional[tuple[Any, str, str]]:
    grid, weight, battery = _decode_hex(chunk)
    if np.count_nonzero(grid) == 0:
        log.debug("Received all-zero sensor grid; skipping frame.")
        return None
    # matrix = _preprocess_grid_data(grid)
    matrix = _preprocess_grid_data(np.array(grid)).tolist()
    return matrix, weight, battery

