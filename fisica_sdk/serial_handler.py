import time
import threading
from queue import Queue

from .parser import *
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

    def set_uart(self, uart):
        self._ser = uart.ser
        self._uart = uart

    def start_listening(self):
        if self.listen_thread and self.listen_thread.is_alive():
            log.debug("Thread `listen_loop` is running.")
            return

        log.debug("Thread running.")
        self.listen_thread = threading.Thread(target=self.listen_loop, daemon=True)
        self.listen_thread.start()

    def start_measurement(self):
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

                    # 종료 구분자별로 데이터 처리
                    self._process_all_data_by_terminator()

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

    def _process_all_data_by_terminator(self):
        """종료 구분자를 기준으로 모든 데이터를 순차적으로 처리"""
        self._check_and_process_plantar_data()
        at_footer_pos = self._buffer.rfind(b'\xff\xf0')       # AT Command 응답

        if at_footer_pos != -1:
            # AT Command 응답 처리
            self._process_at_command_response(at_footer_pos)

    def _process_at_command_response(self, footer_pos):
        """AT Command 응답 처리 (종료: ff f0)"""
        try:
            # AT Command는 8바이트 고정이므로 footer 위치 확인
            if footer_pos >= 6:  # STX(1) + Header(1) + Type(1) + RET(1) + Data(2) + ETX(2) = 8
                packet_start = footer_pos - 6

                # STX 확인
                if self._buffer[packet_start] == 0x24:
                    at_packet = bytes(self._buffer[packet_start:footer_pos + 2])

                    if len(at_packet) == 8:
                        log.debug(f"Processing AT command response: {at_packet.hex()}")
                        self._handle_at_response(at_packet)

                        # 처리된 AT Command 제거
                        del self._buffer[:footer_pos + 2]
                        return

            # AT Command 형식이 맞지 않으면 해당 footer만 제거
            log.debug(f"Invalid AT command format at footer position {footer_pos}")
            log.debug(f"Context: {self._buffer[max(0, footer_pos-10):footer_pos+10].hex()}")

        except Exception as e:
            log.error(f"AT command response parsing failed: {e}")

        # 오류 발생 시 해당 footer까지만 제거
        del self._buffer[:footer_pos + 2]

    def _check_and_process_plantar_data(self):
        """
        족저압 데이터 크기 기반 처리
        조건:
        1. 버퍼 크기가 645 이상
        2. 마지막 footer index - 645 > 0
        """
        # 조건 1: 버퍼 크기 확인
        if len(self._buffer) < 644:
            return False  # 버퍼 계속 쌓기

        # 마지막 f0 0f footer 찾기
        last_footer_index = self._buffer.rfind(b'\xf0\x0f')

        if last_footer_index == -1:
            return False  # footer가 없으면 계속 쌓기

        if last_footer_index < 642:
            return False  # 조건 불만족, 계속 쌓기

        # 조건 만족: 족저압 데이터 처리
        try:
            log.debug(f"Processing plantar data: buffer size={len(self._buffer)}, "
                      f"footer at {last_footer_index}")

            # footer까지의 데이터 추출 (footer 포함)
            frame_data = self._buffer[:last_footer_index + 2]

            # 족저압 데이터 파싱
            matrix = extract_frames_from_buffer(frame_data, last_footer_index)
            if matrix:
                frame = parse_frame_from_chunk(matrix)
                if frame is not None:
                    self.data_queue.put(frame)
                    log.debug(f"Successfully parsed plantar frame, queue size: {self.data_queue.qsize()}")
                else:
                    log.debug("parse_frame_from_chunk returned None")
            else:
                log.debug("extract_frames_from_buffer returned empty")

        except Exception as e:
            log.error(f"Plantar data parsing failed: {e}")
            log.debug(f"Problematic data: {self._buffer[:100].hex()}...")

        # 처리된 데이터 제거 (footer 포함)
        del self._buffer[:last_footer_index + 2]

        return True  # 처리 완료
    def _handle_at_response(self, packet: bytes):
        """AT Command 응답 파싱 (기존 로직 유지)"""
        if len(packet) != 8:
            log.warning(f"AT packet length mismatch: expected 8, got {len(packet)}")
            return

        stx = packet[0]
        header = packet[1]
        type_byte = packet[2]
        ret = packet[3]
        data_high = packet[4]
        data_low = packet[5]
        etx = packet[6:8]

        log.debug(f"[AT Response] Header: 0x{header:02X}, Type: 0x{type_byte:02X}, "
                  f"RET: 0x{ret:02X}, Data: 0x{data_high:02X}{data_low:02X}")

        # 성공/실패 확인
        if ret == 0x01:
            log.debug("AT Command executed successfully")
        elif ret == 0x00:
            error_code = data_low if data_high == 0x00 else (data_high << 8) | data_low
            self._log_error_code(error_code)

        # AT 응답을 큐에 추가
        response = {
            'header': header,
            'type': type_byte,
            'ret': ret,
            'data_high': data_high,
            'data_low': data_low,
            'success': ret == 0x01
        }
        self.at_response_queue.put(response)

    def _log_error_code(self, code):
        """에러 코드별 메시지 출력 (기존 로직 유지)"""
        error_messages = {
            1: "설정 값이 기존 값과 동일하거나 이미 다른 디바이스와 연결됨",
            2: "설정 값이 범위를 벗어남",
            3: "AT Command 포맷이 일치하지 않음",
            4: "저장된 데이터가 존재하지 않음",
            5: "측정 동작 중에는 족저압 측정 횟수 변경 불가",
            6: "연결된 디바이스가 아님",
            7: "OTA 펌웨어 업데이트 오류",
            8: "펌웨어가 이미 최신 버전",
            9: "WiFi 연결이 끊어짐"
        }
        message = error_messages.get(code, f"알 수 없는 오류 ({code})")
        log.debug(f"[Error] Error Code {code}: {message}")

    # 텍스트 메시지 처리는 별도로 필요한 경우에만
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