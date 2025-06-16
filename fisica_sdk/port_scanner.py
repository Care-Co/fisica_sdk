import serial.tools.list_ports


def find_serial_com_ports():
    return [
        p.device for p in serial.tools.list_ports.comports()
        if (('USB' in p.description or 'usb' in p.device.lower() or 'usbserial' in p.device.lower()) and p.vid is not None and p.vid == 4292)
        #if ('USB' in p.description or 'usb' in p.device.lower() or 'usbserial' in p.device.lower())
    ]

def find_bluetooth_com_ports():
    return [
        p.device for p in serial.tools.list_ports.comports()
        if (('Bluetooth' in p.description or 'bluetooth' in p.device.lower() or 'tty.Bluetooth' in p.device) and p.vid is not None and p.vid == 4292)
        #if ('Bluetooth' in p.description or 'bluetooth' in p.device.lower() or 'tty.Bluetooth' in p.device and p.vid is not None and p.vid == "4292")
    ]

def print_all_com_port_details():
    ports = serial.tools.list_ports.comports()
    print(ports)
    if not ports:
        print("검색된 Scale 기기가 없습니다.")
    for port in ports:
        print("============== 포트 정보 ==============")
        print(f"Device     : {port.device}")
        print(f"Name       : {port.name}")
        print(f"Description: {port.description}")
        print(f"HWID       : {port.hwid}")
        print(f"VID:PID    : {port.vid}:{port.pid}" if port.vid and port.pid else "VID:PID    : -")
        print(f"Serial No. : {port.serial_number}")
        print(f"Location   : {port.location}")
        print(f"Manufacturer : {port.manufacturer}")
        print(f"Product       : {port.product}")
        print(f"Interface     : {port.interface}")
        print("=======================================\n")