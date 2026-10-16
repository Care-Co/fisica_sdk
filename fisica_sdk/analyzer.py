"""Analysis helpers backed entirely by the native core (nominal 1 cm cells; not calibrated)."""
from .native import get_core


def analyze(frames):
    return get_core().analyze(frames)


def pca_bounding_box_dimensions(pts, cell_w, cell_h):
    """(width, length, centre x, centre y) of the oriented box around xy points, in the units of cell_w/cell_h."""
    return tuple(get_core().dimensions(pts, cell_w, cell_h)[:4])


def compute_cop(grid, cell_w=1.0, cell_h=1.0):
    """Centre of pressure (x, y) of a grid, in the units of cell_w/cell_h from its top-left corner."""
    return get_core().cop(grid, cell_w, cell_h)
