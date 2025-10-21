# Fisica SDK - Python Interface

[![Python Version](https://img.shields.io/badge/python-3.8+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20|%20macOS%20-lightgrey.svg)](https://github.com/fisica/fisica_sdk)


### [[Readme_ko (한국어 버전)]](doc/Readme_ko.md)

---

This SDK provides a Python interface to communicate with the Fisica scale device for plantar pressure measurement analysis.

## 🚀 Installation

### Requirements

- Python 3.8 or higher
- Operating System: Windows 10/11 or macOS 10.15+

### Install via pip

```bash
  pip install fisica
```

### Install from source

```bash
  git clone https://github.com/care-co/fisica_sdk.git
  cd fisica_sdk
  pip install -e .
```

### Dependencies

The SDK automatically installs the following dependencies:

```bash
  'numpy>=1.21',
  'opencv-python>=4.5',
  'requests>=2.25',
  'packaging>=21.0',
  'Pillow>=8.0',
  'pyserial>=3.5',
  'bleak>=0.22.3' # For Bluetooth support

  # Dependencies for GUI
  'PyQt5>=5.15',
```

## 🔧 Quick Start

### Basic Usage

```python
# examples/basic_use.py
import fisica_sdk as fisica

# Initialize SDK
sdk = fisica.FisicaSDK()

# Scan for devices
devices = sdk.scan_devices()
print("Available devices:", devices)

# Connect to the first device
if devices:
   sdk.connect(devices[0])

   # Set metadata
   sdk.set_metadata(name="John Doe")

   # Start measurement for 10 seconds
   sdk.start_measurement(duration=10)

   # Wait for completion
   sdk.wait()

   # Analyze results
   report = sdk.analyze()
   print("Analysis complete:", report)

   # Disconnect
   sdk.disconnect()

   # Wait for completion
   sdk.wait()
```

### Real-time Data Monitoring

```python
# examples/realtime_monitoring.py
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
   def __init__(self, scale=2.0):
      super().__init__()
      self.sdk = fisica.FisicaSDK()
      self.monitoring_thread = None
      self.scale = scale
      self.init_ui()
      self.start_monitoring()

   def init_ui(self):
      self.setWindowTitle('Fisica SDK - Realtime Monitor')

      base_img_w, base_img_h = 228, 256
      img_w = int(base_img_w * self.scale)
      img_h = int(base_img_h * self.scale)

      window_w = img_w + 24
      window_h = img_h + 80

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

   def start_monitoring(self):
      self.monitoring_thread = MonitoringThread(self.sdk)
      self.monitoring_thread.frame_received.connect(self.update_visualization)
      self.monitoring_thread.status_updated.connect(self.set_status)
      self.monitoring_thread.start()

   def set_status(self, text):
      self.status_label.setText(text)

   def update_visualization(self, frame):
      try:
         image_array = self.sdk.render(frame, mode="BLUR", bbox=True, scale=self.scale)
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
   window = RealtimeMonitor(scale=2.0)
   window.show()
   sys.exit(app.exec_())

if __name__ == '__main__':
   main()
```

### Batch Processing

```python
# examples/batch_processing.py
import fisica_sdk as fisica

sdk = fisica.FisicaSDK()
devices = sdk.scan_devices()

if devices:
   sdk.connect(devices[0])

   # Multiple measurements
   for i in range(5):
      sdk.set_metadata(id=i, name=f"Test_{i}")
      sdk.start_measurement(duration=5)
      sdk.wait()
      sdk.analyze()
      # Get session data
      frames = sdk.get_session_frames()
      print(f"Session {i}: {len(frames)} frames captured")

      # Reset for next measurement
      sdk.reset_session()

   # Export all reports
   reports = sdk.get_all_reports()
   sdk.export_report(reports, output="measurement_results.json")
   sdk.disconnect()
```

## 📱 Device Connection

### Serial Connection
The SDK automatically detects Fisica devices connected via USB.

### Bluetooth Connection
For Bluetooth(BLE) devices:

1. **Windows**: Ensure Bluetooth is enabled
2. **macOS**: Grant Bluetooth permissions when prompted
   ```

## 📊 Data Analysis

### Available Metrics

The SDK provides comprehensive foot pressure analysis:

- **Pressure Distribution**: Heat maps analysis
- **Center of Pressure (COP)**: Movement tracking
- **Foot Size**: Length and width measurements
- **Weight Distribution**: Left/right foot balance
- **Temporal Analysis**: Pressure changes over time

### Visualization Options

```python
# Different rendering modes
modes = ["PIXEL", "BLUR", "BINARY", "BINARY_NONZERO", "BBOX", "CONTOUR"]

for mode in modes:
    image = sdk.render(frame, mode=mode, bbox=False, scale=2.0)
    # Save or display image
```

## 🎯 Examples

### Command Line Interface

```python
# examples/cli_measurement.py
import fisica_sdk as fisica
import argparse

def main():
   parser = argparse.ArgumentParser(description='Fisica measurement CLI')
   parser.add_argument('--duration', type=int, default=10, help='Measurement duration')
   parser.add_argument('--output', type=str, default='results.json', help='Output file')
   args = parser.parse_args()

   sdk = fisica.FisicaSDK()
   devices = sdk.scan_devices()

   if not devices:
      print("No devices found")
      return

   sdk.connect(devices[0])
   sdk.start_measurement(duration=args.duration)
   sdk.wait()

   report = sdk.analyze()
   print(report)

   sdk.export_report(output=args.output)

if __name__ == "__main__":
   main()
```

### GUI Application

```python
# examples/gui_app.py
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
```

## 🔧 Advanced Configuration

### Async Operations

```python
# Non-blocking measurements
sdk.start_measurement(duration=30)

# Do other work while measuring
for i in range(10):
    sdk.sleep(2, after=lambda: print(f"Checkpoint {i}"))

# Wait for completion
sdk.wait()
```

## 🐛 Troubleshooting

### Common Issues

1. **Device not found**
   ```python
   devices = sdk.scan_devices()
   if not devices:
       print("No devices found. Check connection and permissions.")
   ```

2. **Bluetooth permissions (macOS)**
    - Grant Bluetooth access when prompted

### Debugging

Enable debug logging:

```python
import logging

sdk = fisica.FisicaSDK(debug=logging.DEBUG)
# could be replaced logging.DEBUG to 10
```

## 📚 API Reference

---

### `set_metadata(**kwargs)`

**Description**:  
Sets metadata for the current measurement session.

**Parameters**:
- `id` (Any, optional): Identifier for the user/session.
- `name` (str, optional): Identifier for the user/session.

**Returns**:  
None

---

### `scan_devices()`

**Description**:  
Scans for available scale devices via serial or Bluetooth connection.  
Returns wired device information immediately if a wired device is connected,   
otherwise searches for powered-on scale devices via Bluetooth.  

**Parameters**:  
None

**Returns**:
- `List[Dict]`: List of device dictionaries, each containing connection details.

---

### `connect(device: Dict)`

**Description**:  
Establishes connection to the selected device returned by `scan_devices()`.

**Parameters**:
- `device` (Dict): A dictionary containing device ID and type.

**Returns**:  
None

---

### `disconnect()`

**Description**:  
Disconnects from the connected scale device. Automatically waits for measurement operations to complete before closing the connection.  

**Parameters**:
None

**Returns**:  
None

---

### `start_measurement(duration: Optional[float])`

**Description**:  
Begins capturing measurement data. If `duration` is given, it stops automatically after the duration (in seconds).  
If `duration` is not given, the measurement will not stop automatically. This is not the intended operation,  
so it is not recommended to run the measurement for too long without a duration.  
Set a `duration`, or call the SDK's built-in `sleep()` function or the `sleep()` function from the `time` package to measure for a certain period,  
and then call `stop_measurement()` to end the measurement. The SDK's built-in sleep does not affect the main thread execution.  
Alternatively, you can develop a GUI-based application to automatically end the measurement by `duration` or set up a trigger for `stop_measurement()`.  

**Parameters**:
- `duration` (float, optional): Duration of the measurement in seconds.

**Returns**:  
None

---

### `stop_measurement()`

**Description**:  
Stops the current measurement.

**Parameters**:  
None

**Returns**:  
None

---

### `on_data(callback)`

**Description**:  
Registers a callback function that will be called in real-time as measurement frames are received.

**Parameters**:
- `callback` (callable): You can register a function that takes a single argument of type `MeasurementFrame`.

**Returns**:  
None

---

### `sleep(seconds: float, after: Optional[callable])`

**Description**:  
Asynchronously sleeps for the given time without blocking the main thread. An optional callback can be executed afterward.

**Parameters**:
- `seconds` (float): Number of seconds to sleep.
- `after` (callable, optional): Function to call after sleeping.

**Returns**:  
None

---

### `wait()`

**Description**:  
Waits until all asynchronous operations (e.g., `start_measurement()`, `stop_measurement()`, `sleep()`, `disconnect()`) are completed.

This ensures that all SDK operations that run in the background are fully finished before proceeding to the next step, such as data processing or visualization.

⚠️ It **blocks the main thread** while waiting.

**Parameters**:  
None

**Returns**:  
None

---

### `analyze()`

**Description**:  
Analyzes the current session's captured frames and computes relevant metrics such as foot size and pressure distribution.

This function returns values only immediately after a measurement, and may not work as expected afterward.
To get the most recent report of the current session, it is recommended to call `get_current_report()`.  
Please make sure to call `analyze()` before loading or exporting reports.  

**Parameters**:  
None

**Returns**:
- `Dict`: Dictionary containing metadata, sensor grid, weight, etc.

---

### `get_session_frames()`

**Description**:  
Returns the frame data of the current session in serialized format.

**Parameters**:  
None

**Returns**:
- `List[Dict]`: Serialized frames from the current session.

---

### `get_report()`

**Description**:  
Returns the most recently analyzed session report.

**Parameters**:  
None

**Returns**:
- `Dict`: Dictionary containing metadata, sensor grid, and computed results.

---

### `get_all_reports()`

**Description**:  
Retrieves analysis reports from all completed sessions.

**Parameters**:  
None

**Returns**:
- `List[Dict]`: A list of session reports.

---

### `export_report(report: Optional[Union['SessionReport', list]], output: Optional[str])`

**Description**:  
Exports all reports to a JSON file at the specified output path, filename, or directory.  
Creates missing directories automatically and appends .json extension if not provided   
If target is an existing directory, generates filename using predefined naming rules.  
e.g., "output/sample/result" becomes "output/sample/result.json" if result folder doesn't exist.  

If no argument is provided for report exports information from the most recently analyzed SessionReport.  
Calling this method without having performed `analyze()` may result in an error.  


**Parameters**:  

- `report` (SessionReport, optional): Data obtained through `get_all_reports()` or `get_reports()`. If not provided, outputs based on the most recent SessionReport.
- `output` (str, optional): File path or directory for export.

**Returns**:  
None

---

### `reset_session()`

**Description**:  
Clears the current session (frames and metadata).

**Parameters**:  
None

**Returns**:  
None

---

### `reset_reports()`

**Description**:  
Clears all stored analysis reports from memory.

**Parameters**:  
None

**Returns**:  
None

---

### `render(frame: Optional[MeasurementFrame], grid_data: Optional[np.ndarray], mode: Optional[str], scale: Optional[float])`

**Description**:  
Renders a sensor grid (from a frame or raw grid data) into an image using the specified visualization mode and scale.
`frame` is the variable passed as an argument when a callback function is registered to `on_data`.

**Parameters**:
- `frame` (MeasurementFrame, optional): A dataclass instance containing the sensor matrix.
- `grid_data` (np.ndarray, optional): A 2D NumPy array representing the sensor grid.
- `mode` (str, optional): Visualization style (e.g., "BLUR"). For more details, refer to the VisualOption section below. `default:VisualOptions.BLUR`
- `scale` (float, optional): Scale factor for the image size. `default:1.0`

**Returns**:
- `np.ndarray`: Rendered image as a NumPy array.

---

**🖼️ VisualOptions**

The VisualOptions class defines available rendering modes for visualizing sensor data using FisicaSDK.render().

Rendering modes:
- PIXEL: Pixel heatmap rendering
- BLUR: Smooth heatmap rendering
- BINARY: Binary grid rendering
- BINARY_NONZERO: Binary grid rendering excluding zero values
- BBOX: Bounding box rendering based on BLUR and Principal Component Analysis (PCA)
- CONTOUR: Pixel-based contour rendering
- ALL: Returns all rendering results in list format

---

### `run(scale: Optional[float])`

**Description**:  
Launches the GUI viewer for real-time visualizations with the specified scale.

**Parameters**:
- `scale` (float, optional): Scale factor for the GUI window. `default:1.0`

**Returns**:  
None

---

### `update(image: np.ndarray)`

**Description**:  
Updates the GUI viewer with the given rendered image.

**Parameters**:
- `image` (np.ndarray): The rendered image to display.

**Returns**:
None

---
## 📚 API Reference - Additional Methods


### `set_zero()`

**Description**:  
Calibrates the zero point of the device by setting the current reading as the zero reference point for weight measurements. This is equivalent to the tare function on digital scales and should be used when the scale platform is empty to establish a proper baseline.

**Parameters**:  
None

**Returns**:  
None

**Usage Example**:
```python
# Connect to device first
devices = sdk.scan_devices()
if devices:
    sdk.connect(devices[0])
    
    # Ensure platform is empty, then set zero point
    sdk.set_zero()  # Tare the scale
    
    # Now ready for accurate measurements
    sdk.start_measurement(duration=10)
    
    # Wait for finish measurement
    sdk.wait()
```

**Notes**:
- Device connection is required before calling this method
- Call `connect()` first to establish device connection
- Use this function when the scale platform is empty
- This establishes the baseline for all subsequent weight measurements

---

### `set_scale(value)`

**Description**:  
Sets the scale calibration parameter of the device. This function adjusts the internal calibration value used by the device's weight measurement system to ensure accurate readings.

**Parameters**:
- `value` (int): Scale calibration parameter, must be within the range -32768 to 32767

**Returns**:  
None

**Usage Example**:
```python
# Connect to device first
devices = sdk.scan_devices()
if devices:
    sdk.connect(devices[0])
    
    # Set zero point first
    sdk.set_zero()
    
    # Set scale calibration parameter
    sdk.set_scale(12600)  # Set calibration value to 12600
    
    # Start measurement with calibrated settings
    sdk.start_measurement(duration=10)
    sdk.wait()
```

**Notes**:
- Device connection is required before calling this method
- Call `connect()` first to establish device connection
- Value must be an integer within the range -32768 to 32767
- Use after `set_zero()` for optimal calibration
- Consult device documentation for appropriate calibration values

---

## 🔧 Device Calibration Workflow

For optimal measurement accuracy, follow this calibration sequence:

```python
# examples/calibration.py
import fisica_sdk as fisica

# Initialize and connect
sdk = fisica.FisicaSDK()
devices = sdk.scan_devices()

if devices:
    sdk.connect(devices[0])
    
    # Step 1: Set zero point (empty platform)
    print("Please ensure platform is empty...")
    input("Press Enter when ready...")
    sdk.set_zero()
    print("Zero point calibrated.")
    
    # Step 2: Set scale calibration parameter
    # Use appropriate calibration value for your device
    # (consult device documentation for recommended values)
    calibration_value = 12600  # Example value within -32768 to 32767 range
    sdk.set_scale(calibration_value)
    print(f"Scale calibration set to: {calibration_value}")
    
    # Step 3: Verify calibration with known reference weight
    print("Place a known reference weight on platform...")
    reference_weight = float(input("Enter reference weight (kg): "))
    
    # Take a measurement to verify accuracy
    sdk.start_measurement(duration=10)
    sdk.wait()
    report = sdk.analyze()
    measured_weight = report.weight
    
    print(f"Reference: {reference_weight}kg, Measured: {measured_weight}kg")
    print(f"Accuracy: {abs(measured_weight - reference_weight):.3f}kg difference")
    
    # Step 4: Ready for accurate measurements
    print("Calibration complete. Ready for measurements.")
    sdk.disconnect()
```

---

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/care-co/fisica_sdk/issues)
- **Email**: carencoinc@carenco.kr
- **Website**: [https://carenco.kr](https://carenco.kr/en)

## 🔄 Changelog

### v1.0.0 (Latest)
- Initial release
- Serial and Bluetooth device support
- Real-time data visualization
- Comprehensive pressure analysis

---
