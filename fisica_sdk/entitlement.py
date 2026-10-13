"""Runtime entitlement for the Python wrapper: storage and network only.

Signature checks and every status decision are made by the native core. Nothing in
this module enables or disables SDK features; failures here are reported as status
and warnings and never interrupt connection, measurement or analysis.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import urllib.request
import warnings

from . import codes
from .errors import FisicaEntitlementWarning
from .native import NativeEntitlement

AUTO = object()
# Names follow the shared C header (fisica_sdk/codes.py is generated from it).
_names = lambda enum: {member.value: member.name.lower() for member in enum}
VERIFICATION = _names(codes.FisicaVerification)
CONTRACT_STATE = _names(codes.FisicaContract)
CONTRACT_KIND = {**_names(codes.FisicaContractKind), 0: None}
IMPORT_OUTCOME = {**_names(codes.FisicaEntitlementOutcome), 0: None}
KEY_ENVIRONMENT = {**_names(codes.FisicaKeyEnvironment), 0: None}
_MESSAGES = {
    'unverified': 'No verified company/contract information. Register and import the entitlement file for commercial use.',
    'refresh_due': 'Entitlement information was last confirmed more than 30 days ago; connect to refresh it or import a new file. The base SDK license has not expired.',
    'contract_ended': 'The separate contract recorded in the entitlement information has ended. The base SDK license is unaffected.',
    'contract_revoked': 'The server reported the separate contract as revoked. The base SDK license is unaffected.',
    'contract_not_started': 'The contract recorded in the entitlement information has not started yet.',
    'import_rejected': 'The last entitlement token or file was rejected (format, unknown key or signature).',
    'test_key': 'Entitlement information is signed with a TEST key; this is not production verification.',
}
WARNINGS = {member.value: (member.name.lower(), _MESSAGES[member.name.lower()]) for member in codes.FisicaEntitlementWarning}
UNVERIFIED_STATUS = {
    'base_license': 'granted', 'verification': 'none', 'contract_state': 'unverified', 'contract_kind': None,
    'company': None, 'contract': None, 'grants': [], 'verified_at': None, 'refresh_by': None,
    'contract_start': None, 'contract_end': None, 'revoked_at': None, 'last_import': None,
    'key_environment': None, 'warnings': ['unverified'], 'last_sync_error': None,
}


def default_storage_directory():
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'Fisica' / 'sdk'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'Fisica' / 'sdk'
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state') / 'fisica-sdk'


class Entitlement:
    """One SDK instance's verified company/contract context.

    token/file: server-signed entitlement information (file works on a PC that has
    never been online). endpoint: server base URL; without it no network is used.
    online=False keeps a configured endpoint unused. storage: state file path,
    None for memory only, or by default a per-company/contract file in the user's
    application-data directory. Contexts are never shared between instances.
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
        self._warned = 0
        self._sync_thread = None
        self._next_sync = 0.0
        self.last_sync_error = None
        if file is not None:
            token = Path(file).read_text(encoding='ascii', errors='replace')
        if token is not None:
            self.import_token(token)
        else:
            self._notify()

    def _now(self):
        return int(self._clock())

    def _path(self, status):
        if self._storage is None or not status['company']:
            return None
        if self._storage is not AUTO:
            return Path(self._storage)
        # Keyed by company + contract, the only attribution unit; not an installation or device id.
        name = hashlib.sha256((status['company'] + '\n' + status['contract']).encode()).hexdigest()[:16]
        return default_storage_directory() / ('entitlement-' + name + '.state')

    def _save(self):
        try:
            path = self._path(self._native.status(self._now()))
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(path.name + '.tmp')
                temporary.write_bytes(self._native.export_state())
                os.replace(temporary, path)
        except OSError as error:
            self.last_sync_error = 'storage: ' + str(error)

    def import_token(self, token):
        """Import a token or file content. Returns the outcome name; never raises on a bad token."""
        with self._lock:
            now = self._now()
            outcome = self._native.import_token(token, now)
            if outcome == 1:
                try:
                    path = self._path(self._native.status(now))
                    if path and path.is_file():
                        # Stored usage and a possibly newer confirmation win over an older file.
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
        bits = raw['warning_bits']
        time_or_none = lambda value: value or None
        return {
            'base_license': 'granted',
            'verification': VERIFICATION[raw['verification']],
            'contract_state': CONTRACT_STATE[raw['contract_state']],
            'contract_kind': CONTRACT_KIND[raw['contract_kind']],
            'company': raw['company'] or None, 'contract': raw['contract'] or None,
            'grants': [grant for grant in raw['grants'].split(',') if grant],
            'verified_at': time_or_none(raw['verified_at']), 'refresh_by': time_or_none(raw['refresh_by']),
            'contract_start': time_or_none(raw['contract_start']), 'contract_end': time_or_none(raw['contract_end']),
            'revoked_at': time_or_none(raw['revoked_at']),
            'last_import': IMPORT_OUTCOME[raw['last_import']],
            'key_environment': KEY_ENVIRONMENT[raw['key_environment']],
            'warnings': [name for bit, (name, _) in WARNINGS.items() if bits & bit],
            'warning_bits': bits,
            'last_sync_error': self.last_sync_error,
        }

    def _notify(self):
        status = self.status()
        new = status['warning_bits'] & ~self._warned
        self._warned = status['warning_bits']
        for bit, (_, message) in WARNINGS.items():
            if new & bit:
                warnings.warn('Fisica SDK entitlement: ' + message + ' Local SDK features keep working.',
                              FisicaEntitlementWarning, stacklevel=2)
        if self._on_status:
            try:
                self._on_status(status)
            except Exception:
                pass

    def mark_used(self):
        """Record real feature use. Safe to call from measurement paths: never blocks or raises."""
        try:
            with self._lock:
                self._native.mark_used(self._now())
                self._save()
                self._notify()
            self.sync()
        except Exception as error:
            self.last_sync_error = 'usage: ' + str(error)

    def _post(self, path, token, body):
        request = urllib.request.Request(self._endpoint + path, data=json.dumps(body).encode() if isinstance(body, dict) else body,
                                         headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            data = response.read()
            return json.loads(data) if data else None

    def _sync(self):
        """Refresh, then report pending usage. A failure changes no entitlement state."""
        result = {'refreshed': False, 'reported': 0, 'error': None}
        try:
            with self._lock:
                token = self._native.token()
            if not token:
                return result  # Nothing verified: nothing to refresh and nothing attributable to report.
            response = self._post('/sdk/v1/entitlement/refresh', token, {})
            with self._lock:
                result['refreshed'] = self._native.import_token(response['token'], self._now()) == 1
            while True:
                with self._lock:
                    pending = self._native.report(os.urandom(16))
                if pending is None:
                    break
                self._post('/sdk/v1/usage', pending[0], pending[1])
                with self._lock:
                    self._native.report_done()  # Local record is cleared only after the server confirmed.
                result['reported'] += 1
            self.last_sync_error = None
        except Exception as error:
            # A communication failure is not an expiry or a revocation.
            result['error'] = self.last_sync_error = 'network: ' + str(error)
        try:
            with self._lock:
                self._save()
                self._notify()
        except Exception:
            pass  # Closed while a background sync was finishing.
        return result

    def sync(self, wait=False, force=False):
        """Online confirmation and usage report. Background by default; never raises."""
        if not (self._online and self._endpoint):
            return None
        if wait:
            thread = self._sync_thread
            if thread and thread.is_alive():
                thread.join()
            return self._sync()
        with self._lock:
            if (self._sync_thread and self._sync_thread.is_alive()) or (not force and time.monotonic() < self._next_sync):
                return None
            self._next_sync = time.monotonic() + 3600
            self._sync_thread = threading.Thread(target=self._sync, name='fisica-entitlement-sync', daemon=True)
            self._sync_thread.start()
        return None

    def close(self):
        with self._lock:
            self._native.close()
