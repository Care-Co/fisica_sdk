import threading
import time
import queue
import asyncio
from bleak import BleakClient

from .device import ATCommands
from .parser import *
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

        # 전용 이벤트 루프와 스레드
        self._loop = None
        self._loop_thread = None
        self._command_queue = queue.Queue()
        self._connected = False

        # Fisica Scale의 특성 UUID
        self.char_uuid = "beb5483e-36e1-4688-b7f5-ea07361b26a8"

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

    def _execute_command(self, command, *args, timeout=5.0):
        """명령을 이벤트 루프에서 실행하고 결과 반환"""
        if not self._loop or not self._loop_thread.is_alive():
            log.error("Event loop not running")
            return False

        response_queue = queue.Queue()
        self._command_queue.put((command, args, response_queue))

        try:
            return response_queue.get(timeout=timeout)
        except queue.Empty:
            log.error(f"Command {command} timeout")
            return False

    async def _connect_async(self):
        """비동기 연결"""
        try:
            self.client = BleakClient(self.device_address)
            await self.client.connect()
            self._connected = True
            log.info(f"Bluetooth connected to {self.device_address}")
            return True
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

    async def _send_async(self, data):
        """비동기 데이터 전송"""
        try:
            if not self.client or not self.client.is_connected:
                return False
            await self.client.write_gatt_char(self.char_uuid, data)
            return True
        except Exception as e:
            log.error(f"Bluetooth write error: {e}")
            return False

    async def _start_notify_async(self, callback):
        """비동기 알림 시작"""
        try:
            if not self.client or not self.client.is_connected:
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
            if self.client and self.client.is_connected:
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
        return self._execute_command("connect")

    def send(self, data):
        """데이터 전송"""
        try:
            return self._execute_command("send", data)

        except Exception as e:
            log.error(f"Bluetooth send error: {e}")
            return False

    def start_listening(self):
        if self.listen_thread and self.listen_thread.is_alive():
            log.debug("Thread `listen_loop` is running.")
            return

        log.debug("Thread running.")
        self._listening = True

        def notification_callback(sender, data):
            """데이터 수신 콜백"""
            try:
                self._buffer.extend(data)
                self.last_heartbeat = time.time()
            except Exception as e:
                log.error(f"Bluetooth notification processing error: {e}")

        # 알림 시작
        success = self._execute_command("start_notify", notification_callback)

        if success:
            self.listen_thread = threading.Thread(target=self.listen_loop, daemon=True)
            self.listen_thread.start()
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

    def listen_loop(self):
        try:
            while self._listening:
                self.last_heartbeat = time.time()
                try:
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
                    log.error(f"Bluetooth communication error: {e}")
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
                "AT+STOP": lambda v: log.debug(f"Stop : {v}"),
                "AT+MODE=1": lambda v: log.debug(f"Stop : {v}"),
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
                    self.send(ATCommands.STOP_CONTINUOUS_MEASUREMENT)
                    time.sleep(1)
                    self.send(ATCommands.START_CONTINUOUS_MEASUREMENT)
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