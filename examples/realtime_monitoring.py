import sys
import numpy as np
import cv2
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QLabel
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QPixmap, QImage
import fisica_sdk as fisica

class MonitoringThread(QThread):
    frame_received = pyqtSignal(object)
    status_updated = pyqtSignal(str)

    def __init__(self, sdk):
        super().__init__()
        self.sdk = sdk
        self._is_running = True

    def run(self):
        try:
            self.status_updated.emit('Scanning for devices...')
            devices = self.sdk.scan_devices()

            if not devices:
                self.status_updated.emit('No devices found.')
                return

            self.status_updated.emit(f"Connecting to {devices[0]['name']}...")
            self.sdk.connect(devices[0])
            self.sdk.on_data(self.on_frame)

            self.status_updated.emit('Measurement started.')
            self.sdk.start_measurement()
            
            # Keep the thread alive while measuring
            while self._is_running:
                self.msleep(100)

        except Exception as e:
            self.status_updated.emit(f'Error: {e}')
        finally:
            self.sdk.stop_measurement()
            self.status_updated.emit('Measurement stopped.')

    def on_frame(self, frame):
        if self._is_running:
            self.frame_received.emit(frame)

    def stop(self):
        self._is_running = False
        self.wait() # Wait for the run loop to finish

class RealtimeMonitor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.sdk = fisica.FisicaSDK()
        self.monitoring_thread = None
        self.init_ui()
        self.start_monitoring()

    def init_ui(self):
        self.setWindowTitle('Fisica SDK - Realtime Monitor')
        self.setGeometry(100, 100, 480, 560)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        self.image_label = QLabel('Initializing...')
        self.image_label.setMinimumSize(456, 512)
        self.image_label.setStyleSheet("border: 1px solid black;")
        self.image_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.image_label)

        self.status_label = QLabel('Ready')
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

    def start_monitoring(self):
        self.monitoring_thread = MonitoringThread(self.sdk)
        self.monitoring_thread.frame_received.connect(self.update_visualization)
        self.monitoring_thread.status_updated.connect(self.set_status)
        self.monitoring_thread.start()

    def set_status(self, text):
        self.status_label.setText(text)

    def update_visualization(self, frame):
        try:
            image_array = self.sdk.render(frame, mode="BLUR", bbox=True, scale=2.0)
            self.set_image(image_array)
        except Exception as e:
            error_message = f'Visualization error: {e}'
            self.set_status(error_message)

    def set_image(self, image: np.ndarray):
        try:
            if image.dtype != np.uint8:
                if image.max() <= 1.0:
                    image = (image * 255).astype(np.uint8)
                else:
                    image = np.clip(image, 0, 255).astype(np.uint8)

            if len(image.shape) == 3 and image.shape[2] == 3:
                h, w, ch = image.shape
                image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                qimage = QImage(image_rgb.data, w, h, ch * w, QImage.Format_RGB888)
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
            self.monitoring_thread.stop()
        event.accept()

def main():
    app = QApplication(sys.argv)
    window = RealtimeMonitor()
    window.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()