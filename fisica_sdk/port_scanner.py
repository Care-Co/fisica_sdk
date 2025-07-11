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
        log.info(f"Serial scan completed. found count : {len(ports)}")
        return ports
    except Exception as e:
        log.debug(f"Serial scan error: {e}")
        return []

def find_bluetooth_com_ports(timeout=15.0, max_devices=5) -> List[Dict]:
    """빠른 블루투스 스캔 - 여러 기기 감지, 적응적 타임아웃, 신호 강도 순 정렬"""

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

            await scanner.stop()

            # RSSI 기준으로 내림차순 정렬 (신호가 강한 순서대로)
            device_list = sorted(fisica_devices.values(), key=lambda x: x['rssi'], reverse=True)

            log.info(f"Bluetooth scan completed. found count : {len(device_list)}")

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

def find_all_com_ports(timeout=15.0, max_bt_devices=5, prioritize_signal=True) -> List[Dict]:
    """모든 Fisica Scale 기기 검색 (시리얼 + 블루투스)

    Args:
        timeout: 블루투스 스캔 타임아웃 (초)
        max_bt_devices: 최대 블루투스 기기 수
        prioritize_signal: True면 블루투스 기기를 신호 강도 순으로 우선 배치
    """
    all_devices = []

    # 1. 시리얼 포트 기기 찾기 (빠름)
    try:
        serial_ports = find_serial_com_ports()
        for i, port in enumerate(serial_ports):
            all_devices.append({
                'id': port,
                'name': f'Fisica Scale',
                'type': 'Serial',
                'port': port,
                'priority': 1000 + i  # 시리얼은 높은 우선순위
            })
    except Exception as e:
        log.debug(f"Serial scan error: {e}")

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

