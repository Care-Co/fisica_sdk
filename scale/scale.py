from .transport.serial_uart import SerialUART
from .transport.bluetooth_uart import BluetoothUART
from .transport.port_scanner import find_serial_com_ports, find_bluetooth_com_ports, print_all_com_port_details

class Scale:
    def __init__(self, baudrate=9600):
        self.VID = 4292
        self.baudrate = baudrate
        self.uart = None

    def connect(self):
        print_all_com_port_details()
        ports = find_serial_com_ports()
        if ports:
            print(f"[Scale] 유선 포트 감지: {ports[0]}")
            self.uart = SerialUART(ports[0], self.baudrate)
        else:
            bt_ports = find_bluetooth_com_ports()
            if bt_ports:
                print(f"[Scale] 블루투스 포트 감지: {bt_ports[0]}")
                self.uart = BluetoothUART(self.baudrate)
            else:
                raise RuntimeError("유선/블루투스 포트를 찾을 수 없습니다.")
        self.uart.connect()

    def send(self, data):
        self.uart.send(data)

    def receive(self):
        return self.uart.receive()

    def stream(self):
        return self.uart.stream()

    def close(self):
        self.uart.close()