"""Minimal consumer app. Install a binary wheel before running this file."""
import argparse
import json
from pathlib import Path
from fisica_sdk import FisicaSDK, MockTransport, NativeCore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--usb', metavar='PORT', help='COM7 or /dev/cu.usbserial-...')
    parser.add_argument('--ble', metavar='ID', help='ID printed by --list')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--mock', type=Path, help='Path to scale-mock.json; no hardware needed')
    args = parser.parse_args()
    sdk = FisicaSDK()
    if args.list:
        print(json.dumps(sdk.scan_devices(), indent=2, default=str))
        return
    if sum(bool(value) for value in (args.usb, args.ble, args.mock)) != 1:
        parser.error('Choose exactly one of --usb, --ble or --mock')
    transport = None
    device = {'id': 'mock', 'type': 'Serial'}
    if args.mock:
        script = json.loads(args.mock.read_text())
        core = NativeCore()
        exchanges = {core.command(name): [bytes.fromhex(value)] for name, value in script['replies'].items()}
        exchanges[core.command('START_CONTINUOUS_MEASUREMENT')].append(
            bytes.fromhex(script['measurement_hex']) * script['frame_count'])
        transport = MockTransport(exchanges)
    else:
        devices = sdk.scan_devices()
        requested = args.usb or args.ble
        device = next((item for item in devices if item['id'] == requested), None)
        if device is None:
            parser.error('Device not found; run --list and use the exact reported ID')
    try:
        sdk.connect(device, transport=transport)
        sdk.on_data(lambda frame: print('Measurement frame received'))
        sdk.start_measurement(duration=5)
        sdk.wait()
        report = sdk.analyze()
        print(f'Weight: {report.weight} kg; frames: {report.frame_count}' if report is not None
              else 'No stable result; check load and received frames')
    finally:
        sdk.disconnect()
        sdk.wait()


if __name__ == '__main__':
    main()
