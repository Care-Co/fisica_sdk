"""JSON export of session reports and frames. In basic mode a report is exported as its summary only."""
import json
import os
from typing import Any, Dict, List

from .models import MeasurementFrame, MeasurementSession, SessionReport
from .utils import log


def _frame(frame: MeasurementFrame) -> Dict[str, Any]:
    return {
        "timestamp": frame.timestamp,
        "sensor_matrix": frame.sensor_matrix,
        "weight": frame.weight,
        "battery": frame.battery,
        "battery_valid": frame.battery_valid,
        "raw_data": frame.raw_data,
        "raw_grid": frame.raw_grid,
    }


def serialize_session_report(session: SessionReport, frames: bool = True) -> Dict[str, Any]:
    """The report as plain data. frames=False leaves out total_frame (the summary that basic mode exports)."""
    result = {
        "session_id": session.session_id,
        "name": session.name,
        "foot_length": {"left": session.foot_length.get("left"), "right": session.foot_length.get("right")},
        "foot_width": {"left": session.foot_width.get("left"), "right": session.foot_width.get("right")},
        "frame_count": session.frame_count,
        "duration_sec": round(session.duration_sec, 2),
        "weight": session.weight,
        "battery": session.battery,
        "battery_valid": session.battery_valid,
    }
    if frames:
        result["total_frame"] = [_frame(frame) for frame in session.total_frame]
    return result


def serialize_session_frame(session: MeasurementSession) -> List[Dict[str, Any]]:
    return [_frame(frame) for frame in session.frames]


def export_session_reports_to_json(reports: List[Dict[str, Any]], output_path: str) -> str:
    """Write serialized reports to output_path (".json" added when it has no extension); returns the path.

    The file is written to a temporary name first and then moved into place, so a reader never sees half a file.
    """
    if not os.path.splitext(output_path)[1]:
        output_path += ".json"
    output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    temporary = output_path + ".tmp"
    with open(temporary, 'w', encoding='utf-8') as file:
        json.dump(reports, file, ensure_ascii=False, indent=4)
    os.replace(temporary, output_path)
    log.info(f"JSON file successfully saved to '{output_path}'.")
    return output_path
