import fisica_sdk as fisica
import argparse

def main():
    parser = argparse.ArgumentParser(description='Fisica measurement CLI')
    parser.add_argument('--duration', type=int, default=10, help='Measurement duration')
    parser.add_argument('--output', type=str, default='results.json', help='Output file')
    args = parser.parse_args()

    sdk = fisica.FisicaSDK()
    devices = sdk.scan_devices()

    if not devices:
        print("No devices found")
        return

    sdk.connect(devices[0])
    sdk.start_measurement(duration=args.duration)
    sdk.wait()

    report = sdk.analyze()
    print(report)

    sdk.export_report(output=args.output)
    print(f"Results saved to {args.output}")

if __name__ == "__main__":
    main()