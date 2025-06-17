import asyncio
import logging
from fisica_sdk.port_scanner import find_serial_com_ports, find_bluetooth_com_ports
from .receiver import Receiver
from .visualizer import visualize as global_visualizer
from .analyzer import analyze as global_analyzer
from typing import Optional
import time

logger = logging.getLogger(__name__)



class FisicaSDK:
    """
    FisicaSDK - Main interface for interacting with the scale device.

    Available Methods:
        - scan_devices(): Scan for available serial and Bluetooth devices.
        - connect(device): Connect to a selected device from scan_devices().
        - start_measurement(duration=None): Begin data collection.
        - stop_measurement(): Stop data collection.
        - on_data(callback): Register a callback to receive real-time data.
        - analyze(): Analyze the collected data.
        - get_report(): Retrieve the most recent report.
        - get_all_reports(): Get all previous analysis reports.
        - export_report(filename): Save the current report to a file.
        - reset_session(): Clear current session data.
        - reset_reports(): Clear all stored reports.
        - visualize(hex_data, mode, scale): Visualize rawData as an image.

    Example:
        from fisica_sdk import FisicaSDK
        import time
        sdk = FisicaSDK()
        devices = sdk.scan_devices()
        sdk.connect(devices[0])
        sdk.start_measurement()
        time.sleep(5)
        sdk.stop_measurement()

    For more information:
        Append .__doc__ to the method name and call print to view the documentation.
        e.g.    print(FisicaSDK.start_measurement.__doc__)
    """

    def __init__(self, debug=False):
        self._loop = None
        self._receiver = None
        self.available_devices = None
        self.connected_device = None
        self.metadata = None
        self.debug = debug
        self._receive_task = None
        self._callback = None
        self._frames = []
        self._reports = []
        self._current_report = None
        self._connected = False

    def _log(self, *args):
        if self.debug:
            from datetime import datetime
            print(f"[{datetime.now()}] [DEBUG]", *args)

    def _handle_frame(self, frame):
        self._frames.append(frame)
        self._log("Frame received:", frame)
        if self._callback:
            self._callback(frame)

    async def _start_with_timeout(self, duration):
        await self._start_async()
        await asyncio.sleep(duration)
        # time.sleep(duration)
        await self._stop_async()

    async def _start_async(self):
        if not self._connected:
            raise RuntimeError("Not connected to a device.")
        self._frames.clear()
        if self._receiver:
            self._log("Initializing async data receiving loop...")
            self._receiver.start()
        self._log("Measurement started.")

    async def _stop_async(self):
        self._connected = False
        if self._receiver:
            self._receiver.stop()
        if self._receive_task:
            await self._receive_task
        self._log("Measurement stopped and session frozen.")

    def set_metadata(self, **kwargs):
        """
        :param device:
        :return:
        """
        default_metadata = {
            'id': None,
            'name': None,
            'gender': None,
            'age': None
        }

        for key in kwargs:
            if key not in default_metadata:
                raise ValueError(f"Unexpected metadata field: {key}")
            default_metadata[key] = kwargs[key]

        self.metadata = default_metadata
        self._log("Metadata set to:", self.metadata)

    def scan_devices(self):
        """
        :param device:
        :return:
        """
        self._log("Scanning for available devices...")
        serial_ports = find_serial_com_ports()
        bt_ports = find_bluetooth_com_ports()

        self.available_devices = []

        for port in serial_ports:
            self.available_devices.append({"id": port, "type": "serial"})
        for port in bt_ports:
            self.available_devices.append({"id": port, "type": "bluetooth"})

        self._log("Found devices:", self.available_devices)
        return self.available_devices

    def connect(self, device):
        """
        :param device:
        :return:
        """
        try:
            if not isinstance(device, dict):
                raise TypeError("Device must be a dictionary returned from scan_devices().")

            device_id = device.get("id")
            self._log(f"Attempting to connect to device: {device}")

            if not hasattr(self, 'available_devices') or not self.available_devices:
                raise RuntimeError("No devices scanned. Call scan_devices() first.")
            if not any(d["id"] == device_id for d in self.available_devices):
                raise ValueError(f"Device ID {device_id} not found in scanned list.")

            self.connected_device = device_id
            self._connected = True
            self._receiver = Receiver(device_id, self._handle_frame, debug=self.debug)
            self._log(f"Connected to device: {device_id}")
        except Exception as e:
            logger.error(
                "\n\nFisica SDK\n"
                "   It seems that no device was found, the connection was lost, or an invalid parameter was provided.\n"
                "   Please make sure to call `scan_devices()` before using this method.\n"
                "   For usage examples, call `print(FisicaSDK.__doc__)`\n"
                "   To learn how to retrieve the list of devices, call `print(FisicaSDK.scan_devices.__doc__)`\n"
                "   To learn how to connect after retrieving devices, refer to the example in `print(FisicaSDK.__doc__)`\n"
                "   or call `print(FisicaSDK.connect.__doc__)`\n"
            )
            raise e

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
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        if duration:
            loop.run_until_complete(self._start_with_timeout(duration))
        else:
            loop.run_until_complete(self._start_async())
        self._loop = loop

    def stop_measurement(self):
        """
        :return:
        """
        if hasattr(self, "_loop"):
            self._loop.run_until_complete(self._stop_async())

    def on_data(self, callback):
        """
        :param callback:
        :return:
        """
        self._callback = callback
        self._log("Callback for real-time data set.")

    def get_report(self):
        """
        :return:
        """
        return self._current_report

    def get_all_reports(self):
        """
        :return:
        """
        return self._reports

    def export_report(self, filename):
        """
        :param filename:
        :return:
        """
        import json
        with open(filename, 'w') as f:
            json.dump(self._current_report or {}, f, indent=2)
        self._log(f"Report exported to {filename}.")

    def reset_session(self):
        """
        :return:
        """
        self._frames.clear()
        self._current_report = None
        self._log("Session reset for next participant.")

    def reset_reports(self):
        """
        :return:
        """
        self._reports.clear()
        self._log("All reports reset.")

    def analyze(self, hex_data: Optional[str] = None):
        """
        :return:
        """

        # # Placeholder for analysis logic
        # self._current_report = {
        #     "frame_count": len(self._frames),
        #     "duration_sec": round(len(self._frames) * 0.05, 2),
        #     "summary": "Analysis result"
        # }
        # self._reports.append(self._current_report)
        # self._log("Analysis complete.")


        #temporary
        if hex_data is None:
            logger.error(
                "\n\nFisica SDK\n"
                "   To use visualize, you must provide a hex_data value.\n"
                "   This hex_data is provided by the scale device.\n"
                "   If you want to learn about the how use visualize func, please call `print(FisicaSDK.analyze.__doc__)`.\n"
                "   If you want to know about the how use receive data from the scale, please call `print(FisicaSDK.__doc__)`.\n\n"

            )
            raise ValueError("Check Paramater")
        return global_analyzer(hex_data)

    def visualize(self, hex_data: Optional[str] = None, mode: str = "BBOX", scale: float = 1.0):
        """
    Fisica SDK : visualize
        Parameters:
            hex_data (str): Hexadecimal string from the sensor (645 bytes).
            mode (str): Visualization mode. You can import VisualOptions and use
            `print(VisualOptions.__doc__)` to see the available options.
            scale (float): Scaling factor for the output image. The default resolution is 456x512 pixels,
                           and the image will be scaled up proportionally.

        Returns:
            np.ndarray: The visualized image as a NumPy array.
                        To display it on the screen, use OpenCV functions like:
                            cv2.imshow("Preview", image)
                            cv2.waitKey(0)
                            cv2.destroyAllWindows()

        Example:
            image = FisicaSDK.visualize(hex_data="000000...", mode=VisualOptions.BLUR, scale=1.0)
            cv2.imshow("Preview", image)
            cv2.waitKey(0)
            cv2.destroyAllWindows()
        """
        if hex_data is None:
            logger.error(
                "\n\nFisica SDK\n"
                "   To use visualize, you must provide a hex_data value.\n"
                "   This hex_data is provided by the scale device.\n"
                "   If you want to learn about the how use visualize func, please call `print(FisicaSDK.visualize.__doc__)`.\n"
                "   If you want to know about the how use receive data from the scale, please call `print(FisicaSDK.__doc__)`.\n\n"

            )
            raise ValueError("Check Paramater")
        return global_visualizer(hex_data, mode, scale=scale)