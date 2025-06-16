import cv2
import numpy as np
import logging
from typing import Union
from PIL import Image, ImageDraw, ImageFont
from .analyzer import decode_raw, preprocess_grid_data, pca_bounding_box_dimensions

logger = logging.getLogger(__name__)

# def visualize(hex_data: str, mode: str = 'BLUR', scale: float = 1.0) -> Union[np.ndarray, bytes, list]:
#     vis = Visualizer()
#     vis.scale(scale)
#     image = vis.visualize(hex_data, mode)
#     cv2.imshow("test",image)
#     cv2.waitKey()
def visualize(hex_data: str, mode: str = 'BLUR', scale: float = 1.0) -> Union[np.ndarray, bytes, list]:
    vis = Visualizer()
    vis.scale(scale)
    return vis.visualize(hex_data, mode)

class VisualOptions:
    """
    Fisica SDK : VisualOptions
        Available visualization modes FisicaSDK.visualize():
            - PIXEL: Color-mapped pixel visualization
            - BLUR: Smoothly rendered visualization
            - BINARY: Text grid of values
            - BINARY_NONZERO: Text grid hiding zero values
            - BBOX: Bounding box based on PCA
            - CONTOUR: Contour outline of foot shape
            - ALL: Return all visualizations in a list.
                   To display each visualization, use a for-loop to iterate over the list of images.
    """
    ALL    = 'ALL'
    PIXEL  = 'PIXEL'
    BLUR   = 'BLUR'
    BINARY = 'BINARY'
    BINARY_NONZERO = 'BINARY_NONZERO'
    BBOX = 'BBOX'
    CONTOUR = 'CONTOUR'

