"""Legacy Python analysis API backed entirely by the native core."""
import numpy as np
from .native import get_core


def analyze(frames):
    return get_core().analyze(frames)


def _find_foot_dimensions_oriented(grid_data, cell_w_cm, cell_h_cm):
    indices = np.argwhere(np.asarray(grid_data) > 0)
    points = indices[:, ::-1] * [cell_w_cm, cell_h_cm] + [cell_w_cm / 2, cell_h_cm / 2]
    return tuple(get_core().dimensions(points, cell_w_cm, cell_h_cm)[:2])


def _calculate_foot_size(grid_data):
    grid = np.asarray(grid_data)
    if grid.ndim != 2:
        raise ValueError('Expected a 2D grid')
    mid = grid.shape[1] // 2
    return (*_find_foot_dimensions_oriented(grid[:, :mid], 1, 1),
            *_find_foot_dimensions_oriented(grid[:, mid:], 1, 1))


def pca_bounding_box_dimensions(pts, cell_w, cell_h):
    return tuple(get_core().dimensions(pts, cell_w, cell_h)[:4])


def compute_cop(grid, cell_w=1.0, cell_h=1.0):
    return get_core().cop(grid, cell_w, cell_h)
