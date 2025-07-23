import threading
import time

from .serial_uart import SerialUART
from .serial_handler import SerialHandler
from .bluetooth_handler import BluetoothHandler
from .utils import log
from .device import scan, ATCommands


class Receiver:
    def __init__(self, device, callback):
        self._notice_recovery = True
        self.loop_alive = False
        self.heartbeat = False
        self.check_thread = None
        self._handler = None
        self._thread = None
        self._uart = None
        self._device = device
        self._device_id = device.get('id')
        self._callback = callback
        self._check_stop_event = threading.Event()
        self._stop_event = threading.Event()

    def start(self):
        self._stop_event.clear()
        self._check_stop_event.clear()
        self._handler.start_measurement()
        if not self.heartbeat:
            self.heartbeat = True
            self.check_thread = threading.Thread(target=self.is_listening_active, daemon=True)
            self.check_thread.start()


        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

        log.debug("Receiver started.")

    def stop(self):
        if self._check_stop_event:
            self._check_stop_event.set()
        if self.check_thread and self.check_thread.is_alive():
            if threading.current_thread() != self.check_thread:
                self.check_thread.join()
                self.heartbeat = False
        if self.loop_alive:
            if self._handler:
                self._handler.stop_listening()
                self._handler.send(ATCommands.STOP_CONTINUOUS_MEASUREMENT)
        self._stop_event.set()
        if self._thread:
            self._thread.join()
        self._notice_recovery = False
        log.debug("Receiver stopped.")

    def _read_loop(self):
        while not self._stop_event.is_set():
            time.sleep(0.1)
            if self._handler:
                try:
                    data = self._handler.data_queue.get(block=False)
                    if data:
                        self._callback(data)
                except Exception as e:
                    continue

    def is_listening_active(self):
        while not self._check_stop_event.is_set():
            self.loop_alive = self._handler.listen_thread and self._handler.listen_thread.is_alive()
            age = time.time() - self._handler.last_heartbeat
            log.debug(f"Thread alive: {self.loop_alive}, heartbeat age: {age:.2f}s")
            if self.loop_alive and age > 2:
                log.debug("[WARNING] The thread is alive, but the task appears to be stalled.")
                log.debug("Attempting to restart listen_loop...")
                self.stop()
                time.sleep(0.5)
                self.start()
                log.debug("[ACTION] Restart thread completed.")
                self._handler.last_heartbeat = time.time()
            elif not self.loop_alive:
                if not self._device:
                    devices = scan()
                    if devices:
                        self._device = devices[0]
                        self._device_id = self._device.get('id')
                        log.info(f"device detected. : {devices}")
                if self._notice_recovery:
                    log.info("Waiting for the device to reconnect...")
                    self._device = None
                    self._uart = None
                    self._device_id = None
                    self._notice_recovery = False
                if self._device:
                    log.info("Attempting to connect to the first available device...")
                    self.conn()
                    self.stop()
                    time.sleep(0.5)
                    self.start()
            time.sleep(1)
        self.heartbeat = False

    def conn(self):
        if self._device.get('type') == "Serial":
            self._uart = SerialUART(self._device_id, baudrate=115200)
            self._uart.connect()
            if not self._handler:
                self._handler = SerialHandler(self._uart)
            self._handler.set_uart(self._uart)
            log.info(f"Connected to device: {self._device_id}")
            if not self._notice_recovery:
                log.info("Measurement will be continued.")
            self._notice_recovery = True

        if self._device.get('type') == "Bluetooth":
            if not self._handler:
                self._handler = BluetoothHandler(self._device_id)
            self._handler.connect()

        self._handler.start_listening()

    def discon(self):
        if self._uart:
            self._uart.close()
            self._uart = None
        if self._handler:
            self._handler = None
        log.debug("Scale disconnected.")

    def zeroset(self):
        if self._handler:
            self._handler.send(ATCommands.SET_WEIGHT_ZERO)

    def setScale(self, value):
        if self._handler:
            self._handler.send(ATCommands.create_weight_scale(value))