from .serial_uart import SerialUART
from .port_scanner import find_bluetooth_com_ports

class BluetoothUART(SerialUART):
    def __init__(self, baudrate=115200, timeout=1):
        ports = find_bluetooth_com_ports()
        if not ports:
            raise RuntimeError("Bluetooth COM 포트를 찾을 수 없습니다.")
        super().__init__(ports[0], baudrate, timeout)