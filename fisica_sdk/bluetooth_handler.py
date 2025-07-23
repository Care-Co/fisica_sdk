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
        self.at_response_queue = queue.Queue()

        # 공통 파서 초기화
        self.parser = DataParser(self.data_queue, self.at_response_queue)

        # 전용 이벤트 루프와 스레드
        self._loop = None
        self._loop_thread = None
        self._command_queue = queue.Queue()
        self._connected = False

        # 특성 정보 (handle과 UUID 모두 저장)
        self.char_uuid = None
        self.char_handle = None
        self.selected_char = None  # 선택된 특성 객체

        # 연결 재시도 설정
        self.max_retries = 3
        self.retry_delay = 2.0

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

    def _execute_command(self, command, *args, timeout=20.0):  # 타임아웃 더 증가
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
        """비동기 연결 (재시도 로직 포함)"""
        for attempt in range(self.max_retries):
            try:
                log.debug(f"Connection attempt {attempt + 1}/{self.max_retries} to {self.device_address}")

                # 기존 클라이언트가 있다면 정리
                if self.client:
                    try:
                        if self.client.is_connected:
                            await self.client.disconnect()
                    except:
                        pass
                    self.client = None

                # 새 클라이언트 생성
                self.client = BleakClient(self.device_address)

                # 연결 시도 (더 긴 타임아웃)
                await asyncio.wait_for(self.client.connect(), timeout=15.0)

                # 연결 확인
                if not self.client.is_connected:
                    raise Exception("Connection established but client reports not connected")

                self._connected = True
                log.debug(f"Bluetooth connected to {self.device_address}")

                # 연결 후 특성 발견
                success = await self._discover_characteristics()
                if not success:
                    log.warning("Characteristic discovery failed, but connection established")

                return True

            except asyncio.TimeoutError:
                log.warning(f"Connection attempt {attempt + 1} timed out")
            except Exception as e:
                log.warning(f"Connection attempt {attempt + 1} failed: {e}")

            # 재시도 전 대기
            if attempt < self.max_retries - 1:
                log.debug(f"Waiting {self.retry_delay}s before retry...")
                await asyncio.sleep(self.retry_delay)

        log.error(f"All {self.max_retries} connection attempts failed")
        self._connected = False
        return False

    async def _disconnect_async(self):
        """비동기 연결 해제"""
        try:
            if self.client and self.client.is_connected:
                await self.client.disconnect()
            self._connected = False
            self.char_uuid = None
            self.char_handle = None
            self.selected_char = None
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
        """비동기 데이터 전송 (handle 우선 사용)"""
        try:
            if not self.client or not self.client.is_connected:
                log.error("Client not connected for sending data")
                return False

            # 데이터 타입 변환
            cmd_bytes = self._ensure_bytes(data)
            if not cmd_bytes:
                log.error("Empty data after conversion")
                return False

            # handle이 있으면 handle 사용, 없으면 UUID 사용
            if self.char_handle is not None:
                log.debug(f"Sending BLE data via handle {self.char_handle}: {cmd_bytes.hex()}")
                await self.client.write_gatt_char(self.char_handle, cmd_bytes)
            elif self.char_uuid is not None:
                log.debug(f"Sending BLE data via UUID {self.char_uuid}: {cmd_bytes.hex()}")
                await self.client.write_gatt_char(self.char_uuid, cmd_bytes)
            else:
                log.error("No characteristic handle or UUID available")
                return False

            log.debug("BLE data sent successfully")
            return True

        except Exception as e:
            log.error(f"Bluetooth write error: {e}")
            return False

    async def _start_notify_async(self, callback):
        """비동기 알림 시작 (handle 우선 사용)"""
        try:
            if not self.client or not self.client.is_connected:
                log.error("Client not connected for notifications")
                return False

            # handle이 있으면 handle 사용, 없으면 UUID 사용
            if self.char_handle is not None:
                log.debug(f"Starting notifications via handle {self.char_handle}")
                await self.client.start_notify(self.char_handle, callback)
            elif self.char_uuid is not None:
                log.debug(f"Starting notifications via UUID {self.char_uuid}")
                await self.client.start_notify(self.char_uuid, callback)
            else:
                log.error("No characteristic handle or UUID available")
                return False

            log.debug("Bluetooth notifications started")
            return True
        except Exception as e:
            log.error(f"Start notify error: {e}")
            return False

    async def _stop_notify_async(self):
        """비동기 알림 중지"""
        try:
            if self.client and self.client.is_connected:
                # handle이 있으면 handle 사용, 없으면 UUID 사용
                if self.char_handle is not None:
                    await self.client.stop_notify(self.char_handle)
                elif self.char_uuid is not None:
                    await self.client.stop_notify(self.char_uuid)
            log.debug("Bluetooth notifications stopped")
            return True
        except Exception as e:
            log.error(f"Stop notify error: {e}")
            return False

    async def _discover_characteristics(self):
        """연결된 기기의 특성을 탐색하여 적절한 특성 선택"""
        try:
            if not self.client or not self.client.is_connected:
                log.error("Client not connected for characteristic discovery")
                return False

            log.debug("Discovering device characteristics...")

            # 알려진 UUID들 (우선순위 순)
            known_uuids = [
                "beb5483e-36e1-4688-b7f5-ea07361b26a8",  # Fisica Scale 기본
                "6e400002-b5a3-f393-e0a9-e50e24dcca9e",  # Nordic UART TX
                "6e400003-b5a3-f393-e0a9-e50e24dcca9e",  # Nordic UART RX
            ]

            # 모든 특성 수집
            all_characteristics = []
            target_uuid_chars = []

            for service in self.client.services:
                log.debug(f"Service: {service.uuid} ({service.description or 'Unknown'})")

                for char in service.characteristics:
                    char_info = {
                        'char': char,
                        'uuid': char.uuid,
                        'handle': char.handle,
                        'properties': char.properties,
                        'service_uuid': service.uuid
                    }
                    all_characteristics.append(char_info)

                    log.debug(f"  Char: {char.uuid} | Handle: {char.handle} | Props: {char.properties}")

                    # 특정 UUID의 특성들 별도 수집
                    if char.uuid in known_uuids:
                        target_uuid_chars.append(char_info)

            # 특성 선택 로직
            selected_char_info = None

            # 1. 알려진 UUID 중에서 write+notify 가능한 것 선택
            for char_info in target_uuid_chars:
                props = char_info['properties']
                if 'write' in props and 'notify' in props:
                    selected_char_info = char_info
                    log.debug(f"Selected known UUID with write+notify: {char_info['uuid']}")
                    break

            # 2. 알려진 UUID 중에서 write 가능한 것 선택
            if selected_char_info is None:
                for char_info in target_uuid_chars:
                    if 'write' in char_info['properties']:
                        selected_char_info = char_info
                        log.debug(f"Selected known UUID with write: {char_info['uuid']}")
                        break

            # 3. 알려진 UUID 중 첫 번째 선택
            if selected_char_info is None and target_uuid_chars:
                selected_char_info = target_uuid_chars[0]
                log.debug(f"Selected first known UUID: {selected_char_info['uuid']}")

            # 4. 모든 특성 중에서 write+notify 가능한 것 선택
            if selected_char_info is None:
                for char_info in all_characteristics:
                    props = char_info['properties']
                    if 'write' in props and 'notify' in props:
                        selected_char_info = char_info
                        log.warning(f"Selected unknown UUID with write+notify: {char_info['uuid']}")
                        break

            # 5. 마지막 수단: write 가능한 특성 선택
            if selected_char_info is None:
                for char_info in all_characteristics:
                    if 'write' in char_info['properties']:
                        selected_char_info = char_info
                        log.warning(f"Selected write-capable characteristic: {char_info['uuid']}")
                        break

            if selected_char_info:
                # 선택된 특성 정보 저장
                self.selected_char = selected_char_info['char']
                self.char_uuid = selected_char_info['uuid']
                self.char_handle = selected_char_info['handle']

                log.debug(f"Characteristic selected - UUID: {self.char_uuid}, Handle: {self.char_handle}")
                return True
            else:
                log.error("No suitable bluetooth characteristics found")
                return False

        except Exception as e:
            log.error(f"Characteristic discovery failed: {e}")
            # 백업으로 기본값 설정
            if self.char_uuid is None:
                self.char_uuid = "beb5483e-36e1-4688-b7f5-ea07361b26a8"
                self.char_handle = None  # handle은 unknown
                log.warning(f"Using fallback UUID: {self.char_uuid}")
            return False

    def set_uart(self, uart):
        """호환성을 위한 더미 메서드"""
        pass

    def connect(self):
        """동기식 연결"""
        log.debug(f"Starting connection to {self.device_address}")
        result = self._execute_command("connect", timeout=30.0)  # 더 긴 타임아웃
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

        # 종료된 스레드 정리
        if self.listen_thread and not self.listen_thread.is_alive():
            self.listen_thread = None

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
                log.debug(f"Raw data received: {data.hex()}, buffer len: {len(self._buffer)}")
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
        # 리스닝이 중지된 상태라면 다시 시작
        if not self._listening or not (self.listen_thread and self.listen_thread.is_alive()):
            log.debug("Restarting listening for new measurement...")
            self.start_listening()
        
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
                time.sleep(0.5)
            # log.debug("listen_loop stopped.")
            # self.listen_thread = None
        except Exception as e:
            log.critical(f"listen_loop exception: {e}")
            raise RuntimeError
        finally:
            self.listen_thread = None

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