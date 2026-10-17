"""USB serial receive loop: bytes go to the native session, which does all framing and decoding."""
import time
import threading
from queue import Queue

from .utils import log


class SerialHandler:
    def __init__(self, uart):
        self.listen_thread = None
        self._listening = False
        self._uart = uart
        self.raw_queue = Queue()

    def is_alive(self):
        """True while the receive loop runs; False once the link failed or listening stopped."""
        thread = self.listen_thread
        return bool(self._listening and thread and thread.is_alive())

    def start_listening(self):
        if self.is_alive():
            log.debug("Thread `listen_loop` is running.")
            return
        self._listening = True
        self.listen_thread = threading.Thread(target=self.listen_loop, name='fisica-serial-read', daemon=True)
        self.listen_thread.start()

    def send(self, data):
        if self._uart:
            self._uart.send(data)

    def stop_listening(self):
        self._listening = False
        thread = self.listen_thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=1)
        self.listen_thread = None

    def listen_loop(self):
        try:
            while self._listening:
                try:
                    data = self._uart.receive_bytes()
                except Exception as error:
                    # The receiver sees the stopped loop and lets the core recover the link.
                    log.warning(f"Serial link lost; waiting for the Scale to reconnect: {error}")
                    break
                if data:
                    self.raw_queue.put(bytes(data))
                time.sleep(0.01)
        finally:
            self._listening = False
