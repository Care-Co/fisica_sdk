import time
from queue import Queue
import threading

class SerialHandler:
    def __init__(self, uart, debug=False):
        self.check_thread = None
        self.uart = uart
        self.ser = uart.ser
        self.debug = debug
        self.buffer = bytearray()
        self.hOffsetValue = 0
        self.stopwatch_start = time.time()
        self.auto_mode = False
        self.data_queue = Queue()
        self.listening = False
        self.listen_thread = None
        self.last_heartbeat = time.time()
        self.handler_count = 0

    def _log(self, *args):
        if self.debug:
            from datetime import datetime
            print(f"[{datetime.now()}] [DEBUG]", *args, flush=True)


    def start_listening(self):
        if self.listen_thread and self.listen_thread.is_alive():
            self._log("[INFO] Thread `listen_loop is running.")
            return

        self._log("[INFO] Thread running.")
        self.listening = True
        self.listen_thread = threading.Thread(target=self.listen_loop, daemon=True)
        self.listen_thread.start()

    def stop_listening(self):
        self.listening = False
        if self.listen_thread:
            self._log("[INFO] Waiting to stop thread...")
            self.listen_thread.join(timeout=1)
            self._log("[INFO] Thread stopped.")
            self.listen_thread = None


    def listen_loop(self):
        try:
            while self.listening:
                self.last_heartbeat = time.time()
                try:
                    if not self.handler_count % 24:
                        self._log(f"[DEBUG] loop active, in_waiting: {self.ser.in_waiting}")
                    self.handler_count += 1

                    # 데이터 읽기
                    if self.ser.in_waiting:
                        data = self.ser.read(self.ser.in_waiting)
                        self.buffer.extend(data)

                    # 텍스트 처리: 짧고, 사람이 읽을 수 있는 줄
                    while b'\n' in self.buffer:
                        line, sep, self.buffer = self.buffer.partition(b'\n')
                        line = line.strip()

                        # 텍스트로 명확히 판별되면 처리
                        if self._is_probably_text(line):
                            try:
                                self._handle_text_line(line)
                            except Exception as e:
                                self._log(f"[ERROR] Text parse failed: {e}")
                            continue
                        else:
                            self.buffer = line + sep + self.buffer  # rollback
                            break

                    while True:
                        footer_index = self.buffer.rfind(b'\xf0\x0f')

                        if footer_index >= 643:
                            start_index = footer_index - 643
                            packet = self.buffer[start_index : footer_index + 2]

                            if len(packet) == 645:
                                try:
                                    self._handle_sensor_packet(packet)
                                except Exception as e:
                                    print(f"[ERROR] Binary parse failed: {e}")
                                self.buffer = self.buffer[footer_index + 2:]
                            else:
                                break  # 데이터 부족
                        elif footer_index != -1:
                            self.buffer = self.buffer[footer_index + 2:]  # 정렬
                        else:
                            break  # 푸터 없음

                except Exception as e:
                    self._log(f"[에러] listen_loop exception: {e}")
                    import traceback
                    traceback.print_exc()
                    time.sleep(0.125)
                    continue

                time.sleep(0.125)
            self._log("[DEBUG] listen_loop stopped.")
        except Exception as e:
            time.sleep(0.125)
            self._log(f"[에러] listen_loop exception: {e}")

    def _handle_text_line(self, line: bytes):
        try:
            str_data = line.decode('utf-8').strip()
        except UnicodeDecodeError:
            self._log("[Warning] Decoding failed (not UTF-8):", line)
            return

        if str_data.startswith("AT+"):
            self._log("[Received AT response]:", str_data)

            handlers = {
                "AT+AUTO": lambda v: setattr(self, 'auto_mode', True),
                "AT+WEIGHT=": lambda v: self._log("Weight:", v),
                "AT+SCALE=": lambda v: self._log("Scale:", v),
                "AT+UNIT=": lambda v: self._log("Unit:", v),
                "AT+POWEROFF=": lambda v: self._log("Runtime:", v),
                "AT+UPSENS=": lambda v: self._log("Wakeup Threshold:", v),
                "AT+HMMAX=": lambda v: self._log("Max Pressure:", int(v)) if v.isdigit() else self._log("[Warning] Parsing AT+HMMAX failed"),
                "AT+BATGAUGE=": lambda v: self._log("Battery Level:", v)
            }

            for prefix, action in handlers.items():
                if str_data.startswith(prefix):
                    try:
                        action(str_data[len(prefix):])
                    except Exception as e:
                        self._log(f"[Error] '{prefix}' exception while processing : {e}")
                    break
        else:
            # 👇 ESP32 부트 메시지 등 처리
            if any(str_data.startswith(prefix) for prefix in [
                "ESP-ROM:", "Build:", "rst:", "SPIWP:", "mode:", "load:", "entry"
            ]):
                self._log("[BOOT]", str_data)

                # entry일 때만 AT 명령어 전송
                if str_data.startswith("entry"):
                    self._log("[AUTO CMD] 'entry' detected. Sending AT+STOP then AT+AUTO...")
                    self.uart.send("AT+STOP", newLine=True)
                    time.sleep(1)
                    self.uart.send("AT+AUTO", newLine=True)
            else:
                self._log("[General message]:", str_data)

    def _handle_sensor_packet(self, packet: bytes):
        if len(packet) != 645:
            self._log(f"[Ignore] length doesn't match : {len(packet)}")
            return

        try:
            self._log("Received sensor data")
            stats = packet.hex()
            self.stopwatch_start = time.time()
            self.data_queue.put(stats)

        except Exception as e:
            self._log(f"[Error] Failed to process packet.: {e}")

    def _is_probably_text(self, line: bytes) -> bool:
        return all(32 <= b < 127 or b in (9, 10, 13) for b in line)