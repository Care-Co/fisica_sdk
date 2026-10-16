"""Python data conversion and C ABI binding; no protocol/analysis implementation."""
import ctypes as ct
from functools import lru_cache
import json
from pathlib import Path
import sys
import time
import numpy as np
from .models import DecodedMeasurement
from .versions import CoreCompatibilityError, check_compatibility, local_release
from .errors import FisicaArgumentError, FisicaBufferError, FisicaLicenseError, FisicaRuntimeError

U8P = ct.POINTER(ct.c_uint8)
U32P = ct.POINTER(ct.c_uint32)
I32P = ct.POINTER(ct.c_int32)
DOUBLEP = ct.POINTER(ct.c_double)
U32 = ct.c_uint32
U64 = ct.c_uint64
EVENT_BYTES = 16384


def _byte_array(data):
    data = bytes(data)
    return (ct.c_uint8 * len(data)).from_buffer_copy(data)


def _grid(data):
    grid = np.ascontiguousarray(data, dtype=np.float64)
    if grid.ndim != 2 or not all(grid.shape):
        raise FisicaArgumentError(1, 'Expected a nonempty 2D grid')
    return grid


def _ptr(data):
    return data.ctypes.data_as(DOUBLEP)


def _license(license, now_s):
    """Handle and wall-clock seconds for the calls that need full mode; no license is basic mode."""
    if license is None:
        return None, 0
    return license._handle, license._time(now_s)


