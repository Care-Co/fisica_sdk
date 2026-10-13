import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fisica_sdk as fisica


def main():
    parser = argparse.ArgumentParser(description="Fisica scale calibration helper")
    parser.add_argument("--scale", type=int, default=None, help="Calibration value (-32768 to 32767)")
    parser.add_argument("--duration", type=int, default=10, help="Verification measurement duration in seconds")
    args = parser.parse_args()

    if args.duration <= 0:
        print("--duration must be greater than 0", file=sys.stderr)
        return 2

    sdk = fisica.FisicaSDK(debug=10)
    connected = False

    try:
        devices = sdk.scan_devices()
        if not devices:
            print("No devices found")
            return 1

        sdk.connect(devices[0])
        connected = True

        print("Please ensure platform is empty...")
        input("Press Enter when ready...")
        sdk.set_zero()
        print("Zero point calibration command sent.")

        calibration_value = args.scale
        if calibration_value is None:
            calibration_value = int(input("Enter scale calibration value (-32768 to 32767): "))
        sdk.set_scale(calibration_value)
        print(f"Scale calibration command sent: {calibration_value}")

        print("Place a known reference weight on platform...")
        reference_weight = float(input("Enter reference weight (kg): "))

        sdk.start_measurement(duration=args.duration)
        sdk.wait()
        report = sdk.analyze()
        if report is None:
            print("Verification skipped: not enough valid measurement data.")
            return 1

        measured_weight = report.weight
        print(f"Reference: {reference_weight}kg, Measured: {measured_weight}kg")
        print(f"Accuracy: {abs(measured_weight - reference_weight):.3f}kg difference")
        print("Calibration complete. Ready for measurements.")
        return 0
    finally:
        if connected:
            sdk.disconnect()
            sdk.wait()


if __name__ == "__main__":
    raise SystemExit(main())
