import sys
import numpy as np
import cv2
import fisica_sdk as fisica
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QPushButton, QLabel, QTextEdit
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QPixmap, QImage

class MeasurementThread(QThread):
    frame_received = pyqtSignal(object)

    def __init__(self, sdk):
        super().__init__()
        self.sdk = sdk
        self.sdk.on_data(self.on_frame)

    def on_frame(self, frame):
        self.frame_received.emit(frame)

    def run(self):
        self.sdk.start_measurement(duration=10)
        self.sdk.wait()

class FisicaGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.sdk = fisica.FisicaSDK()
        self.devices = []
        self.measurement_thread = None
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle('Fisica SDK GUI')
        self.setGeometry(100, 100, 800, 600)

        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Status label
        self.status_label = QLabel('Ready')
        layout.addWidget(self.status_label)

        # Buttons
        self.scan_btn = QPushButton('Scan Devices')
        self.scan_btn.clicked.connect(self.scan_devices)
        layout.addWidget(self.scan_btn)

        self.connect_btn = QPushButton('Connect')
        self.connect_btn.clicked.connect(self.connect_device)
        self.connect_btn.setEnabled(False)
        layout.addWidget(self.connect_btn)

        self.start_btn = QPushButton('Start Measurement')
        self.start_btn.clicked.connect(self.start_measurement)
        self.start_btn.setEnabled(False)
        layout.addWidget(self.start_btn)

        # Device list
        self.device_text = QTextEdit()
        self.device_text.setMaximumHeight(100)
        layout.addWidget(self.device_text)

        # Visualization area
        self.image_label = QLabel('Pressure visualization will appear here')
        self.image_label.setMinimumHeight(400)
        self.image_label.setStyleSheet("border: 1px solid black;")
        self.image_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.image_label)

    def scan_devices(self):
        self.status_label.setText('Scanning devices...')
        self.devices = self.sdk.scan_devices()

        if self.devices:
            device_info = '\n'.join([f"{i}: [{d['type']}] {d['name']} - {d['id']}"
                                     for i, d in enumerate(self.devices)])
            self.device_text.setText(device_info)
            self.connect_btn.setEnabled(True)
            self.status_label.setText(f'Found {len(self.devices)} devices')
        else:
            self.device_text.setText('No devices found')
            self.status_label.setText('No devices found')

    def connect_device(self):
        if self.devices:
            try:
                self.sdk.connect(self.devices[0])  # Connect to first device
                self.status_label.setText(f'Connected to {self.devices[0]["name"]}')
                self.start_btn.setEnabled(True)
                self.connect_btn.setEnabled(False)
            except Exception as e:
                self.status_label.setText(f'Connection failed: {e}')

    def start_measurement(self):
        if self.measurement_thread and self.measurement_thread.isRunning():
            return

        self.status_label.setText('Starting measurement...')
        self.start_btn.setEnabled(False)

        self.measurement_thread = MeasurementThread(self.sdk)
        self.measurement_thread.frame_received.connect(self.update_visualization)
        self.measurement_thread.finished.connect(self.measurement_finished)
        self.measurement_thread.start()

    def set_image(self, image: np.ndarray):
        """Improved image display method based on the provided code"""
        try:
            if len(image.shape) == 2:
                # Grayscale image
                h, w = image.shape
                qimage = QImage(image.data, w, h, w, QImage.Format_Grayscale8)
            elif len(image.shape) == 3:
                if image.shape[2] == 3:
                    # RGB/BGR image
                    h, w, ch = image.shape
                    # Convert BGR to RGB if needed
                    if image.dtype == np.uint8:
                        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                    else:
                        image_rgb = image
                    qimage = QImage(image_rgb.data, w, h, ch * w, QImage.Format_RGB888)
                elif image.shape[2] == 4:
                    # RGBA image
                    image = cv2.cvtColor(image, cv2.COLOR_BGRA2RGBA)
                    h, w, ch = image.shape
                    qimage = QImage(image.data, w, h, ch * w, QImage.Format_RGBA8888)
                else:
                    raise ValueError("Unsupported image format.")
            else:
                raise ValueError("Unsupported image format.")

            pixmap = QPixmap.fromImage(qimage)
            # Scale to fit the label while maintaining aspect ratio
            scaled_pixmap = pixmap.scaled(self.image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.image_label.setPixmap(scaled_pixmap)

        except Exception as e:
            print(f"Image display error: {e}")
            self.status_label.setText(f'Image display error: {e}')

    def update_visualization(self, frame):
        try:
            # Render the frame
            image_array = self.sdk.render(frame, mode="BLUR", scale=2.0)

            # Ensure the image is in the correct format
            if image_array.dtype != np.uint8:
                # Normalize to 0-255 range if needed
                if image_array.max() <= 1.0:
                    image_array = (image_array * 255).astype(np.uint8)
                else:
                    image_array = np.clip(image_array, 0, 255).astype(np.uint8)

            # Use the improved image display method
            self.set_image(image_array)

        except Exception as e:
            print(f"Visualization error: {e}")
            self.status_label.setText(f'Visualization error: {e}')

    def measurement_finished(self):
        self.status_label.setText('Measurement completed')
        self.start_btn.setEnabled(True)

        # Analyze results
        try:
            report = self.sdk.analyze()
            print("Analysis completed:", report)
        except Exception as e:
            print(f"Analysis error: {e}")

def main():
    app = QApplication(sys.argv)
    window = FisicaGUI()
    window.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()