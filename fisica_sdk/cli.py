"""Terminal commands for the SDK key of this PC: python -m fisica_sdk license <command>.

  pair      Get a key by approving a code in the developer portal (no key needed)
  status    Show the key stored for this PC and whether it gives full mode
  import    Import a key or key file (activation or renewal file) and activate it online
  code      Show the activation code of a stored key that is not activated yet (offline activation)
  release   Release the stored key; online the seat is freed at once, otherwise a release code is shown

The key is stored once per PC in the SDK's application-data directory, where every Python script,
C# program and Flutter desktop app on this PC finds it. An app with its own app ID (iOS, Android, a
macOS .app) has its own target: it pairs itself with pair_device() or the wrapper's pair call.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import threading
import warnings
import webbrowser

from .entitlement import DEFAULT_ENDPOINT, Entitlement
from .errors import FisicaEntitlementWarning


def _date(value):
    return datetime.fromtimestamp(value).strftime('%Y-%m-%d %H:%M') if value else None


def _summary(status):
    if not status['key']:
        return ['No SDK key is stored for this PC. Run: python -m fisica_sdk license pair']
    target = status['bind'] or status['target'] or ''
    target = target if status['bind_type'] == 'app' else (target[:8] + '…' if target else '')
    lines = [f"Mode: {status['mode']}" + (f" ({status['reason']})" if status['reason'] else ''),
             f"Key: {status['key']} · {status['track'] or 'contract'} {status['contract']} of {status['company']}",
             f"Activated for: {status['bind_type'] + ' ' + target if status['bind_type'] else 'not activated yet'}"]
    if status['key_until']:
        lines.append(f"Key valid until: {_date(status['key_until'])} (renewed online before then)")
    if status['full_until']:
        lines.append(f"Full mode until: {_date(status['full_until'])}")
    if status['reason']:
        lines.append(status['message'])
    lines += [f"Warning: {name}" for name in status['warnings']]
    return lines


def _print(status, as_json):
    if as_json:
        print(json.dumps({key: value for key, value in status.items() if key != 'warning_bits'}, ensure_ascii=False, indent=2))
    else:
        print('\n'.join(_summary(status)))


def _pair(args):
    entitlement = Entitlement(endpoint=args.endpoint)

    def show(info):
        print(f"Open {info['verification_uri']} and enter this code:\n\n    {info['user_code']}\n")
        print(f"The code is valid for {info['expires_in'] // 60} minutes. An owner or admin of your organization "
              'approves it with a new or unused key. Waiting… (Ctrl+C to stop)', flush=True)
        if not args.no_browser:
            try:
                webbrowser.open(info['verification_uri_complete'])
            except Exception:
                pass

    cancel = threading.Event()
    try:
        outcome = entitlement.pair(on_code=show, timeout=args.timeout, cancel=cancel)
    except KeyboardInterrupt:
        cancel.set()
        print('\nStopped.')
        return 1
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    messages = {'approved': 'Approved: the key is stored for this PC.', 'denied': 'The request was denied in the portal.',
                'expired': 'The code expired. Run the command again for a new code.', 'cancelled': 'Stopped.'}
    print(messages[outcome])
    if outcome == 'approved':
        print()
        _print(entitlement.status(), args.json)
    return 0 if outcome == 'approved' else 1


def _import(args):
    source = Path(args.key)
    file = source if source.is_file() else None
    entitlement = Entitlement(token=None if file else args.key, file=file, endpoint=None if args.offline else args.endpoint)
    entitlement.sync(wait=True)
    status = entitlement.status()
    _print(status, args.json)
    if status['activation_code'] and not args.json:
        print('\nOffline: enter this activation code at the key in the developer portal and import the file it gives:')
        print(status['activation_code'])
    return 0 if status['mode'] == 'full' else 1


def _stored(args, online=False):
    entitlement = Entitlement(endpoint=args.endpoint, online=online)
    return entitlement, entitlement.status()


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m fisica_sdk', description='Fisica SDK commands.')
    groups = parser.add_subparsers(dest='group', required=True)
    license_parser = groups.add_parser('license', help='the SDK key of this PC', description='The SDK key of this PC.',
                                       formatter_class=argparse.RawDescriptionHelpFormatter,
                                       epilog=__doc__.split('\n\n', 1)[1])
    commands = license_parser.add_subparsers(dest='command', required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--endpoint', default=DEFAULT_ENDPOINT, help=f'developer portal URL (default {DEFAULT_ENDPOINT})')
    common.add_argument('--json', action='store_true', help='print the status as JSON')
    pair = commands.add_parser('pair', parents=[common], help='get a key by approving a code in the developer portal')
    pair.add_argument('--no-browser', action='store_true', help='do not open the approval page')
    pair.add_argument('--timeout', type=float, help='seconds to wait at most (default: until the code expires)')
    commands.add_parser('status', parents=[common], help='show the stored key')
    imported = commands.add_parser('import', parents=[common], help='import a key or key file')
    imported.add_argument('key', help='key file path or the key itself (FSE1.…)')
    imported.add_argument('--offline', action='store_true', help='do not activate or renew online')
    commands.add_parser('code', parents=[common], help='show the activation code of a stored key')
    release = commands.add_parser('release', parents=[common], help='release the stored key')
    release.add_argument('--yes', action='store_true', help='do not ask for confirmation')
    args = parser.parse_args(argv)

    with warnings.catch_warnings():
        warnings.simplefilter('ignore', FisicaEntitlementWarning)  # the commands print the status themselves
        if args.command == 'pair':
            return _pair(args)
        if args.command == 'import':
            return _import(args)
        entitlement, status = _stored(args, online=args.command == 'release')
        if args.command == 'status':
            _print(status, args.json)
            return 0 if status['mode'] == 'full' else 1
        if not status['key']:
            print(_summary(status)[0])
            return 1
        if args.command == 'code':
            if not status['activation_code']:
                print('The stored key is already activated or cannot be activated here.')
                return 1
            print(status['activation_code'])
            return 0
        if not args.yes:
            answer = input(f"Release key {status['key']} on this PC? Full mode stops here. [y/N] ")
            if answer.strip().lower() not in ('y', 'yes'):
                print('Kept.')
                return 1
        try:
            code = entitlement.release()
        except Exception as error:
            print(f'The key cannot be released here: {error}', file=sys.stderr)
            return 1
        if entitlement.released_online:
            print('Released. The seat is free in the developer portal.')
        else:
            print('Released on this PC. Enter this release code at the key in the developer portal to free the seat now:')
            print(code)
        return 0


if __name__ == '__main__':
    sys.exit(main())