class NativeCore:
    """Verify ABI/versions before calling a bundled or explicitly trusted binary.

    No source fallback, runtime compilation or library download is performed.
    """
    def __init__(self, library_path=None):
        release = local_release()
        self.wrapper = release['wrappers']['python']
        self.sdk_version = release['sdk_version']
        if library_path is None:
            filename = 'fisica_core.dll' if sys.platform == 'win32' else (
                'libfisica_core.dylib' if sys.platform == 'darwin' else 'libfisica_core.so'
            )
            library_path = Path(__file__).parent / '_native' / filename
        self.library_path = Path(library_path).resolve()
        if not self.library_path.is_file():
            raise CoreCompatibilityError(
                'Native core is missing. Install a binary wheel for your platform; '
                'source developers must run python tools/native_build.py first.'
            )
        try:
            self._lib = ct.CDLL(str(self.library_path))
            for name in ('version', 'policy_json'):
                function = getattr(self._lib, 'fisica_core_' + name)
                function.argtypes = []
                function.restype = ct.c_char_p
            self._lib.fisica_core_abi_version.argtypes = []
            self._lib.fisica_core_abi_version.restype = U32
            self.core_version = self._lib.fisica_core_version().decode('utf-8')
            self.abi_version = self._lib.fisica_core_abi_version()
            self.policy = json.loads(self._lib.fisica_core_policy_json().decode('utf-8'))
            check_compatibility(self.policy, self.wrapper, self.sdk_version, self.core_version, self.abi_version)
            self._bind()
        except (OSError, AttributeError, UnicodeError, ValueError, KeyError) as error:
            raise CoreCompatibilityError(f'Cannot load a compatible native core: {error}') from error

    def _bind(self):
        signatures = {
            'decode_packet': [ct.c_void_p,U64,U8P,U32,DOUBLEP,U32,DOUBLEP,U32,DOUBLEP,U32],
            'preprocess': [ct.c_void_p,U64,DOUBLEP,U32,U32,DOUBLEP,U32],
            'display_grid': [ct.c_void_p,U64,DOUBLEP,U32,DOUBLEP,U32],
            'next_packet': [U8P,U32,U32P,U32P,U8P,U32,U32P],
            'decode_response': [U8P,U32,I32P,U32],
            'response_payload': [U8P,U32,U8P,U32,U32P],
            'analyze': [DOUBLEP,DOUBLEP,U32,U32,U32,DOUBLEP,U32],
            'analyze_packets': [U8P,U32,DOUBLEP,U32],
            'dimensions': [DOUBLEP,U32,ct.c_double,ct.c_double,DOUBLEP,U32],
            'cop': [DOUBLEP,U32,U32,ct.c_double,ct.c_double,DOUBLEP,U32],
            'command': [ct.c_char_p,ct.c_double,U8P,U32,U8P,U32,U32P],
        }
        for name, arguments in signatures.items():
            function = getattr(self._lib, 'fisica_core_' + name)
            function.argtypes = arguments
            function.restype = ct.c_int32
        session_signatures = {
            'create': [ct.POINTER(ct.c_void_p)],
            'action': [ct.c_void_p,U32,ct.c_int64,ct.c_uint64],
            'command': [ct.c_void_p,ct.c_char_p,ct.c_double,U8P,U32,ct.c_uint64],
            'feed': [ct.c_void_p,U8P,U32,ct.c_uint64],
            'tick': [ct.c_void_p,ct.c_uint64],
            'poll': [ct.c_void_p,U32P,U32,U8P,U32],
            'license': [ct.c_void_p,ct.c_void_p,ct.c_uint64,ct.c_uint64],
            'analyze': [ct.c_void_p,DOUBLEP,U32],
        }
        for name, arguments in session_signatures.items():
            function = getattr(self._lib, 'fisica_core_session_' + name)
            function.argtypes, function.restype = arguments, ct.c_int32
        self._lib.fisica_core_session_destroy.argtypes = [ct.c_void_p]
        self._lib.fisica_core_session_destroy.restype = None
        entitlement_signatures = {
            'create': [ct.POINTER(ct.c_void_p)],
            'import': [ct.c_void_p,U8P,U32,U64,U32P],
            'status': [ct.c_void_p,U64,ct.POINTER(U64),U32],
            'text': [ct.c_void_p,U32,U8P,U32,U32P],
            'export': [ct.c_void_p,U8P,U32,U32P],
            'restore': [ct.c_void_p,U8P,U32],
            'release': [ct.c_void_p,U8P,U32,U32P],
        }
        for name, arguments in entitlement_signatures.items():
            function = getattr(self._lib, 'fisica_core_entitlement_' + name)
            function.argtypes, function.restype = arguments, ct.c_int32
        self._lib.fisica_core_entitlement_destroy.argtypes = [ct.c_void_p]
        self._lib.fisica_core_entitlement_destroy.restype = None
        self._lib.fisica_core_error_message.argtypes = [ct.c_int32]
        self._lib.fisica_core_error_message.restype = ct.c_char_p

    def _check(self, status):
        if status == 0:
            return
        message = self._lib.fisica_core_error_message(status).decode('utf-8')
        if status in (1,4):
            raise FisicaArgumentError(status, message)
        if status == 2:
            raise FisicaBufferError(status, message)
        if status == 13:
            raise FisicaLicenseError(status, message)
        raise FisicaRuntimeError(status, message)

    def version_info(self):
        return {
            'sdk_version': self.sdk_version, 'wrapper_name': self.wrapper['name'],
            'wrapper_version': self.wrapper['version'], 'core_version': self.core_version,
            'abi_major': self.abi_version >> 16, 'abi_minor': self.abi_version & 65535,
            'result_schema_version': self.policy['result_schema_version'],
            'license': self.policy.get('license'),
            'entitlement': dict(self.policy.get('entitlement', {})),
        }

    def decode_packet(self, packet, license=None, now_s=None):
        """Raw and display grids of one packet. Needs full mode with raw data (a NativeEntitlement)."""
        data = _byte_array(packet)
        raw, display, metrics = np.empty(638), np.empty(841), np.empty(2)
        self._check(self._lib.fisica_core_decode_packet(
            *_license(license, now_s),data,len(data),_ptr(raw),raw.size,_ptr(display),display.size,_ptr(metrics),metrics.size,
        ))
        return DecodedMeasurement(
            raw_grid=raw.reshape(29,22).tolist(), sensor_matrix=display.reshape(29,29).tolist(),
            weight=float(metrics[0]), battery=float(metrics[1]), raw_data=list(bytes(packet)),
            core_version=self.core_version, schema_version=self.policy['result_schema_version'],
        )

    def preprocess(self, grid, license=None, now_s=None):
        data = _grid(grid)
        output = np.empty_like(data)
        self._check(self._lib.fisica_core_preprocess(*_license(license, now_s),_ptr(data),*data.shape,_ptr(output),output.size))
        return output

    def display_grid(self, grid, gap_cols=7, license=None, now_s=None):
        data = _grid(grid)
        if data.shape != (29,22) or not isinstance(gap_cols,int) or not 0 <= gap_cols <= 1024:
            raise FisicaArgumentError(1, 'Expected a 29x22 grid and gap_cols between 0 and 1024')
        output = np.empty((29,22+gap_cols))
        self._check(self._lib.fisica_core_display_grid(*_license(license, now_s),_ptr(data),gap_cols,_ptr(output),output.size))
        return output

    def next_packet(self, buffer):
        data = _byte_array(buffer)
        consumed, kind, length = U32(), U32(), U32()
        packet = (ct.c_uint8 * 4096)()
        status = self._lib.fisica_core_next_packet(data,len(data),ct.byref(consumed),ct.byref(kind),packet,len(packet),ct.byref(length))
        if status != 3:
            self._check(status)
        return consumed.value, kind.value, bytes(packet[:length.value])

    def decode_response(self, packet):
        data = _byte_array(packet)
        response = (ct.c_int32 * 6)()
        self._check(self._lib.fisica_core_decode_response(data,len(data),response,6))
        result = dict(zip(('header','type','ret','data_high','data_low','success'),response))
        result['success'] = bool(result['success'])
        payload, length = (ct.c_uint8 * 4096)(), U32()
        self._check(self._lib.fisica_core_response_payload(data,len(data),payload,len(payload),ct.byref(length)))
        result['payload'] = bytes(payload[:length.value])
        return result

    def analyze(self, frames):
        if not frames:
            return None
        return self.analyze_matrices([f.sensor_matrix for f in frames],[f.weight for f in frames])

    def analyze_matrices(self, matrices, weights):
        data = np.ascontiguousarray(matrices,dtype=np.float64)
        weights = np.ascontiguousarray(weights,dtype=np.float64)
        if data.ndim != 3 or not all(data.shape) or weights.ndim != 1 or len(weights) != len(data):
            raise FisicaArgumentError(1, 'Expected matching frame matrices and weights')
        output = np.empty(5)
        status = self._lib.fisica_core_analyze(_ptr(data),_ptr(weights),*data.shape,_ptr(output),output.size)
        if status == 3:
            return None
        self._check(status)
        return tuple(output.tolist())

    def analyze_packets(self, packets):
        """Analyze 645-byte measurement packets as received; same result as analyze(frames)."""
        packets = [bytes(packet) for packet in packets]
        if not packets or any(len(packet) != 645 for packet in packets):
            raise FisicaArgumentError(1, 'Expected 645-byte measurement packets')
        data = _byte_array(b''.join(packets))
        output = np.empty(5)
        status = self._lib.fisica_core_analyze_packets(data,len(packets),_ptr(output),output.size)
        if status == 3:
            return None
        self._check(status)
        return tuple(output.tolist())

    def dimensions(self, points, cell_width=1.0, cell_height=1.0):
        data = np.ascontiguousarray(points,dtype=np.float64)
        if data.size == 0:
            data = np.empty((0,2))
        if data.ndim != 2 or data.shape[1] != 2:
            raise FisicaArgumentError(1, 'Expected xy coordinate pairs')
        output = np.empty(12)
        self._check(self._lib.fisica_core_dimensions(_ptr(data),len(data),cell_width,cell_height,_ptr(output),output.size))
        return output

    def cop(self, grid, cell_width=1.0, cell_height=1.0):
        data = _grid(grid)
        output = np.empty(2)
        self._check(self._lib.fisica_core_cop(_ptr(data),*data.shape,cell_width,cell_height,_ptr(output),output.size))
        return tuple(output.tolist())

    def command(self, name, value=0, payload=b''):
        if not isinstance(name,str) or '\x00' in name:
            raise FisicaArgumentError(1, 'Expected a command name without NUL bytes')
        data = _byte_array(payload)
        output = (ct.c_uint8 * 4102)()
        length = U32()
        self._check(self._lib.fisica_core_command(name.encode('utf-8'),value,data,len(data),output,len(output),ct.byref(length)))
        return bytes(output[:length.value])


