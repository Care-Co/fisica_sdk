import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fisica_sdk as fisica


def main():
    sdk = fisica.FisicaSDK()
    connected = False

    try:
        devices = sdk.scan_devices()
        if not devices:
            print("No devices found")
            return

        sdk.connect(devices[0])
        connected = True

        for i in range(5):
            sdk.set_metadata(id=i, name=f"Test_{i}")
            sdk.start_measurement(duration=5)
            sdk.wait()

            frames = sdk.get_session_frames() or []
            print(f"Session {i}: {len(frames)} frames captured")

            report = sdk.analyze()
            if report is None:
                print(f"Session {i}: analysis skipped; not enough valid data.")

            sdk.reset_session()

        reports = sdk.get_all_reports()
        if reports:
            sdk.export_report(reports, output="measurement_results.json")
            print("Exported reports to measurement_results.json")
        else:
            print("No reports to export.")
    finally:
        if connected:
            sdk.disconnect()
            sdk.wait()


if __name__ == "__main__":
    main()
