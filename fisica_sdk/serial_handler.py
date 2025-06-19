import time
import threading
from queue import Queue
from .parser import *
from .utils import log
import errno

class SerialHandler:
    def __init__(self, uart):
        self.listen_thread = None
        self._listening = False
        self._ser = uart.ser
        self._uart = uart
        self.last_heartbeat = time.time()
        self._buffer = bytearray()
        self.data_queue = Queue()

    def set_uart(self, uart):
        self._ser = uart.ser
        self._uart = uart

    def start_listening(self):
        if self.listen_thread and self.listen_thread.is_alive():
            log.debug("Thread `listen_loop` is running.")
            return

        log.debug("Thread running.")
        self._listening = True
        self.listen_thread = threading.Thread(target=self.listen_loop, daemon=True)
        self.listen_thread.start()

    def stop_listening(self):
        self._listening = False
        if self.listen_thread:
            log.debug("[INFO] Waiting to stop thread...")
            self.listen_thread.join(timeout=1)
            log.debug("[INFO] Thread stopped.")
            self.listen_thread = None


    def listen_loop(self):
        try:
            while self._listening:
                self.last_heartbeat = time.time()
                try:
                    if self._ser.in_waiting:
                        data = self._ser.read(self._ser.in_waiting)
                        self._buffer.extend(data)

                    # 텍스트 처리: 짧고, 사람이 읽을 수 있는 줄
                    while b'\n' in self._buffer:
                        line, sep, self._buffer = self._buffer.partition(b'\n')
                        line = line.strip()

                        # 텍스트로 명확히 판별되면 처리
                        if self._is_probably_text(line):
                            try:
                                self._handle_text_line(line)
                            except Exception as e:
                                log.error(f"Text parse failed: {e}")
                            continue
                        else:
                            self._buffer = line + sep + self._buffer  # rollback
                            break

                    while True:
                        footer_index = self._buffer.rfind(b'\xf0\x0f')
                        if footer_index == -1:
                            break

                        matrix = extract_frames_from_buffer(self._buffer, footer_index)
                        if not matrix:
                            break
                        try:
                            frame = parse_frame_from_chunk(matrix)
                            if frame is not None:
                                self.data_queue.put(frame)
                        except Exception as e:
                            log.error(f"Binary parse failed: {e}")

                        del self._buffer[:footer_index + 2]

                except Exception as e:
                    if isinstance(e, OSError) and e.errno == errno.ENXIO:
                        log.error(
                            "\nFisica SDK\n"
                            "   The connection to the device appears to have been lost during measurement.\n"
                            "   Entering standby mode until the device is reconnected.\n"
                        )
                    else:
                        log.critical(f"[Unexpected Exception] {e}")
                    self._listening = False
                time.sleep(0.01)
            log.debug("listen_loop stopped.")
            self.listen_thread = None
        except Exception as e:
            log.critical(f"listen_loop exception: {e}")
            self.listen_thread = None
            raise RuntimeError

    def _handle_text_line(self, line: bytes):
        try:
            str_data = line.decode('utf-8').strip()
        except UnicodeDecodeError:
            log.error("[Warning] Decoding failed (not UTF-8):", line)
            return

        if str_data.startswith("AT+"):
            log.debug(f"[Received AT response] : {str_data}")

            handlers = {
                "AT+AUTO": lambda v: log.debug(f"Auto : {v}"),
                "AT+WEIGHT=": lambda v: log.debug(f"Weight : {v}"),
                "AT+SCALE=": lambda v: log.debug(f"Scale : {v}"),
                "AT+UNIT=": lambda v: log.debug(f"Unit : {v}"),
                "AT+POWEROFF=": lambda v: log.debug(f"Runtime : {v}"),
                "AT+UPSENS=": lambda v: log.debug(f"Wakeup Threshold : {v}"),
                "AT+HMMAX=": lambda v: log.debug(f"Max Pressure:", int(v)) if v.isdigit() else log.debug("[Warning] Parsing AT+HMMAX failed"),
                "AT+BATGAUGE=": lambda v: log.debug(f"Battery Level : {v}")
            }

            for prefix, action in handlers.items():
                if str_data.startswith(prefix):
                    try:
                        action(str_data[len(prefix):])
                    except Exception as e:
                        log.error(f"'{prefix}' exception while processing : {e}")
                    break
        else:
            # 👇 ESP32 부트 메시지 등 처리
            if any(str_data.startswith(prefix) for prefix in [
                "ESP-ROM:", "Build:", "rst:", "SPIWP:", "mode:", "load:", "entry"
            ]):
                log.debug(f"[BOOT] : {str_data}")

                # entry일 때만 AT 명령어 전송
                if str_data.startswith("entry"):
                    log.debug("[AUTO CMD] 'entry' detected. Sending AT+STOP then AT+AUTO...")
                    self._uart.send("AT+STOP", newLine=True)
                    time.sleep(1)
                    self._uart.send("AT+AUTO", newLine=True)
            else:
                log.debug(f"[General message] : {str_data}")

    def _handle_sensor_packet(self, packet: bytes):
        if len(packet) != 645:
            log.debug(f"[Ignore] length doesn't match : {len(packet)}")
            return

        try:
            log.debug("Received sensor data")
            stats = packet.hex()
            self.data_queue.put(stats)

        except Exception as e:
            log.critical(f"Failed to process packet.: {e}")

    def _is_probably_text(self, line: bytes) -> bool:
        return all(32 <= b < 127 or b in (9, 10, 13) for b in line)