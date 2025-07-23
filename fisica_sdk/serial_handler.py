# serial_handler.py (완성된 버전)
import time
import threading
from queue import Queue

from .parser import DataParser
from .device import ATCommands
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
        self.at_response_queue = Queue()

        # 공통 파서 초기화
        self.parser = DataParser(self.data_queue, self.at_response_queue)

    def set_uart(self, uart):
        self._ser = uart.ser
        self._uart = uart

    def start_listening(self):
        if self.listen_thread and self.listen_thread.is_alive():
            log.debug("Thread `listen_loop` is running.")
            return

        # 종료된 스레드 정리
        if self.listen_thread and not self.listen_thread.is_alive():
            self.listen_thread = None

        log.debug("Thread running.")
        self.listen_thread = threading.Thread(target=self.listen_loop, daemon=True)
        self.listen_thread.start()

        self._listening = True

    def start_measurement(self):

        if not self._listening or not (self.listen_thread and self.listen_thread.is_alive()):
            log.debug("Restarting listening for new measurement...")
            self.start_listening()

        if self._listening:
            self.send(ATCommands.SET_DEVICE_BLE)
            self.send(ATCommands.START_CONTINUOUS_MEASUREMENT)
            log.info("Measurement has successfully started.")

    def send(self, data):
        if self._uart:
            self._uart.send(data)

    def stop_listening(self):
        self._listening = False
        if self.listen_thread:
            log.debug("[INFO] Waiting to stop thread...")
            self.listen_thread.join(timeout=1)
            log.debug("[INFO] Thread stopped.")
            self.listen_thread = None

    def listen_loop(self):
        try:
            self._listening = True
            while self._listening:
                self.last_heartbeat = time.time()
                try:
                    if self._ser.in_waiting:
                        data = self._ser.read(self._ser.in_waiting)
                        self._buffer.extend(data)
                        log.debug(f"Raw data received: {data.hex()}, buffer len : {len(self._buffer)}")

                    # 공통 파서 사용
                    self.parser.process_all_data_by_terminator(self._buffer)

                except Exception as e:
                    if isinstance(e, OSError) and e.errno == errno.ENXIO:
                        log.error(
                            "\nFisica SDK\n"
                            "   The connection to the device appears to have been lost during measurement.\n"
                            "   Entering standby mode until the device is reconnected.\n"
                        )
                    else:
                        log.critical(f"[Unexpected Exception] {e}")
                time.sleep(0.01)
            # log.debug("listen_loop stopped.")
            # self.listen_thread = None
        except Exception as e:
            log.critical(f"listen_loop exception: {e}")
            raise RuntimeError
        finally:
            self.listen_thread = None
            self._listening = False

    def _process_text_messages_if_needed(self):
        """ESP32 부트 메시지 등 특별한 경우에만 처리"""
        # 버퍼에 알려진 종료 구분자가 없고, 개행 문자가 있는 경우에만
        if (b'\xf0\x0f' not in self._buffer and
                b'\xff\xf0' not in self._buffer and
                b'\n' in self._buffer and
                len(self._buffer) < 500):  # 작은 크기일 때만

            lines_processed = 0
            while b'\n' in self._buffer and lines_processed < 3:
                newline_pos = self._buffer.find(b'\n')
                line = self._buffer[:newline_pos]

                if self._is_probably_text(line):
                    try:
                        self._handle_text_line(line)
                        del self._buffer[:newline_pos + 1]
                        lines_processed += 1
                    except Exception as e:
                        log.error(f"Text processing failed: {e}")
                        del self._buffer[:newline_pos + 1]
                else:
                    break

    def _is_probably_text(self, line: bytes) -> bool:
        """텍스트 여부 판단"""
        if len(line) == 0:
            return True
        return all(32 <= b < 127 or b in (9, 10, 13) for b in line[:50])  # 처음 50바이트만 확인

    def _handle_text_line(self, line: bytes):
        """텍스트 라인 처리 (기존 로직 유지)"""
        try:
            str_data = line.decode('utf-8').strip()
        except UnicodeDecodeError:
            log.debug(f"[Warning] Decoding failed (not UTF-8): {line}")
            return

        # ESP32 부트 메시지 처리
        if any(str_data.startswith(prefix) for prefix in [
            "ESP-ROM:", "Build:", "rst:", "SPIWP:", "mode:", "load:", "entry"
        ]):
            log.debug(f"[BOOT] : {str_data}")

            # entry일 때 장치 초기화
            if str_data.startswith("entry"):
                log.debug("[AUTO CMD] 'entry' detected. Initializing device...")
        else:
            log.debug(f"[General message] : {str_data}")