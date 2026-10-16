"""MeasurementFrame from a session MEASUREMENT event. Framing, decoding and processing run in the native core."""
from datetime import datetime
from .models import MeasurementFrame


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