class Visualizer:
    def __init__(self):
        self._scale = 1.0  # default scale

    def scale(self, factor: float):
        self._scale = factor

    def _visualize_with_transparency(self, hex_data: str) -> np.ndarray:
        grid_data = decode_raw(hex_data)
        if grid_data is None:
            logger.error("Failed to visualize: could not decode hex_data.")
            raise ValueError

        grid_data = preprocess_grid_data(grid_data)
        grid_norm = ((grid_data - np.min(grid_data)) / (np.max(grid_data) - np.min(grid_data)) * 255).astype(np.uint8)
        heatmap = cv2.resize(grid_norm, (456, 512), interpolation=cv2.INTER_LINEAR)
        heatmap_blurred = cv2.GaussianBlur(heatmap, (21, 21), 0)

        kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
        sharpened = cv2.filter2D(heatmap_blurred, -1, kernel)

        dst = cv2.applyColorMap(heatmap_blurred, cv2.COLORMAP_JET)
        alpha = np.ones(dst.shape[:2], dtype=np.uint8) * 255
        alpha[heatmap_blurred == np.min(heatmap_blurred)] = 0
        rgba = cv2.cvtColor(dst, cv2.COLOR_BGR2BGRA)
        rgba[:, :, 3] = alpha
        rgba[sharpened < 10, 3] = 0

        scale = self._scale
        target_size = (int(456 * scale), int(512 * scale))
        rgba_resized = cv2.resize(rgba, target_size, interpolation=cv2.INTER_LINEAR)

        return rgba_resized
        # retval, buffer = cv2.imencode('.png', rgba)
        # return buffer.tobytes()

    def _visualize_pixel(self, hex_data: str) -> np.ndarray:
        grid_data = decode_raw(hex_data)
        if grid_data is None:
            logger.error("Failed to visualize: could not decode hex_data.")
            raise ValueError

        grid_data = preprocess_grid_data(grid_data)
        grid_norm = ((grid_data - np.min(grid_data)) / (np.max(grid_data) - np.min(grid_data)) * 255).astype(np.uint8)
        img = cv2.resize(cv2.applyColorMap(grid_norm, cv2.COLORMAP_JET), (456, 512), interpolation=cv2.INTER_NEAREST)

        scale = self._scale
        target_size = (int(456 * scale), int(512 * scale))
        img_resized = cv2.resize(img, target_size, interpolation=cv2.INTER_NEAREST)

        return img_resized

    def _visualize_binary(self, hex_data: str, hide_zeros: bool = False) -> np.ndarray:
        grid_data = decode_raw(hex_data)
        if grid_data is None:
            logger.error("Failed to visualize: could not decode hex_data.")
            raise ValueError

        grid_data = preprocess_grid_data(grid_data)
        rows, cols = grid_data.shape
        base_w, base_h = 456, 512
        cell_w = base_w // cols
        cell_h = base_h // rows

        # Create image canvas
        image = Image.new("RGB", (base_w, base_h), (0, 0, 0))
        draw = ImageDraw.Draw(image)

        try:
            font_size = int(min(cell_w, cell_h) * 0.8)
            font = ImageFont.truetype("SansSerif.ttf", font_size)
        except IOError:
            logger.warning("TrueType font not found. Falling back to default font.")
            font = ImageFont.load_default()

        for i in range(rows):
            for j in range(cols):
                val = int(grid_data[i, j])
                if hide_zeros and val == 0:
                    continue
                text = str(val)
                bbox = font.getbbox(text)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
                x = j * cell_w + 8 + (cell_w - text_width) // 2
                y = i * cell_h + (cell_h - text_height) // 2
                draw.text((x, y), text, fill=(255, 255, 255), font=font)

        # Resize after rendering
        scale = self._scale
        target_size = (int(base_w * scale), int(base_h * scale))
        image_resized = image.resize(target_size, resample=Image.BICUBIC)

        return cv2.cvtColor(np.array(image_resized), cv2.COLOR_RGB2BGR)

    def _visualize_bbox(self, hex_data: str) -> np.ndarray:
        import math
        img = self._visualize_pixel(hex_data)
        grid = decode_raw(hex_data)
        grid = preprocess_grid_data(grid)
        rows, cols = grid.shape
        h, w = img.shape[:2]
        cell_w = w / cols
        cell_h = h / rows

        def draw_pca_box(subgrid, col_offset, color):
            pts_idx = np.argwhere(subgrid > 0)
            if pts_idx.size == 0:
                return
            pts_idx = pts_idx.copy()
            pts_idx[:,1] += col_offset
            pts = np.stack([
                pts_idx[:,1] * cell_w + cell_w/2,
                pts_idx[:,0] * cell_h + cell_h/2
            ], axis=1)
            width, length, cx, cy = pca_bounding_box_dimensions(pts, cell_w, cell_h)
            center = np.array([cx, cy])
            cov = np.cov(pts, rowvar=False)
            eigvals, eigvecs = np.linalg.eigh(cov)
            principal = eigvecs[:, eigvals.argmax()]
            secondary = eigvecs[:, eigvals.argmin()]
            half_len = length / 2
            half_wid = width / 2
            corners = np.array([
                center + principal*half_len + secondary*half_wid,
                center + principal*half_len - secondary*half_wid,
                center - principal*half_len - secondary*half_wid,
                center - principal*half_len + secondary*half_wid
            ]).astype(int)
            cv2.polylines(img, [corners], True, color, 2)

        mid = cols // 2
        # left foot in green
        draw_pca_box(grid[:, :mid], 0, (0,255,0))
        # right foot in blue
        draw_pca_box(grid[:, mid:], mid, (255,0,0))

        return img


    def _visualize_contour(self, hex_data: str) -> np.ndarray:
        img = self._visualize_pixel(hex_data)
        grid = decode_raw(hex_data)
        grid = preprocess_grid_data(grid)
        mask = (grid > 0).astype(np.uint8) * 255
        h, w = img.shape[:2]
        mask_resized = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
        contours, _ = cv2.findContours(mask_resized, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(img, contours, -1, (0,0,255), 2)
        return img

    def visualize(self, hex_data: str, mode: str) -> Union[np.ndarray, bytes, list]:
        if mode == VisualOptions.PIXEL:
            return self._visualize_pixel(hex_data)
        elif mode == VisualOptions.BLUR:
            return self._visualize_with_transparency(hex_data)
        elif mode == VisualOptions.BINARY:
            return self._visualize_binary(hex_data)
        elif mode == VisualOptions.BINARY_NONZERO:
            return self._visualize_binary(hex_data, hide_zeros=True)
        elif mode == VisualOptions.BBOX:
            return self._visualize_bbox(hex_data)
        elif mode == VisualOptions.CONTOUR:
            return self._visualize_contour(hex_data)
        elif mode == VisualOptions.ALL:
            return [
                self._visualize_pixel(hex_data),
                self._visualize_with_transparency(hex_data),
                self._visualize_binary(hex_data),
                self._visualize_binary(hex_data, hide_zeros=True),
                self._visualize_bbox(hex_data),
                self._visualize_contour(hex_data)
            ]
        else:
            raise ValueError(f"Unsupported visualize mode: {mode}")
