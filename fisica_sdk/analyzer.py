import numpy as np
from typing import Tuple, List, Optional
from .utils import log
from .models import MeasurementFrame


def analyze(frames: Optional[List[MeasurementFrame]]):
    if not frames:
        log.debug("No frame data provided for analysis.")
        return None

    STABILITY_THRESHOLD = 0.5  # kg
    STABLE_COUNT_MIN = 10

    stable_frames = []
    for i in range(1, len(frames)):
        prev_weight = frames[i - 1].weight
        curr_weight = frames[i].weight
        if abs(curr_weight - prev_weight) <= STABILITY_THRESHOLD:
            stable_frames.append(frames[i])
        else:
            stable_frames.clear()

        if len(stable_frames) >= STABLE_COUNT_MIN:
            break

    if not stable_frames:
        log.warning("No stable weight region found.")
        return None

    import numpy as np

    # 평균 weight 계산
    avg_weight = round(np.mean([f.weight for f in stable_frames]), 2)

    # sensor_matrix 평균 계산 (IQR 기반 필터 포함)
    matrices = np.array([f.sensor_matrix for f in stable_frames])
    matrices = np.array(matrices, dtype=np.float32)  # shape: (n, h, w)

    # IQR 필터 적용 후 평균
    def iqr_filtered_mean(stack):
        q1 = np.percentile(stack, 25, axis=0)
        q3 = np.percentile(stack, 75, axis=0)
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        mask = (stack >= lower) & (stack <= upper)
        filtered = np.where(mask, stack, np.nan)
        return np.nanmean(filtered, axis=0)

    matrix = iqr_filtered_mean(matrices).tolist()
    # Calculate foot sizes
    lw, ll, rw, rl = _calculate_foot_size(np.array(matrix))
    return avg_weight, lw, ll, rw, rl
    # return matrix, avg_weight, lw, ll, rw, rl


def _find_foot_dimensions_oriented(grid_data: np.ndarray, cell_w_cm: float, cell_h_cm: float) -> Tuple[float, float]:
    indices = np.argwhere(grid_data > 0)
    if indices.size == 0:
        return 0.0, 0.0
    pts = np.stack([
        indices[:,1] * cell_w_cm + cell_w_cm/2,
        indices[:,0] * cell_h_cm + cell_h_cm/2
    ], axis=1)
    width, length, _, _ = pca_bounding_box_dimensions(pts, cell_w_cm, cell_h_cm)
    return width, length


def _calculate_foot_size(grid_data: np.ndarray) -> Tuple[float, float, float, float]:
    # decode and preprocess
    if grid_data is None:
        raise ValueError("Invalid hex_data")
    rows, cols = grid_data.shape
    # cell_width_cm = grid_width_cm / cols
    # cell_length_cm = grid_length_cm / rows
    cell_width_cm = 1.0
    cell_length_cm = 1.0
    left = grid_data[:, :cols // 2]
    right = grid_data[:, cols // 2 :]
    lw, ll = _find_foot_dimensions_oriented(left, cell_width_cm, cell_length_cm)
    rw, rl = _find_foot_dimensions_oriented(right, cell_width_cm, cell_length_cm)
    return lw, ll, rw, rl

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

def compute_cop(grid: np.ndarray, cell_w: float = 1.0, cell_h: float = 1.0) -> tuple[float, float]:
    """
    Compute the center of pressure (weighted center) from a pressure matrix.
    Args:
        grid (np.ndarray): 2D pressure values (e.g., sensor_matrix).
        cell_w (float): Width of each cell in cm.
        cell_h (float): Height of each cell in cm.
    Returns:
        Tuple of (x_center, y_center) in cm.
    """
    total_weight = np.sum(grid)
    if total_weight == 0:
        return 0.0, 0.0

    rows, cols = grid.shape
    y_indices = np.arange(rows).reshape(-1, 1) * cell_h + cell_h / 2
    x_indices = np.arange(cols).reshape(1, -1) * cell_w + cell_w / 2

    x_weighted_sum = np.sum(grid * x_indices)
    y_weighted_sum = np.sum(grid * y_indices)

    x_center = x_weighted_sum / total_weight
    y_center = y_weighted_sum / total_weight

    return x_center, y_center
