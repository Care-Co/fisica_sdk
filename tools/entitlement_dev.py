"""Internal development tools for entitlement tokens: test signer and mock server.

Never packaged. The Ed25519 signer is the RFC 8032 reference algorithm in plain
Python for tests and local development only; a production server signs with a
vetted library and keeps the private key on the server.
"""
import argparse
import base64
import hashlib
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_KEY = ROOT / 'tests' / 'fixtures' / 'entitlement-test-key.json'
REFRESH_SECONDS = 30 * 24 * 3600

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P


def _add(a, b):
    e = (a[1]-a[0]) * (b[1]-b[0]) % _P
    f = (a[1]+a[0]) * (b[1]+b[0]) % _P
    g = 2 * a[3] * b[3] * _D % _P
    h = 2 * a[2] * b[2] % _P
    return ((f-e) * (h-g) % _P, (h+g) * (f+e) % _P, (h-g) * (h+g) % _P, (f-e) * (f+e) % _P)


def _multiply(scalar, point):
    result = (0, 1, 1, 0)
    while scalar:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _base_point():
    y = 4 * pow(5, _P - 2, _P) % _P
    x2 = (y*y - 1) * pow(_D*y*y + 1, _P - 2, _P) % _P
    x = pow(x2, (_P + 3) // 8, _P)
    if (x*x - x2) % _P:
        x = x * pow(2, (_P - 1) // 4, _P) % _P
    if x & 1:
        x = _P - x
    return (x, y, 1, x*y % _P)


_G = _base_point()


def _compress(point):
    inverse = pow(point[2], _P - 2, _P)
    x, y = point[0] * inverse % _P, point[1] * inverse % _P
    return (y | ((x & 1) << 255)).to_bytes(32, 'little')


def _expand(secret):
    digest = hashlib.sha512(secret).digest()
    scalar = int.from_bytes(digest[:32], 'little') & ((1 << 254) - 8) | (1 << 254)
    return scalar, digest[32:]


def public_key(secret):
    return _compress(_multiply(_expand(secret)[0], _G))


def sign(secret, message):
    scalar, prefix = _expand(secret)
    public = _compress(_multiply(scalar, _G))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), 'little') % _L
    point = _compress(_multiply(r, _G))
    h = int.from_bytes(hashlib.sha512(point + public + message).digest(), 'little') % _L
    return point + ((r + h * scalar) % _L).to_bytes(32, 'little')


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def test_key():
    key = json.loads(TEST_KEY.read_text())
    return key['kid'], bytes.fromhex(key['secret'])


def pc_target(machine):
    """PC hash as the core computes it from the platform's machine id."""
    return hashlib.sha256(('fisica-pc-v1:' + machine).encode()).hexdigest()


def host_machine_id():
    """The machine id the core hashes for a PC target on this host (independent reimplementation)."""
    import subprocess
    import sys
    if sys.platform == 'darwin':
        output = subprocess.check_output(['ioreg', '-rd1', '-c', 'IOPlatformExpertDevice'], text=True)
        return next(line.split('"')[3] for line in output.splitlines() if '"IOPlatformUUID"' in line).upper()
    if sys.platform == 'win32':
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography', 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            return winreg.QueryValueEx(key, 'MachineGuid')[0].strip()
    for path in ('/etc/machine-id', '/var/lib/dbus/machine-id'):
        if Path(path).is_file():
            return Path(path).read_text().strip()
    raise RuntimeError('No machine id on this host')


def host_pc_vectors(now):
    """host_vectors() for command-line test programs on this host, which the core binds to the PC."""
    return host_vectors('pc', pc_target(host_machine_id()), now)


def issue_token(company, contract, verified_at, kind='basic', refresh_by=None, contract_start=0,
                contract_end=0, revoked_at=0, grants='', kid=None, secret=None, version=1, key='', bind_type='',
                bind='', key_until=0, full_until=0, features='', track=''):
    """Sign a key. Member order matches the developer portal's signer (app/fse.py)."""
    default_kid, default_secret = test_key()
    payload = {'v': version, 'kid': kid or default_kid, 'company': company, 'contract': contract, 'kind': kind,
               'verified_at': int(verified_at),
               'refresh_by': int(verified_at + REFRESH_SECONDS if refresh_by is None else refresh_by)}
    for name, value in (('contract_start', contract_start), ('contract_end', contract_end), ('revoked_at', revoked_at)):
        if value:
            payload[name] = int(value)
    if grants:
        payload['grants'] = grants
    for name, value in (('key', key), ('bind_type', bind_type), ('bind', bind), ('key_until', key_until),
                        ('full_until', full_until), ('features', features), ('track', track)):
        if value:
            payload[name] = value
    signing_input = ('FSE1.' + _b64(json.dumps(payload, separators=(',', ':')).encode('ascii'))).encode('ascii')
    return signing_input.decode() + '.' + _b64(sign(secret or default_secret, signing_input))


