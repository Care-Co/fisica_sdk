import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np
import fisica_sdk as fisica
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QApplication, QLabel, QMainWindow, QPushButton, QTextEdit, QVBoxLayout, QWidget


class ScanThread(QThread):
    devices_found = pyqtSignal(list)
    scan_failed = pyqtSignal(str)

    def __init__(self, sdk):
        super().__init__()
        self.sdk = sdk

    def run(self):
        try:
            self.devices_found.emit(self.sdk.scan_devices())
        except Exception as e:
            self.scan_failed.emit(str(e))


class MeasurementThread(QThread):
    frame_received = pyqtSignal(object)
    measurement_failed = pyqtSignal(str)

    def __init__(self, sdk, duration=10):
        super().__init__()
        self.sdk = sdk
        self.duration = duration
        self.sdk.on_data(self.on_frame)

    def on_frame(self, frame):
        self.frame_received.emit(frame)

    def run(self):
        try:
            self.sdk.start_measurement(duration=self.duration)
            self.sdk.wait()
        except Exception as e:
            self.measurement_failed.emit(str(e))


class FisicaGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.sdk = fisica.FisicaSDK()
        self.devices = []
        self.connected = False
        self.scan_thread = None
        self.measurement_thread = None
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("Fisica SDK GUI")
        self.setGeometry(100, 100, 800, 600)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        self.status_label = QLabel("Ready")
        layout.addWidget(self.status_label)

        self.scan_btn = QPushButton("Scan Devices")
        self.scan_btn.clicked.connect(self.scan_devices)
        layout.addWidget(self.scan_btn)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.connect_device)
        self.connect_btn.setEnabled(False)
        layout.addWidget(self.connect_btn)

        self.start_btn = QPushButton("Start Measurement")
        self.start_btn.clicked.connect(self.start_measurement)
        self.start_btn.setEnabled(False)
        layout.addWidget(self.start_btn)

        self.device_text = QTextEdit()
        self.device_text.setMaximumHeight(100)
        self.device_text.setReadOnly(True)
        layout.addWidget(self.device_text)

        self.image_label = QLabel("Pressure visualization will appear here")
        self.image_label.setMinimumHeight(400)
        self.image_label.setStyleSheet("border: 1px solid black;")
        self.image_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.image_label)

    def scan_devices(self):
        if self.scan_thread and self.scan_thread.isRunning():
            return

        self.status_label.setText("Scanning devices...")
        self.scan_btn.setEnabled(False)
        self.connect_btn.setEnabled(False)
        self.scan_thread = ScanThread(self.sdk)
        self.scan_thread.devices_found.connect(self.scan_finished)
        self.scan_thread.scan_failed.connect(self.scan_failed)
        self.scan_thread.finished.connect(lambda: self.scan_btn.setEnabled(True))
        self.scan_thread.start()

    def scan_finished(self, devices):
        self.devices = devices
        if self.devices:
            device_info = "\n".join(
                f"{i}: [{d['type']}] {d['name']} - {d['id']}" for i, d in enumerate(self.devices)
            )
            self.device_text.setText(device_info)
            self.connect_btn.setEnabled(not self.connected)
            self.status_label.setText(f"Found {len(self.devices)} devices")
        else:
            self.device_text.setText("No devices found")
            self.status_label.setText("No devices found")

    def scan_failed(self, message):
        self.device_text.setText("")
        self.status_label.setText(f"Scan failed: {message}")

    def connect_device(self):
        if not self.devices:
            return

        try:
            self.sdk.connect(self.devices[0])
            self.connected = True
            self.status_label.setText(f'Connected to {self.devices[0]["name"]}')
            self.start_btn.setEnabled(True)
            self.connect_btn.setEnabled(False)
        except Exception as e:
            self.status_label.setText(f"Connection failed: {e}")

    def start_measurement(self):
        if not self.connected:
            self.status_label.setText("Connect to a device first.")
            return
        if self.measurement_thread and self.measurement_thread.isRunning():
            return

        self.status_label.setText("Starting measurement...")
        self.start_btn.setEnabled(False)

        self.measurement_thread = MeasurementThread(self.sdk, duration=10)
        self.measurement_thread.frame_received.connect(self.update_visualization)
        self.measurement_thread.measurement_failed.connect(self.measurement_failed)
        self.measurement_thread.finished.connect(self.measurement_finished)
        self.measurement_thread.start()

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
                h, w, _ = image.shape
                image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                qimage = QImage(image_rgb.data, w, h, image_rgb.strides[0], QImage.Format_RGB888).copy()
            elif len(image.shape) == 3 and image.shape[2] == 4:
                h, w, _ = image.shape
                image_rgba = cv2.cvtColor(image, cv2.COLOR_BGRA2RGBA)
                qimage = QImage(image_rgba.data, w, h, image_rgba.strides[0], QImage.Format_RGBA8888).copy()
            else:
                raise ValueError("Unsupported image format.")

            pixmap = QPixmap.fromImage(qimage)
            scaled_pixmap = pixmap.scaled(self.image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.image_label.setPixmap(scaled_pixmap)
        except Exception as e:
            self.status_label.setText(f"Image display error: {e}")

    def update_visualization(self, frame):
        try:
            image_array = self.sdk.render(frame, mode="BLUR", scale=2.0)
            self.set_image(image_array)
        except Exception as e:
            self.status_label.setText(f"Visualization error: {e}")

    def measurement_failed(self, message):
        self.status_label.setText(f"Measurement failed: {message}")

    def measurement_finished(self):
        self.status_label.setText("Measurement completed")
        self.start_btn.setEnabled(True)
        try:
            report = self.sdk.analyze()
            if report is None:
                self.status_label.setText("Measurement completed; analysis skipped.")
            else:
                print("Analysis completed:", report)
        except Exception as e:
            self.status_label.setText(f"Analysis error: {e}")

    def closeEvent(self, event):
        if self.measurement_thread and self.measurement_thread.isRunning():
            self.measurement_thread.wait(3000)
        if self.connected:
            self.sdk.disconnect()
            self.sdk.wait()
            self.connected = False
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = FisicaGUI()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
