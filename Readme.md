# Fisica SDK - Python Interface

This SDK provides a Python interface to communicate with the Fisica scale device for plantar pressure and foot measurement analysis.

## 📦 Key Functions

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

### `start_measurement(duration: int = None)`

**Description**:  
Begins capturing measurement data. If `duration` is given, it stops automatically after the duration (in seconds).
If `duration` is not given, the measurement will not stop automatically. This is not the intended operation,
so it is not recommended to run the measurement for too long without a duration. Set a `duration`, or call the SDK's built-in `sleep()` function or the `sleep()` function from the `time` package to measure for a certain period, and then
call `stop_measurement()` to end the measurement. The SDK's built-in sleep does not affect the main thread execution. Alternatively, you can develop a GUI-based application to automatically end the measurement by `duration`
or set up a trigger for `stop_measurement()`.

**Parameters**:
- `duration` (int, optional): Duration of the measurement in seconds.

**Returns**:  
None

---

### `stop_measurement()`

**Description**:  
Stops the current measurement and finalizes the session data.

**Parameters**:  
None

**Returns**:  
None

---

### `on_data(callback)`

**Description**:  
Registers a callback function that will be called in real-time as measurement frames are received.

**Parameters**:
- `callback` (callable): A function that takes a single argument of type `MeasurementFrame`.

**Returns**:  
None

---


### `sleep(seconds: float, after: Optional[callable] = None)`

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

**Parameters**:  
None

**Returns**:
- `Dict`: Dictionary containing metadata, sensor grid, weight, etc.

---

### `get_session_frames()`

**Description**:  
Returns the current session's raw frame data in a serializable format.

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

### `export_report(output: str)`

**Description**:  
Exports all reports to a JSON file. The path can include a filename or directory; if only a filename is given without an extension, `.json` will be appended automatically.

**Parameters**:
- `output` (str): File path or directory for export.

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

### `render(frame: Optional[MeasurementFrame] = None, grid_data: Optional[np.ndarray] = None, mode: str = "BLUR", scale: float = 1.0)`

**Description**:  
Renders a sensor grid (from a frame or raw grid data) into an image using the specified visualization mode and scale.

**Parameters**:
- `frame` (MeasurementFrame, optional): A dataclass instance containing the sensor matrix.
- `grid_data` (np.ndarray, optional): A 2D NumPy array representing the sensor grid.
- `mode` (str): Visualization style (e.g., "BLUR"). For more details, refer to the VisualOption section below.
- `scale` (float): Scale factor for the image size.

**Returns**:
- `np.ndarray`: Rendered image as a NumPy array.

---

**🖼️ VisualOptions**

The VisualOptions class defines available rendering modes for visualizing sensor data using FisicaSDK.render().

Rendering Modes:
- PIXEL: Color-mapped pixel grid.
- BLUR: Smooth and continuous heatmap rendering.
- BINARY: ASCII-style grid displaying all raw values.
- BINARY_NONZERO: ASCII-style grid omitting zero values.
- BBOX: Draws the bounding box based on PCA of the activated region.
- CONTOUR: Extracts and overlays the contour of the foot shape.
- ALL: Returns all rendering results in a list.

---

### `run(scale: float = 1.0)`

**Description**:  
Launches the GUI viewer for real-time visualizations with the specified scale.

**Parameters**:
- `scale` (float): Scale factor for the GUI window.

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
