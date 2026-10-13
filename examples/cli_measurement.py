import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fisica_sdk as fisica


def main():
    parser = argparse.ArgumentParser(description="Fisica measurement CLI")
    parser.add_argument("--duration", type=int, default=10, help="Measurement duration in seconds")
    parser.add_argument("--output", type=str, default="results.json", help="Output JSON file")
    args = parser.parse_args()

    if args.duration <= 0:
        print("--duration must be greater than 0", file=sys.stderr)
        return 2

    sdk = fisica.FisicaSDK()
    connected = False

    try:
        devices = sdk.scan_devices()
        if not devices:
            print("No devices found", file=sys.stderr)
            return 1

        sdk.connect(devices[0])
        connected = True
        sdk.start_measurement(duration=args.duration)
        sdk.wait()

        report = sdk.analyze()
        if report is None:
            print("Analysis skipped: not enough valid measurement data.", file=sys.stderr)
            return 1

        print(report)
        sdk.export_report(report, output=args.output)
        return 0
    finally:
        if connected:
            sdk.disconnect()
            sdk.wait()


if __name__ == "__main__":
    raise SystemExit(main())
