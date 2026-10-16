"""Device discovery. Commands are built by the native core (NativeCore.command)."""
from .port_scanner import find_all_com_ports


def scan(transport=None):
    return find_all_com_ports(transport=transport)
