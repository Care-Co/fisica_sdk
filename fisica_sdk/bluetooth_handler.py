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
        self.desired_mtu = 517  # 사전 정의된 MTU 값
        self.negotiated_mtu = 23  # 기본 MTU 값, 새로 추가
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
                    elif command == "negotiate_mtu":  # 새로 추가
                        mtu = args[0] if args else self.desired_mtu
                        result = await self._negotiate_mtu_async(mtu)
                    else:
                        result = False

                    response_queue.put(result)
                except Exception as e:
                    log.error(f"Command {command} error: {e}")
                    response_queue.put(False)

            except Exception as e:
                log.error(f"Command processor error: {e}")
                await asyncio.sleep(0.1)

    def _execute_command(self, command, *args, timeout=20.0):
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

    async def _negotiate_mtu_async(self, mtu_size):
        """MTU 협상 비동기 함수 - 플랫폼별 구현"""
        try:
            if not self.client or not self.client.is_connected:
                log.error("Client not connected for MTU negotiation")
                return False

            log.debug(f"Attempting MTU negotiation for {mtu_size} bytes")

            # 플랫폼별 MTU 처리
            backend_name = self.client._backend.__class__.__name__
            log.debug(f"Backend: {backend_name}")

            if backend_name == "BleakClientBlueZDBus":
                # Linux BlueZ 백엔드의 경우 - _acquire_mtu 사용
                try:
                    await self.client._backend._acquire_mtu()
                    self.negotiated_mtu = self.client.mtu_size
                    log.info(f"BlueZ MTU acquired: {self.negotiated_mtu} bytes")
                except Exception as e:
                    log.warning(f"BlueZ _acquire_mtu failed: {e}")
                    # 수동으로 MTU 설정 시도
                    self.client._backend._mtu_size = mtu_size
                    self.negotiated_mtu = mtu_size
                    log.info(f"BlueZ MTU manually set to: {self.negotiated_mtu} bytes")

            elif backend_name in ["BleakClientWinRT", "BleakClientCoreBluetooth"]:
                # Windows 또는 macOS의 경우 - 수동 MTU 설정 시도
                try:
                    # 먼저 OS의 자동 협상 MTU 확인
                    current_mtu = self.client.mtu_size
                    log.debug(f"Current OS negotiated MTU: {current_mtu} bytes")

                    # 원하는 MTU가 현재 MTU와 다르면 수동 설정 시도
                    if current_mtu != mtu_size:
                        log.info(f"Attempting manual MTU override from {current_mtu} to {mtu_size}")

                        # Windows WinRT 백엔드의 경우
                        if backend_name == "BleakClientWinRT":
                            # WinRT 백엔드에서 MTU 수동 설정
                            if hasattr(self.client._backend, '_mtu_size'):
                                self.client._backend._mtu_size = mtu_size
                                log.info(f"WinRT MTU manually set to: {mtu_size} bytes")
                            elif hasattr(self.client._backend, 'mtu_size'):
                                self.client._backend.mtu_size = mtu_size
                                log.info(f"WinRT MTU property set to: {mtu_size} bytes")
                            else:
                                log.warning("WinRT backend MTU property not found, using internal override")

                        # macOS CoreBluetooth 백엔드의 경우
                        elif backend_name == "BleakClientCoreBluetooth":
                            # CoreBluetooth 백엔드에서 MTU 수동 설정
                            if hasattr(self.client._backend, '_mtu_size'):
                                self.client._backend._mtu_size = mtu_size
                                log.info(f"CoreBluetooth MTU manually set to: {mtu_size} bytes")
                            elif hasattr(self.client._backend, 'mtu_size'):
                                self.client._backend.mtu_size = mtu_size
                                log.info(f"CoreBluetooth MTU property set to: {mtu_size} bytes")
                            else:
                                log.warning("CoreBluetooth backend MTU property not found, using internal override")

                        # 수동 설정 후 MTU 확인
                        try:
                            updated_mtu = self.client.mtu_size
                            if updated_mtu == mtu_size:
                                self.negotiated_mtu = mtu_size
                                log.info(f"MTU successfully updated to: {mtu_size} bytes")
                            else:
                                log.warning(f"MTU update failed, using fallback: {mtu_size} bytes")
                                self.negotiated_mtu = mtu_size  # 강제 설정
                        except Exception as e:
                            log.warning(f"MTU verification failed: {e}, using fallback")
                            self.negotiated_mtu = mtu_size  # 강제 설정
                    else:
                        # 이미 원하는 MTU와 같은 경우
                        self.negotiated_mtu = current_mtu
                        log.info(f"MTU already at desired value: {self.negotiated_mtu} bytes")

                except Exception as e:
                    log.warning(f"Failed to get/set MTU on {backend_name}: {e}")
                    # 실패 시 강제로 원하는 MTU 설정
                    self.negotiated_mtu = mtu_size
                    log.info(f"Fallback MTU set to: {mtu_size} bytes")
            else:
                log.warning(f"Unknown backend {backend_name}, using manual MTU")
                self.negotiated_mtu = mtu_size

            # 특성별 최대 쓰기 크기 확인
            if hasattr(self, 'selected_char') and self.selected_char:
                try:
                    max_write_size = self.selected_char.max_write_without_response_size
                    log.debug(f"Characteristic max write without response: {max_write_size} bytes")

                    # 실제 사용 가능한 MTU는 특성의 제한과 협상된 MTU 중 작은 값
                    if max_write_size > 0:
                        original_mtu = self.negotiated_mtu
                        self.negotiated_mtu = min(self.negotiated_mtu, max_write_size + 3)  # ATT 헤더 추가

                        if self.negotiated_mtu != original_mtu:
                            log.warning(f"MTU limited by characteristic: {original_mtu} -> {self.negotiated_mtu} bytes")
                        else:
                            log.info(f"MTU within characteristic limits: {self.negotiated_mtu} bytes")
                except Exception as e:
                    log.debug(f"Could not get characteristic write size: {e}")

            log.info(f"Final negotiated MTU: {self.negotiated_mtu} bytes")
            log.info(f"Maximum data payload: {self.negotiated_mtu - 3} bytes")

            return True

        except Exception as e:
            log.warning(f"MTU negotiation failed: {e}")
            log.info(f"Using fallback MTU: {mtu_size} bytes")
            self.negotiated_mtu = mtu_size  # 실패해도 원하는 MTU로 설정
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

                # MTU 정보 확인 및 설정 - connect() 호출 시 플랫폼별 MTU 처리
                log.debug("Attempting MTU negotiation and acquisition...")
                await self._negotiate_mtu_async(self.desired_mtu)

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
            self.negotiated_mtu = 23  # MTU 초기화 - 새로 추가
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
        """비동기 데이터 전송 (MTU 크기 고려) - 수정됨"""
        try:
            if not self.client or not self.client.is_connected:
                log.error("Client not connected for sending data")
                return False

            # 데이터 타입 변환
            cmd_bytes = self._ensure_bytes(data)
            if not cmd_bytes:
                log.error("Empty data after conversion")
                return False

            # MTU 크기 체크 및 분할 전송
            max_data_size = self.negotiated_mtu - 3  # ATT 헤더 3바이트 제외

            if len(cmd_bytes) > max_data_size:
                log.warning(f"Data size ({len(cmd_bytes)}) exceeds MTU payload ({max_data_size})")
                log.info("Splitting data into multiple packets...")

                # 데이터 분할 전송
                for i in range(0, len(cmd_bytes), max_data_size):
                    chunk = cmd_bytes[i:i + max_data_size]

                    # handle이 있으면 handle 사용, 없으면 UUID 사용
                    if self.char_handle is not None:
                        log.debug(f"Sending chunk {i//max_data_size + 1} via handle {self.char_handle}: {chunk.hex()}")
                        await self.client.write_gatt_char(self.char_handle, chunk)
                    elif self.char_uuid is not None:
                        log.debug(f"Sending chunk {i//max_data_size + 1} via UUID {self.char_uuid}: {chunk.hex()}")
                        await self.client.write_gatt_char(self.char_uuid, chunk)
                    else:
                        log.error("No characteristic handle or UUID available")
                        return False

                    # 청크 간 짧은 대기
                    await asyncio.sleep(0.01)

                log.debug("All data chunks sent successfully")
                return True
            else:
                # 단일 패킷 전송
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
                    log.info(f"Selected known UUID with write+notify: {char_info['uuid']}")
                    break

            # 2. 알려진 UUID 중에서 write 가능한 것 선택
            if selected_char_info is None:
                for char_info in target_uuid_chars:
                    if 'write' in char_info['properties']:
                        selected_char_info = char_info
                        log.info(f"Selected known UUID with write: {char_info['uuid']}")
                        break

            # 3. 알려진 UUID 중 첫 번째 선택
            if selected_char_info is None and target_uuid_chars:
                selected_char_info = target_uuid_chars[0]
                log.info(f"Selected first known UUID: {selected_char_info['uuid']}")

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

                log.info(f"Characteristic selected - UUID: {self.char_uuid}, Handle: {self.char_handle}")
                return True
            else:
                log.error("No suitable characteristics found")
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
        """동기식 연결 - 500 MTU로 자동 협상"""
        log.debug(f"Starting connection to {self.device_address}")
        log.debug(f"Will negotiate MTU to {self.desired_mtu} bytes during connection")
        result = self._execute_command("connect", timeout=30.0)
        if result:
            log.info("Bluetooth connection successful")
            log.debug(f"Final MTU: {self.negotiated_mtu} bytes (max payload: {self.negotiated_mtu - 3} bytes)")
        else:
            log.error("Bluetooth connection failed")
        return result

    def negotiate_mtu(self, mtu_size=None):
        """MTU 협상 (동기식) - 새로 추가"""
        if mtu_size is None:
            mtu_size = self.desired_mtu

        log.debug(f"Requesting MTU negotiation: {mtu_size} bytes")
        result = self._execute_command("negotiate_mtu", mtu_size, timeout=10.0)

        if result:
            log.debug(f"MTU negotiation successful: {self.negotiated_mtu} bytes")
        else:
            log.warning("MTU negotiation failed, using current MTU")

        return result

    def get_mtu_info(self):
        """MTU 정보 반환 - 새로 추가"""
        return {
            'desired_mtu': self.desired_mtu,
            'negotiated_mtu': self.negotiated_mtu,
            'max_payload': self.negotiated_mtu - 3  # ATT 헤더 제외
        }

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
            log.debug("listen_loop stopped.")
            self.listen_thread = None
        except Exception as e:
            log.critical(f"listen_loop exception: {e}")
            self.listen_thread = None
            raise RuntimeError

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