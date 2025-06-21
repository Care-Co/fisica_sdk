import asyncio
from bleak import BleakScanner
import serial.tools.list_ports
from typing import List, Dict
from .utils import log

def find_serial_com_ports():
    """기존 시리얼 포트 스캔 함수"""
    return [
        p.device for p in serial.tools.list_ports.comports()
        if (('USB' in p.description or 'usb' in p.device.lower() or 'usbserial' in p.device.lower()) and p.vid is not None and p.vid == 4292)
    ]

def find_bluetooth_com_ports(timeout=10.0) -> List[Dict]:

    def _get_or_create_loop():
        """새로운 이벤트 루프 생성 또는 기존 루프 사용"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError("Loop is closed")
            return loop
        except RuntimeError:
            return asyncio.new_event_loop()

    async def _scan_bluetooth():
        """블루투스 기기 스캔"""
        try:
            devices = await BleakScanner.discover(timeout=timeout)
            fisica_devices = []

            for device in devices:
                device_name = device.name or 'Unknown'
                # Fisica Scale 기기 필터링
                if 'fisica scale' in device_name.lower():
                    fisica_devices.append({
                        'id': device.address,  # 시리얼과 동일한 키 이름 사용
                        'name': device_name,
                        'type': 'bluetooth',
                        'address': device.address,  # 추가 정보
                        'rssi': getattr(device, 'rssi', None)
                    })

            return fisica_devices

        except Exception as e:
            log.debug(f"Bluetooth scan error: {e}")
            return []

    try:
        loop = _get_or_create_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(_scan_bluetooth())
    except Exception as e:
        log.debug(f"Bluetooth scan failed: {e}")
        return []

def find_all_com_ports(timeout=10.0) -> List[Dict]:

    all_devices = []

    try:
        serial_ports = find_serial_com_ports()
        for port in serial_ports:
            all_devices.append({
                'id': port,
                'name': f'Fisica Scale (Serial)',
                'type': 'serial',
                'port': port
            })
        if len(serial_ports):
            return all_devices
    except Exception as e:
        log.debug(f"Serial scan error: {e}")

    # 2. 블루투스 기기 찾기
    try:
        bluetooth_devices = find_bluetooth_com_ports(timeout)
        all_devices.extend(bluetooth_devices)
    except Exception as e:
        log.debug(f"Bluetooth scan error: {e}")

    return all_devices
