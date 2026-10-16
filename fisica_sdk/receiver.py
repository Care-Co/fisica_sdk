"""Host transport, threads and callbacks; session/ACK/deadlines live in C ABI."""
import math
import threading
import time
import warnings
from collections import defaultdict, deque
from queue import Empty, Queue

from .serial_uart import SerialUART
from .serial_handler import SerialHandler
from .utils import log
from .device import scan
from .native import NativeSession, get_core
from .parser import parse_frame_from_chunk
from .errors import FisicaFirmwareWarning, FisicaTransportError, FisicaCommandError, FisicaArgumentError


class Receiver:
    def __init__(self, device, callback, transport=None, record_callback=None):
        self._notice_recovery = True
        self.loop_alive = self.heartbeat = False
        self.check_thread = self._thread = self._handler = self._uart = None
        self._device = dict(device)
        self._device_id = device.get('id')
        self._target_device = dict(device)
        self._transport = transport
        self.device_info = {'id': self._device_id, 'transport': device.get('type'),
                            'firmware_version': None, 'firmware_verified': False,
                            'battery': None, 'battery_valid': False}
        self._callback = callback
        self._record_callback = record_callback
        self.on_stopped = self.on_error = None
        self._check_stop_event = threading.Event()
        self._stop_event = threading.Event()
        self._shutdown = threading.Event()
        self._api_lock = threading.Lock()
        self._condition = threading.Condition(threading.RLock())
        self._session = NativeSession()
        self._results = defaultdict(deque)
        self._waiters = set()
        self._notifications = Queue()
        self._reconnect = False
        self._next_reconnect = 0
        self._worker_error = None
        self._send_error = None

    def _request(self, operation, call):
        with self._api_lock:
            return self._wait_for_result(operation, call)

    def _wait_for_result(self, operation, call):
        with self._condition:
            if self._shutdown.is_set() or self._worker_error:
                raise self._worker_error or FisicaTransportError('Receive loop is closed')
            self._results[operation].clear()
            self._waiters.add(operation)
            try:
                call()
                while not self._results[operation]:
                    if self._worker_error:
                        raise self._worker_error
                    self._condition.wait(0.1)  # scheduling only; core owns the deadline
                event = self._results[operation].popleft()
                if event['status']:
                    if event['status'] == 11 and self._send_error:
                        raise self._send_error
                    raise FisicaCommandError(event)
                return event
            finally:
                self._waiters.discard(operation)

    def start(self, duration=None):
        if self.check_thread and self.check_thread.is_alive() and self._check_stop_event.is_set():
            raise FisicaTransportError('Previous connection recovery is still stopping; retry after it finishes')
        if duration is not None and (not isinstance(duration, (int,float)) or not math.isfinite(duration) or duration < 0):
            raise FisicaArgumentError(1, 'Measurement duration must be nonnegative finite seconds')
        value = -1 if duration is None or duration == 0 else int(duration * 1000)
        self._refresh_battery()  # so this measurement's report carries a current reading
        self._stop_event.clear()
        self._request(2, lambda: self._session.action(2, value))
        log.info('Measurement start acknowledged.')

    def stop(self):
        self._stop_event.set()
        self._request(3, lambda: self._session.action(3))
        log.debug('Measurement stop acknowledged.')

    def _read_loop(self):
        while not self._shutdown.is_set():
            try:
                kind, data = self._notifications.get(timeout=0.1)
                if kind == 'frame':
                    self._callback(data)
                elif kind == 'stop' and self.on_stopped:
                    self.on_stopped()
                elif kind == 'error' and self.on_error:
                    self.on_error(data)
            except Empty:
                continue
            except Exception:
                log.error('Measurement callback failed')

    def _drain_events(self):
        for event in self._session.events():
            kind, operation = event['kind'], event['operation']
            if kind == 1:
                try:
                    self._handler.send(event['payload'])
                except Exception as error:
                    self._send_error = error
                    self._session.action(5, 1)
            elif kind == 3:
                if operation in self._waiters:
                    self._results[operation].append(event)
                if operation == 4:
                    self._shutdown.set()
                self._condition.notify_all()
                if operation == 1:
                    self._update_firmware(event)
                if operation in (2,3) and operation not in self._waiters and 4 not in self._waiters:
                    self._notifications.put(('error', FisicaCommandError(event)) if event['status'] else
                                            ('stop', None) if operation == 3 else ('started', None))
            elif kind == 4:
                frame = parse_frame_from_chunk(event['payload'].hex())
                if self._record_callback:
                    self._record_callback(frame)
                self._notifications.put(('frame', frame))
            elif kind == 6:
                self._reconnect = True

    def _pump_loop(self):
        self.heartbeat = True
        try:
            while not self._shutdown.wait(0.01):
                with self._condition:
                    self._session.tick()
                    if self._handler:
                        self.loop_alive = bool(self._handler._listening and self._handler.listen_thread and
                                               self._handler.listen_thread.is_alive())
                        if not self.loop_alive and not self._reconnect:
                            self._session.action(5)
                        if self.loop_alive:
                            for _ in range(64):
                                try:
                                    data = self._handler.raw_queue.get_nowait()
                                except Empty:
                                    break
                                if self._shutdown.is_set():
                                    break
                                for offset in range(0, len(data), 65536):
                                    self._session.feed(data[offset:offset+65536])
                                    self._drain_events()
                                    if self._shutdown.is_set():
                                        break
                    self._drain_events()
                if self._reconnect and not self._check_stop_event.is_set() and time.monotonic() >= self._next_reconnect:
                    self._next_reconnect = time.monotonic() + 1
                    try:
                        self._recover_connection()
                    except Exception as error:
                        log.debug(f'Waiting for the selected Scale to reconnect: {error}')
        except Exception as error:
            with self._condition:
                self._worker_error = error
                self._condition.notify_all()
            self._notifications.put(('error', error))
        finally:
            self.heartbeat = False

    def _find_original_device(self, devices):
        matches = []
        for device in devices:
            if device.get('type') != self._target_device.get('type'):
                continue
            serial_number = self._target_device.get('serial_number')
            if serial_number and device.get('serial_number') == serial_number:
                if (device.get('vid'), device.get('pid')) == (self._target_device.get('vid'), self._target_device.get('pid')):
                    matches.append(device)
            elif not serial_number and device.get('id') == self._target_device.get('id'):
                matches.append(device)
        return matches[0] if len(matches) == 1 else None

    def _recover_connection(self):
        self._close_transport()
        device = self._target_device if self._transport else self._find_original_device(
            scan(transport=self._target_device.get('type')))
        if not device or self._check_stop_event.is_set():
            return
        self._device = dict(device)
        self._device_id = device.get('id')
        self._open_transport()
        with self._condition:
            if self._check_stop_event.is_set():
                self._close_transport()
                return
            self._session.action(1, 2 if self._device['type'] == 'Serial' else 1)
            self._reconnect = False
            self._drain_events()  # core decides whether to resume after firmware probe

    def _open_transport(self):
        if self._transport is not None:
            if self._device.get('type') != 'Serial':
                raise ValueError('Injected byte transports currently use the Serial/USB command profile')
            self._uart = self._transport
            self._uart.connect()
            self._handler = SerialHandler(self._uart)
        elif self._device.get('type') == 'Serial':
            self._uart = SerialUART(self._device_id, baudrate=115200)
            self._uart.connect()
            self._handler = SerialHandler(self._uart)
        elif self._device.get('type') == 'Bluetooth':
            from .bluetooth_handler import BluetoothHandler
            self._handler = BluetoothHandler(self._device_id)
            if not self._handler.connect():
                raise FisicaTransportError('Bluetooth connection failed')
        else:
            raise ValueError('Device type must be Serial or Bluetooth')
        self._handler.session_input = True
        self._handler.start_listening()
        if not self._handler._listening:
            raise FisicaTransportError('Cannot start device receive loop')

    def conn(self):
        self._open_transport()
        self.check_thread = threading.Thread(target=self._pump_loop, daemon=True)
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        # Register before starting the pump so the result cannot be missed.
        with self._condition:
            self._waiters.add(1)
            self._session.action(1, 2 if self._device['type'] == 'Serial' else 1)
            self.check_thread.start()
            self._thread.start()
            try:
                while not self._results[1]:
                    if self._worker_error:
                        raise self._worker_error
                    self._condition.wait(0.1)
                event = self._results[1].popleft()
                # NACK/missing version is permitted; transport write failures are not.
                if event['status'] == 11:
                    raise self._send_error or FisicaCommandError(event)
            finally:
                self._waiters.discard(1)
        self._warn_firmware()
        self._refresh_battery()

    def _update_firmware(self, event):
        version = None
        if not event['status']:
            try:
                version = get_core().decode_response(event['payload'])['payload'].decode('utf-8').strip('\x00 \r\n') or None
            except UnicodeError:
                pass
        verified = get_core().policy.get('firmware_support', {}).get('verified_versions', [])
        self.device_info.update(id=self._device_id, firmware_version=version,
                                firmware_verified=bool(version and version in verified))
        if 1 not in self._waiters:
            self._warn_firmware()

    def _warn_firmware(self):
        if not self.device_info['firmware_verified']:
            version = self.device_info['firmware_version'] or 'unknown'
            warnings.warn(f'Scale firmware {version} is unverified; use continues at the partner\'s responsibility.',
                          FisicaFirmwareWarning, stacklevel=3)

    def _close_transport(self):
        try:
            if self._handler:
                try:
                    if hasattr(self._handler, 'close'):
                        self._handler.close()
                    else:
                        self._handler.stop_listening()
                finally:
                    self._handler = None
        finally:
            if self._uart:
                try:
                    self._uart.close()
                finally:
                    self._uart = None

    def discon(self):
        self._check_stop_event.set()
        try:
            if self._handler and self.check_thread and self.check_thread.is_alive():
                self._request(4, lambda: self._session.action(4))
        finally:
            self._shutdown.set()
            for thread in (self.check_thread, self._thread):
                if thread and thread.is_alive() and thread is not threading.current_thread():
                    thread.join(timeout=3)
            try:
                self._close_transport()
            finally:
                self._session.close()
                self.loop_alive = False
        log.debug('Scale disconnected.')

    def battery(self):
        """Ask the Scale for its battery level (GET_BATTERY_LEVEL); works idle or measuring.

        Measurement packets carry no battery field, so this query is the only real reading.
        """
        event = self._request(5, lambda: self._session.command('GET_BATTERY_LEVEL'))
        response = get_core().decode_response(event['payload'])
        percent = (response['data_high'] << 8) | response['data_low']
        if not response['success'] or percent > 100:
            raise FisicaCommandError(event)
        self.device_info.update(battery=percent, battery_valid=True)
        return percent

    def _refresh_battery(self):
        # Only on links the SDK opened itself: an injected (mock, replay or partner) transport may
        # not answer, and the core would wait its 2 s ACK deadline. Those call battery() explicitly.
        # A missing reading only leaves the previous one (or none); it never stops use.
        if self._transport is not None:
            return
        try:
            self.battery()
        except (FisicaCommandError, FisicaTransportError) as error:
            log.debug(f'Battery level not read: {error}')

    def zeroset(self):
        self._request(5, lambda: self._session.command('SET_WEIGHT_ZERO'))

    def setScale(self, value):
        self._request(5, lambda: self._session.command('create_weight_scale', value=value))
