import asyncio
from bleak import BleakScanner
import serial.tools.list_ports
from typing import List, Dict
from .utils import log

def find_serial_com_ports():
    """시리얼 포트에서 Fisica Scale 기기 검색"""
    try:
        ports = [
            p.device for p in serial.tools.list_ports.comports()
            if (('USB' in p.description or 'usb' in p.device.lower() or 'usbserial' in p.device.lower())
                and p.vid is not None and p.vid == 4292)
        ]
        log.info(f"Found {len(ports)} serial devices")
        return ports
    except Exception as e:
        log.debug(f"Serial scan error: {e}")
        return []

def find_bluetooth_com_ports_fast(timeout=10.0, max_devices=5) -> List[Dict]:
    """빠른 블루투스 스캔 - 여러 기기 감지, 적응적 타임아웃"""

    def _get_or_create_loop():
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError("Loop is closed")
            return loop
        except RuntimeError:
            return asyncio.new_event_loop()

    async def _scan_bluetooth():
        try:
            # 주소별로 중복 제거 (같은 기기의 여러 신호 방지)
            fisica_devices = {}
            last_discovery_time = 0

            def detection_callback(device, advertisement_data):
                nonlocal last_discovery_time
                device_name = device.name or 'Unknown'

                if 'fisica scale' in device_name.lower():
                    # 새 기기 발견 시간 기록
                    if device.address not in fisica_devices:
                        last_discovery_time = asyncio.get_event_loop().time()
                        log.info(f"Found Fisica Scale: {device_name} ({device.address})")

                    # RSSI가 더 강한 신호로 업데이트
                    if (device.address not in fisica_devices or
                            advertisement_data.rssi > fisica_devices[device.address]['rssi']):
                        fisica_devices[device.address] = {
                            'id': device.address,
                            'name': device_name,
                            'type': 'bluetooth',
                            'address': device.address,
                            'rssi': advertisement_data.rssi
                        }

            scanner = BleakScanner(detection_callback)
            await scanner.start()

            start_time = asyncio.get_event_loop().time()

            # 적응적 타임아웃: 기기 발견 상황에 따라 조기 종료
            while True:
                await asyncio.sleep(0.2)  # 0.2초마다 체크
                current_time = asyncio.get_event_loop().time()

                # 전체 타임아웃 초과
                if current_time - start_time > timeout:
                    break

                # 최대 기기 수 도달
                if len(fisica_devices) >= max_devices:
                    break

                # 기기를 찾은 후 2초간 추가 기기가 없으면 종료
                if (len(fisica_devices) > 0 and
                        last_discovery_time > 0 and
                        current_time - last_discovery_time > 2.0):
                    log.info(f"No new devices found for 2 seconds, stopping scan")
                    break

            await scanner.stop()

            device_list = list(fisica_devices.values())
            log.info(f"Bluetooth scan completed: found {len(device_list)} devices")
            return device_list

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

def find_bluetooth_com_ports_simple(timeout=10.0) -> List[Dict]:
    """간단한 버전 - discover 방식, 중복 제거"""

    def _get_or_create_loop():
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError("Loop is closed")
            return loop
        except RuntimeError:
            return asyncio.new_event_loop()

    async def _scan_bluetooth():
        try:
            devices = await BleakScanner.discover(timeout=timeout)
            fisica_devices = {}  # 중복 제거용

            for device in devices:
                device_name = device.name or 'Unknown'
                if 'fisica scale' in device_name.lower():
                    # 같은 주소는 하나만 유지
                    fisica_devices[device.address] = {
                        'id': device.address,
                        'name': device_name,
                        'type': 'bluetooth',
                        'address': device.address,
                    }

            device_list = list(fisica_devices.values())
            log.info(f"Bluetooth simple scan: found {len(device_list)} devices")
            return device_list

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

def find_all_com_ports(timeout=10.0, max_bt_devices=5, fast_scan=True) -> List[Dict]:
    """모든 Fisica Scale 기기 검색 (시리얼 + 블루투스)

    Args:
        timeout: 블루투스 스캔 타임아웃 (초)
        max_bt_devices: 최대 블루투스 기기 수
        fast_scan: True면 빠른 스캔(콜백), False면 간단한 스캔(discover)
    """
    all_devices = []

    # 1. 시리얼 포트 기기 찾기 (빠름)
    try:
        serial_ports = find_serial_com_ports()
        for port in serial_ports:
            all_devices.append({
                'id': port,
                'name': f'Fisica Scale (Serial)',
                'type': 'serial',
                'port': port
            })
    except Exception as e:
        log.debug(f"Serial scan error: {e}")

    # 2. 블루투스 기기 찾기
    try:
        if fast_scan:
            bluetooth_devices = find_bluetooth_com_ports_fast(
                timeout=timeout,
                max_devices=max_bt_devices
            )
        else:
            bluetooth_devices = find_bluetooth_com_ports_simple(timeout=timeout)

        all_devices.extend(bluetooth_devices)

    except Exception as e:
        log.debug(f"Bluetooth scan error: {e}")

    log.info(f"Total devices found: {len(all_devices)}")
    return all_devices

# 편의 함수들
def find_all_com_ports_quick(timeout=2.0) -> List[Dict]:
    """빠른 스캔 - 2초 타임아웃, 첫 번째 기기 발견 시 빠른 종료"""
    return find_all_com_ports(timeout=timeout, max_bt_devices=3, fast_scan=True)

def find_all_com_ports_thorough(timeout=5.0) -> List[Dict]:
    """정확한 스캔 - 5초 타임아웃, 모든 기기 찾기"""
    return find_all_com_ports(timeout=timeout, max_bt_devices=10, fast_scan=True)