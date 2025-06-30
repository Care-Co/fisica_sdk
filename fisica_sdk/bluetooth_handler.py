import threading
import time
import queue
import asyncio
from bleak import BleakClient

from .device import ATCommands
from .parser import DataParser
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

        # 공통 파서 초기화
        self.parser = DataParser(self.data_queue, self.at_response_queue)

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
        result = self._execute_command("connect", timeout=20.0)
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
                    self._listening = False
                time.sleep(0.01)
            log.debug("listen_loop stopped.")
            self.listen_thread = None
        except Exception as e:
            log.critical(f"listen_loop exception: {e}")
            self.listen_thread = None
            raise RuntimeError

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
