import json
import os
from typing import List, Dict, Any, Union
from .models import SessionReport, MeasurementSession
from .utils import log



# Serialize SessionReport for JSON export. frames=False: the summary only (basic mode).
def serialize_session_report(session: SessionReport, frames: bool = True) -> Dict[str, Any]:
    result = {
        "session_id": session.session_id,
        "name": session.name,
        "foot_length": {
            "left": session.foot_length.get("left"),
            "right": session.foot_length.get("right")
        },
        "foot_width": {
            "left": session.foot_width.get("left"),
            "right": session.foot_width.get("right")
        },
        "frame_count": session.frame_count,
        "duration_sec": round(session.duration_sec, 2),
        "weight": session.weight,
        "battery": session.battery,
        "battery_valid": session.battery_valid,
        "total_frame": [
            {
                "timestamp": frame.timestamp,
                "sensor_matrix": frame.sensor_matrix,
                "weight": frame.weight,
                "battery": frame.battery,
                "battery_valid": frame.battery_valid,
                "raw_data": frame.raw_data,
                "raw_grid": frame.raw_grid,
            }
            for frame in session.total_frame
        ]
    }
    if not frames:
        del result["total_frame"]
    return result

def serialize_session_frame(session: MeasurementSession) -> List[Dict[str, Any]]:
    return [
            {
                "timestamp": frame.timestamp,
                "sensor_matrix": frame.sensor_matrix,
                "weight": frame.weight,
                "battery": frame.battery,
                "battery_valid": frame.battery_valid,
                "raw_data": frame.raw_data,
                "raw_grid": frame.raw_grid,
            }
            for frame in session.frames
    ]

def export_session_reports_to_json(
        reports: List[Dict[str, Union[str, float, int, dict, list]]],
        output_path: str
) -> None:
    """
    Save multiple serialize_session_report results into a JSON file.

    Args:
        reports: A list of results returned from serialize_session_report.
        output_path: Full path (including filename) where the JSON file will be saved.
    """
    log.debug(reports)

    if not os.path.splitext(output_path)[1]:
        output_path += ".json"
    try:
        output_path = os.path.abspath(output_path)
        log.debug(f"output_path : {output_path}")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            pretty = json.dumps(reports, ensure_ascii=False, indent=4)
            f.write(pretty)
        log.info(f"JSON file successfully saved to '{output_path}'.")
    except Exception as e:
        log.error(f"Error occurred while saving JSON file: {e}")
        raise e