@lru_cache(maxsize=1)
def get_core():
    return NativeCore()


class NativeSession:
    """Owned C ABI session. Serialize calls; transport and monotonic time are host inputs.

    action codes: 1 connected (value 1 BLE / 2 USB), 2 start (duration ms / -1),
    3 stop, 4 disconnect, 5 lost (value 1 send failure / 0 link loss).
    Drain events after each call. close() only frees memory, it never sends bytes.
    """
    def __init__(self, core=None):
        self.core = core or get_core()
        self._handle = ct.c_void_p()
        self.core._check(self.core._lib.fisica_core_session_create(ct.byref(self._handle)))
        self.state = 0

    def close(self):
        if self._handle.value:
            self.core._lib.fisica_core_session_destroy(self._handle)
            self._handle.value = None

    def __del__(self):
        if hasattr(self, '_handle'):
            self.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _time(self, now_ms):
        if not self._handle.value:
            raise FisicaRuntimeError(6, 'Session is closed')
        if now_ms is None:
            now_ms = time.monotonic_ns() // 1000000
        if not isinstance(now_ms, int) or not 0 <= now_ms <= (1 << 63)-1:
            raise FisicaArgumentError(1, 'Expected nonnegative monotonic milliseconds')
        return now_ms

    def action(self, action, value=0, now_ms=None):
        if not isinstance(action, int) or not 1 <= action <= 5 or not isinstance(value, int) or not -(1 << 63) <= value < (1 << 63):
            raise FisicaArgumentError(1, 'Invalid session action or value')
        self.core._check(self.core._lib.fisica_core_session_action(self._handle,action,value,self._time(now_ms)))

    def command(self, name, value=0, payload=b'', now_ms=None):
        if not isinstance(name, str) or '\x00' in name:
            raise FisicaArgumentError(1, 'Invalid command name')
        data = _byte_array(payload)
        self.core._check(self.core._lib.fisica_core_session_command(
            self._handle,name.encode('utf-8'),value,data,len(data),self._time(now_ms)))

    def feed(self, data, now_ms=None):
        data = _byte_array(data)
        self.core._check(self.core._lib.fisica_core_session_feed(self._handle,data,len(data),self._time(now_ms)))

    def tick(self, now_ms=None):
        self.core._check(self.core._lib.fisica_core_session_tick(self._handle,self._time(now_ms)))

    def poll(self):
        """Next event, or None. A MEASUREMENT event also carries weight, sensor_matrix (29x29) and,
        only when the license opens raw data, raw_grid (29x22) and packet (645 bytes)."""
        self._time(None)
        meta, payload = (U32 * 8)(), (ct.c_uint8 * EVENT_BYTES)()
        status = self.core._lib.fisica_core_session_poll(self._handle,meta,8,payload,len(payload))
        if status == 3:
            return None
        self.core._check(status)
        event = dict(zip(('kind','state','status','device_error','operation','header','type','length'),meta))
        event['payload'] = bytes(payload[:event['length']])
        self.state = event['state']
        if event['kind'] == 4:
            event.update(measurement_payload(event['payload']))
        return event

    def license(self, entitlement, now_s=None, now_ms=None):
        """Copy the license decision of a NativeEntitlement (None: basic mode) into this session."""
        if entitlement is None:
            handle, seconds = None, int(time.time()) if now_s is None else now_s
        else:
            handle, seconds = _license(entitlement, now_s)
        self.core._check(self.core._lib.fisica_core_session_license(self._handle,handle,seconds,self._time(now_ms)))

    def analyze(self):
        """Weight and foot size from every packet since the last start, in any mode; None if unstable."""
        self._time(None)
        output = np.empty(5)
        status = self.core._lib.fisica_core_session_analyze(self._handle,_ptr(output),output.size)
        if status == 3:
            return None
        self.core._check(status)
        return tuple(output.tolist())

    def events(self):
        while True:
            event = self.poll()
            if event is None:
                break
            yield event


