import sys
import numpy as np
import cv2
from PyQt5.QtWidgets import QApplication, QLabel, QMainWindow
from PyQt5.QtGui import QPixmap, QImage
from PyQt5.QtCore import Qt, pyqtSignal, QObject


# Add ImageUpdateSignal definition
class ImageUpdateSignal(QObject):
    update = pyqtSignal(np.ndarray)


class ImageUpdater(QObject):
    update_signal = pyqtSignal(np.ndarray)

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.update_signal.connect(self.window.set_image)

    def update(self, image: np.ndarray):
        self.update_signal.emit(image)


class ImageWindow(QMainWindow):
    def __init__(self, width, height):
        super().__init__()
        self.setWindowTitle("Fisica Scale")
        self.label = QLabel(self)
        self.setCentralWidget(self.label)
        self.resize(width, height)

    def set_image(self, image: np.ndarray):
        if len(image.shape) == 2:
            h, w = image.shape
            qimage = QImage(image.data, w, h, w, QImage.Format_Grayscale8)
        elif image.shape[2] == 3:
            h, w, ch = image.shape
            qimage = QImage(image.data, w, h, ch * w, QImage.Format_BGR888)
        elif image.shape[2] == 4:
            image = cv2.cvtColor(image, cv2.COLOR_BGRA2RGBA)
            h, w, ch = image.shape
            qimage = QImage(image.data, w, h, ch * w, QImage.Format_RGBA8888)
        else:
            raise ValueError("Unsupported image format.")
        pixmap = QPixmap.fromImage(qimage)
        self.label.setPixmap(pixmap)
        self.label.setAlignment(Qt.AlignCenter)

_window = None

def setup_gui(scale: float = 1.0):
    global _window
    app = QApplication(sys.argv)
    _window = ImageWindow(int(456 * scale), int(512 * scale))
    _window.show()
    return app

def update_gui(image: np.ndarray):
    if _window:
        _window.set_image(image)