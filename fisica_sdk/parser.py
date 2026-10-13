"""Queue/dataclass adapters. Framing, decoding and processing run in native code."""
from datetime import datetime
import numpy as np
from .models import MeasurementFrame
from .native import get_core


def _decode_hex(hex_data):
    result = get_core().decode_packet(bytes.fromhex(hex_data))
    return np.asarray(result.raw_grid), result.weight, str(int(result.battery)), result.raw_data


def _preprocess_grid_data(grid_data):
    return get_core().preprocess(grid_data)


def _insert_center_gap(grid_data, gap_cols=7):
    grid = np.asarray(grid_data)
    return get_core().display_grid(grid, gap_cols) if grid.shape == (29,22) else grid


def extract_frames_from_buffer(buffer, index):
    if index >= 643:
        packet = buffer[index-643:index+2]
        if len(packet) == 645:
            get_core().decode_packet(packet)
            return packet.hex()


def parse_frame_from_chunk(chunk):
    result = get_core().decode_packet(bytes.fromhex(chunk))
    return MeasurementFrame(
        timestamp=datetime.now().replace(microsecond=0).isoformat(),
        sensor_matrix=result.sensor_matrix, weight=result.weight,
        battery=result.battery, raw_data=result.raw_data, raw_grid=result.raw_grid,
        battery_valid=result.battery_valid,
    )


def extract_data(chunk):
    result = get_core().decode_packet(bytes.fromhex(chunk))
    return result.sensor_matrix, result.weight, str(int(result.battery)), result.raw_data


class DataParser:
    def __init__(self, data_queue, at_response_queue):
        self.data_queue = data_queue
        self.at_response_queue = at_response_queue

    def process_all_data_by_terminator(self, buffer):
        while buffer:
            consumed, kind, packet = get_core().next_packet(buffer)
            if not consumed:
                break
            del buffer[:consumed]
            if kind == 1:
                self.data_queue.put(parse_frame_from_chunk(packet.hex()))
            elif kind == 2:
                self.at_response_queue.put(get_core().decode_response(packet))

    def _handle_at_response(self, packet):
        self.at_response_queue.put(get_core().decode_response(packet))
