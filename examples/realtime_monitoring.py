import sys
import time
import json
import os
from pathlib import Path
import numpy as np
import cv2
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QPushButton
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QPixmap, QImage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fisica_sdk as fisica
from fisica_sdk import VisualOptions
from fisica_sdk.analyzer import pca_bounding_box_dimensions


def calculate_pca_foot_dimensions(grid_data):
    grid_data = np.asarray(grid_data)
    _, cols = grid_data.shape
    mid = cols // 2

    def measure(subgrid):
        indices = np.argwhere(subgrid > 0)
        if indices.size == 0:
            return 0.0, 0.0
        pts = np.stack([
            indices[:, 1] + 0.5,
            indices[:, 0] + 0.5,
        ], axis=1)
        width, length, _, _ = pca_bounding_box_dimensions(pts, 1.0, 1.0)
        return width, length

    left_width, left_length = measure(grid_data[:, :mid])
    right_width, right_length = measure(grid_data[:, mid:])
    return left_width, left_length, right_width, right_length

class MonitoringThread(QThread):
    frame_received = pyqtSignal(object)
    status_updated = pyqtSignal(str)
    connection_updated = pyqtSignal(bool)

    def __init__(self, sdk, connected=False):
        super().__init__()
        self.sdk = sdk
        self._is_running = True
        self._connected = connected
        self._measurement_started = False
        self._last_frame_emit = 0.0
        self._frame_interval = 1 / 30
        self._displayed_frame = None
        self._raw_log_path = os.path.join(os.getcwd(), "stable_raw_data.log")

    def run(self):
        try:
            if self._connected:
                self.status_updated.emit('Using existing device connection.')
            else:
                self.status_updated.emit('Scanning for devices...')
                devices = self.sdk.scan_devices()

                if not devices:
                    self.status_updated.emit('No devices found.')
                    return

                self.status_updated.emit(f"Connecting to {devices[0]['name']}...")
                self.sdk.connect(devices[0])
                self._connected = True
                self.connection_updated.emit(True)

            self.sdk.on_data(self.on_frame)
            self.status_updated.emit('Measurement started.')
            self.sdk.start_measurement()
            self._measurement_started = True
            
            # Keep the thread alive while measuring
            while self._is_running:
                self.msleep(100)

        except Exception as e:
            self.status_updated.emit(f'Error: {e}')
        finally:
            self._write_displayed_raw_data()
            if self._measurement_started:
                self.sdk.stop_measurement()
                self.sdk.wait()
                self._measurement_started = False
                self.sdk.reset_session()
            self.status_updated.emit('Monitoring stopped.')

    def on_frame(self, frame):
        now = time.monotonic()
        is_blank = np.count_nonzero(frame.sensor_matrix) == 0
        if self._is_running and (is_blank or now - self._last_frame_emit >= self._frame_interval):
            self._last_frame_emit = now
            self.frame_received.emit(frame)

    def set_displayed_frame(self, frame):
        self._displayed_frame = frame

    def _write_displayed_raw_data(self):
        frame = self._displayed_frame
        if frame is None or not getattr(frame, "raw_data", None):
            return
        raw_data = "".join(f"{value:02X}" for value in frame.raw_data)

        entry = {
            "timestamp": frame.timestamp,
            "weight": frame.weight,
            "battery": frame.battery,
            "source": "displayed_frame",
            "rawData": raw_data,
        }
        try:
            with open(self._raw_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self.status_updated.emit(f"Displayed rawData saved: {self._raw_log_path}")
        except OSError as e:
            self.status_updated.emit(f"Failed to save stable rawData: {e}")

    def request_stop(self):
        self._is_running = False

    def stop(self, timeout_ms=None):
        self.request_stop()
        if timeout_ms is None:
            self.wait() # Wait for the run loop to finish
            return True
        return self.wait(timeout_ms)

class RealtimeMonitor(QMainWindow):
    def __init__(self, scale=2.0):
        super().__init__()
        self.sdk = fisica.FisicaSDK()
        self.monitoring_thread = None
        self.connected = False
        self.scale = scale
        self.init_ui()
        self.start_monitoring()

    def init_ui(self):
        self.setWindowTitle('Fisica SDK - Realtime Monitor')

        base_img_w, base_img_h = 512, 512
        img_w = int(base_img_w * self.scale)
        img_h = int(base_img_h * self.scale)
        
        window_w = img_w + 24 
        window_h = img_h + 104

        self.setGeometry(100, 100, window_w, window_h)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        self.image_label = QLabel('Initializing...')
        self.image_label.setMinimumSize(img_w, img_h)
        self.image_label.setStyleSheet("border: 1px solid black;")
        self.image_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.image_label)

        self.status_label = QLabel('Ready')
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

        self.measurement_label = QLabel('Left L/W: - / -    Right L/W: - / -')
        self.measurement_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.measurement_label)

        controls = QHBoxLayout()
        self.start_btn = QPushButton('Start')
        self.start_btn.clicked.connect(self.start_monitoring)
        controls.addWidget(self.start_btn)

        self.stop_btn = QPushButton('Stop')
        self.stop_btn.clicked.connect(self.stop_monitoring)
        self.stop_btn.setEnabled(False)
        controls.addWidget(self.stop_btn)
        layout.addLayout(controls)

    def start_monitoring(self):
        if self.monitoring_thread and self.monitoring_thread.isRunning():
            return

        self.monitoring_thread = MonitoringThread(self.sdk, connected=self.connected)
        self.monitoring_thread.frame_received.connect(self.update_visualization)
        self.monitoring_thread.status_updated.connect(self.set_status)
        self.monitoring_thread.connection_updated.connect(self.set_connected)
        self.monitoring_thread.finished.connect(self.monitoring_finished)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.monitoring_thread.start()

    def stop_monitoring(self):
        if self.monitoring_thread and self.monitoring_thread.isRunning():
            self.status_label.setText('Stopping...')
            self.stop_btn.setEnabled(False)
            self.monitoring_thread.request_stop()

    def monitoring_finished(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def set_connected(self, connected):
        self.connected = connected

    def set_status(self, text):
        self.status_label.setText(text)

    def update_visualization(self, frame):
        try:
            image_array = self.sdk.render(frame, mode=VisualOptions.BINARY_NONZERO, bbox=True, scale=self.scale)
            self.set_image(image_array)
            self.update_measurements(frame)
            if self.monitoring_thread:
                self.monitoring_thread.set_displayed_frame(frame)
        except Exception as e:
            error_message = f'Visualization error: {e}'
            self.set_status(error_message)

    def update_measurements(self, frame):
        left_width, left_length, right_width, right_length = calculate_pca_foot_dimensions(frame.sensor_matrix)
        self.measurement_label.setText(
            f"Left L/W: {left_length:.1f} / {left_width:.1f}    "
            f"Right L/W: {right_length:.1f} / {right_width:.1f}"
        )

    def set_image(self, image: np.ndarray):
        try:
            if image.dtype != np.uint8:
                if image.max() <= 1.0:
                    image = (image * 255).astype(np.uint8)
                else:
                    image = np.clip(image, 0, 255).astype(np.uint8)

            if len(image.shape) == 2:
                h, w = image.shape
                qimage = QImage(image.data, w, h, image.strides[0], QImage.Format_Grayscale8).copy()
            elif len(image.shape) == 3 and image.shape[2] == 3:
                h, w, ch = image.shape
                image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                qimage = QImage(image_rgb.data, w, h, image_rgb.strides[0], QImage.Format_RGB888).copy()
            elif len(image.shape) == 3 and image.shape[2] == 4:
                h, w, ch = image.shape
                image_rgba = cv2.cvtColor(image, cv2.COLOR_BGRA2RGBA)
                qimage = QImage(image_rgba.data, w, h, image_rgba.strides[0], QImage.Format_RGBA8888).copy()
            else:
                # Handle other formats if necessary, or raise an error
                raise ValueError("Unsupported image format for display.")

            pixmap = QPixmap.fromImage(qimage)
            scaled_pixmap = pixmap.scaled(self.image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.image_label.setPixmap(scaled_pixmap)
        except Exception as e:
            self.set_status(f'Image display error: {e}')

    def closeEvent(self, event):
        if self.monitoring_thread and self.monitoring_thread.isRunning():
            if not self.monitoring_thread.stop(timeout_ms=3000):
                self.set_status('Still stopping. Please try closing again.')
                event.ignore()
                return
        if self.connected:
            self.sdk.disconnect()
            self.sdk.wait()
            self.connected = False
        event.accept()

def main():
    app = QApplication(sys.argv)
    window = RealtimeMonitor(scale=2.0)
    window.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