def measurement_payload(payload):
    """Fields of an ABI 2 MEASUREMENT payload (see the C header)."""
    values = np.frombuffer(payload, dtype='<f8', count=842)
    result = {'weight': float(values[0]), 'sensor_matrix': values[1:].reshape(29,29).copy(), 'raw_grid': None, 'packet': None}
    if len(payload) >= 8 * (842 + 638) + 645:
        result['raw_grid'] = np.frombuffer(payload, dtype='<f8', count=638, offset=8 * 842).reshape(29,22).copy()
        result['packet'] = payload[8 * (842 + 638):8 * (842 + 638) + 645]
    return result


class NativeEntitlement:
    """Owned C ABI license context. Serialize calls; wall-clock seconds are a host input.

    The core decides full or basic mode from the signed key and the app ID or PC hash it
    reads itself. No record of SDK use is kept. close() only frees memory.
    """
    STATUS_FIELDS = ('mode','reason','features','contract_state','contract_kind','track','bind_type','target_type',
                     'verified_at','refresh_by','key_until','full_until','contract_start','contract_end','revoked_at',
                     'last_import','key_environment','warning_bits','latest_time')
    TEXT_FIELDS = {'company': 1, 'contract': 2, 'grants': 3, 'signing_key': 4, 'key': 6, 'bind': 7,
                   'target': 8, 'features_text': 9}

    def __init__(self, core=None):
        self.core = core or get_core()
        self._handle = ct.c_void_p()
        self.core._check(self.core._lib.fisica_core_entitlement_create(ct.byref(self._handle)))

    def close(self):
        if self._handle.value:
            self.core._lib.fisica_core_entitlement_destroy(self._handle)
            self._handle.value = None

    def __del__(self):
        if hasattr(self, '_handle'):
            self.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _time(self, now_s):
        if not self._handle.value:
            raise FisicaRuntimeError(6, 'Entitlement context is closed')
        if now_s is None:
            now_s = int(time.time())
        if not isinstance(now_s, int) or not 0 <= now_s <= 1 << 53:
            raise FisicaArgumentError(1, 'Expected Unix seconds')
        return now_s

    def import_token(self, token, now_s=None):
        """Returns the import outcome code; the previous verified state survives a rejection."""
        data = _byte_array(token.encode('utf-8') if isinstance(token, str) else token)
        outcome = U32()
        self.core._check(self.core._lib.fisica_core_entitlement_import(self._handle,data,len(data),self._time(now_s),ct.byref(outcome)))
        return outcome.value

    def _text(self, field, capacity=4096):
        output, length = (ct.c_uint8 * capacity)(), U32()
        self.core._check(self.core._lib.fisica_core_entitlement_text(self._handle,field,output,capacity,ct.byref(length)))
        return bytes(output[:length.value]).decode('utf-8')

    def status(self, now_s=None):
        values = (ct.c_uint64 * len(self.STATUS_FIELDS))()
        self.core._check(self.core._lib.fisica_core_entitlement_status(self._handle,self._time(now_s),values,len(values)))
        status = dict(zip(self.STATUS_FIELDS, values))
        status.update({name: self._text(field) for name, field in self.TEXT_FIELDS.items()})
        return status

    def token(self):
        return self._text(5)

    def state_name(self):
        """File name for the stored state of this seat (company, contract and this app or PC); '' without a key."""
        return self._text(12)

    def activation_request(self):
        """JSON body for POST /sdk/v1/entitlement/activate, or None when the core cannot read a target."""
        try:
            return self._text(10)
        except FisicaRuntimeError as error:
            if error.code == 6:
                return None
            raise

    def activation_code(self):
        """FAC1 code to activate the loaded key on another device, or None without an unactivated key."""
        try:
            return self._text(11)
        except FisicaRuntimeError as error:
            if error.code == 6:
                return None
            raise

    def release(self):
        """Forget the key activated here and return its FRC1 release code for the developer portal."""
        self._time(0)
        output, length = (ct.c_uint8 * 1024)(), U32()
        self.core._check(self.core._lib.fisica_core_entitlement_release(self._handle,output,len(output),ct.byref(length)))
        return bytes(output[:length.value]).decode('ascii')

    def export_state(self):
        self._time(0)
        output, length = (ct.c_uint8 * 262144)(), U32()
        self.core._check(self.core._lib.fisica_core_entitlement_export(self._handle,output,len(output),ct.byref(length)))
        return bytes(output[:length.value])

    def restore_state(self, state):
        self._time(0)
        data = _byte_array(state)
        self.core._check(self.core._lib.fisica_core_entitlement_restore(self._handle,data,len(data)))
