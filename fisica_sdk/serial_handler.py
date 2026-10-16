"""USB serial receive loop: bytes go to the native session, which does all framing and decoding."""
import time
import threading
from queue import Queue

from .utils import log
import errno

class SerialHandler:
    def __init__(self, uart):
        self.listen_thread = None
        self._listening = False
        self._uart = uart
        self.last_heartbeat = time.time()
        self.raw_queue = Queue()

    def set_uart(self, uart):
        self._uart = uart

    def start_listening(self):
        if self.listen_thread and self.listen_thread.is_alive():
            log.debug("Thread `listen_loop` is running.")
            return

        # 종료된 스레드 정리
        if self.listen_thread and not self.listen_thread.is_alive():
            self.listen_thread = None

        log.debug("Thread running.")
        self._listening = True
        self.listen_thread = threading.Thread(target=self.listen_loop, daemon=True)
        self.listen_thread.start()

        self._listening = True

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
                    data = self._uart.receive_bytes()
                    if data:
                        self.raw_queue.put(bytes(data))
                        log.debug(f"Data received: {len(data)} bytes")

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
            # log.debug("listen_loop stopped.")
            # self.listen_thread = None
        except Exception as e:
            log.critical(f"listen_loop exception: {e}")
            raise RuntimeError
        finally:
            self.listen_thread = None
            self._listening = False
