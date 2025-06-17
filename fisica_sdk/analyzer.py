import numpy as np
import time
import logging
from typing import Optional, Tuple, Union, Any

from numpy import ndarray, dtype

logger = logging.getLogger(__name__)

ALL = "ALL"
DIMENSION = "DIMENSION"
LENGTH = "LENGTH"
WEIGHT = "WEIGHT"
TIMER = "TIMER"

def analyze(hex_data: str, args: str = ALL) -> Union[
    dict[str, float], dict[str, np.ndarray], dict[str, Union[float, str, None, dict[str, float], np.ndarray]], dict[
        str, float], dict[str, dict[str, float]], None]:

    start = time.time()
    grid_data, weight, battery = _decode_hex(hex_data)
    left_width, left_height, right_width, right_height = _calculate_foot_size(hex_data)
    elapsed = round((time.time() - start) * 1000, 3)  # ms 단위
    if grid_data is None:
        logger.error("Failed to decode plantar pressure data: input length mismatch or malformed data.")
        raise ValueError

    processed_grid = preprocess_grid_data(grid_data)

    # 반환
    if args == ALL:
        return {
            "dimension": processed_grid,
            "weight": float(weight),
            "battery": battery,
            "size": {
                "left_width": left_width,
                "left_height": left_height,
                "right_width": right_width,
                "right_height": right_height
            },
            # "extra1": extra1,
            # "extra2": extra2,
            "timer(ms)": elapsed
        }
    elif args == DIMENSION:
        return {"dimension": processed_grid}
    elif args == WEIGHT:
        return {"weight": float(weight)}
    elif args == TIMER:
        return {"timer(ms)": elapsed}
    elif args == LENGTH:
        return { "size": {
            "left_width": left_width,
            "left_height": left_height,
            "right_width": right_width,
            "right_height": right_height
        }
    }
    else:
        return None

def _decode_hex(hex_data: str) -> tuple[ndarray[Any, dtype[Any]], str, str]:
    try:
        data_int = [int(hex_data[i:i+2], 16) for i in range(0, len(hex_data), 2)]
    except ValueError:
        logger.warning("Hex decoding failed due to invalid characters.")
        raise ValueError

    rawData = np.array(data_int)

    if len(rawData) != 645:
        logger.error(f"Invalid data length: expected 645, got {len(rawData)}")
        raise ValueError

    weight = f"{rawData[640]}.{rawData[641]}"
    battery = str(rawData[642])
    grid_data = rawData[:638].reshape((29, 22))

    return grid_data, weight, battery

def preprocess_grid_data(grid_data: np.ndarray) -> np.ndarray:
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

def decode_raw(hex_data: str) -> Optional[np.ndarray]:
    grid_data, *_ = _decode_hex(hex_data)
    return grid_data

def _find_foot_dimensions_oriented(foot_data: np.ndarray, cell_w_cm: float, cell_h_cm: float) -> Tuple[float, float]:
    indices = np.argwhere(foot_data > 0)
    if indices.size == 0:
        return 0.0, 0.0
    pts = np.stack([
        indices[:,1] * cell_w_cm + cell_w_cm/2,
        indices[:,0] * cell_h_cm + cell_h_cm/2
    ], axis=1)
    width, length, _, _ = pca_bounding_box_dimensions(pts, cell_w_cm, cell_h_cm)
    return width, length

def _calculate_foot_size(hex_data: str, grid_width_cm: float = 300.0, grid_height_cm: float = 150.0) -> Tuple[float, float, float, float]:
    # decode and preprocess
    grid = decode_raw(hex_data)
    if grid is None:
        raise ValueError("Invalid hex_data")
    grid = preprocess_grid_data(grid)
    rows, cols = grid.shape
    # cell_width_cm = grid_width_cm / cols
    # cell_height_cm = grid_height_cm / rows
    cell_width_cm = 1.0
    cell_height_cm = 1.0
    left = grid[:, :cols // 2]
    right = grid[:, cols // 2 :]
    lw, lh = _find_foot_dimensions_oriented(left, cell_width_cm, cell_height_cm)
    rw, rh = _find_foot_dimensions_oriented(right, cell_width_cm, cell_height_cm)
    return lw, lh, rw, rh

def pca_bounding_box_dimensions(pts: np.ndarray, cell_w: float, cell_h: float) -> tuple[float, float, float, float]:
    import math
    center = pts.mean(axis=0)
    cov = np.cov(pts, rowvar=False)
    eigvals, eigvecs = np.linalg.eigh(cov)
    secondary = eigvecs[:, eigvals.argmin()]
    principal = eigvecs[:, eigvals.argmax()]
    # Project points onto the principal and secondary axes
    disp = pts - center
    proj_pr = disp.dot(principal)
    proj_sec = disp.dot(secondary)
    # Calculate half-lengths and bounding box center in PCA space
    half_len = (proj_pr.max() - proj_pr.min()) / 2
    half_wid = (proj_sec.max() - proj_sec.min()) / 2
    center_pr = (proj_pr.max() + proj_pr.min()) / 2
    center_sec = (proj_sec.max() + proj_sec.min()) / 2
    # The true center is mean plus offset along principal/secondary to box center
    center = center + principal * center_pr + secondary * center_sec
    diag = math.hypot(cell_w, cell_h) / 2
    half_len = (half_len + diag) * 1.025
    half_wid = (half_wid + diag) * 1.025
    return half_wid * 2, half_len * 2, center[0], center[1]
