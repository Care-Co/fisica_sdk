import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict
from .utils import log

BLE_START_TIMEOUT = 5.0

def find_serial_devices():
    """시리얼 포트에서 Fisica Scale 기기 검색"""
    try:
        import serial.tools.list_ports
        devices = [{'id': p.device, 'name': 'Fisica Scale', 'type': 'Serial',
                    'port': p.device, 'vid': p.vid, 'pid': p.pid,
                    'serial_number': p.serial_number}
                   for p in serial.tools.list_ports.comports() if p.vid == 4292]
        log.info(f"Serial scan completed. found count : {len(devices)}")
        return devices
    except Exception as e:
        log.debug(f"Serial scan error: {e}")
        return []


def find_serial_com_ports():
    """Legacy port-name API; VID detection also works with localized Windows descriptions."""
    return [device['id'] for device in find_serial_devices()]

def find_bluetooth_com_ports(timeout=15.0, max_devices=5) -> List[Dict]:
    """빠른 블루투스 스캔 - 여러 기기 감지, 적응적 타임아웃, 신호 강도 순 정렬"""

    async def _scan_bluetooth():
        scanner = None
        try:
            from bleak import BleakScanner
            # 주소별로 중복 제거 (같은 기기의 여러 신호 방지)
            fisica_devices = {}
            last_discovery_time = 0

            def detection_callback(device, advertisement_data):
                nonlocal last_discovery_time
                device_name = advertisement_data.local_name or device.name or 'Unknown'

                if 'fisica scale' in device_name.lower():
                    # 새 기기 발견 시간 기록
                    if device.address not in fisica_devices:
                        last_discovery_time = asyncio.get_event_loop().time()

                    # RSSI가 더 강한 신호로 업데이트
                    if (device.address not in fisica_devices or
                            advertisement_data.rssi > fisica_devices[device.address]['rssi']):
                        fisica_devices[device.address] = {
                            'id': device.address,
                            'name': device_name,
                            'type': 'Bluetooth',
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
                    break

            # RSSI 기준으로 내림차순 정렬 (신호가 강한 순서대로)
            device_list = sorted(fisica_devices.values(), key=lambda x: x['rssi'], reverse=True)

            log.info(f"Bluetooth scan completed. found count : {len(device_list)}")

            return device_list

        except Exception as e:
            log.debug(f"Bluetooth scan error: {e}")
            return []
        finally:
            if scanner is not None:
                try:
                    await asyncio.wait_for(scanner.stop(), timeout=2)
                except Exception as error:
                    log.debug(f'Bluetooth scanner cleanup failed: {error}')

    async def _bounded_scan():
        return await asyncio.wait_for(_scan_bluetooth(), timeout=timeout + BLE_START_TIMEOUT)

    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_bounded_scan())
        # A synchronous SDK call must not replace or run the application's loop.
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(lambda: asyncio.run(_bounded_scan())).result()
    except Exception as e:
        log.debug(f"Bluetooth scan failed: {e}")
        return []

def find_all_com_ports(timeout=15.0, max_bt_devices=5, prioritize_signal=True, transport=None) -> List[Dict]:
    """모든 Fisica Scale 기기 검색 (시리얼 + 블루투스)

    Args:
        timeout: 블루투스 스캔 타임아웃 (초)
        max_bt_devices: 최대 블루투스 기기 수
        prioritize_signal: True면 블루투스 기기를 신호 강도 순으로 우선 배치
    """
    if transport not in (None, 'Serial', 'Bluetooth'):
        raise ValueError('transport must be Serial, Bluetooth, or None')
    all_devices = []

    # 1. 시리얼 포트 기기 찾기 (빠름)
    try:
        if transport != 'Bluetooth':
            for i, device in enumerate(find_serial_devices()):
                all_devices.append(dict(device, priority=1000 + i))
    except Exception as e:
        log.debug(f"Serial scan error: {e}")

    if transport == 'Serial':
        return all_devices

    # 2. 블루투스 기기 찾기
    try:
        bluetooth_devices = find_bluetooth_com_ports(
            timeout=timeout,
            max_devices=max_bt_devices
        )
        # 블루투스 기기에 우선순위 추가
        for device in bluetooth_devices:
            if prioritize_signal:
                # RSSI가 높을수록 높은 우선순위 (RSSI는 음수이므로 절댓값이 작을수록 강함)
                device['priority'] = 500 + device.get('rssi', -100)  # -30dBm이면 470 우선순위
            else:
                device['priority'] = 100  # 기본 우선순위

        all_devices.extend(bluetooth_devices)

    except Exception as e:
        log.debug(f"Bluetooth scan error: {e}")

    # 3. 전체 기기 정렬
    if prioritize_signal:
        # 우선순위 순으로 정렬 (높은 숫자가 앞으로)
        all_devices.sort(key=lambda x: x.get('priority', 0), reverse=True)

    log.info(f"Total device count : {len(all_devices)}")
    for i, device in enumerate(all_devices):
        device_info = f"{i+1}. {device['name']} ({device.get('type', device.get('port', 'N/A'))})"
        if device['type'] == 'Bluetooth':
            device_info += f" - RSSI: {device.get('rssi', 'N/A')} dBm"
        log.info(device_info)

    return all_devices
