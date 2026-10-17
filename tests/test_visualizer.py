"""Image rendering of a sensor matrix (the visualization extra)."""
import json
from pathlib import Path
import numpy as np
import pytest

cv2 = pytest.importorskip('cv2')
pytest.importorskip('PIL')
from fisica_sdk import FisicaSDK, VisualOptions  # noqa: E402
from fisica_sdk.analyzer import compute_cop, pca_bounding_box_dimensions  # noqa: E402
from fisica_sdk.visualizer import LEFT_BOX, RIGHT_BOX, render  # noqa: E402

MODES = [VisualOptions.PIXEL, VisualOptions.BLUR, VisualOptions.BINARY, VisualOptions.BINARY_NONZERO, VisualOptions.CONTOUR]


@pytest.fixture
def grid():
    """Two feet on the 29x29 display grid, as a frame's sensor_matrix holds them."""
    value = np.zeros((29, 29))
    value[4:24, 3:8] = np.linspace(20, 200, 20)[:, None]
    value[4:24, 21:26] = np.linspace(20, 120, 20)[:, None]
    return value


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('scale', [0.5, 1.0, 2.0])
def test_every_mode_is_a_bgr_image_of_the_documented_size(grid, mode, scale):
    image = render(grid, mode=mode, bbox=True, scale=scale)
    assert image.dtype == np.uint8 and image.shape == (int(512 * scale), int(512 * scale), 3)


def test_all_returns_the_six_documented_images(grid):
    images = render(grid, mode=VisualOptions.ALL)
    assert len(images) == 6 and all(image.shape == (512, 512, 3) for image in images)
    # The fifth is PIXEL with the foot boxes.
    assert not np.array_equal(images[0], images[4])


def test_blur_draws_no_pressure_as_black_background(grid):
    image = render(grid, mode=VisualOptions.BLUR)
    assert not image[0:20, 0:20].any() and image[200:300, 60:120].any()


def test_foot_boxes_use_distinct_colours(grid):
    image = render(grid, mode=VisualOptions.BINARY, bbox=True)
    pixels = image.reshape(-1, 3)
    assert (pixels == LEFT_BOX).all(axis=1).any() and (pixels == RIGHT_BOX).all(axis=1).any()


def test_vmax_keeps_colours_comparable_across_frames(grid):
    # Without vmax each frame is scaled to its own maximum: half the pressure looks the same.
    np.testing.assert_array_equal(render(grid, 'PIXEL'), render(grid * 0.5, 'PIXEL'))
    # With a fixed vmax the colours follow the values.
    assert not np.array_equal(render(grid, 'PIXEL', vmax=255), render(grid * 0.5, 'PIXEL', vmax=255))
    assert render(np.zeros((29, 29)), 'PIXEL', vmax=255).shape == (512, 512, 3)  # an empty grid renders


@pytest.mark.parametrize('call', [
    lambda g: render(g, mode='BBOX'), lambda g: render(g, scale=0), lambda g: render(g, vmax=0),
    lambda g: render(g[0], mode='PIXEL'),
])
def test_invalid_arguments_are_rejected(grid, call):
    with pytest.raises(ValueError):
        call(grid)


def test_sdk_render_takes_a_frame_or_a_grid(grid):
    sdk = FisicaSDK()
    fixtures = Path(__file__).parent / 'fixtures'
    assert sdk.render(grid_data=grid, mode='PIXEL').shape == (512, 512, 3)
    with pytest.raises(ValueError):
        sdk.render()
    assert json.loads((fixtures / 'legacy-processing.json').read_text())  # fixtures stay readable


def test_analysis_helpers_use_the_core(grid):
    x, y = compute_cop(grid)
    assert 0 < x < 29 and 0 < y < 29
    width, length, cx, cy = pca_bounding_box_dimensions(np.argwhere(grid[:, :14] > 0)[:, ::-1] + 0.5, 1, 1)
    assert round(width) >= 5 and round(length) >= 20 and 3 < cx < 8 and 4 < cy < 24
