import asyncio
import time
import threading
import numpy as np

from .analyzer import analyze as global_analyzer
from .visualizer import render as global_render
from .receiver import Receiver
from .exporter import *
from .models import *
from .device import *
from .utils import *

logger = logging.getLogger(__name__)



class FisicaSDK:
    """
    FisicaSDK - Main interface for interacting with the scale device.

    Available Methods:
        - set_metadata(**kwargs): Set custom metadata for the current measurement session (e.g., id, name).
        - scan_devices(): Scan for available serial and Bluetooth devices.
        - connect(device: dict): Connect to a selected device from scan_devices().
        - disconnect(): Disconnect from the currently connected device.
        - start_measurement(duration: Optional[float] = None): Begin data collection. Optionally stop after a given duration.
        - stop_measurement(): Stop ongoing data collection.
        - on_data(callback: Callable): Register a callback function to receive real-time frame data.
        - sleep(seconds: float, after: Optional[Callable]): Non-blocking delay with optional callback after delay.
        - wait(): Wait until all internal states (starting, stopping, working, sleeping, disconnecting) are cleared.
        - set_zero(): Calibrate the zero point of the device.
        - set_scale(value): Adjust the weight calibration of the device.
        - analyze(): Analyze collected session frames and generate a session report.
        - get_session_frames(): Retrieve serialized raw frames from the current session.
        - get_report(): Retrieve the most recent analysis report.
        - get_all_reports(): Retrieve all previous session reports stored in memory.
        - export_report(report: Optional[Union[SessionReport, list]], output: Optional[str]): Save the current or specified report(s) to a JSON file.
        - reset_session(): Clear the current session's metadata and frame data.
        - reset_reports(): Clear all stored reports in memory.
        - render(frame: Optional[MeasurementFrame], grid_data: Optional[np.ndarray], mode: Optional[str], scale: Optional[float]): Render a visualization image from raw sensor data.

    Note:
        `time.sleep()` pauses the main thread, which may block further execution.
        If you are running code that requires the main thread (e.g., GUI applications),
        avoid using `time.sleep()` between `start_measurement()` and `stop_measurement()`.
        Instead, use `sdk.sleep()` or pass the duration parameter to `start_measurement()`.
        When passing a `duration` parameter to `start_measurement()`, `stop_measurement()` is automatically executed,
        so there's no need to call `stop_measurement()` separately in the main thread.

    For more information:
        Append .__doc__ to the method name and call print to view the documentation.
        e.g.    print(FisicaSDK.start_measurement.__doc__)
    """

    def __init__(self, debug=logging.INFO):
        self._disconnecting = False
        self._connected = False
        self._sleeping = False
        self._starting = False
        self._stopping = False
        self._available_devices = None
        self._connected_device = None
        self._current_report = None
        self._start_time = None
        self._callback = None
        self._duration = None
        self._receiver = None
        self._session = None
        self._working = None
        self._loop = None
        self._reports = []
        self._frames = []
        self._counter = 0
        self._debug = debug
        log.level = self._debug

    def _set_metadata(self, **kwargs):
        default_metadata = {
            'id': None,
            'name': None
        }

        for key in kwargs:
            if key not in default_metadata:
                raise ValueError(f"Unexpected metadata field: {key}")
            default_metadata[key] = kwargs[key]
        if default_metadata["id"] is None:
            import datetime
            default_metadata["id"] = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

        self._session = MeasurementSession(metadata=default_metadata, frames=[])
        log.info(f"Metadata set: {default_metadata}")

    def _handle_frame(self, frame):
        self._frames.append(frame)
        log.info("Frame received:", frame)
        if self._callback:
            self._callback(frame)

    async def _start_with_timeout(self, duration):
        await self._start_async()
        self.sleep(duration, after=lambda: asyncio.run_coroutine_threadsafe(self._stop_async(), self._loop))

    async def _start_async(self):
        while self._stopping:
            time.sleep(0.1)
        if not self._session:
            self._set_metadata()
        if self._receiver:
            log.debug("Initializing async data receiving loop...")
            self._start_time = time.time()
            self._frames.clear()
            self._receiver.start()
            self._working = True
            self._starting = False
        log.info("Measurement started.")

    async def _stop_async(self):
        try:
            if self._receiver:
                self._receiver.stop()
            if self._session:
                self._session.frames.extend(self._frames)
            log.info("Measurement stopped and session frozen.")
            self._working = False
            self._stopping = False
            self._duration = time.time() - self._start_time
        except Exception as e:
            log.critical(
                "\nFisica SDK\n"
                f"  Unknown error occurred while stopping measurement : {e}"
            )
            raise RuntimeError

    def sleep(self, seconds: float, after: Optional[callable] = None):
        """
    Fisica SDK: sleep

        Parameters:
            seconds (float): Duration to wait in seconds.
            after (callable, optional): Function to call after sleep ends.

        Notes:
           This function is used as an alternative to `time.sleep()` for controlling the measurement interval
           between `start_measurement()` and `stop_measurement()` when executing tasks that should not block
           the main thread (such as GUI programs).

           Since this applies only to these two functions and not to other Fisica SDK features, please use
           the built-in sleep function with caution.

           Since `stop_measurement()` also executes asynchronously, if you need to ensure that
           `stop_measurement()` has completed execution, please call the built-in `wait()` function,
           which will block the main thread.

        """
        def _delayed():
            self._sleeping = True
            time.sleep(seconds)
            if after:
                after()
            self._sleeping = False
        threading.Thread(target=_delayed, daemon=True).start()

    def set_metadata(self, **kwargs):
        """
    Fisica SDK: set_metadata

        Parameters:
            id (Any, optional): Identifier for the metadata.
            name (Any, optional): Name for the metadata.

        Notes:
            The id is automatically generated based on the current time if not specified.
            The name defaults to None if not specified.
            While no specific format is required, it is recommended to use numeric
            or string formats for both parameters.
        """
        self._set_metadata(**kwargs)

    def scan_devices(self):
        """
    Fisica SDK: scan_devices

        Returns:
            list: List of available Scale devices

        Notes:
            Scans for Scale devices connected via Bluetooth or wired connection.
            If a wired Scale device is available, only that device information is returned.
            If no wired Scale device is found, searches for Bluetooth devices.
            Before connecting a device with `connect()`, you must pass the device obtained
            through `scan_devices()` as an argument.
            The return value is in list format, so use array indexing like `device[0]`
            to attempt connection with a specific device.
        """
        log.info("Scanning for available devices...")
        devices = scan()
        if not devices:
            log.error(
                "\nFisica SDK\n"
                "   No devices found. Please check if the device is powered on.\n"
                "   For comprehensive information about Fisica SDK, please refer to the Readme or call `print(FisicaSDK.__doc__)`.\n"
                "   For further operation details, call `print(FisicaSDK.scan_devices.__doc__)`.\n"
            )
            raise RuntimeError
        self._available_devices = devices
        return devices

    def connect(self, device):
        """
    Fisica SDK: connect

        Parameters:
            device: Device object obtained from scan_devices()

        Notes:
            Attempts to connect to a specific Scale device.
            Use the return value from `scan_devices()` for the device parameter.
            For detailed information, print `FisicaSDK.scan_devices.__doc__`.
        """
        try:
            if not isinstance(device, dict):
                raise TypeError("Device must be a dictionary returned from scan_devices().")

            # device_id = device.get("id")
            log.info(f"Attempting to connect to Fisica Scale ({device.get('type')}) device..")

            if not hasattr(self, '_available_devices') or not self._available_devices:
                raise RuntimeError("No devices scanned. Call scan_devices() first.")

            self._connected_device = device.get('id')
            self._connected = True
            self._receiver = Receiver(device, self._handle_frame)
            self._receiver.conn()

        except Exception as e:
            log.error(
                "\nFisica SDK\n"
                "   It seems that no device was found, the connection was lost, or an invalid parameter was provided.\n"
                "   Please make sure to call `scan_devices()` before using this method.\n"
                "   To learn how to retrieve the list of devices, call `print(FisicaSDK.scan_devices.__doc__)`\n"
                "   To learn how to connect after retrieving devices, refer to general information in `print(FisicaSDK.__doc__)`\n"
                "   or call `print(FisicaSDK.connect.__doc__)`\n"
            )
            raise e
    def disconnect(self):
        """
    Fisica SDK: disconnect

        Notes:
            Disconnects from the currently connected Scale device.
            This method does not block the main thread execution.
            If you need to ensure completion before executing subsequent methods
            like `analyze()` or `get_report()`, call the built-in `wait()` function.

            After disconnection, you must call `connect()` again to execute
            measurement processes on the scale. Use this method when you need to
            change device connections after initially connecting to a scale.

            Attempting to connect to another device without disconnecting first
            may cause unintended behavior and is not recommended.
        """
        def _disconnect():
            while self._working or self._stopping:
                time.sleep(0.1)

            self._disconnecting = True
            if not self._connected_device:
                log.error("No device was detected to disconnect.")
                self._disconnecting = False
                return
            try:
                log.info(f"Attempting to disconnect to device: {self._connected_device}")
                if self._receiver:
                    self._receiver.discon()
                self._connected_device = None
                self._connected = False
            except Exception as e:
                log.error(f"Error occurred during device disconnection. : {e}")
            finally:
                log.info("Scale disconnected.")
                self._disconnecting = False

        threading.Thread(target=_disconnect, daemon=True).start()

    def start_measurement(self, duration=None):
        """
    Fisica SDK: start_measurement

        Parameters:
            duration (float, optional): Duration (in seconds) to automatically stop the measurement after starting.
                                        It is recommended to use an integer value.

        Notes:
            - You can use like `sdk.start_measurement(duration=5)` to run for 5 seconds and automatically stop.
            - This prevents blocking the main thread (e.g., using time.sleep()).
            - When duration is specified, there is no need to call stop_measurement() manually.
            - Make sure to call scan_devices() and connect() before using this method to establish a device connection.
        """
        self._starting = True
        if not self._connected:
            log.error(
                "\nFisica SDK\n"
                "   The device is either not connected or the connection has been lost.\n"
                "   Please make sure to call `connect` before calling `start_measurement()`.\n"
                "   For general information about the Fisica SDK, call `print(FisicaSDK.__doc__)`.\n"
                "   For guidance on device scanning and connection, use:\n"
                "   `print(FisicaSDK.scan_devices.__doc__)` and `print(FisicaSDK.connect.__doc__)`.\n"
            )
            self._starting = False
            raise RuntimeError("Not connected to a device.")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.create_task(self._start_async())
        self._loop = loop

        if duration:
            self.sleep(duration, after=lambda: asyncio.run_coroutine_threadsafe(self._stop_async(), self._loop))

        threading.Thread(target=loop.run_forever, daemon=True).start()

    def stop_measurement(self):
        """
    Fisica SDK: stop_measurement

        Notes:
            Stops the measurement process. This method does not block the main thread,
            which may cause unintended errors in subsequent methods like `analyze()`
            that should be executed after measurement completion.

            To ensure the measurement stop operation is completed, use the built-in
            `wait()` function. Note that this will block the main thread execution.
        """
        if getattr(self, "_sleeping", False):
            log.debug("Stop requested during sleep; deferring stop.")

            def _wait_and_stop():
                while getattr(self, "_sleeping", False):
                    self._counter = (self._counter + 1) % 25
                    if not self._counter:
                        log.debug("sleep in the stop_measurement()")
                    time.sleep(0.1)
                asyncio.run_coroutine_threadsafe(self._stop_async(), self._loop)

            threading.Thread(target=_wait_and_stop, daemon=True).start()
            return

        self._stopping = True
        # sleep 중이 아니면 즉시 종료
        if hasattr(self, "_loop") and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._stop_async(), self._loop)
    def on_data(self, callback):
        """
    Fisica SDK: on_data

        Parameters:
            callback (function): User-defined callback function to handle plantar pressure data

        Notes:
            Registers a user-defined function as a callback through this `on_data()` method.
            When plantar pressure data is received from the Scale, this function calls
            the registered callback function with the plantar pressure data as an argument.
        """
        self._callback = callback
        log.debug("Callback for real-time data set.")

    def get_session_frames(self):
        """
    Fisica SDK: get_session_frames

        Returns:
            Serialized plantar pressure frame data from the current session

        Notes:
            Returns serialized plantar pressure frame data from the current session.
            Note that this must be called after measurement has stopped.
        """

        if self._session:
            return serialize_session_frame(self._session)
        else:
            log.info("It seems there is no active session.")

    def get_report(self):
        """
    Fisica SDK: get_report

        Returns:
            Serialized SessionReport information from the most recent analysis

        Notes:
            Returns serialized SessionReport information from the most recently
            executed `analyze()`. Note that this function must be called after
            the current session measurement has ended and `analyze()` has been executed.
        """
        try:
            if not self._current_report:
                raise
            return [serialize_session_report(self._current_report)]
        except Exception as e:
            log.error(
                "\nFisica SDK\n"
                "   It seems that analyze has not been run."
                "   Please make sure to call analyze before calling get_report."
                "   For general information about the Fisica SDK, call `print(FisicaSDK.__doc__)`.\n"
                "   For guidance on device scanning and connection, use:\n"
                "   `print(FisicaSDK.analyze.__doc__)` and `print(FisicaSDK.get_report.__doc__)`.\n"
            )


    def get_all_reports(self):
        """
    Fisica SDK: get_all_reports

        Returns:
           list: Serialized list of all analyzed SessionReport objects

        """
        return [serialize_session_report(r) for r in self._reports]

    def set_zero(self):
        """
    Fisica SDK: set_zero

        Calibrates the zero point of the device.

        This function sets the current reading as the zero reference point for
        weight measurements. This is typically used to establish a baseline
        when the scale platform is empty for accurate weight calibration.

        Notes:
            - Device connection is required before calling this method
            - Call connect() first to establish device connection
            - Use this function when the scale platform is empty to set proper zero point
            - This calibrates the fundamental zero reference point of the sensor
        """
        if self._receiver:
            self._receiver.zeroset()
        else:
            log.debug(
                "\nFisica SDK\n"
                "   Device is not connected.\n"
                "   You must establish a connection to the device using `connect()` before calling `set_zero()`.\n"
            )
    def set_scale(self, value):
        """
    Fisica SDK: set_scale

        Adjusts the weight calibration of the device.

        This function allows you to modify the scale value to correct measurement
        errors in weight readings. The device must be connected via connect()
        before using this function.

        Parameters:
            value: Scale calibration value to adjust weight measurement accuracy

        Notes:
            - The value must be within the range -32768 to 32767
            - Device connection is required before calling this method
            - Use this to fine-tune weight measurement precision
            - Call connect() first to establish device connection
        """
        if self._receiver:
            self._receiver.setScale(value)
        else:
            log.debug(
                "\nFisica SDK\n"
                "   Device is not connected.\n"
                "   You must establish a connection to the device using `connect()` before calling `set_scale()`.\n"
            )


    def export_report(self, report: Optional[Union['SessionReport', list]] = None, output: Optional[str] = None):
        """
    Fisica SDK: export_report

        Save the report(s) to a JSON file.

        Parameters:
            report (Optional[Union['SessionReport', list]]): A single SessionReport or a list of SessionReport objects.
                       If None, export the most recent session report.

            output (Optional[str]): Output can be a directory, a filename, or a full file path.

        Notes:
            Exports all reports to a JSON file at the specified output path, filename, or directory.
            Creates missing directories automatically and appends .json extension if not provided
            If target is an existing directory, generates filename using predefined naming rules.
            If no argument is provided for report exports information from the most recently analyzed SessionReport.
            Calling this method without having performed `analyze()` may result in an error.
            e.g., "output/sample/result" becomes "output/sample/result.json" if result folder doesn't exist.

        """
        try:
            if report is None:
                if not self._current_report:
                    log.error(
                        "\nFisica SDK\n"
                        "   It seems that analyze has not been run."
                        "   Please make sure to call analyze before calling export_report."
                        "   For general information about the Fisica SDK, call `print(FisicaSDK.__doc__)`.\n"
                        "   For guidance on device scanning and connection, use:\n"
                        "   `print(FisicaSDK.analyze.__doc__)` and `print(FisicaSDK.export_report.__doc__)`.\n"
                    )
                else:
                    report = [serialize_session_report(self._current_report)]

            output_path = resolve_output_path(output)
            return export_session_reports_to_json(report, output_path)

        except Exception as e:
            log.error(
                "\nFisica SDK\n"
                "   Export failed due to an invalid parameter format or an error occurred during processing."
            )
            raise e

    def reset_session(self):
        """
    Fisica SDK: reset_session

        Notes:
            Resets the current session. You must call `analyze()` before performing this operation
            to ensure the current session is properly reflected when performing subsequent operations like export.
        """

        self._session = None
        self._current_report = None
        log.info("Session reset for next measurement.")

    def reset_reports(self):
        """
    Fisica SDK: reset_reports

        Notes:
           Clears all SessionReport objects obtained through `analyze()`.
           It is recommended to save all necessary information, such as using
           `export_report()`, before calling this method.
        """
        self._reports.clear()
        log.info("All reports reset.")

    def analyze(self):
        """
    Fisica SDK: analyze

        Returns:
            SessionReport: Generated report based on current session data

        Notes:
            Generates a SessionReport based on the plantar pressure frame data
            stored in the current session. While this function returns the
            SessionReport on first execution, it clears the plantar pressure
            data (excluding metadata) from the current session to allow for
            re-measurement.

            Calling this function again on the same session without re-measurement
            may cause unexpected errors. Use `get_report()` to retrieve the
            SessionReport instead.
        """
        if not self._session or not self._session.frames:
            log.warning("It seems that measurement was not performed or data was not collected. Analyze will be skipped.")
            return

        metadata = self._session.metadata
        frames = self._session.frames

        result = global_analyzer(frames)

        if not result:
            log.warning(
                "\nFisica SDK\n"
                "   No frame data was collected.\n"
                "   If this message appears even after taking a measurement, try stepping onto the device before starting the measurement, or increase the measurement duration."
                "   Please make sure to call `start_measurement()` and `stop_measurement()` before running `analyze()`.\n"
                "   For general information about the Fisica SDK, call `print(FisicaSDK.__doc__)`.\n"
                "   If you would like to learn how to perform measurement to obtain frame data, call :\n"
                "   `print(FisicaSDK.start_measurement.__doc__)` and `print(FisicaSDK.stop_measurement.__doc__)`.\n"
            )
            return
        weight, lw, ll, rw, rl = result

        self._current_report = SessionReport(
            session_id=metadata.get("id"),
            name=metadata.get("name"),
            foot_length={"left": round(ll,2), "right": round(rl,2)},
            foot_width={"left": round(lw,2), "right": round(rw,2)},
            total_frame=frames,
            frame_count=len(frames),
            duration_sec=round(self._duration, 2),
            weight=weight,
            battery=frames[-1].battery
        )

        self._reports.append(self._current_report)
        self._session.frames = []
        return self._current_report

    def render(self, frame: Optional[MeasurementFrame] = None, grid_data: Optional[np.ndarray] = None, mode: str = "BLUR", scale: float = 1.0, bbox: bool = False):
        """
    Fisica SDK: render
        Parameters:
            frame (MeasurementFrame): This dataclass is used when measurement data is received through the on_data callback function.
                                          If you are calling the render function directly, please provide grid_data instead.
                                          When calling render from within the on_data callback, you can pass the data argument directly.
                                          For more details, refer to the example below.

            grid_data (str): Hexadecimal string from the sensor (645 bytes). > deprecated

            mode (str): Visualization mode for rendering the sensor data.
                       Available options: 'ALL', 'PIXEL', 'BLUR', 'BINARY', 'BINARY_NONZERO', 'CONTOUR'.
                       Default is 'BLUR'. You can also import VisualOptions for these constants.

            scale (float): Scaling factor for the output image. The default resolution is 456x512 pixels,
                           and the image will be scaled up proportionally.

            bbox (bool): When set to True, bounding boxes will be drawn on the rendered image.

        Returns:
            np.ndarray: It returns image data as a NumPy array.
                        Implement your own logic using the image data.

        Notes:
            If you need to display image data in real time during scale measurement,
            you can use the built-in visualize function provided by the Fisica SDK.
            Refer to realtime_monitoring.py in the examples directory.
        """

        if frame is None and grid_data is None:
            log.error(
                "\nFisica SDK\n"
                "   No data was detected.\n"
                "   Please make sure you have entered valid data.\n"
                "   If you want to know about the how use render func, please call `print(FisicaSDK.render.__doc__)`.\n"
                "   If you want to know about the how use receive data from the scale, please call `print(FisicaSDK.__doc__)`.\n\n"
            )
            raise ValueError("Check Paramater")

        if frame:
            grid_data = np.array(frame.sensor_matrix)

        return global_render(grid_data, mode, scale=scale, bbox=bbox)

    def wait(self):
        """
    Fisica SDK: wait

        Notes:
            Used to safely complete asynchronous operations such as `start_measurement()`,
            `stop_measurement()`, and `disconnect()`.
            This method blocks the main thread execution.
        """
        while self._stopping or self._working or self._sleeping or self._starting or self._disconnecting:
            self._counter = (self._counter + 1) % 25
            if not self._counter:
                log.debug(
                    "wait for..\n"
                    f"starting ? > {self._starting}\n"
                    f"stopping ? > {self._stopping}\n"
                    f"working ? > {self._working}\n"
                    f"sleeping? > {self._sleeping}\n"
                    f"disconnecting? > {self._disconnecting}"
                )
            time.sleep(0.1)
