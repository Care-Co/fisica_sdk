"""Queue/dataclass adapters. Framing, decoding and processing run in native code.

Decoding a packet yourself returns raw and processed grids, so it needs full mode: pass a
NativeEntitlement (license). A measurement session hands out frames in every mode.
"""
from datetime import datetime
import numpy as np
from .models import MeasurementFrame
from .native import get_core


def _decode_hex(hex_data, license=None):
    result = get_core().decode_packet(bytes.fromhex(hex_data), license)
    return np.asarray(result.raw_grid), result.weight, str(int(result.battery)), result.raw_data


def _preprocess_grid_data(grid_data, license=None):
    return get_core().preprocess(grid_data, license)


def _insert_center_gap(grid_data, gap_cols=7, license=None):
    grid = np.asarray(grid_data)
    return get_core().display_grid(grid, gap_cols, license) if grid.shape == (29,22) else grid


def extract_frames_from_buffer(buffer, index, license=None):
    if index >= 643:
        packet = buffer[index-643:index+2]
        if len(packet) == 645:
            get_core().decode_packet(packet, license)
            return packet.hex()


def frame_from_event(event):
    """MeasurementFrame from a session MEASUREMENT event (raw fields only when the license opens raw data)."""
    packet = event.get('packet')
    raw_grid = event.get('raw_grid')
    return MeasurementFrame(
        timestamp=datetime.now().replace(microsecond=0).isoformat(),
        sensor_matrix=event['sensor_matrix'].tolist(), weight=event['weight'],
        battery=99.0,  # Measurement packets carry no battery field (legacy placeholder, battery_valid False).
        raw_data=list(packet) if packet is not None else None,
        raw_grid=raw_grid.tolist() if raw_grid is not None else None,
    )


def parse_frame_from_chunk(chunk, license=None):
    result = get_core().decode_packet(bytes.fromhex(chunk), license)
    return MeasurementFrame(
        timestamp=datetime.now().replace(microsecond=0).isoformat(),
        sensor_matrix=result.sensor_matrix, weight=result.weight,
        battery=result.battery, raw_data=result.raw_data, raw_grid=result.raw_grid,
        battery_valid=result.battery_valid,
    )


def extract_data(chunk, license=None):
    result = get_core().decode_packet(bytes.fromhex(chunk), license)
    return result.sensor_matrix, result.weight, str(int(result.battery)), result.raw_data


class DataParser:
    """Legacy framing for handlers used without a session. Decoding frames needs a full-mode license."""
    def __init__(self, data_queue, at_response_queue, license=None):
        self.data_queue = data_queue
        self.at_response_queue = at_response_queue
        self.license = license

    def process_all_data_by_terminator(self, buffer):
        while buffer:
            consumed, kind, packet = get_core().next_packet(buffer)
            if not consumed:
                break
            del buffer[:consumed]
            if kind == 1:
                self.data_queue.put(parse_frame_from_chunk(packet.hex(), self.license))
            elif kind == 2:
                self.at_response_queue.put(get_core().decode_response(packet))

    def _handle_at_response(self, packet):
        self.at_response_queue.put(get_core().decode_response(packet))
