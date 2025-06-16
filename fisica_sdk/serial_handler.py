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

    def _log(self, *args):
        if self.debug:
            from datetime import datetime
            print(f"[{datetime.now()}] [DEBUG]", *args, flush=True)


    def start_listening(self):
        if self.listen_thread and self.listen_thread.is_alive():
            self._log("[INFO] 이미 listen_loop 쓰레드가 실행 중입니다.")
            return

        self._log("[INFO] 쓰레드 시작됨.")
        self.listening = True
        self.listen_thread = threading.Thread(target=self.listen_loop, daemon=True)
        self.listen_thread.start()

    def stop_listening(self):
        self.listening = False
        if self.listen_thread:
            self._log("[INFO] 쓰레드 종료 대기 중...")
            self.listen_thread.join(timeout=1)
            self._log("[INFO] 쓰레드 종료됨.")
            self.listen_thread = None


    def listen_loop(self):
        try:
            while self.listening:
                self.last_heartbeat = time.time()
                try:
                    # self._log(f"[DEBUG] loop active, in_waiting: {self.ser.in_waiting}")

                    if self.ser.in_waiting:
                        data = self.ser.read(self.ser.in_waiting)
                        self._log(f"[DEBUG] data read: {data}")
                        self._log(f"[DEBUG] data read: {data}")
                        self.buffer += data

                        # \n 기준으로 명령어/로그 분리
                        while b'\n' in self.buffer:
                            line, sep, self.buffer = self.buffer.partition(b'\n')
                            line = line.strip()

                            if self._is_probably_text(line):
                                self._handle_text_line(line)
                            else:
                                self._handle_binary_line(line)
                        self._log(f"[DEBUG] buffer snapshot: {self.buffer.hex()[-100:]}")
                        while len(self.buffer) >= 645:
                            footer_index = self.buffer.rfind(b'\xf0\x0f')
                            self._log(f"[DEBUG] buffer len: {len(self.buffer)}, footer_index: {footer_index}")
                            if footer_index != -1:
                                packet_start = footer_index - 643
                                self._log(f"[DEBUG] packet_start: {packet_start}")
                                self._log(f"[DEBUG] computed packet_start: {packet_start}")
                                if packet_start >= 0 and len(self.buffer) >= packet_start + 645:
                                    packet = self.buffer[packet_start:packet_start + 645]
                                    self._log("[DEBUG] valid packet detected, calling _handle_sensor_packet")
                                    self._log(f"[DEBUG] Calling _handle_sensor_packet()")
                                    self.buffer = self.buffer[packet_start + 645:]
                                    self._handle_sensor_packet(packet)
                                elif packet_start < 0:
                                    self._log("[DEBUG] footer too early; skipping")
                                    self.buffer = self.buffer[footer_index + 2:]
                                    continue
                                else:
                                    self._log("[DEBUG] waiting for more data...")
                                    break
                            else:
                                self._log("[DEBUG] no footer yet")
                                continue
                    # self._log("[DEBUG] next loop")
                except Exception as e:
                    self._log(f"[에러] listen_loop 예외: {e}")
                    import traceback
                    traceback.print_exc()
                    time.sleep(0.125)
                    continue

                time.sleep(0.125)
            self._log("[DEBUG] listen_loop 종료됨")
        except Exception as e:
            time.sleep(0.125)
            self._log(f"[에러] listen_loop 예외: {e}")

    def _handle_line(self, line: bytes):
        # 길이가 짧고 ASCII 문자만 포함되면 텍스트로 처리
        if self._is_probably_text(line):
            self._handle_text_line(line)
        else:
            self._handle_binary_line(line)

    def _handle_text_line(self, line: bytes):

        try:
            str_data = line.decode('utf-8').strip()
        except UnicodeDecodeError:
            self._log("[경고] 디코딩 실패 (UTF-8 아님):", line)
            return

        if str_data.startswith("AT+"):
            self._log("[AT 응답 수신]:", str_data)

            handlers = {
                "AT+AUTO": lambda v: setattr(self, 'auto_mode', True),
                "AT+WEIGHT=": lambda v: self._log("Weight:", v),
                "AT+SCALE=": lambda v: self._log("Scale:", v),
                "AT+UNIT=": lambda v: self._log("Unit:", v),
                "AT+POWEROFF=": lambda v: self._log("Runtime:", v),
                "AT+UPSENS=": lambda v: self._log("Wakeup Threshold:", v),
                "AT+HMMAX=": lambda v: self._log("Max Pressure:", int(v)) if v.isdigit() else self._log("[경고] AT+HMMAX 값 파싱 실패"),
                "AT+BATGAUGE=": lambda v: self._log("Battery Level:", v)
            }

            for prefix, action in handlers.items():
                if str_data.startswith(prefix):
                    try:
                        action(str_data[len(prefix):])
                    except Exception as e:
                        self._log(f"[에러] '{prefix}' 처리 중 예외: {e}")
                    break
        else:
            # 👇 ESP32 부트 메시지 등 처리
            if any(str_data.startswith(prefix) for prefix in [
                "ESP-ROM:", "Build:", "rst:", "SPIWP:", "mode:", "load:", "entry"
            ]):
                print("[BOOT]", str_data)

                # entry일 때만 AT 명령어 전송
                if str_data.startswith("entry"):
                    print("[AUTO CMD] 'entry' detected. Sending AT+STOP then AT+AUTO...")
                    self.uart.send("AT+STOP", newLine=True)
                    time.sleep(1)
                    self.uart.send("AT+AUTO", newLine=True)
            else:
                self._log("[일반 메시지]:", str_data)

    def _handle_binary_line(self, line: bytes):
        # 단순히 센서 데이터 일부일 가능성 → 버퍼에 재합류
        self.buffer = line + b'\n' + self.buffer

    def _handle_sensor_packet(self, packet: bytes):
        if len(packet) != 645:
            self._log(f"[무시] 길이 맞지 않음: {len(packet)}")
            return

        try:
            self._log("trying")
            weight = f"{packet[640]}.{packet[641]}"
            battery = str(packet[642])
            data_list = [val for val in packet[:638] if val != 0]

            stats = {
                "min": min(data_list) if data_list else 0,
                "max": max(data_list) if data_list else 0,
                "avg": int(sum(data_list) / len(data_list)) if data_list else 0,
                "count": len(data_list),
                "weight": weight,
                "battery": battery,
                "heatmap": packet.hex(),
                "duration_ms": int((time.time() - self.stopwatch_start) * 1000)
            }

            self._log("[센서 데이터 수신 완료]")
            self.stopwatch_start = time.time()
            self.data_queue.put(stats)

        except Exception as e:
            self._log(f"[에러] 센서 패킷 처리 실패: {e}")

    def _is_probably_text(self, line: bytes) -> bool:
        return all(32 <= b < 127 or b in (9, 10, 13) for b in line)