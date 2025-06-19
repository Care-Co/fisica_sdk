from .port_scanner import find_serial_com_ports, find_bluetooth_com_ports
from .utils import log


def scan():
    serial_ports = find_serial_com_ports()
    bt_ports = find_bluetooth_com_ports()

    _available_devices = []

    for port in serial_ports:
        _available_devices.append({"id": port, "type": "serial"})
    for port in bt_ports:
        _available_devices.append({"id": port, "type": "bluetooth"})
    if _available_devices:
        log.info(f"Found devices : {_available_devices}")
    return _available_devices
