import serial
from .base_uart import BaseUART

class SerialUART(BaseUART):
    def __init__(self, port, baudrate=9600, timeout=1):
        self.port = port
        self.ser = serial.Serial(port, baudrate, timeout=timeout)

    def connect(self):
        if not self.ser.is_open:
            self.ser.open()

    def send(self, data):
        self.ser.write(data)

    def close(self):
        self.ser.close()

    def stream(self):
        while True:
            yield self.receive()

    def receive(self):
        if self.ser.in_waiting:
            return self.ser.readline().decode('utf-8').strip()
        return ""