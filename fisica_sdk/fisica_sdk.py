import logging
import queue
import time
import threading
from collections import Counter, deque
from typing import Optional, Union
import numpy as np

from .analyzer import analyze as global_analyzer
from .codes import FisicaLimit
from .native import get_core
from .receiver import Receiver
from .exporter import export_session_reports_to_json, serialize_session_frame, serialize_session_report
from .models import MeasurementFrame, MeasurementSession, SessionReport
from .device import scan
from .utils import enable_console_logging, log, resolve_output_path

_ANALYSIS_FRAMES = int(FisicaLimit.ANALYSIS_FRAMES)
_NOT_ANALYZED = object()  # no core analysis since the last start (None is the core's "no stable result")
_IDLE_WORKER_SECONDS = 5


def _frames():
    # Frames kept per session: as many as one analysis accepts (about 50 KB each).
    return deque(maxlen=_ANALYSIS_FRAMES)


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
        - wait(): Wait until queued commands, the running measurement and sleep() calls have finished.
        - set_zero(): Calibrate the zero point of the device.
        - set_scale(value): Adjust the weight calibration of the device.
        - analyze(): Analyze collected session frames and generate a session report.
        - get_session_frames(): Retrieve serialized raw frames from the current session.
        - get_report(): Retrieve the most recent analysis report.
        - get_all_reports(): Retrieve all previous session reports stored in memory.
        - export_report(report: Optional[Union[SessionReport, list]], output: Optional[str]): Save the current or specified report(s) to a JSON file.
        - configure_entitlement(token=..., file=..., endpoint=...): Use an SDK key for full mode.
        - sync_entitlement(wait=True, force=False): Activate or renew the SDK key with the server now.
        - pair_device(): Get an SDK key by approving a code in the developer portal (no key needed).
        - entitlement_status(): Full or basic mode, why, and the key's dates.
        - activation_code() / release_key(): Offline activation code, and releasing the key on this app or PC.
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

        The SDK writes log records to the `fisica_sdk` logger and prints nothing by itself;
        configure `logging` in your application, or pass `FisicaSDK(debug=True)`.

    For more information:
        Append .__doc__ to the method name and call print to view the documentation.
        e.g.    print(FisicaSDK.start_measurement.__doc__)
    """

    def __init__(self, debug=False):
        """debug: True, or a logging level such as logging.DEBUG, prints this library's log records on stderr."""
        self._core = get_core()
        self._connected = False
        self._available_devices = None
        self._connected_device = None
        self._current_report = None
        self._start_time = None
        self._callback = None
        self._duration = None
        self._receiver = None
        self._last_device_info = None  # kept after disconnect so a later analyze() keeps the battery reading
        self._session = None
        self._reports = []
        self._frames = _frames()  # the running measurement's frames
        self._analysis = _NOT_ANALYZED  # the core's analysis of every packet of the last measurement
        self._working = False  # a measurement is running
        self._last_error = None
        self._entitlement = None
        self._looked_for_key = False
        self._unverified_notice = False
        # start, stop and disconnect run in order on one worker thread; wait() waits for them.
        self._commands = queue.Queue()
        self._worker = None
        self._state = threading.Condition()
        self._queued = Counter()
        self._sleeping = 0
        self._local = threading.local()
        if debug:
            enable_console_logging(logging.DEBUG if debug is True else int(debug))

    # Commands: queued and running counts per kind tell wait() and the flags what is left.

    @property
    def _starting(self):
        return self._queued['start'] > 0

    @property
    def _stopping(self):
        return self._queued['stop'] > 0

    @property
    def _disconnecting(self):
        return self._queued['disconnect'] > 0

    def _submit(self, kind, function, *args):
        with self._state:
            self._queued[kind] += 1
            self._commands.put((kind, function, args))
            if self._worker is None:
                self._worker = threading.Thread(target=self._run_commands, name='fisica-sdk', daemon=True)
                self._worker.start()

    def _run_commands(self):
        while True:
            try:
                kind, function, args = self._commands.get(timeout=_IDLE_WORKER_SECONDS)
            except queue.Empty:
                with self._state:
                    if self._commands.empty():
                        self._worker = None
                        return
                continue
            try:
                function(*args)
            except Exception as error:
                self._async_error(kind, error)
            finally:
                with self._state:
                    self._queued[kind] -= 1
                    self._state.notify_all()

    def _async_error(self, what, error):
        """An error of a background operation; wait() raises the first one."""
        log.warning(f"{what} failed: {error!r}", exc_info=log.isEnabledFor(logging.DEBUG))
        if self._last_error is None:
            self._last_error = error

    def _set_working(self, working):
        with self._state:
            self._working = working
            self._state.notify_all()

    def version_info(self):
        """Report independent SDK, Python wrapper, core and ABI versions."""
        return self._core.version_info()

    def configure_entitlement(self, token=None, file=None, endpoint=None, online=True, **options):
        """
    Fisica SDK: configure_entitlement

        Parameters:
            token (str): SDK key from the developer portal.
            file (str): Path of a key file (an activation or renewal file from the portal works on a PC that is never online).
                Without token and file, the key stored for this app or PC (paired or imported earlier) is used.
            endpoint (str): Server URL for activation and renewal. With token or file, no network is used
                without it; for the stored key it defaults to the developer portal, as without this call.
            online (bool): Set False to use no network at all.

        Returns:
            dict: Same content as entitlement_status().

        Notes:
            Without an SDK key activated for this app or PC the SDK runs in basic mode:
            connection, measurement, settings, weight and foot size work; frames come at
            most once a second without raw data, and report exports hold the summary only.
            Online, a new key is activated here automatically and renewed before it expires.
        """
        from .entitlement import DEFAULT_ENDPOINT, Entitlement
        if token is None and file is None and endpoint is None:
            endpoint = DEFAULT_ENDPOINT
        self._use(Entitlement(token=token, file=file, endpoint=endpoint, online=online, **options))
        return self._entitlement.status()

    def sync_entitlement(self, wait=True, force=False):
        """
    Fisica SDK: sync_entitlement

        Parameters:
            wait (bool): Wait for the server's answer (default); False syncs in the background.
            force (bool): Renew now even when renewal is not due yet.

        Returns:
            dict: 'activated', 'refreshed' and 'error' (a failure is reported here, never raised),
            or None without a key, without an endpoint, offline, or when not waiting.

        Notes:
            Online, the SDK activates a new key and renews it before it expires by itself.
            Call this to activate a new key before the first measurement.
        """
        if not self._stored_entitlement():
            return None
        result = self._entitlement.sync(wait=wait, force=force)
        self._apply_license()
        return result

    def pair_device(self, endpoint=None, on_code=None, timeout=None, **options):
        """
    Fisica SDK: pair_device

        Parameters:
            endpoint (str): Developer portal URL. Default: https://developer.fisicahealthcare.com
            on_code (callable): Called with a dict holding 'user_code', 'verification_uri',
                'verification_uri_complete' and 'expires_in'. Without it the code is printed.
            timeout (float): Seconds to wait at most. Default: until the code expires (10 minutes).

        Returns:
            dict: entitlement_status() plus 'pairing': 'approved', 'denied', 'expired' or 'cancelled'.

        Notes:
            For an app or PC without an SDK key. The SDK shows a code; an owner or admin of the
            organization enters it in the developer portal (/activate) and approves it with a new or
            unused key. The key arrives already activated for this app or PC and is stored, so later
            runs use it without configure_entitlement(). Only the app ID or PC hash is sent.
            On a PC, `python -m fisica_sdk license pair` does the same from a terminal.
        """
        from .entitlement import DEFAULT_ENDPOINT, Entitlement
        entitlement = Entitlement(endpoint=endpoint or DEFAULT_ENDPOINT, **options)
        try:
            outcome = entitlement.pair(on_code=on_code, timeout=timeout)
        except Exception:
            entitlement.close()
            raise
        self._use(entitlement)
        return {**entitlement.status(), 'pairing': outcome}

    def _use(self, entitlement):
        previous, self._entitlement = self._entitlement, entitlement
        self._looked_for_key = True
        try:
            self._apply_license()  # before the previous context closes: the session never uses a closed one
        finally:
            if previous is not None and previous is not entitlement:
                previous.close()

    def _stored_entitlement(self):
        """Without configure_entitlement(): the key stored for this app or PC, looked up once."""
        if self._entitlement is None and not self._looked_for_key:
            self._looked_for_key = True
            from .entitlement import DEFAULT_ENDPOINT, Entitlement, default_storage_directory
            try:
                stored = any(default_storage_directory().glob('entitlement-*.state'))
            except OSError:
                stored = False
            if stored:
                entitlement = Entitlement(endpoint=DEFAULT_ENDPOINT)
                if entitlement.status()['key']:
                    self._use(entitlement)
                else:
                    entitlement.close()
        return self._entitlement

    def entitlement_status(self):
        """Full or basic mode, the reason for basic mode, open features and the key's dates."""
        if self._stored_entitlement():
            return self._entitlement.status()
        from .entitlement import NO_KEY_STATUS
        return dict(NO_KEY_STATUS)

    def activation_code(self):
        """Code to enter in the developer portal to activate the loaded key for a device that is never online."""
        return self._entitlement.activation_code() if self._stored_entitlement() else None

    def release_key(self):
        """Release the SDK key on this app or PC; returns the code to enter in the developer portal."""
        if not self._stored_entitlement():
            raise RuntimeError('No SDK key is configured')
        code = self._entitlement.release()
        self._apply_license()
        return code

    def _apply_license(self):
        receiver = self._receiver
        if receiver:
            try:
                receiver.refresh_license()
            except Exception:
                log.debug("License not applied to the session", exc_info=True)

    def _has_feature(self, name):
        try:
            return bool(self._stored_entitlement() and self._entitlement.has_feature(name))
        except Exception:
            return False

    def _check_entitlement(self):
        # Status notice only; nothing about the use is recorded. Problems here must never
        # reach the measurement path.
        try:
            if self._stored_entitlement():
                self._entitlement.check_status()
            elif not self._unverified_notice:
                self._unverified_notice = True
                import warnings
                from .errors import FisicaEntitlementWarning
                from .entitlement import REASON_MESSAGES
                warnings.warn('Fisica SDK license: ' + REASON_MESSAGES['no_key'], FisicaEntitlementWarning, stacklevel=3)
        except Exception as error:
            log.debug(f"Entitlement notice skipped: {error}")

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

        self._session = MeasurementSession(metadata=default_metadata, frames=_frames())
        self._analysis = _NOT_ANALYZED
        log.info(f"Metadata set: {default_metadata}")

    def _record_frame(self, frame):
        # Record before ACK waiters are released, independent of app callback speed.
        self._frames.append(frame)

    def _handle_frame(self, frame):
        callback = self._callback
        if callback:
            self._local.callback = True
            try:
                callback(frame)
            finally:
                self._local.callback = False

    def _start(self, duration):
        receiver = self._receiver
        if receiver is None:
            raise RuntimeError("Not connected to a device.")
        if self._working:
            raise RuntimeError("A measurement is already running; stop it before starting another.")
        if not self._session:
            self._set_metadata()
        self._frames.clear()
        self._analysis = _NOT_ANALYZED
        self._start_time = time.time()
        try:
            receiver.start(duration)
        except Exception:
            try:
                receiver.stop()
            except Exception:
                log.debug("Cleanup after a failed start also failed", exc_info=True)
            raise
        self._set_working(True)
        log.info("Measurement started.")

    def _stop(self):
        # A stop requested while sleep() runs takes effect after it, as with time.sleep().
        with self._state:
            while self._sleeping:
                self._state.wait(0.1)
        if not self._working:
            return
        receiver = self._receiver
        try:
            if receiver:
                receiver.stop()
        finally:
            self._end_measurement(receiver)

    def _on_core_stopped(self, measurement):
        # The core ended the measurement (duration reached).
        self._submit('stop', self._finish, measurement)

    def _on_session_error(self, error, measurement):
        self._submit('stop', self._fail, error, measurement)

    def _current(self, measurement):
        receiver = self._receiver
        return receiver is not None and (measurement is None or receiver.measurement == measurement)

    def _finish(self, measurement):
        if self._working and self._current(measurement):
            self._end_measurement(self._receiver)

    def _fail(self, error, measurement):
        if self._working and self._current(measurement):
            self._end_measurement(self._receiver)
        self._async_error('Measurement', error)

    def _end_measurement(self, receiver):
        """Freeze the measurement: the core's analysis of every packet, and its frames into the session."""
        try:
            self._analysis = receiver.analyze() if receiver else None
        except Exception:
            log.exception("Measurement analysis failed")
            self._analysis = None
        finally:
            if self._session is not None:
                frames = deque(self._session.frames, maxlen=_ANALYSIS_FRAMES)
                frames.extend(self._frames)
                self._session.frames = frames
            self._duration = time.time() - self._start_time if self._start_time else 0.0
            self._set_working(False)
            log.info("Measurement stopped and session frozen.")

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

           An exception raised by `after` is raised by the next `wait()`.
        """
        with self._state:
            self._sleeping += 1

        def _delayed():
            self._local.sleep_callback = True
            try:
                time.sleep(seconds)
                if after:
                    after()
            except Exception as error:
                self._async_error('sleep() callback', error)
            finally:
                with self._state:
                    self._sleeping -= 1
                    self._state.notify_all()

        try:
            threading.Thread(target=_delayed, name='fisica-sleep', daemon=True).start()
        except BaseException:
            with self._state:
                self._sleeping -= 1
                self._state.notify_all()
            raise

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

    def scan_devices(self, transport=None):
        """
    Fisica SDK: scan_devices

        Returns:
            list: List of available Scale devices

        Notes:
            Scans for Scale devices connected via Bluetooth or wired connection.
            Searches both transports by default. Pass transport='Serial' or
            transport='Bluetooth' to restrict discovery; then a transport that cannot be
            scanned (for example Bluetooth off or not permitted) raises FisicaTransportError.
            Before connecting a device with `connect()`, you must pass the device obtained
            through `scan_devices()` as an argument.
            The return value is in list format, so use array indexing like `device[0]`
            to attempt connection with a specific device.
        """
        log.info("Scanning for available devices...")
        devices = scan(transport=transport)
        self._available_devices = devices
        if not devices:
            log.warning("No Fisica Scale found; check that it is powered on.")
        return devices

    def connect(self, device, transport=None):
        """
    Fisica SDK: connect

        Parameters:
            device: Device object obtained from scan_devices()

        Notes:
            Attempts to connect to a specific Scale device.
            Use the return value from `scan_devices()` for the device parameter.
            For detailed information, print `FisicaSDK.scan_devices.__doc__`.
        """
        with self._state:  # a disconnect() still queued finishes first
            while self._queued['disconnect']:
                self._state.wait(0.1)
        if self._receiver:
            raise RuntimeError('Disconnect the current device before connecting another Scale')
        self._check_entitlement()
        if not isinstance(device, dict):
            raise TypeError("Device must be a dictionary returned from scan_devices().")
        if transport is None and not self._available_devices:
            raise RuntimeError("No devices scanned. Call scan_devices() first.")

        log.info(f"Attempting to connect to Fisica Scale ({device.get('type')}) device..")
        receiver = Receiver(device, self._handle_frame, transport=transport, record_callback=self._record_frame,
                            license=lambda: self._entitlement)
        receiver.on_stopped = self._on_core_stopped
        receiver.on_error = self._on_session_error
        try:
            receiver.conn()
        except Exception:
            try:
                receiver.discon()
            except Exception:
                log.debug("Cleanup after a failed connection also failed", exc_info=True)
            raise
        self._receiver = receiver
        self._connected_device = device.get('id')
        self._connected = True

    def get_device_info(self):
        """Firmware query result and verification state, and the last battery reading; unknown firmware never blocks use."""
        receiver = self._receiver
        return dict(receiver.device_info) if receiver else None

    def get_battery(self):
        """
    Fisica SDK: get_battery

        Returns:
            int: Battery level in percent (0-100), read from the Scale now.

        Notes:
            Works while idle or during a measurement without interrupting it.
            On USB/BLE links the SDK opens, it also reads the level on connect and
            before each measurement; with an injected transport, call this method.
            Reports carry the latest reading with `battery_valid=True`; measurement
            frames have no battery field and keep `battery=None`, `battery_valid=False`.
            Raises FisicaCommandError if the Scale does not answer.
        """
        return self._connected_receiver('reading its battery level').battery()

    def _connected_receiver(self, purpose):
        receiver = self._receiver
        if not receiver:
            raise RuntimeError(f'Connect a Scale before {purpose}')
        return receiver

    def _battery_reading(self):
        info = self._receiver.device_info if self._receiver else self._last_device_info
        return info.get('battery') if info and info.get('battery_valid') else None

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
        self._submit('disconnect', self._disconnect)

    def _disconnect(self):
        receiver = self._receiver
        if receiver is None and not self._connected_device:
            log.warning("disconnect(): no Scale is connected.")
            return
        log.info(f"Attempting to disconnect to device: {self._connected_device}")
        try:
            if receiver:
                receiver.discon()
        finally:
            if self._working:
                self._end_measurement(receiver)
            info = getattr(receiver, 'device_info', None)
            if info:
                self._last_device_info = dict(info)
            self._receiver = None
            self._connected_device = None
            self._connected = False
            log.info("Scale disconnected.")

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
            - Errors while starting are raised by `wait()`.
        """
        if not self._connected:
            raise RuntimeError("Not connected to a device. Call scan_devices() and connect() first.")
        self._check_entitlement()
        self._last_error = None
        self._submit('start', self._start, duration)

    def stop_measurement(self):
        """
    Fisica SDK: stop_measurement

        Notes:
            Stops the measurement process. This method does not block the main thread,
            which may cause unintended errors in subsequent methods like `analyze()`
            that should be executed after measurement completion.

            To ensure the measurement stop operation is completed, use the built-in
            `wait()` function. Note that this will block the main thread execution.
            A stop requested while `sleep()` runs takes effect when the sleep ends.
        """
        self._submit('stop', self._stop)

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
            A session keeps its latest frames, as many as one analysis accepts.
        """

        if self._session and self._session.frames:
            return serialize_session_frame(self._session)
        if self._current_report:
            return serialize_session_frame(
                MeasurementSession(metadata={}, frames=self._current_report.total_frame)
            )
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
        if not self._current_report:
            log.warning("get_report(): no report yet; call analyze() after a measurement.")
            return None
        return [serialize_session_report(self._current_report, frames=self._has_feature('export'))]


    def get_all_reports(self):
        """
    Fisica SDK: get_all_reports

        Returns:
           list: Serialized list of all analyzed SessionReport objects

        Notes:
           Reports stay in memory until `reset_reports()`.
        """
        return [serialize_session_report(r, frames=self._has_feature('export')) for r in self._reports]

    def set_zero(self):
        """
    Fisica SDK: set_zero

        Calibrates the zero point of the device.

        This function sets the current reading as the zero reference point for
        weight measurements. This is typically used to establish a baseline
        when the scale platform is empty for accurate weight calibration.

        Notes:
            - Device connection is required before calling this method; without it RuntimeError is raised
            - Call connect() first to establish device connection
            - Use this function when the scale platform is empty to set proper zero point
            - This calibrates the fundamental zero reference point of the sensor
        """
        self._connected_receiver('setting its zero point').zeroset()

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
            - Device connection is required before calling this method; without it RuntimeError is raised
            - Use this to fine-tune weight measurement precision
            - Call connect() first to establish device connection
        """
        self._connected_receiver('setting its weight scale').setScale(value)


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
            In basic mode the report is exported as its summary (weight, foot size) without frames.

        Returns:
            str: The path of the written JSON file, or None when there is no report.
        """
        if report is None:
            if not self._current_report:
                log.warning("export_report(): no report yet; call analyze() after a measurement.")
                return None
            report = [self._current_report]
        elif isinstance(report, SessionReport):
            report = [report]
        # Basic mode exports the summary (weight, foot size); frames need full mode.
        frames = self._has_feature('export')
        if not frames:
            log.info("Basic mode: the exported report holds the summary without frames.")
        if isinstance(report, list) and report and isinstance(report[0], SessionReport):
            report = [serialize_session_report(r, frames=frames) for r in report]
        elif isinstance(report, list) and not frames:
            report = [{k: v for k, v in r.items() if k != 'total_frame'} if isinstance(r, dict) else r for r in report]

        output_path = resolve_output_path(output)
        return export_session_reports_to_json(report, output_path)

    def reset_session(self):
        """
    Fisica SDK: reset_session

        Notes:
            Resets the current session. You must call `analyze()` before performing this operation
            to ensure the current session is properly reflected when performing subsequent operations like export.
        """

        self._session = None
        self._current_report = None
        self._analysis = _NOT_ANALYZED
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

            Weight and foot size come from the core's analysis of every packet of the
            last measurement, in basic and full mode alike.

            Calling this function again on the same session without re-measurement
            may cause unexpected errors. Use `get_report()` to retrieve the
            SessionReport instead.
        """
        if not self._session or not self._session.frames:
            log.warning("analyze(): no frames in this session; run a measurement first.")
            return
        self._check_entitlement()

        metadata = self._session.metadata
        frames = list(self._session.frames)
        if self._analysis is not _NOT_ANALYZED:
            result = self._analysis
        else:
            # Frames placed in the session directly: one analysis accepts a bounded number, the latest are used.
            analyzed = frames[-_ANALYSIS_FRAMES:]
            if len(analyzed) < len(frames):
                log.warning(f"Analyzing the latest {len(analyzed)} of {len(frames)} frames "
                            f"(one analysis accepts at most {_ANALYSIS_FRAMES}).")
            result = global_analyzer(analyzed)

        if not result:
            log.warning("analyze(): no stable result; step onto the Scale before starting, or measure longer.")
            return
        weight, lw, ll, rw, rl = result

        self._current_report = SessionReport(
            session_id=metadata.get("id"),
            name=metadata.get("name"),
            foot_length={"left": round(ll,2), "right": round(rl,2)},
            foot_width={"left": round(lw,2), "right": round(rw,2)},
            total_frame=frames,
            frame_count=len(frames),
            duration_sec=round(self._duration or 0.0, 2),
            weight=weight,
            # Measurement packets have no battery field; the Scale's last GET_BATTERY_LEVEL answer does.
            battery=reading if (reading := self._battery_reading()) is not None else frames[-1].battery,
            battery_valid=reading is not None or frames[-1].battery_valid,
        )

        self._reports.append(self._current_report)
        self._session.frames = _frames()
        self._analysis = _NOT_ANALYZED
        return self._current_report

    def render(self, frame: Optional[MeasurementFrame] = None, grid_data: Optional[np.ndarray] = None, mode: str = "BLUR",
               scale: float = 1.0, bbox: bool = False, vmax: Optional[float] = None):
        """
    Fisica SDK: render (needs the visualization extra: pip install fisica[visualization])

        Parameters:
            frame (MeasurementFrame): A frame from the on_data callback; its sensor_matrix is drawn.
            grid_data (np.ndarray): A 2D grid to draw instead of a frame, for example a stored sensor_matrix.
            mode (str): 'PIXEL', 'BLUR', 'BINARY', 'BINARY_NONZERO', 'CONTOUR' or 'ALL' (VisualOptions).
                        Default 'BLUR'. 'ALL' returns a list of six images (see VisualOptions).
            scale (float): The image is 512x512 pixels times scale.
            bbox (bool): Draws each foot's oriented box (left green, right magenta).
            vmax (float): Value drawn as the top of the colour map, for colours that compare across
                          frames. Default: each frame's own maximum.

        Returns:
            np.ndarray: BGR image (uint8), or a list of them for 'ALL'.

        Notes:
            Works in every license mode: sensor_matrix is the display grid, which basic mode
            hands out at most once a second. Showing the image is up to your application.
        """
        if frame is None and grid_data is None:
            raise ValueError('Pass a frame from on_data or a grid_data array')
        if frame is not None:
            grid_data = np.array(frame.sensor_matrix)

        from .visualizer import render as global_render
        return global_render(grid_data, mode, scale=scale, bbox=bbox, vmax=vmax)

    def wait(self):
        """
    Fisica SDK: wait

        Notes:
            Used to safely complete asynchronous operations such as `start_measurement()`,
            `stop_measurement()`, `disconnect()` and `sleep()`, and a measurement started
            with a duration. This method blocks the main thread execution.
            Raises the first error of those operations since the last start_measurement().
            It cannot be called from an `on_data` or `sleep()` callback, which it would wait for.
        """
        if getattr(self._local, 'callback', False) or getattr(self._local, 'sleep_callback', False):
            raise RuntimeError('wait() cannot be called from an on_data() or sleep() callback; '
                               'the SDK would wait for that callback itself')
        with self._state:
            while sum(self._queued.values()) or self._sleeping or self._working:
                self._state.wait(0.1)
        error, self._last_error = self._last_error, None
        if error is not None:
            raise error
