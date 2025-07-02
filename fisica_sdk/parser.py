# parser.py (기존 파싱 함수들 + 새로운 DataParser 클래스)
import numpy as np
from typing import Optional, Any
from queue import Queue

from numpy import ndarray, dtype

from .models import MeasurementFrame
from datetime import datetime
from .utils import log


def _decode_hex(hex_data: str) -> tuple[ndarray[Any, dtype[Any]], str, str]:
    """
    Decode a hex string into sensor data (29x22 grid), weight, and battery level.
    Emulates the behavior of the original _decode_hex function.
    """
    try:
        data_int = [int(hex_data[i:i+2], 16) for i in range(0, len(hex_data), 2)]
    except ValueError:
        log.warning("Hex decoding failed due to invalid characters.")
        raise ValueError("Invalid hex string")

    rawData = np.array(data_int)

    if len(rawData) != 645:
        log.error(f"Invalid data length: expected 645, got {len(rawData)}")
        raise ValueError("Incorrect data length")

    # Extract grid data
    grid_data = rawData[1:639].reshape((29, 22))

    # Extract weight in "a.b" format
    value = (rawData[641] << 8) | rawData[642]  # big endian 기준
    weight = value / 10  # 10배 스케일링 해석

    # Extract battery as integer string
    battery = "99"

    return grid_data, weight, battery


def _preprocess_grid_data(grid_data: np.ndarray) -> np.ndarray:
    """
    센서 데이터 전처리 수행
    """
    # Step 1: 각 열의 0이 아닌 값 개수로 보정
    non_zero_counts = np.count_nonzero(grid_data, axis=0)
    grid_data = np.where(grid_data != 0, grid_data * non_zero_counts, grid_data)

    # Step 2: 구간화
    non_zero_values = grid_data[grid_data != 0]
    if non_zero_values.size == 0:
        return grid_data  # 모두 0인 경우는 그대로 반환

    mean_value = np.mean(non_zero_values)
    bins = np.linspace(0, 2 * mean_value, 11)
    bin_midpoints = (bins[:-1] + bins[1:]) / 2

    def assign_to_nearest_bin(value):
        if value == 0:
            return 0
        bin_idx = min(np.digitize(value, bins) - 1, len(bin_midpoints) - 1)
        return bin_midpoints[bin_idx]

    grid_data = np.vectorize(assign_to_nearest_bin)(grid_data)

    # Step 3: 255로 스케일링
    max_value = np.max(grid_data)
    if max_value > 255:
        scale_factor = 255 / max_value
        grid_data = (grid_data * scale_factor).astype(int)

    return grid_data


def extract_frames_from_buffer(buffer: bytearray, index: int) -> Optional[str]:
    if index >= 643:
        start_index = index - 643
        frame = buffer[start_index : index + 2]
        if len(frame) == 645:
            return frame.hex()


def parse_frame_from_chunk(chunk: str) -> Optional[MeasurementFrame]:
    if (data := extract_data(chunk)) is None:
        return None
    matrix, weight, battery = data
    return MeasurementFrame(
        timestamp=datetime.now().replace(microsecond=0).isoformat(),
        sensor_matrix=matrix,
        weight=float(weight),
        battery=float(battery)
    )


def extract_data(chunk: str) -> Optional[tuple[Any, str, str]]:
    grid, weight, battery = _decode_hex(chunk)
    if np.count_nonzero(grid) == 0:
        log.debug("Received all-zero sensor grid; skipping frame.")
        return None
    matrix = _preprocess_grid_data(np.array(grid)).tolist()
    return matrix, weight, battery


