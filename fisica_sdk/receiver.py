import threading
import time
from fisica_sdk.serial_uart import SerialUART
from fisica_sdk.bluetooth_uart import BluetoothUART
from fisica_sdk.serial_handler import SerialHandler
from fisica_sdk.port_scanner import find_bluetooth_com_ports

class Receiver:
    def __init__(self, device_id, callback, debug=False):
        self.check_thread = None
        self.heartbeat = False
        self.device_id = device_id
        self.callback = callback
        self.debug = debug
        self._stop_event = threading.Event()
        self._thread = None
        self.uart = None
        self.handler = None

        if self.device_id in find_bluetooth_com_ports():
            self.uart = BluetoothUART(baudrate=115200)
        else:
            self.uart = SerialUART(self.device_id, baudrate=115200)

        self.uart.connect()
        self.handler = SerialHandler(self.uart, debug=self.debug)

    def start(self):
        self._stop_event.clear()

        self.handler.start_listening()
        if not self.heartbeat:
            self.heartbeat = True
            # self.last_heartbeat = time.time()
            self.check_thread = threading.Thread(target=self.is_listening_active, daemon=True)
            self.check_thread.start()


        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

        # self._log("Receiver started.")
        self.uart.send("AT+AUTO", newLine=True)

    def stop(self):
        self._stop_event.set()
        if self.handler:
            self.handler.stop_listening()
        if self.uart:
            self.uart.send("AT+STOP", newLine=True)
            self.uart.close()
        if self._thread:
            self._thread.join()
        # self._log("Receiver stopped.")

    def _read_loop(self):
        while not self._stop_event.is_set():
            time.sleep(0.25)
            if self.handler:
                try:
                    data = self.handler.data_queue.get(block=False)
                    if data:
                        self.callback(data)
                except Exception as e:
                    # print(f"[에러] listen_loop 예외: {e}", flush=True)
                    continue

    def is_listening_active(self):
        while True:
            alive = self.handler.listen_thread and self.handler.listen_thread.is_alive()
            age = time.time() - self.handler.last_heartbeat
            # print(f"[HEALTH] Thread alive: {alive}, heartbeat age: {age:.2f}s", flush=True)
            if alive and age > 2:
                # print("[WARNING] 쓰레드는 살아있지만 작업이 중단된 것 같습니다!", flush=True)
                # print("[ACTION] listen_loop 재시작 시도 중...", flush=True)
                self.stop()
                time.sleep(0.5)
                self.start()
                # print("[ACTION] 재시작 완료", flush=True)
                self.handler.last_heartbeat = time.time()
            time.sleep(1)

    # def _log(self, *args):
    #     if self.debug:
    #         from datetime import datetime
    #         print(f"[{datetime.now()}] [RECEIVER DEBUG]", *args)