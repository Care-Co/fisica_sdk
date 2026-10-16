"""Images of a sensor matrix for quick checks (optional extra: pip install fisica[visualization]).

Presentation only: the values are a frame's `sensor_matrix` (29x29, every mode; in basic mode
frames arrive at most once a second) and the foot boxes come from the core's dimension analysis.
Images are BGR uint8 arrays of 512x512 pixels times `scale`.
"""
from typing import List, Optional, Union

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .native import get_core

LEFT_BOX = (0, 255, 0)     # BGR green
RIGHT_BOX = (255, 0, 255)  # BGR magenta: stays visible on every colour of the JET map


def render(grid_data: np.ndarray, mode: str = 'BLUR', bbox: bool = False, scale: float = 1.0,
           vmax: Optional[float] = None) -> Union[np.ndarray, List[np.ndarray]]:
    vis = Visualizer()
    vis.scale(scale)
    return vis.render(grid_data, mode, bbox, vmax)


class VisualOptions:
    """
    Fisica SDK : VisualOptions
        Available rendering modes FisicaSDK.render():
            - PIXEL: Color-mapped cells
            - BLUR: Smoothed heat map on a black background
            - BINARY: Cell values as text
            - BINARY_NONZERO: Cell values as text, zero cells left blank
            - CONTOUR: Color-mapped cells with the outline of each foot
            - ALL: [PIXEL, BLUR, BINARY, BINARY_NONZERO, PIXEL with foot boxes, CONTOUR]
    """

    ALL    = 'ALL'
    PIXEL  = 'PIXEL'
    BLUR   = 'BLUR'
    BINARY = 'BINARY'
    BINARY_NONZERO = 'BINARY_NONZERO'
    CONTOUR = 'CONTOUR'


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)  # Pillow 10.1+: scalable built-in font
    except TypeError:
        return ImageFont.load_default()


class Visualizer:
    BASE_W = 512
    BASE_H = 512

    def __init__(self):
        self._scale = 1.0
        self._vmax = None

    def scale(self, factor: float):
        if not isinstance(factor, (int, float)) or not 0 < factor <= 8:
            raise ValueError('scale must be a number between 0 and 8')
        self._scale = float(factor)

    def _size(self):
        return int(self.BASE_W * self._scale), int(self.BASE_H * self._scale)

    def _normalize_grid(self, grid_data: np.ndarray) -> np.ndarray:
        """0..255 from zero to vmax (fixed colours across frames) or, without vmax, to the frame's maximum."""
        top = self._vmax if self._vmax else float(np.max(grid_data))
        if top <= 0:
            return np.zeros(grid_data.shape, dtype=np.uint8)
        return np.clip(grid_data / top * 255, 0, 255).astype(np.uint8)

    def _render_with_transparency(self, grid_data: np.ndarray, bbox: bool) -> np.ndarray:
        heatmap = cv2.resize(self._normalize_grid(grid_data), (self.BASE_W, self.BASE_H), interpolation=cv2.INTER_LINEAR)
        blurred = cv2.GaussianBlur(heatmap, (21, 21), 0)
        sharpened = cv2.filter2D(blurred, -1, np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]]))
        image = cv2.applyColorMap(blurred, cv2.COLORMAP_JET)
        image[(blurred == np.min(blurred)) | (sharpened < 10)] = 0  # no pressure: background
        image = cv2.resize(image, self._size(), interpolation=cv2.INTER_LINEAR)
        return self._render_bbox(grid_data, image) if bbox else image

    def _render_pixel(self, grid_data: np.ndarray, bbox: bool) -> np.ndarray:
        image = cv2.resize(cv2.applyColorMap(self._normalize_grid(grid_data), cv2.COLORMAP_JET), self._size(),
                           interpolation=cv2.INTER_NEAREST)
        return self._render_bbox(grid_data, image) if bbox else image

    def _render_binary(self, grid_data: np.ndarray, bbox: bool, hide_zeros: bool = False) -> np.ndarray:
        rows, cols = grid_data.shape
        width, height = self._size()
        cell_w, cell_h = width / cols, height / rows  # the same cells the foot boxes use
        image = Image.new("RGB", (width, height), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        font = _font(max(6, int(min(cell_w, cell_h) * 0.55)))
        for i in range(rows):
            for j in range(cols):
                value = int(grid_data[i, j])
                if hide_zeros and value == 0:
                    continue
                left, top, right, bottom = font.getbbox(str(value))
                x = j * cell_w + (cell_w - (right - left)) / 2 - left
                y = i * cell_h + (cell_h - (bottom - top)) / 2 - top
                draw.text((x, y), str(value), fill=(255, 255, 255), font=font)
        result = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        return self._render_bbox(grid_data, result) if bbox else result

    def _render_bbox(self, grid_data: np.ndarray, img: np.ndarray) -> np.ndarray:
        """Draws each foot's oriented box from the core's dimension analysis onto img."""
        rows, cols = grid_data.shape
        h, w = img.shape[:2]
        cell_w, cell_h = w / cols, h / rows

        def draw_box(subgrid, col_offset, color):
            cells = np.argwhere(subgrid > 0)
            if cells.size == 0:
                return
            points = np.stack([(cells[:, 1] + col_offset) * cell_w + cell_w / 2, cells[:, 0] * cell_h + cell_h / 2], axis=1)
            corners = get_core().dimensions(points, cell_w, cell_h)[4:].reshape(4, 2).astype(int)
            cv2.polylines(img, [corners], True, color, 2)

        mid = cols // 2
        draw_box(grid_data[:, :mid], 0, LEFT_BOX)
        draw_box(grid_data[:, mid:], mid, RIGHT_BOX)
        return img

    def _render_contour(self, grid_data: np.ndarray, bbox: bool) -> np.ndarray:
        img = self._render_pixel(grid_data, False)
        h, w = img.shape[:2]
        mask = cv2.resize((grid_data > 0).astype(np.uint8) * 255, (w, h), interpolation=cv2.INTER_NEAREST)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(img, contours, -1, (255, 255, 255), 2)
        return self._render_bbox(grid_data, img) if bbox else img

    def render(self, grid_data: np.ndarray, mode: str, bbox: bool = False,
               vmax: Optional[float] = None) -> Union[np.ndarray, List[np.ndarray]]:
        grid_data = np.asarray(grid_data, dtype=np.float64)
        if grid_data.ndim != 2 or not all(grid_data.shape):
            raise ValueError('Expected a nonempty 2D grid such as frame.sensor_matrix')
        if vmax is not None and not vmax > 0:
            raise ValueError('vmax must be positive')
        self._vmax = vmax
        if mode == VisualOptions.PIXEL:
            return self._render_pixel(grid_data, bbox)
        if mode == VisualOptions.BLUR:
            return self._render_with_transparency(grid_data, bbox)
        if mode == VisualOptions.BINARY:
            return self._render_binary(grid_data, bbox)
        if mode == VisualOptions.BINARY_NONZERO:
            return self._render_binary(grid_data, bbox, hide_zeros=True)
        if mode == VisualOptions.CONTOUR:
            return self._render_contour(grid_data, bbox)
        if mode == VisualOptions.ALL:
            return [
                self._render_pixel(grid_data, bbox),
                self._render_with_transparency(grid_data, bbox),
                self._render_binary(grid_data, bbox),
                self._render_binary(grid_data, bbox, hide_zeros=True),
                self._render_pixel(grid_data, True),
                self._render_contour(grid_data, bbox),
            ]
        raise ValueError(f"Unsupported rendering mode: {mode}")
