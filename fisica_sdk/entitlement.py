"""SDK key handling for the Python wrapper: storage and network only.

Signature checks, the app/PC the key is activated for and the full/basic decision are made by
the native core, which also limits what a measurement session hands out. This module stores the
key, activates it online, renews it, pairs a device without a key (the portal approves a code)
and reports the status.

The SDK does not track its own use: nothing here records or reports whether, when or how often
it is used. The stored state is the signed key and the latest time seen, kept for offline checks.
"""
import json
import os
from pathlib import Path
import sys
import threading
import time
import urllib.error
import urllib.request
import warnings

from . import codes
from .errors import FisicaEntitlementWarning
from .native import NativeEntitlement

AUTO = object()
DEFAULT_ENDPOINT = 'https://developer.fisicahealthcare.com'
# Names follow the shared C header (fisica_sdk/codes.py is generated from it).
_names = lambda enum: {member.value: member.name.lower() for member in enum}
MODE = _names(codes.FisicaMode)
REASON = {**_names(codes.FisicaModeReason), 0: None}
CONTRACT_STATE = _names(codes.FisicaContract)
CONTRACT_KIND = {**_names(codes.FisicaContractKind), 0: None}
TRACK = {**_names(codes.FisicaTrack), 0: None}
BIND_TYPE = {**_names(codes.FisicaBindType), 0: None}
IMPORT_OUTCOME = {**_names(codes.FisicaEntitlementOutcome), 0: None}
KEY_ENVIRONMENT = {**_names(codes.FisicaKeyEnvironment), 0: None}
FEATURES = {member.value: member.name.lower() for member in codes.FisicaFeature}
_BASIC = 'Basic mode keeps connection, measurement, weight and foot size; raw data and full-rate frames need full mode.'
REASON_MESSAGES = {
    'no_key': 'No SDK key. Create one in the developer portal and import it. ' + _BASIC,
    'legacy_token': 'This is a contract confirmation, not an SDK key. Create an SDK key in the developer portal. ' + _BASIC,
    'not_activated': 'The SDK key is not activated yet: connect to the internet, or enter the activation code in the developer portal and import the file it gives. ' + _BASIC,
    'no_target': 'The SDK cannot read this app\'s ID or this PC\'s identity, so a key cannot be activated here. ' + _BASIC,
    'other_target': 'This SDK key is activated for another app or PC. ' + _BASIC,
    'revoked': 'The SDK key was released or its contract was revoked. ' + _BASIC,
    'clock_rollback': 'This device\'s clock is earlier than a time the SDK has already seen; correct the clock. ' + _BASIC,
    'key_expired': 'The SDK key expired: connect to renew it, or import a renewal file from the developer portal. ' + _BASIC,
    'not_started': 'The contract of this SDK key has not started yet. ' + _BASIC,
    'full_ended': 'The trial or contract of this SDK key has ended. ' + _BASIC,
}
_WARNING_MESSAGES = {
    'renew_due': 'The SDK key is due for renewal: connect to the internet or import a renewal file.',
    'grace': 'The contract has ended; full mode continues during the grace period.',
    'full_ending': 'Full mode ends within 7 days (trial or contract end).',
    'import_rejected': 'The last SDK key or file was rejected (format, unknown signing key, signature or another app/PC).',
    'test_key': 'The SDK key is signed with a TEST key; this is not a production license.',
}
WARNINGS = {member.value: (member.name.lower(), _WARNING_MESSAGES[member.name.lower()]) for member in codes.FisicaEntitlementWarning}
_times = ('verified_at', 'refresh_by', 'key_until', 'full_until', 'contract_start', 'contract_end', 'revoked_at')
NO_KEY_STATUS = {
    'mode': 'basic', 'reason': 'no_key', 'features': [], 'contract_state': 'unverified', 'contract_kind': None,
    'track': None, 'company': None, 'contract': None, 'key': None, 'grants': [], 'bind_type': None, 'bind': None,
    **{name: None for name in _times}, 'last_import': None, 'key_environment': None, 'warnings': [],
    'message': REASON_MESSAGES['no_key'], 'last_sync_error': None,
}


def default_storage_directory():
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'Fisica' / 'sdk'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'Fisica' / 'sdk'
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state') / 'fisica-sdk'


