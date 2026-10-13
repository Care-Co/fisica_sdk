import serial
from .base_uart import BaseUART
from .errors import FisicaTransportError

class SerialUART(BaseUART):
    def __init__(self, port, baudrate=9600, timeout=1):
        self.port = port
        self.ser = serial.Serial(baudrate=baudrate, timeout=timeout, write_timeout=timeout)
        self.ser.port = port

    def connect(self):
        if not self.ser.is_open:
            try:
                self.ser.open()
            except serial.SerialException as error:
                raise FisicaTransportError(f'Cannot open serial port {self.port}: {error}') from error

    def send(self, data):
        data = data.encode('utf-8') if isinstance(data, str) else bytes(data)
        try:
            if self.ser.write(data) != len(data):
                raise FisicaTransportError('Incomplete serial write')
        except serial.SerialException as error:
            raise FisicaTransportError(f'Serial write failed: {error}') from error

    def receive_bytes(self):
        try:
            return self.ser.read(self.ser.in_waiting) if self.ser.in_waiting else b''
        except serial.SerialException as error:
            raise FisicaTransportError(f'Serial read failed: {error}') from error

    def close(self):
        self.ser.close()

    def stream(self):
        while True:
            yield self.receive()

    def receive(self):
        if self.ser.in_waiting:
            return self.ser.readline().decode('utf-8').strip()
        return ""
