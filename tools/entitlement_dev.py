"""Internal development tools for entitlement tokens: test signer and mock server.

Never packaged. The Ed25519 signer is the RFC 8032 reference algorithm in plain
Python for tests and local development only; a production server signs with a
vetted library and keeps the private key on the server.
"""
import argparse
import base64
import hashlib
import json
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


def issue_token(company, contract, verified_at, kind='basic', refresh_by=None, contract_start=0,
                contract_end=0, revoked_at=0, grants='', kid=None, secret=None, version=1):
    """Sign verification info. Confirmation time and contract dates stay separate fields."""
    default_kid, default_secret = test_key()
    payload = {'v': version, 'kid': kid or default_kid, 'company': company, 'contract': contract, 'kind': kind,
               'verified_at': int(verified_at),
               'refresh_by': int(verified_at + REFRESH_SECONDS if refresh_by is None else refresh_by)}
    for name, value in (('contract_start', contract_start), ('contract_end', contract_end), ('revoked_at', revoked_at)):
        if value:
            payload[name] = int(value)
    if grants:
        payload['grants'] = grants
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


class MockEntitlementServer:
    """In-memory reference for the server API contract (doc/entitlement-api.md).

    Company and contract always come from the verified bearer token, never from the body.
    It keeps no usage data: a refresh is not a record of SDK use.
    """
    def __init__(self, clock=time.time):
        self.clock = clock
        self.contracts = {}   # (company, contract) -> dict(kind, contract_start, contract_end, revoked_at, grants)
        self.fail_requests = 0
        self.requests = []
        self._lock = threading.Lock()
        self._http = None

    def add_contract(self, company, contract, **fields):
        self.contracts[(company, contract)] = {'kind': 'basic', 'contract_start': 0, 'contract_end': 0,
                                               'revoked_at': 0, 'grants': '', **fields}

    def issue(self, company, contract):
        return issue_token(company, contract, int(self.clock()), **self.contracts[(company, contract)])

    def handle(self, path, token, body):
        with self._lock:
            self.requests.append((path, body))
            if self.fail_requests:
                self.fail_requests -= 1
                return 503, {'error': 'unavailable'}
            claims = read_token(token) if token else None
            key = (claims['company'], claims['contract']) if claims else None
            if key not in self.contracts:
                return 401, {'error': 'invalid_token'}
            if path != '/sdk/v1/entitlement/refresh':
                return 404, {'error': 'not_found'}  # Refresh is the only SDK call; there is no usage endpoint.
            if body:
                return 400, {'error': 'unexpected_body'}  # No activity or device data is accepted.
            return 200, {'token': self.issue(*key)}

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
