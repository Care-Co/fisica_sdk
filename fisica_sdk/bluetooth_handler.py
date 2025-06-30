import threading
import time
import queue
import asyncio
from bleak import BleakClient

from .device import ATCommands
from .parser import *
from .utils import log
import errno

class BluetoothHandler:
    def __init__(self, device_address):
        self.device_address = device_address
        self.client = None
        self.listen_thread = None
        self._listening = False
        self.last_heartbeat = time.time()
        self._buffer = bytearray()
        self.data_queue = queue.Queue()
        self.at_response_queue = queue.Queue()  # AT 응답 큐 추가

        # 전용 이벤트 루프와 스레드
        self._loop = None
        self._loop_thread = None
        self._command_queue = queue.Queue()
        self._connected = False

        # 특성 UUID (연결 후 자동 발견)
        self.char_uuid = None

        # 이벤트 루프 스레드 시작
        self._start_loop_thread()

    def _start_loop_thread(self):
        """전용 이벤트 루프 스레드 시작"""
        self._loop_thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self._loop_thread.start()

        # 루프가 시작될 때까지 대기
        while self._loop is None:
            time.sleep(0.01)

    def _run_event_loop(self):
        """전용 이벤트 루프 실행"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            # 명령 처리 태스크 시작
            self._loop.create_task(self._command_processor())
            self._loop.run_forever()
        except Exception as e:
            log.error(f"Event loop error: {e}")
        finally:
            self._loop.close()

    async def _command_processor(self):
        """명령 큐를 처리하는 태스크"""
        while True:
            try:
                # 명령 대기 (논블로킹)
                try:
                    command, args, response_queue = self._command_queue.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.01)
                    continue

                # 명령 실행
                try:
                    if command == "connect":
                        result = await self._connect_async()
                    elif command == "disconnect":
                        result = await self._disconnect_async()
                    elif command == "send":
                        data = args[0]
                        result = await self._send_async(data)
                    elif command == "start_notify":
                        callback = args[0]
                        result = await self._start_notify_async(callback)
                    elif command == "stop_notify":
                        result = await self._stop_notify_async()
                    else:
                        result = False

                    response_queue.put(result)
                except Exception as e:
                    log.error(f"Command {command} error: {e}")
                    response_queue.put(False)

            except Exception as e:
                log.error(f"Command processor error: {e}")
                await asyncio.sleep(0.1)

    def _execute_command(self, command, *args, timeout=15.0):  # 타임아웃 증가
        """명령을 이벤트 루프에서 실행하고 결과 반환"""
        if not self._loop or not self._loop_thread.is_alive():
            log.error("Event loop not running")
            return False

        response_queue = queue.Queue()
        self._command_queue.put((command, args, response_queue))

        try:
            result = response_queue.get(timeout=timeout)
            log.debug(f"Command {command} completed: {result}")
            return result
        except queue.Empty:
            log.error(f"Command {command} timeout after {timeout}s")
            return False

    async def _connect_async(self):
        """비동기 연결"""
        try:
            log.debug(f"Attempting BLE connection to {self.device_address}")
            self.client = BleakClient(self.device_address)

            # 연결 시도 (더 긴 타임아웃)
            await asyncio.wait_for(self.client.connect(), timeout=10.0)
            self._connected = True
            log.debug(f"Bluetooth connected to {self.device_address}")

            # 연결 후 적절한 특성 UUID 찾기
            if self.char_uuid is None:
                await self._discover_characteristics()

            return True
        except asyncio.TimeoutError:
            log.error(f"Bluetooth connection timeout to {self.device_address}")
            self._connected = False
            return False
        except Exception as e:
            log.error(f"Bluetooth connection failed: {e}")
            self._connected = False
            return False

    async def _disconnect_async(self):
        """비동기 연결 해제"""
        try:
            if self.client and self.client.is_connected:
                await self.client.disconnect()
            self._connected = False
            log.debug("Bluetooth disconnected")
            return True
        except Exception as e:
            log.error(f"Bluetooth disconnect error: {e}")
            return False

    def _ensure_bytes(self, data):
        """데이터를 bytes 타입으로 변환"""
        if isinstance(data, bytes):
            return data
        elif isinstance(data, (list, tuple)):
            return bytes(data)
        elif isinstance(data, str):
            return data.encode('utf-8')
        else:
            log.warning(f"Unknown data type: {type(data)} - {data}")
            try:
                return bytes(data)
            except Exception as e:
                log.error(f"Failed to convert to bytes: {e}")
                return b''

    async def _send_async(self, data):
        """비동기 데이터 전송"""
        try:
            if not self.client or not self.client.is_connected:
                log.error("Client not connected for sending data")
                return False

            if self.char_uuid is None:
                log.error("No characteristic UUID available for sending data")
                return False

            # 데이터 타입 변환
            cmd_bytes = self._ensure_bytes(data)

            if not cmd_bytes:
                log.error("Empty data after conversion")
                return False

            log.debug(f"Sending BLE data: {cmd_bytes.hex()} (length: {len(cmd_bytes)})")
            await self.client.write_gatt_char(self.char_uuid, cmd_bytes)
            log.debug("BLE data sent successfully")
            return True

        except Exception as e:
            log.error(f"Bluetooth write error: {e}")
            return False

    async def _start_notify_async(self, callback):
        """비동기 알림 시작"""
        try:
            if not self.client or not self.client.is_connected:
                log.error("Client not connected for notifications")
                return False

            if self.char_uuid is None:
                log.error("No characteristic UUID available for notifications")
                return False

            await self.client.start_notify(self.char_uuid, callback)
            log.debug("Bluetooth notifications started")
            return True
        except Exception as e:
            log.error(f"Start notify error: {e}")
            return False

    async def _stop_notify_async(self):
        """비동기 알림 중지"""
        try:
            if self.client and self.client.is_connected and self.char_uuid:
                await self.client.stop_notify(self.char_uuid)
            log.debug("Bluetooth notifications stopped")
            return True
        except Exception as e:
            log.error(f"Stop notify error: {e}")
            return False

    def set_uart(self, uart):
        """호환성을 위한 더미 메서드"""
        pass

    def connect(self):
        """동기식 연결"""
        log.debug(f"Starting connection to {self.device_address}")
        result = self._execute_command("connect", timeout=15.0)
        if result:
            log.info("Bluetooth connection successful")
        else:
            log.error("Bluetooth connection failed")
        return result

    def send(self, data):
        """데이터 전송 (타입 변환 포함)"""
        try:
            # 미리 변환해서 로그 출력
            cmd_bytes = self._ensure_bytes(data)
            log.debug(f"Sending command: original={data}, type={type(data)}, bytes={cmd_bytes.hex()}")

            result = self._execute_command("send", cmd_bytes, timeout=5.0)
            if result:
                log.debug("Command sent successfully")
            else:
                log.error("Command send failed")
            return result
        except Exception as e:
            log.error(f"Bluetooth send error: {e}")
            return False

    def start_listening(self):
        """BLE 리스닝 시작"""
        if self.listen_thread and self.listen_thread.is_alive():
            log.debug("Thread `listen_loop` is running.")
            return

        if not self._connected:
            log.error("Cannot start listening: not connected")
            return

        log.debug("Starting BLE listening...")
        self._listening = True

        def notification_callback(sender, data):
            """데이터 수신 콜백"""
            try:
                self._buffer.extend(data)
                self.last_heartbeat = time.time()
                log.debug(f"Raw data received: {data.hex()}, buffer len : {len(self._buffer)}")
            except Exception as e:
                log.error(f"Bluetooth notification processing error: {e}")

        # 알림 시작
        success = self._execute_command("start_notify", notification_callback, timeout=10.0)

        if success:
            self.listen_thread = threading.Thread(target=self.listen_loop, daemon=True)
            self.listen_thread.start()
            time.sleep(1)
        else:
            self._listening = False
            log.error("Failed to start Bluetooth listening")

    def stop_listening(self):
        self._listening = False
        if self.listen_thread:
            log.debug("[INFO] Waiting to stop thread...")
            self.listen_thread.join(timeout=1)
            log.debug("[INFO] Thread stopped.")
            self.listen_thread = None

        self._execute_command("stop_notify")

    def start_measurement(self):
        if self._listening:
            log.debug("Sending initial AT commands...")
            self.send(ATCommands.SET_DEVICE_BLE)
            time.sleep(0.5)
            self.send(ATCommands.START_CONTINUOUS_MEASUREMENT)
            log.debug("Measurement has successfully started.")

    def listen_loop(self):
        try:
            while self._listening:
                self.last_heartbeat = time.time()
                try:
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
        """AT Command 응답 파싱"""
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

    async def _discover_characteristics(self):
        """연결된 기기의 특성을 탐색하여 적절한 UUID 찾기"""
        try:
            if not self.client or not self.client.is_connected:
                log.error("Client not connected for characteristic discovery")
                return False

            log.debug("Discovering device characteristics...")

            # 알려진 UUID들 (우선순위 순)
            known_uuids = [
                "beb5483e-36e1-4688-b7f5-ea07361b26a8",  # Fisica Scale 기본
                # 다른 알려진 UUID들 추가 가능
            ]

            # 모든 서비스의 특성 수집
            available_chars = []
            writable_chars = []
            notify_chars = []

            for service in self.client.services:
                log.debug(f"Service: {service.uuid} - {service.description}")

                for char in service.characteristics:
                    available_chars.append(char.uuid)

                    # 속성 확인
                    if "write" in char.properties:
                        writable_chars.append(char.uuid)
                    if "notify" in char.properties:
                        notify_chars.append(char.uuid)

                    log.debug(f"  Characteristic: {char.uuid} - Properties: {char.properties}")

            # UUID 선택 우선순위
            selected_uuid = None

            # 1. 알려진 UUID가 있는지 확인
            for known_uuid in known_uuids:
                if known_uuid in available_chars:
                    selected_uuid = known_uuid
                    log.debug(f"Using known UUID: {selected_uuid}")
                    break

            # 2. 알려진 UUID가 없으면 write+notify 가능한 특성 찾기
            if selected_uuid is None:
                for char_uuid in available_chars:
                    if char_uuid in writable_chars and char_uuid in notify_chars:
                        selected_uuid = char_uuid
                        log.info(f"Using write+notify capable UUID: {selected_uuid}")
                        break

            # 3. write 가능한 특성 찾기
            if selected_uuid is None and writable_chars:
                selected_uuid = writable_chars[0]
                log.info(f"Using writable UUID: {selected_uuid}")

            # 4. 첫 번째 특성 사용
            if selected_uuid is None and available_chars:
                selected_uuid = available_chars[0]
                log.warning(f"Using first available UUID: {selected_uuid}")

            if selected_uuid:
                self.char_uuid = selected_uuid
                log.debug(f"Characteristic UUID set to: {self.char_uuid}")
                return True
            else:
                log.error("No suitable characteristics found")
                return False

        except Exception as e:
            log.error(f"Characteristic discovery failed: {e}")
            # 백업으로 기본 UUID 사용
            if self.char_uuid is None:
                self.char_uuid = "beb5483e-36e1-4688-b7f5-ea07361b26a8"
                log.warning(f"Using fallback UUID: {self.char_uuid}")
            return False

    def _log_error_code(self, code):
        """에러 코드별 메시지 출력"""
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

    def close(self):
        """연결 해제"""
        self.stop_listening()
        result = self._execute_command("disconnect")

        # 이벤트 루프 정리
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

        if self._loop_thread and self._loop_thread.is_alive():
            self._loop_thread.join(timeout=1.0)

        return result