def read_token(token, secret=None):
    """Server-side check: Ed25519 is deterministic, so re-signing proves the signature."""
    try:
        prefix, body, signature = token.strip().split('.')
        signing_input = (prefix + '.' + body).encode('ascii')
        if prefix != 'FSE1' or _unb64(signature) != sign(secret or test_key()[1], signing_input):
            return None
        return json.loads(_unb64(body))
    except (ValueError, UnicodeError):
        return None


def host_vectors(target_type, target, now, company='acme', contract='ctr-1', key='key_test_1'):
    """Keys activated for one target (this host's app or PC), for binding tests.

    Static vectors cannot hold them: the target is read by the core on each host.
    """
    seat = dict(key=key, bind_type=target_type, bind=target, track='trial')
    day = 86400
    return {
        'target_type': target_type, 'target': target,
        'full': issue_token(company, contract, now, key_until=now + 90 * day, full_until=now + 14 * day, **seat),
        'full_older': issue_token(company, contract, now - day, key_until=now + 89 * day, full_until=now + 14 * day, **seat),
        'revoked': issue_token(company, contract, now + day, revoked_at=now + day, key_until=now + 91 * day,
                               full_until=now + 14 * day, **seat),
        'limited': issue_token(company, contract, now, key_until=now + 90 * day, full_until=now + 14 * day,
                               features='export', **seat),
    }