class DataParser:
    """공통 데이터 파싱 로직을 담당하는 클래스"""

    def __init__(self, data_queue: Queue, at_response_queue: Queue):
        self.data_queue = data_queue
        self.at_response_queue = at_response_queue

    def process_all_data_by_terminator(self, buffer: bytearray):
        """종료 구분자를 기준으로 모든 데이터를 순차적으로 처리"""
        self._check_and_process_plantar_data(buffer)
        at_footer_pos = buffer.rfind(b'\xff\xf0')       # AT Command 응답

        if at_footer_pos != -1:
            # AT Command 응답 처리
            self._process_at_command_response(buffer, at_footer_pos)

    def _process_at_command_response(self, buffer: bytearray, footer_pos: int):
        """AT Command 응답 처리 (종료: ff f0)"""
        try:
            # AT Command는 8바이트 고정이므로 footer 위치 확인
            if footer_pos >= 6:  # STX(1) + Header(1) + Type(1) + RET(1) + Data(2) + ETX(2) = 8
                packet_start = footer_pos - 6

                # STX 확인
                if buffer[packet_start] == 0x24:
                    at_packet = bytes(buffer[packet_start:footer_pos + 2])

                    if len(at_packet) == 8:
                        log.debug(f"Processing AT command response: {at_packet.hex()}")
                        self._handle_at_response(at_packet)

                        # 처리된 AT Command 제거
                        del buffer[:footer_pos + 2]
                        return

            # AT Command 형식이 맞지 않으면 해당 footer만 제거
            log.debug(f"Invalid AT command format at footer position {footer_pos}")
            log.debug(f"Context: {buffer[max(0, footer_pos-10):footer_pos+10].hex()}")

        except Exception as e:
            log.error(f"AT command response parsing failed: {e}")

        # 오류 발생 시 해당 footer까지만 제거
        del buffer[:footer_pos + 2]

    def _check_and_process_plantar_data(self, buffer: bytearray):
        """
        족저압 데이터 크기 기반 처리
        조건:
        1. 버퍼 크기가 645 이상
        2. 마지막 footer index - 645 > 0
        """
        # 조건 1: 버퍼 크기 확인
        if len(buffer) < 644:
            return False  # 버퍼 계속 쌓기

        # 마지막 f0 0f footer 찾기
        last_footer_index = buffer.rfind(b'\xf0\x0f')

        if last_footer_index == -1:
            return False  # footer가 없으면 계속 쌓기

        if last_footer_index < 642:
            return False  # 조건 불만족, 계속 쌓기

        # 조건 만족: 족저압 데이터 처리
        try:
            log.debug(f"Processing plantar data: buffer size={len(buffer)}, "
                      f"footer at {last_footer_index}")

            # footer까지의 데이터 추출 (footer 포함)
            frame_data = buffer[:last_footer_index + 2]

            # 족저압 데이터 파싱
            matrix = extract_frames_from_buffer(frame_data, last_footer_index)
            if matrix:
                frame = parse_frame_from_chunk(matrix)
                if frame is not None:
                    self.data_queue.put(frame)
                    log.debug(f"Successfully parsed plantar frame, queue size: {self.data_queue.qsize()}")
                else:
                    log.debug("parse_frame_from_chunk returned None")
            else:
                log.debug("extract_frames_from_buffer returned empty")

        except Exception as e:
            log.error(f"Plantar data parsing failed: {e}")
            log.debug(f"Problematic data: {buffer[:100].hex()}...")

        # 처리된 데이터 제거 (footer 포함)
        del buffer[:last_footer_index + 2]

        return True  # 처리 완료

    def _handle_at_response(self, packet: bytes):
        """AT Command 응답 파싱"""
        if len(packet) != 8:
            log.warning(f"AT packet length mismatch: expected 8, got {len(packet)}")
            return

        stx = packet[0]
        header = packet[1]
        type_byte = packet[2]
        ret = packet[3]
        data_high = packet[4]
        data_low = packet[5]
        etx = packet[6:8]

        log.debug(f"[AT Response] Header: 0x{header:02X}, Type: 0x{type_byte:02X}, "
                  f"RET: 0x{ret:02X}, Data: 0x{data_high:02X}{data_low:02X}")

        # 성공/실패 확인
        if ret == 0x01:
            log.debug("AT Command executed successfully")
        elif ret == 0x00:
            error_code = data_low if data_high == 0x00 else (data_high << 8) | data_low
            self._log_error_code(error_code)

        # AT 응답을 큐에 추가
        response = {
            'header': header,
            'type': type_byte,
            'ret': ret,
            'data_high': data_high,
            'data_low': data_low,
            'success': ret == 0x01
        }
        self.at_response_queue.put(response)

    def _log_error_code(self, code):
        """에러 코드별 메시지 출력"""
        error_messages = {
            1: "설정 값이 기존 값과 동일하거나 이미 다른 디바이스와 연결됨",
            2: "설정 값이 범위를 벗어남",
            3: "AT Command 포맷이 일치하지 않음",
            4: "저장된 데이터가 존재하지 않음",
            5: "측정 동작 중에는 족저압 측정 횟수 변경 불가",
            6: "연결된 디바이스가 아님",
            7: "OTA 펌웨어 업데이트 오류",
            8: "펌웨어가 이미 최신 버전",
            9: "WiFi 연결이 끊어짐"
        }
        message = error_messages.get(code, f"알 수 없는 오류 ({code})")
        log.debug(f"[Error] Error Code {code}: {message}")
