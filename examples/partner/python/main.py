"""Minimal consumer app. Install a binary wheel before running this file."""
import argparse
import json
from pathlib import Path
import webbrowser
from fisica_sdk import FisicaSDK, MockTransport, NativeCore


def show_code(info):
    print(f"Pairing code: {info['user_code']}")
    print(f"Approve it at {info['verification_uri']} (valid for {info['expires_in'] // 60} minutes). Waiting...", flush=True)
    webbrowser.open(info['verification_uri_complete'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--usb', metavar='PORT', help='COM7 or /dev/cu.usbserial-...')
    parser.add_argument('--ble', metavar='ID', help='ID printed by --list')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--mock', type=Path, help='Path to scale-mock.json; no hardware needed')
    parser.add_argument('--key', type=Path, help='SDK key file from the developer portal (full mode)')
    parser.add_argument('--pair', action='store_true',
                        help='Without a stored key: show a code to approve in the developer portal; the key is stored for this PC')
    parser.add_argument('--endpoint', help='Developer portal URL, to activate and renew the key online '
                                           '(stored and paired keys: https://developer.fisicahealthcare.com by default)')
    args = parser.parse_args()
    sdk = FisicaSDK()
    if args.key:
        sdk.configure_entitlement(file=args.key, endpoint=args.endpoint)
        if args.endpoint:
            sdk._entitlement.sync(wait=True)  # activate a new key before measuring
    elif args.endpoint:
        sdk.configure_entitlement(endpoint=args.endpoint)  # the key stored for this PC, renewed with this server
    # Without --key the SDK uses the key stored for this PC: paired with --pair or
    # `python -m fisica_sdk license pair`, or imported earlier.
    if args.pair and not sdk.entitlement_status()['key']:
        print(f"Pairing: {sdk.pair_device(endpoint=args.endpoint, on_code=show_code)['pairing']}")
    status = sdk.entitlement_status()
    print(f"License: {status['mode']} mode" + (f" ({status['reason']})" if status['reason'] else ''))
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
