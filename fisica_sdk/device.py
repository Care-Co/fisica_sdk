from .port_scanner import find_all_com_ports


def scan():
    devices = find_all_com_ports()
    return devices