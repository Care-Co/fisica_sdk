# import cv2
# # carenco/foot.py
# import math
#
# import numpy as np
# import time
# import logging
# from typing import Optional, Tuple, Union, Dict
# from PIL import Image, ImageDraw, ImageFont
# from numpy import ndarray
#
# logger = logging.getLogger(__name__)
#
#
# class Visualizer:
#     ALL    = 'ALL'
#     PIXEL  = 'PIXEL'
#     BLUR   = 'BLUR'
#     BINARY = 'BINARY'
#     BINARY_NONZERO = 'BINARY_NONZERO'
#     BBOX = 'BBOX'
#     CONTOUR = 'CONTOUR'
#     def __init__(self, foot: 'Foot'):
#         self.foot = foot
#         self._scale = 1.0  # default scale
#
#     def scale(self, factor: float):
#         self._scale = factor
#
#     def _visualize_with_transparency(self, hex_data: str) -> np.ndarray:
#         grid_data = self.foot.decode_raw(hex_data)
#         if grid_data is None:
#             logger.error("Failed to visualize: could not decode hex_data.")
#             raise ValueError
#
#         grid_data = self.foot.preprocess(grid_data)
#         grid_norm = ((grid_data - np.min(grid_data)) / (np.max(grid_data) - np.min(grid_data)) * 255).astype(np.uint8)
#         heatmap = cv2.resize(grid_norm, (456, 512), interpolation=cv2.INTER_LINEAR)
#         heatmap_blurred = cv2.GaussianBlur(heatmap, (21, 21), 0)
#
#         kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
#         sharpened = cv2.filter2D(heatmap_blurred, -1, kernel)
#
#         dst = cv2.applyColorMap(heatmap_blurred, cv2.COLORMAP_JET)
#         alpha = np.ones(dst.shape[:2], dtype=np.uint8) * 255
#         alpha[heatmap_blurred == np.min(heatmap_blurred)] = 0
#         rgba = cv2.cvtColor(dst, cv2.COLOR_BGR2BGRA)
#         rgba[:, :, 3] = alpha
#         rgba[sharpened < 10, 3] = 0
#
#         scale = self._scale
#         target_size = (int(456 * scale), int(512 * scale))
#         rgba_resized = cv2.resize(rgba, target_size, interpolation=cv2.INTER_LINEAR)
#
#         return rgba_resized
#         # retval, buffer = cv2.imencode('.png', rgba)
#         # return buffer.tobytes()
#
#     def _visualize_pixel(self, hex_data: str) -> np.ndarray:
#         grid_data = self.foot.decode_raw(hex_data)
#         if grid_data is None:
#             logger.error("Failed to visualize: could not decode hex_data.")
#             raise ValueError
#
#         grid_data = self.foot.preprocess(grid_data)
#         grid_norm = ((grid_data - np.min(grid_data)) / (np.max(grid_data) - np.min(grid_data)) * 255).astype(np.uint8)
#         img = cv2.resize(cv2.applyColorMap(grid_norm, cv2.COLORMAP_JET), (456, 512), interpolation=cv2.INTER_NEAREST)
#
#         scale = self._scale
#         target_size = (int(456 * scale), int(512 * scale))
#         img_resized = cv2.resize(img, target_size, interpolation=cv2.INTER_NEAREST)
#
#         return img_resized
#
#     def _visualize_binary(self, hex_data: str, hide_zeros: bool = False) -> np.ndarray:
#         grid_data = self.foot.decode_raw(hex_data)
#         if grid_data is None:
#             logger.error("Failed to visualize: could not decode hex_data.")
#             raise ValueError
#
#         grid_data = self.foot.preprocess(grid_data)
#         rows, cols = grid_data.shape
#         base_w, base_h = 456, 512
#         cell_w = base_w // cols
#         cell_h = base_h // rows
#
#         # Create image canvas
#         image = Image.new("RGB", (base_w, base_h), (0, 0, 0))
#         draw = ImageDraw.Draw(image)
#
#         try:
#             font_size = int(min(cell_w, cell_h) * 0.8)
#             font = ImageFont.truetype("SansSerif.ttf", font_size)
#         except IOError:
#             logger.warning("TrueType font not found. Falling back to default font.")
#             font = ImageFont.load_default()
#
#         for i in range(rows):
#             for j in range(cols):
#                 val = int(grid_data[i, j])
#                 if hide_zeros and val == 0:
#                     continue
#                 text = str(val)
#                 bbox = font.getbbox(text)
#                 text_width = bbox[2] - bbox[0]
#                 text_height = bbox[3] - bbox[1]
#                 x = j * cell_w + 8 + (cell_w - text_width) // 2
#                 y = i * cell_h + (cell_h - text_height) // 2
#                 draw.text((x, y), text, fill=(255, 255, 255), font=font)
#
#         # Resize after rendering
#         scale = self._scale
#         target_size = (int(base_w * scale), int(base_h * scale))
#         image_resized = image.resize(target_size, resample=Image.BICUBIC)
#
#         return cv2.cvtColor(np.array(image_resized), cv2.COLOR_RGB2BGR)
#
#     def _visualize_bbox(self, hex_data: str) -> np.ndarray:
#         import math
#         img = self._visualize_pixel(hex_data)
#         grid = self.foot.decode_raw(hex_data)
#         grid = self.foot.preprocess(grid)
#         rows, cols = grid.shape
#         h, w = img.shape[:2]
#         cell_w = w / cols
#         cell_h = h / rows
#
#         def draw_pca_box(subgrid, col_offset, color):
#             pts_idx = np.argwhere(subgrid > 0)
#             if pts_idx.size == 0:
#                 return
#             pts_idx = pts_idx.copy()
#             pts_idx[:,1] += col_offset
#             pts = np.stack([
#                 pts_idx[:,1] * cell_w + cell_w/2,
#                 pts_idx[:,0] * cell_h + cell_h/2
#             ], axis=1)
#             width, length, cx, cy = _pca_bounding_box_dimensions(pts, cell_w, cell_h)
#             center = np.array([cx, cy])
#             cov = np.cov(pts, rowvar=False)
#             eigvals, eigvecs = np.linalg.eigh(cov)
#             principal = eigvecs[:, eigvals.argmax()]
#             secondary = eigvecs[:, eigvals.argmin()]
#             half_len = length / 2
#             half_wid = width / 2
#             corners = np.array([
#                 center + principal*half_len + secondary*half_wid,
#                 center + principal*half_len - secondary*half_wid,
#                 center - principal*half_len - secondary*half_wid,
#                 center - principal*half_len + secondary*half_wid
#             ]).astype(int)
#             cv2.polylines(img, [corners], True, color, 2)
#
#         mid = cols // 2
#         # left foot in green
#         draw_pca_box(grid[:, :mid], 0, (0,255,0))
#         # right foot in blue
#         draw_pca_box(grid[:, mid:], mid, (255,0,0))
#
#         return img
#
#
#     def _visualize_contour(self, hex_data: str) -> np.ndarray:
#         img = self._visualize_pixel(hex_data)
#         grid = self.foot.decode_raw(hex_data)
#         grid = self.foot.preprocess(grid)
#         mask = (grid > 0).astype(np.uint8) * 255
#         h, w = img.shape[:2]
#         mask_resized = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
#         contours, _ = cv2.findContours(mask_resized, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
#         cv2.drawContours(img, contours, -1, (0,0,255), 2)
#         return img
#
#     def visualize(self, hex_data: str, mode: str) -> Union[np.ndarray, bytes, list]:
#         if mode == self.PIXEL:
#             return self._visualize_pixel(hex_data)
#         elif mode == self.BLUR:
#             return self._visualize_with_transparency(hex_data)
#         elif mode == self.BINARY:
#             return self._visualize_binary(hex_data)
#         elif mode == self.BINARY_NONZERO:
#             return self._visualize_binary(hex_data, hide_zeros=True)
#         elif mode == self.BBOX:
#             return self._visualize_bbox(hex_data)
#         elif mode == self.CONTOUR:
#             return self._visualize_contour(hex_data)
#         elif mode == self.ALL:
#             return [
#                 self._visualize_pixel(hex_data),
#                 self._visualize_with_transparency(hex_data),
#                 self._visualize_binary(hex_data),
#                 self._visualize_binary(hex_data, hide_zeros=True),
#                 self._visualize_bbox(hex_data),
#                 self._visualize_contour(hex_data)
#             ]
#         else:
#             raise ValueError(f"Unsupported visualize mode: {mode}")
#
# class Foot:
#     ALL       = 'ALL'
#     DIMENSION = 'DIMENSION'
#     LENGTH    = 'LENGTH'  # 현재는 미구현, 예약
#     WEIGHT    = 'WEIGHT'
#     TIMER     = 'TIMER'
#
#     def __init__(self):
#         pass
#
#     def visual(self) -> Visualizer:
#         return Visualizer(self)
#
#     def decode(self, hex_data: str, args: str = ALL) -> Union[
#         dict[str, float], dict[str, ndarray], dict[str, Union[float, str, None, dict[str, float], ndarray]], dict[
#             str, float], dict[str, dict[str, float]], None]:
#
#         start = time.time()
#         grid_data, weight, battery, _, _ = self._decode_hex(hex_data)
#         left_width, left_height, right_width, right_height = self._calculate_foot_size(hex_data)
#         elapsed = round((time.time() - start) * 1000, 3)  # ms 단위
#         if grid_data is None:
#             logger.error("Failed to decode plantar pressure data: input length mismatch or malformed data.")
#             raise ValueError
#
#         processed_grid = self._preprocess_grid_data(grid_data)
#
#         # 반환
#         if args == self.ALL:
#             return {
#                 "dimension": processed_grid,
#                 "weight": float(weight),
#                 "battery": battery,
#                 "size": {
#                     "left_width": left_width,
#                     "left_height": left_height,
#                     "right_width": right_width,
#                     "right_height": right_height
#                 },
#                 # "extra1": extra1,
#                 # "extra2": extra2,
#                 "timer(ms)": elapsed
#             }
#         elif args == self.DIMENSION:
#             return {"dimension": processed_grid}
#         elif args == self.WEIGHT:
#             return {"weight": float(weight)}
#         elif args == self.TIMER:
#             return {"timer(ms)": elapsed}
#         elif args == self.LENGTH:
#             return { "size": {
#                     "left_width": left_width,
#                     "left_height": left_height,
#                     "right_width": right_width,
#                     "right_height": right_height
#                 }
#             }
#         else:
#             return None
#
#     def _decode_hex(self, hex_data: str) -> Tuple[Optional[np.ndarray], Optional[str], Optional[str], Optional[np.ndarray], Optional[np.ndarray]]:
#         try:
#             data_int = [int(hex_data[i:i+2], 16) for i in range(0, len(hex_data), 2)]
#         except ValueError:
#             logger.warning("Hex decoding failed due to invalid characters.")
#             raise ValueError
#
#         rawData = np.array(data_int)
#
#         if len(rawData) != 645:
#             logger.error(f"Invalid data length: expected 645, got {len(rawData)}")
#             raise ValueError
#
#         weight = f"{rawData[640]}.{rawData[641]}"
#         battery = str(rawData[642])
#         grid_data = rawData[:638].reshape((29, 22))
#         extra1 = rawData[638:640]
#         extra2 = rawData[643:645]
#
#         return grid_data, weight, battery, extra1, extra2
#
#     def _preprocess_grid_data(self, grid_data: np.ndarray) -> np.ndarray:
#         """
#         센서 데이터 전처리 수행
#         """
#         # Step 1: 각 열의 0이 아닌 값 개수로 보정
#         non_zero_counts = np.count_nonzero(grid_data, axis=0)
#         grid_data = np.where(grid_data != 0, grid_data * non_zero_counts, grid_data)
#
#         # Step 2: 구간화
#         non_zero_values = grid_data[grid_data != 0]
#         if non_zero_values.size == 0:
#             return grid_data  # 모두 0인 경우는 그대로 반환
#
#         mean_value = np.mean(non_zero_values)
#         bins = np.linspace(0, 2 * mean_value, 11)
#         bin_midpoints = (bins[:-1] + bins[1:]) / 2
#
#         def assign_to_nearest_bin(value):
#             if value == 0:
#                 return 0
#             bin_idx = min(np.digitize(value, bins) - 1, len(bin_midpoints) - 1)
#             return bin_midpoints[bin_idx]
#
#         grid_data = np.vectorize(assign_to_nearest_bin)(grid_data)
#
#         # Step 3: 255로 스케일링
#         max_value = np.max(grid_data)
#         if max_value > 255:
#             scale_factor = 255 / max_value
#             grid_data = (grid_data * scale_factor).astype(int)
#
#         return grid_data
#
#     def preprocess(self, grid_data: np.ndarray) -> np.ndarray:
#         return self._preprocess_grid_data(grid_data)
#
#     def decode_raw(self, hex_data: str) -> Optional[np.ndarray]:
#         grid_data, *_ = self._decode_hex(hex_data)
#         return grid_data
#
#     # Axis-aligned bounding box (AABB) method (deprecated, replaced by oriented bounding box)
#     # def _find_foot_dimensions(self, foot_data: np.ndarray, cell_width_cm: float, cell_height_cm: float) -> Tuple[float, float]:
#     #     indices = np.argwhere(foot_data > 0)
#     #     if indices.size == 0:
#     #         return 0.0, 0.0
#     #     min_row, min_col = np.min(indices, axis=0)
#     #     max_row, max_col = np.max(indices, axis=0)
#     #     width_cm  = (max_col - min_col + 1) * cell_width_cm
#     #     height_cm = (max_row - min_row + 1) * cell_height_cm
#     #     return width_cm, height_cm
#
#     def _find_foot_dimensions_oriented(self, foot_data: np.ndarray, cell_w_cm: float, cell_h_cm: float) -> Tuple[float, float]:
#         indices = np.argwhere(foot_data > 0)
#         if indices.size == 0:
#             return 0.0, 0.0
#         pts = np.stack([
#             indices[:,1] * cell_w_cm + cell_w_cm/2,
#             indices[:,0] * cell_h_cm + cell_h_cm/2
#         ], axis=1)
#         width, length, _, _ = _pca_bounding_box_dimensions(pts, cell_w_cm, cell_h_cm)
#         return width, length
#
#     def _calculate_foot_size(self, hex_data: str, grid_width_cm: float = 300.0, grid_height_cm: float = 150.0) -> Tuple[float, float, float, float]:
#         # decode and preprocess
#         grid = self.decode_raw(hex_data)
#         if grid is None:
#             raise ValueError("Invalid hex_data")
#         grid = self.preprocess(grid)
#         rows, cols = grid.shape
#         # cell_width_cm = grid_width_cm / cols
#         # cell_height_cm = grid_height_cm / rows
#         cell_width_cm = 1.0
#         cell_height_cm = 1.0
#         left = grid[:, :cols // 2]
#         right = grid[:, cols // 2 :]
#         lw, lh = self._find_foot_dimensions_oriented(left, cell_width_cm, cell_height_cm)
#         rw, rh = self._find_foot_dimensions_oriented(right, cell_width_cm, cell_height_cm)
#         return lw, lh, rw, rh
#
#
# def _pca_bounding_box_dimensions(pts: np.ndarray, cell_w: float, cell_h: float) -> tuple[float, float, float, float]:
#     import math
#     center = pts.mean(axis=0)
#     center[1] += cell_h * 0.7  # Move center slightly downward
#     cov = np.cov(pts, rowvar=False)
#     eigvals, eigvecs = np.linalg.eigh(cov)
#     secondary = eigvecs[:, eigvals.argmin()]
#     # Determine left/right foot by mean x position
#     is_left = pts[:, 0].mean() < (cell_w * 11)
#     offset_dir = 1 if is_left else -1  # Outward direction
#     center += secondary * offset_dir * cell_w * 0.15  # Move slightly outward
#     principal = eigvecs[:, eigvals.argmax()]
#     disp = pts - center
#     proj_pr = disp.dot(principal)
#     proj_sec = disp.dot(secondary)
#     half_len = (proj_pr.max() - proj_pr.min()) / 2
#     half_wid = (proj_sec.max() - proj_sec.min()) / 2
#     diag = math.hypot(cell_w, cell_h) / 2
#     half_len = (half_len + diag) * 1.025
#     half_wid = (half_wid + diag) * 1.025
#     return half_wid * 2, half_len * 2, center[0], center[1]
