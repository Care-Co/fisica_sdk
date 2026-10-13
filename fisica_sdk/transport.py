"""Byte transport injection and deterministic hardware-free development."""
from queue import Empty, Queue
from typing import Protocol
from .errors import FisicaTransportError


class ByteTransport(Protocol):
    """Host owns I/O. receive_bytes returns promptly (b'' if idle).

    send accepts bytes; failures raise an exception. close must unblock reads.
    SDK runs reads on a worker; no callbacks into the core are required.
    """
    def connect(self): ...
    def send(self, data: bytes): ...
    def receive_bytes(self) -> bytes: ...
    def close(self): ...


class MockTransport:
    """Script request bytes -> response chunks; contains no device algorithms.

    Pass exchanges obtained from shared fixtures or recorded traffic. Unknown
    requests are recorded but receive no automatic success response.
    """
    def __init__(self, exchanges=None):
        self.exchanges = exchanges or {}
        self.sent = []
        self.connected = False
        self._incoming = Queue()

    def connect(self):
        self.connected = True

    def send(self, data: bytes):
        if not self.connected:
            raise FisicaTransportError('Mock transport is disconnected')
        data = bytes(data)
        self.sent.append(data)
        for chunk in self.exchanges.get(data, ()):
            self.feed(chunk)

    def feed(self, data: bytes):
        self._incoming.put(bytes(data))

    def receive_bytes(self):
        if not self.connected:
            raise FisicaTransportError('Mock transport is disconnected')
        try:
            return self._incoming.get_nowait()
        except Empty:
            return b''

    def close(self):
        self.connected = False