class MockEntitlementServer:
    """In-memory reference for the developer portal's SDK API (activate, refresh and pairing).

    Company, contract and key always come from the verified bearer token, never from the body.
    It keeps no usage data: a refresh is not a record of SDK use. Pairing takes only the target;
    approve() and deny() stand in for an owner entering the code in the portal.
    """
    def __init__(self, clock=time.time, pairing_interval=0):
        self.clock = clock
        self.contracts = {}   # (company, contract) -> dict(kind, contract_start, contract_end, revoked_at, grants, ...)
        self.keys = {}        # key -> dict(company, contract, bind_type, bind, days, released)
        self.pairings = {}    # device code -> dict(user_code, bind_type, bind, state, key)
        self.pairing_interval = pairing_interval
        self.on_pairing = None  # called with the user code when a device asks
        self.fail_requests = 0
        self.requests = []
        self._lock = threading.Lock()
        self._http = None

    def add_contract(self, company, contract, **fields):
        self.contracts[(company, contract)] = {'kind': 'basic', 'contract_start': 0, 'contract_end': 0,
                                               'revoked_at': 0, 'grants': '', **fields}

    def add_key(self, key, company, contract, days=90, bind_type='', bind=''):
        self.keys[key] = {'company': company, 'contract': contract, 'days': days, 'bind_type': bind_type,
                          'bind': bind, 'released': False}

    def issue(self, company, contract, key=None):
        now = int(self.clock())
        fields = dict(self.contracts[(company, contract)])
        if key:
            seat = self.keys[key]
            fields.update(key=key, bind_type=seat['bind_type'], bind=seat['bind'],
                          key_until=now + seat['days'] * 86400 if seat['bind'] and seat['days'] else 0)
            if seat['released']:
                fields['revoked_at'] = now
        return issue_token(company, contract, now, **fields)

    def _pairing(self, user_code):
        return next(p for p in self.pairings.values() if p['user_code'] == user_code)

    def approve(self, user_code, key):
        """An owner approves the code with an unbound key: the key is activated for the device's target."""
        with self._lock:
            pairing = self._pairing(user_code)
            self.keys[key].update(bind_type=pairing['bind_type'], bind=pairing['bind'])
            pairing.update(state='approved', key=key)

    def deny(self, user_code):
        with self._lock:
            self._pairing(user_code)['state'] = 'denied'

    def _pair(self, path, body):
        if path == '/sdk/v1/pairing':
            if set(body) != {'bind_type', 'bind'}:
                return 400, {'error': 'unexpected_body'}
            device = secrets.token_urlsafe(16)
            user_code = '%s-%s' % tuple(''.join(secrets.choice('BCDFGHJKLMNPQRSTVWXZ') for _ in range(4)) for _ in range(2))
            self.pairings[device] = {'user_code': user_code, 'bind_type': body['bind_type'], 'bind': body['bind'],
                                     'state': 'pending', 'key': None}
            page = 'https://developer.example.test/activate'
            return 200, {'device_code': device, 'user_code': user_code, 'verification_uri': page,
                         'verification_uri_complete': page + '?code=' + user_code, 'expires_in': 600,
                         'interval': self.pairing_interval}
        pairing = self.pairings.get(body.get('device_code'))
        if pairing is None or pairing['state'] in ('claimed', 'expired'):
            return 400, {'error': 'expired_token'}
        if pairing['state'] != 'approved':
            return 400, {'error': 'access_denied' if pairing['state'] == 'denied' else 'authorization_pending'}
        pairing['state'] = 'claimed'
        seat = self.keys[pairing['key']]
        return 200, {'token': self.issue(seat['company'], seat['contract'], pairing['key'])}

    def handle(self, path, token, body):
        callback = None
        with self._lock:
            self.requests.append((path, body))
            if self.fail_requests:
                self.fail_requests -= 1
                return 503, {'error': 'unavailable'}
            if path in ('/sdk/v1/pairing', '/sdk/v1/pairing/token'):
                status, answer = self._pair(path, body)
                if path == '/sdk/v1/pairing' and status == 200:
                    callback = self.on_pairing
            else:
                return self._handle(path, token, body)
        if callback:
            callback(answer['user_code'])  # outside the lock: it may approve or deny
        return status, answer

    def _handle(self, path, token, body):
        claims = read_token(token) if token else None
        contract = (claims['company'], claims['contract']) if claims else None
        if contract not in self.contracts or (claims.get('key') and claims['key'] not in self.keys):
            return 401, {'error': 'invalid_token'}
        key = claims.get('key')
        if path == '/sdk/v1/entitlement/activate':
            if not key or set(body) != {'bind_type', 'bind'}:
                return 400, {'error': 'invalid_target'}
            seat = self.keys[key]
            if seat['bind'] and (seat['bind_type'], seat['bind']) != (body['bind_type'], body['bind']):
                return 409, {'error': 'key_in_use'}
            seat.update(bind_type=body['bind_type'], bind=body['bind'])
            return 200, {'token': self.issue(*contract, key)}
        if path != '/sdk/v1/entitlement/refresh':
            return 404, {'error': 'not_found'}  # There is no usage endpoint.
        if body:
            return 400, {'error': 'unexpected_body'}  # No activity or device data is accepted.
        return 200, {'token': self.issue(*contract, key)}

    def start(self, port=0):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get('Content-Length') or 0)
                try:
                    body = json.loads(self.rfile.read(length) or b'{}')
                except ValueError:
                    body = None
                authorization = self.headers.get('Authorization', '')
                token = authorization[7:] if authorization.startswith('Bearer ') else None
                status, response = (400, {'error': 'invalid_json'}) if not isinstance(body, dict) else server.handle(self.path, token, body)
                data = b'' if response is None else json.dumps(response).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self._http = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        threading.Thread(target=self._http.serve_forever, daemon=True).start()
        return 'http://127.0.0.1:%d' % self._http.server_address[1]

    def stop(self):
        if self._http:
            self._http.shutdown()
            self._http.server_close()
            self._http = None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest='command', required=True)
    issue = commands.add_parser('issue', help='Write a test-key token file for offline import')
    issue.add_argument('--company', required=True)
    issue.add_argument('--contract', required=True)
    issue.add_argument('--kind', choices=['basic', 'special'], default='basic')
    issue.add_argument('--contract-end', type=int, default=0)
    issue.add_argument('--revoked-at', type=int, default=0)
    issue.add_argument('--grants', default='')
    issue.add_argument('--out', type=Path, required=True)
    serve = commands.add_parser('serve', help='Run the mock entitlement server on localhost')
    serve.add_argument('--port', type=int, default=8765)
    serve.add_argument('--company', default='demo-company')
    serve.add_argument('--contract', default='demo-contract')
    args = parser.parse_args()
    if args.command == 'issue':
        args.out.write_text(issue_token(args.company, args.contract, int(time.time()), kind=args.kind,
                                        contract_end=args.contract_end, revoked_at=args.revoked_at, grants=args.grants) + '\n')
        print('Wrote', args.out)
    else:
        server = MockEntitlementServer()
        server.add_contract(args.company, args.contract)
        print('Mock entitlement server:', server.start(args.port))
        print('Token:', server.issue(args.company, args.contract))
        threading.Event().wait()


if __name__ == '__main__':
    main()
