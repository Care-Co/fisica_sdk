"""Minimal consumer app. Install a binary wheel before running this file.

The same steps as every partner sample: SDK and core version, the license, connect to the
chosen Scale (or the synthetic mock), read the battery, measure for 5 seconds, stop, show the
report, disconnect.
"""
import argparse
import json
from pathlib import Path
import sys
import warnings
import webbrowser
from fisica_sdk import FisicaError, FisicaEntitlementWarning, FisicaSDK, MockTransport, NativeCore

NO_RESULT = 'No stable result; check the load on the Scale and the received frames'


def show_code(info):
    print(f"Pairing code: {info['user_code']}")
    print(f"Approve it at {info['verification_uri']} (valid for {info['expires_in'] // 60} minutes). Waiting...", flush=True)
    webbrowser.open(info['verification_uri_complete'])


def show_license(status):
    # "full mode", or "basic mode (no_key)" with the SDK's explanation of the reason.
    print(f"License: {status['mode']} mode" + (f" ({status['reason']})" if status['reason'] else ''))
    if status['message']:
        print(status['message'])
    if status['activation_code']:
        print(f"Activation code: {status['activation_code']}")


def show_report(report):
    if report is None:
        print(NO_RESULT)
        return
    # Weight and foot size come from every packet, in basic and full mode alike.
    print(f'Weight: {report.weight:.1f} kg')
    print(f"Left foot: length {report.foot_length['left']:.1f} cm, width {report.foot_width['left']:.1f} cm")
    print(f"Right foot: length {report.foot_length['right']:.1f} cm, width {report.foot_width['right']:.1f} cm")
    print(f'Frames: {report.frame_count}')
    print(f'Battery: {report.battery:.0f}%' if report.battery_valid else 'Battery: not read')


def show_error(error):
    sys.stdout.flush()  # keep the order of the lines above
    print(f'Error: {error}', file=sys.stderr)


def mock_transport(path):
    """Scripted synthetic replies from scale-mock.json; no measurement or protocol logic."""
    script = json.loads(path.read_text())
    core = NativeCore()
    exchanges = {core.command(name): [bytes.fromhex(value)] for name, value in script['replies'].items()}
    exchanges[core.command('START_CONTINUOUS_MEASUREMENT')].append(
        bytes.fromhex(script['measurement_hex']) * script['frame_count'])
    return MockTransport(exchanges)


def run():
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
    if not args.list and sum(bool(value) for value in (args.usb, args.ble, args.mock)) != 1:
        parser.error('Choose exactly one of --usb, --ble or --mock')
    sdk = FisicaSDK()  # quiet: this sample prints what you need; FisicaSDK(debug=True) shows the SDK's log
    version = sdk.version_info()
    print(f"Fisica SDK {version['sdk_version']} (core {version['core_version']})")
    if args.key:
        sdk.configure_entitlement(file=args.key, endpoint=args.endpoint)
        synced = sdk.sync_entitlement(wait=True)  # activate a new key before measuring (needs --endpoint)
        if synced and synced['error']:
            print(f"Activation failed, continuing with the key as it is: {synced['error']}")
    elif args.endpoint:
        sdk.configure_entitlement(endpoint=args.endpoint)  # the key stored for this PC, renewed with this server
    # Without --key the SDK uses the key stored for this PC: paired with --pair or
    # `python -m fisica_sdk license pair`, or imported earlier.
    if args.pair and not sdk.entitlement_status()['key']:
        print(f"Pairing: {sdk.pair_device(endpoint=args.endpoint, on_code=show_code)['pairing']}")
    show_license(sdk.entitlement_status())
    if args.list:
        devices = sdk.scan_devices()
        print(json.dumps(devices, indent=2, default=str) if devices else
              'No Scale found; check that it is powered on (and that Bluetooth is on for wireless Scales)')
        return 0
    transport = None
    device = {'id': 'mock', 'type': 'Serial'}
    if args.mock:
        transport = mock_transport(args.mock)
    else:
        requested = args.usb or args.ble
        device = next((item for item in sdk.scan_devices() if item['id'] == requested), None)
        if device is None:
            parser.error('Device not found; run --list and use the exact reported ID')
    try:
        sdk.connect(device, transport=transport)
        print(f"Connected to {device['id']} (firmware {sdk.get_device_info()['firmware_version'] or 'unknown'})")
        try:
            sdk.get_battery()  # measurement packets carry no battery field; the report shows this reading
        except FisicaError:
            pass  # the report then shows "Battery: not read"; measuring goes on
        print('Measuring for 5 seconds...')
        sdk.start_measurement(duration=5)  # the SDK stops the measurement after 5 seconds
        sdk.wait()
        show_report(sdk.analyze())
        return 0
    except Exception as error:  # one clear line instead of a stack trace
        show_error(error)
        return 1
    finally:
        sdk.disconnect()  # always, also after an error
        sdk.wait()
        print('Disconnected')


def main():
    # This sample prints the license itself; other SDK notices (such as an unverified
    # firmware) are shown as one line each.
    warnings.simplefilter('ignore', FisicaEntitlementWarning)
    warnings.formatwarning = lambda message, *args, **kwargs: f'Warning: {message}\n'
    try:
        return run()
    except Exception as error:  # for example a missing key file or core
        show_error(error)
        return 1


if __name__ == '__main__':
    sys.exit(main())