class Entitlement:
    """One SDK instance's license: its SDK key, the key's activation here, and renewal.

    token/file: an SDK key from the developer portal, or a key file the portal made for this
    device (activation or renewal file; works on a PC that is never online). endpoint: server
    base URL; without it no network is used. online=False keeps a configured endpoint unused.
    storage: state file path, None for memory only, or by default a per-seat file in the user's
    application-data directory; there, without token and file, the key stored for this app or PC
    (paired or imported earlier) is loaded. Contexts are never shared between instances.

    Online, an unactivated key is activated for this app or PC automatically, and a key is
    renewed before it expires. Network failures never change the license.
    """
    def __init__(self, token=None, file=None, endpoint=None, online=True, storage=AUTO,
                 clock=time.time, timeout=5.0, on_status=None, core=None):
        self._native = NativeEntitlement(core)
        self._lock = threading.RLock()
        self._clock = clock
        self._endpoint = endpoint.rstrip('/') if endpoint else None
        self._online = bool(online)
        self._storage = storage
        self._timeout = timeout
        self._on_status = on_status
        self._noticed = (None, 0)
        self._sync_thread = None
        self._sync_lock = threading.Lock()  # One exchange with the server at a time.
        self._next_sync = 0.0
        self.last_sync_error = None
        if file is not None:
            token = Path(file).read_text(encoding='ascii', errors='replace')
        if token is not None:
            self.import_token(token)
        elif storage is AUTO and self._load_stored():
            self._notify()
            self.sync()
        else:
            self._notify()

    @property
    def native(self):
        """The core license context, for NativeSession.license()."""
        return self._native

    def _now(self):
        return int(self._clock())

    def _path(self, status):
        if self._storage is None or not status['company']:
            return None
        if self._storage is not AUTO:
            return Path(self._storage)
        # One file per seat on this device, named by the core from the company, the contract and this
        # app's or PC's target: every wrapper on the PC finds the same file. Never a usage record.
        return default_storage_directory() / self._native.state_name()

    def _save(self, path=None):
        try:
            path = path or self._path(self._native.status(self._now()))
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(path.name + '.tmp')
                temporary.write_bytes(self._native.export_state())
                os.replace(temporary, path)
        except OSError as error:
            self.last_sync_error = 'storage: ' + str(error)

    def _load_stored(self):
        """Without a key: load the key stored for this app or PC (paired or imported earlier), if any.

        State files are named by the core from the company, the contract and the target, so a file
        whose name the core gives again after restoring it belongs to this app or PC. With several,
        a full-mode key wins, then the most recently verified one.
        """
        try:
            paths = sorted(default_storage_directory().glob('entitlement-*.state'))
        except OSError:
            return False
        best = None
        for path in paths:
            try:
                state = path.read_bytes()
                with NativeEntitlement(self._native.core) as probe:
                    probe.restore_state(state)
                    if probe.state_name() != path.name:
                        continue
                    raw = probe.status(self._now())
            except (OSError, ValueError, RuntimeError):
                continue
            rank = (MODE[raw['mode']] == 'full', raw['verified_at'])
            if best is None or rank > best[0]:
                best = (rank, state)
        if best is None:
            return False
        with self._lock:
            try:
                self._native.restore_state(best[1])
            except (ValueError, RuntimeError):
                return False
        return True

    def import_token(self, token):
        """Import a key or key file content. Returns the outcome name; never raises on a bad key."""
        with self._lock:
            now = self._now()
            outcome = self._native.import_token(token, now)
            if outcome == 1:
                self._next_sync = 0.0  # a new key may need activation now
                try:
                    path = self._path(self._native.status(now))
                    if path and path.is_file():
                        # The key activated or renewed earlier wins over the original key file.
                        self._native.restore_state(path.read_bytes())
                        self._native.import_token(token, now)
                except (OSError, ValueError):
                    pass
                self._save()
            self._notify()
        if outcome == 1:
            self.sync()
        return IMPORT_OUTCOME[outcome]

    def import_file(self, path):
        return self.import_token(Path(path).read_text(encoding='ascii', errors='replace'))

    def status(self):
        with self._lock:
            raw = self._native.status(self._now())
            code = self._native.activation_code() if REASON[raw['reason']] == 'not_activated' else None
        bits = raw['warning_bits']
        reason = REASON[raw['reason']]
        status = {
            'mode': MODE[raw['mode']], 'reason': reason,
            'features': [name for bit, name in FEATURES.items() if raw['features'] & bit],
            'contract_state': CONTRACT_STATE[raw['contract_state']],
            'contract_kind': CONTRACT_KIND[raw['contract_kind']], 'track': TRACK[raw['track']],
            'company': raw['company'] or None, 'contract': raw['contract'] or None, 'key': raw['key'] or None,
            'grants': [grant for grant in raw['grants'].split(',') if grant],
            'bind_type': BIND_TYPE[raw['bind_type']], 'bind': raw['bind'] or None,
            'target_type': BIND_TYPE[raw['target_type']], 'target': raw['target'] or None,
            **{name: raw[name] or None for name in _times},
            'last_import': IMPORT_OUTCOME[raw['last_import']],
            'key_environment': KEY_ENVIRONMENT[raw['key_environment']],
            'warnings': [name for bit, (name, _) in WARNINGS.items() if bits & bit],
            'warning_bits': bits,
            'message': REASON_MESSAGES.get(reason),
            'activation_code': code,
            'last_sync_error': self.last_sync_error,
        }
        return status

    def _notify(self):
        status = self.status()
        reason, seen = self._noticed
        new = status['warning_bits'] & ~seen
        self._noticed = (status['reason'], status['warning_bits'])
        if status['reason'] and status['reason'] != reason:
            warnings.warn('Fisica SDK license: ' + status['message'], FisicaEntitlementWarning, stacklevel=2)
        for bit, (_, message) in WARNINGS.items():
            if new & bit:
                warnings.warn('Fisica SDK license: ' + message, FisicaEntitlementWarning, stacklevel=2)
        if self._on_status:
            try:
                self._on_status(status)
            except Exception:
                pass

    def check_status(self):
        """Re-evaluate the license for the current time and renew in the background when due.

        Called from feature paths so a long-running app notices an ending key or contract.
        It records nothing about the use and never blocks or raises.
        """
        try:
            self._notify()
            self.sync()
        except Exception as error:
            self.last_sync_error = 'check: ' + str(error)

    def license_session(self, session, now_ms=None):
        """Copy this license into a NativeSession (serialized with every other use of the context)."""
        with self._lock:
            session.license(self._native, self._now(), now_ms)

    def full_rate(self):
        """True while full mode hands out every frame."""
        with self._lock:
            return bool(self._native.status(self._now())['features'] & codes.FisicaFeature.FULL_RATE)

    def has_feature(self, name):
        """True while full mode opens the feature ('raw_data', 'export', 'advanced')."""
        return name in self.status()['features']

    def activation_code(self):
        """FAC1 code for activating the loaded key in the developer portal, for a device that is never online."""
        with self._lock:
            return self._native.activation_code()

    def release(self):
        """Release the key activated here: forget it and return the FRC1 code to enter in the developer portal.

        Online, the seat is also freed when the server next sees the key as released; offline, the
        code is how the portal learns that this device no longer holds the key.
        """
        with self._lock:
            path = self._path(self._native.status(self._now()))
            code = self._native.release()
            try:
                if path:
                    path.unlink(missing_ok=True)
            except OSError as error:
                self.last_sync_error = 'storage: ' + str(error)
            self._notify()
        return code

    def pair(self, on_code=None, timeout=None, cancel=None):
        """Get a key without one: the developer portal approves a code shown here (device authorization).

        The SDK sends only this app's ID or this PC's hash and receives a code. on_code(info) is called
        with 'user_code', 'verification_uri', 'verification_uri_complete' and 'expires_in' (seconds);
        without it the code is printed. An owner or admin of the organization enters the code at
        verification_uri and approves it; the key, already activated for this app or PC, then arrives
        here, is stored and is found again by Entitlement() without a key.

        Blocks until the code is approved, denied or expired (or timeout seconds, or cancel, a
        threading.Event, is set). Returns 'approved', 'denied', 'expired' or 'cancelled'. Raises
        RuntimeError without an endpoint, without a readable app ID or PC, or when the server cannot
        be reached to start.
        """
        if not self._endpoint:
            raise RuntimeError('Pairing needs the developer portal endpoint')
        with self._lock:
            request = self._native.activation_request()
        if not request:
            raise RuntimeError(REASON_MESSAGES['no_target'])
        try:
            device = self._post('/sdk/v1/pairing', None, request)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"Pairing refused: {error.code} {error.read().decode('utf-8', 'replace')[:200]}") from None
        except OSError as error:
            raise RuntimeError(f'Pairing could not reach {self._endpoint}: {error}') from None
        info = {name: device[name] for name in ('user_code', 'verification_uri', 'verification_uri_complete', 'expires_in')}
        if on_code:
            on_code(info)
        else:
            print(f"Fisica SDK: open {info['verification_uri']} and enter the code {info['user_code']} "
                  f"(valid for {info['expires_in'] // 60} minutes).", file=sys.stderr, flush=True)
        cancel = cancel or threading.Event()
        interval = max(0.0, float(device.get('interval', 5)))
        # A key approved just before the code expires can still be received for a short while.
        limit = info['expires_in'] + 30 if timeout is None else min(timeout, info['expires_in'] + 30)
        deadline = time.monotonic() + limit
        while True:
            if cancel.wait(min(interval, max(0.0, deadline - time.monotonic()))):
                return 'cancelled'
            try:
                answer = self._post('/sdk/v1/pairing/token', None, {'device_code': device['device_code']})
            except urllib.error.HTTPError as error:
                try:
                    code = json.loads(error.read() or b'{}').get('error')
                except ValueError:
                    code = None
                if code == 'access_denied':
                    return 'denied'
                if code == 'expired_token':
                    return 'expired'
                if code == 'slow_down':
                    interval += 5
            except OSError as error:
                self.last_sync_error = 'network: ' + str(error)  # keep polling until the code expires
            else:
                outcome = self.import_token(answer['token'])
                if outcome != 'accepted':
                    raise RuntimeError(f'The paired key was not accepted here ({outcome})')
                return 'approved'
            if time.monotonic() >= deadline:
                return 'expired'

    def _post(self, path, token, body):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        request = urllib.request.Request(self._endpoint + path, data=body.encode() if isinstance(body, str) else json.dumps(body).encode(),
                                         headers=headers, method='POST')
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            data = response.read()
            return json.loads(data) if data else None

    def _due(self):
        """What the server should do now: activate, renew, or nothing."""
        with self._lock:
            raw = self._native.status(self._now())
            token = self._native.token()
            request = self._native.activation_request()
        reason = REASON[raw['reason']]
        if not token or reason in ('no_key', 'legacy_token', 'other_target', 'no_target', 'revoked'):
            return None, token, None
        if reason == 'not_activated':
            return ('/sdk/v1/entitlement/activate', token, request) if request else (None, token, None)
        if raw['warning_bits'] & codes.FisicaEntitlementWarning.RENEW_DUE or reason in ('key_expired', 'full_ended', 'not_started'):
            return '/sdk/v1/entitlement/refresh', token, {}
        return None, token, None

    def _sync(self, force=False):
        """Activate or renew with the server. A failure changes no license state."""
        with self._sync_lock:
            return self._sync_locked(force)

    def _sync_locked(self, force):
        result = {'refreshed': False, 'activated': False, 'error': None}
        save = False
        try:
            path, token, body = self._due()
            if not path and force and token:
                path, body = '/sdk/v1/entitlement/refresh', {}
            if not path:
                return result
            try:
                response = self._post(path, token, body)
            except urllib.error.HTTPError as error:
                # A refused activation (key in use elsewhere, released, trial already used) is reported.
                raise RuntimeError(f"{error.code} {error.read().decode('utf-8', 'replace')[:200]}") from None
            with self._lock:
                save = self._native.import_token(response['token'], self._now()) == 1
                result['activated' if path.endswith('activate') else 'refreshed'] = save
                if REASON[self._native.status(self._now())['reason']] == 'revoked':
                    # The server reports this key released: the device lets go of it.
                    stored = self._path(self._native.status(self._now()))
                    if stored:
                        stored.unlink(missing_ok=True)
                    save = False
            self.last_sync_error = None
            self._next_sync = time.monotonic() + 3600
        except Exception as error:
            # A communication failure is not an expiry, a release or a revocation.
            result['error'] = self.last_sync_error = 'network: ' + str(error)
        try:
            with self._lock:
                if save:
                    self._save()
                self._notify()
        except Exception:
            pass  # Closed while a background sync was finishing.
        return result

    def sync(self, wait=False, force=False):
        """Activate or renew online when due (force: refresh now). Background by default; never raises."""
        if not (self._online and self._endpoint):
            return None
        if wait:
            thread = self._sync_thread
            if thread and thread.is_alive():
                thread.join()
            return self._sync(force)
        with self._lock:
            if (self._sync_thread and self._sync_thread.is_alive()) or (not force and time.monotonic() < self._next_sync):
                return None
            self._next_sync = time.monotonic() + 60  # until this attempt succeeds
            self._sync_thread = threading.Thread(target=self._sync, args=(force,), name='fisica-entitlement-sync', daemon=True)
            self._sync_thread.start()
        return None

    def close(self):
        with self._lock:
            self._native.close()
