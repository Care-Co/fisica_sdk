import json
import os
from typing import List, Dict, Any, Union

from .models import SessionReport, MeasurementSession
from .utils import log



# Serialize SessionReport for JSON export
def serialize_session_report(session: SessionReport) -> Dict[str, Any]:
    return {
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
        "total_frame": [
            {
                "timestamp": frame.timestamp,
                "sensor_matrix": frame.sensor_matrix
            }
            for frame in session.total_frame
        ]
    }

def serialize_session_frame(session: MeasurementSession) -> List[Dict[str, Any]]:
    return [
            {
                "timestamp": frame.timestamp,
                "sensor_matrix": frame.sensor_matrix
            }
            for frame in session.frames
    ]


def get_sensor_matrix_as_string(matrix: list[list[int]]) -> str:
    return "[" + ", ".join(f"[{', '.join(map(str, row))}]" for row in matrix) + "]"

def format_sensor_matrix_inline(report: dict) -> None:
    if "sensor_matrix" in report and isinstance(report["sensor_matrix"], list):
        matrix = report["sensor_matrix"]
        if all(isinstance(row, list) for row in matrix):
            inline_rows = [f"  [{', '.join(map(str, row))}]" for row in matrix]
            inline_string = "[\n" + ",\n".join(inline_rows) + "\n]"
            report["sensor_matrix"] = inline_string
def reshape_sensor_matrix(report: dict, rows: int, cols: int):
    if "sensor_matrix" in report and isinstance(report["sensor_matrix"], list):
        flat = report["sensor_matrix"]
        if len(flat) == rows * cols:
            import numpy as np
            report["sensor_matrix"] = np.array(flat).reshape((rows, cols)).tolist()


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
        for report in reports:
            log.debug(f"individual report : {report}")
            if 'total_frame' in report:
                for frame in report['total_frame']:
                    log.debug(f"frame : {frame}")
                    if "sensor_matrix" in frame:
                        # Reshape and convert to pretty string before serialization
                        reshape_sensor_matrix({"sensor_matrix": frame["sensor_matrix"]}, 22, 11)
                        frame["sensor_matrix"] = get_sensor_matrix_as_string(frame["sensor_matrix"])
        with open(output_path, 'w', encoding='utf-8') as f:
            pretty = json.dumps(reports, ensure_ascii=False, indent=4)
            f.write(pretty)
        log.info(f"JSON file successfully saved to '{output_path}'.")
    except Exception as e:
        log.error(f"Error occurred while saving JSON file: {e}")
        raise e