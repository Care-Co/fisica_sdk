import time
from queue import Queue
import threading
from time import sleep

class SerialHandler:
    def __init__(self, ser):
        self.ser = ser
        self.buffer = bytearray()
        self.hOffsetValue = 0
        self.stopwatch_start = time.time()
        self.auto_mode = False
        self.data_queue = Queue()

    def start_listening(self):
        threading.Thread(target=self.listen_loop, daemon=True).start()

    def listen_loop(self):
        while True:
            try:
                if self.ser.in_waiting:
                    data = self.ser.read(self.ser.in_waiting)
                    self.buffer += data

                    # \n 기준으로 명령어/로그 분리
                    while b'\n' in self.buffer:
                        line, sep, self.buffer = self.buffer.partition(b'\n')
                        line = line.strip()

                        if self._is_probably_text(line):
                            self._handle_text_line(line)
                        else:
                            self._handle_binary_line(line)

                    # 센서 데이터: 645 단위 수신
                    while len(self.buffer) >= 645:
                        packet = self.buffer[:645]
                        self.buffer = self.buffer[645:]
                        self._handle_sensor_packet(packet)

                time.sleep(0.01)

            except Exception as e:
                print(f"[에러] listen_loop 예외: {e}", flush=True)
                
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
            print("[경고] 디코딩 실패 (UTF-8 아님):", line)
            return

        if str_data.startswith("AT+"):
            print("[AT 응답 수신]:", str_data)

            if str_data.startswith("AT+AUTO"):
                self.auto_mode = True
            elif str_data.startswith("AT+WEIGHT="):
                print("Weight:", str_data[10:])
            elif str_data.startswith("AT+SCALE="):
                print("Scale:", str_data[9:])
            elif str_data.startswith("AT+UNIT="):
                print("Unit:", str_data[8:])
            elif str_data.startswith("AT+POWEROFF="):
                print("Runtime:", str_data[12:])
            elif str_data.startswith("AT+UPSENS="):
                print("Wakeup Threshold:", str_data[10:])
            elif str_data.startswith("AT+HMMAX="):
                try:
                    max_pressure = int(str_data[9:])
                    print("Max Pressure:", max_pressure)
                except ValueError:
                    print("[경고] AT+HMMAX 값 파싱 실패")
            elif str_data.startswith("AT+BATGAUGE="):
                print("Battery Level:", str_data[12:])
        else:
            # 👇 ESP32 부트 메시지 등 처리
            if any(str_data.startswith(prefix) for prefix in [
                "ESP-ROM:", "Build:", "rst:", "SPIWP:", "mode:", "load:", "entry"
            ]):
                print("[ESP 부트 메시지]:", str_data)
            else:
                print("[일반 메시지]:", str_data)

    def _handle_binary_line(self, line: bytes):
        # 단순히 센서 데이터 일부일 가능성 → 버퍼에 재합류
        self.buffer = line + b'\n' + self.buffer
        
    def _handle_sensor_packet(self, packet: bytes):
        if not self.auto_mode:
            return
        if len(packet) != 645:
            print(f"[무시] 길이 맞지 않음: {len(packet)}", flush=True)
            return

        try:
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
                "heatmap": [
                    packet[22 * y + x] + self.hOffsetValue
                    for y in range(29) for x in range(22)
                ],
                "duration_ms": int((time.time() - self.stopwatch_start) * 1000)
            }

            print("[센서 데이터 수신 완료]")
            self.stopwatch_start = time.time()
            self.data_queue.put(stats)

        except Exception as e:
            print(f"[에러] 센서 패킷 처리 실패: {e}", flush=True)

    def _is_probably_text(self, line: bytes) -> bool:
        try:
            line.decode('utf-8')
            return True
        except UnicodeDecodeError:
            return False