"""Python data conversion and C ABI binding; no protocol/analysis implementation."""
import ctypes as ct
from functools import lru_cache
import json
from pathlib import Path
import sys
import numpy as np
from .models import DecodedMeasurement
from .versions import CoreCompatibilityError, check_compatibility, local_release
from .errors import FisicaArgumentError, FisicaBufferError, FisicaRuntimeError

U8P = ct.POINTER(ct.c_uint8)
U32P = ct.POINTER(ct.c_uint32)
I32P = ct.POINTER(ct.c_int32)
DOUBLEP = ct.POINTER(ct.c_double)
U32 = ct.c_uint32


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
            'decode_packet': [U8P,U32,DOUBLEP,U32,DOUBLEP,U32,DOUBLEP,U32],
            'preprocess': [DOUBLEP,U32,U32,DOUBLEP,U32],
            'display_grid': [DOUBLEP,U32,DOUBLEP,U32],
            'next_packet': [U8P,U32,U32P,U32P,U8P,U32,U32P],
            'decode_response': [U8P,U32,I32P,U32],
            'response_payload': [U8P,U32,U8P,U32,U32P],
            'analyze': [DOUBLEP,DOUBLEP,U32,U32,U32,DOUBLEP,U32],
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
        }
        for name, arguments in session_signatures.items():
            function = getattr(self._lib, 'fisica_core_session_' + name)
            function.argtypes, function.restype = arguments, ct.c_int32
        self._lib.fisica_core_session_destroy.argtypes = [ct.c_void_p]
        self._lib.fisica_core_session_destroy.restype = None
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
        raise FisicaRuntimeError(status, message)

    def version_info(self):
        return {
            'sdk_version': self.sdk_version, 'wrapper_name': self.wrapper['name'],
            'wrapper_version': self.wrapper['version'], 'core_version': self.core_version,
            'abi_major': self.abi_version >> 16, 'abi_minor': self.abi_version & 65535,
            'result_schema_version': self.policy['result_schema_version'],
            'license': self.policy.get('license'),
            'distribution': dict(self.policy.get('distribution', {})),
        }

    def decode_packet(self, packet):
        data = _byte_array(packet)
        raw, display, metrics = np.empty(638), np.empty(841), np.empty(2)
        self._check(self._lib.fisica_core_decode_packet(
            data,len(data),_ptr(raw),raw.size,_ptr(display),display.size,_ptr(metrics),metrics.size,
        ))
        return DecodedMeasurement(
            raw_grid=raw.reshape(29,22).tolist(), sensor_matrix=display.reshape(29,29).tolist(),
            weight=float(metrics[0]), battery=float(metrics[1]), raw_data=list(bytes(packet)),
            core_version=self.core_version, schema_version=self.policy['result_schema_version'],
        )

    def preprocess(self, grid):
        data = _grid(grid)
        output = np.empty_like(data)
        self._check(self._lib.fisica_core_preprocess(_ptr(data),*data.shape,_ptr(output),output.size))
        return output

    def display_grid(self, grid, gap_cols=7):
        data = _grid(grid)
        if data.shape != (29,22) or not isinstance(gap_cols,int) or not 0 <= gap_cols <= 1024:
            raise FisicaArgumentError(1, 'Expected a 29x22 grid and gap_cols between 0 and 1024')
        output = np.empty((29,22+gap_cols))
        self._check(self._lib.fisica_core_display_grid(_ptr(data),gap_cols,_ptr(output),output.size))
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
            import time
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
        self._time(None)
        meta, payload = (U32 * 8)(), (ct.c_uint8 * 4102)()
        status = self.core._lib.fisica_core_session_poll(self._handle,meta,8,payload,len(payload))
        if status == 3:
            return None
        self.core._check(status)
        event = dict(zip(('kind','state','status','device_error','operation','header','type','length'),meta))
        event['payload'] = bytes(payload[:event['length']])
        self.state = event['state']
        return event

    def events(self):
        while True:
            event = self.poll()
            if event is None:
                break
            yield event
