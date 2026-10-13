import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fisica_sdk as fisica


def main():
    sdk = fisica.FisicaSDK()
    connected = False

    try:
        devices = sdk.scan_devices()
        print("Available devices:", devices)

        if not devices:
            print("No devices found")
            return

        sdk.connect(devices[0])
        connected = True

        sdk.set_metadata(name="John Doe")
        sdk.start_measurement(duration=10)
        sdk.wait()

        report = sdk.analyze()
        if report is None:
            print("Analysis skipped: not enough valid measurement data.")
            return

        print("Analysis complete:", report)
    finally:
        if connected:
            sdk.disconnect()
            sdk.wait()


if __name__ == "__main__":
    main()
