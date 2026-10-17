import ctypes as ct
import struct
import pytest
from fisica_sdk import NativeCore
from tools.audit_artifacts import validate_windows_dll
from tools import native_build
import runpy
from pathlib import Path
pe_dll=runpy.run_path(str(Path(__file__).with_name('test_windows_build.py')))['pe_dll']

@pytest.mark.parametrize('target',['win_arm64','win_amd64','macosx_11_0_universal2'])
def test_explicit_desktop_targets(target,monkeypatch):
    monkeypatch.setenv('FISICA_BUILD_PLATFORM',target)
    assert native_build.platform_tag()==target


def test_arm64_pe_is_not_mislabeled_as_x64():
    validate_windows_dll(pe_dll(machine=0xaa64),0xaa64)
    with pytest.raises(ValueError): validate_windows_dll(pe_dll(),0xaa64)
    with pytest.raises(ValueError): validate_windows_dll(pe_dll(machine=0xaa64))

@pytest.mark.parametrize('name',['python','flutter','swift','kotlin','dotnet'])
def test_shared_native_wrapper_policy(name):
    core=NativeCore()
    lib,version=core._lib,core.policy['wrappers'][name]['version']
    fn=lib.fisica_core_check_wrapper;fn.argtypes=[ct.c_char_p,ct.c_char_p,ct.c_char_p,ct.POINTER(ct.c_uint32)];fn.restype=ct.c_int32
    warnings=ct.c_uint32()
    assert fn(name.encode(),core.sdk_version.encode(),version.encode(),ct.byref(warnings))==0 and warnings.value==0
    # ABI 2 cores accept no SDK before 3.0.0: the license model changed.
    for sdk in [b'2.4.0',b'0.9.0',b'4.0.0',b'3.0.0-rc1',b'01.2.3',b'1.2',b'42949672960.0.0']:
        assert fn(name.encode(),sdk,version.encode(),ct.byref(warnings))==12


def test_ble_handler_has_native_input_queue():
    from fisica_sdk.bluetooth_handler import BluetoothHandler
    handler=BluetoothHandler('unused')
    assert handler.raw_queue.empty()


def test_generated_language_codes_match_the_public_header():
    """One definition for every language: regenerate with tools/sync_binding_metadata.py."""
    from tools.sync_binding_metadata import generated, header_enums, podspecs
    stale = [path.name for path, text in {**generated(), **podspecs()}.items() if not path.is_file() or path.read_text() != text]
    assert not stale, 'Run python tools/sync_binding_metadata.py: ' + ', '.join(stale)
    enums = header_enums()
    assert dict(enums['FisicaSessionAction'])['CONNECTED'] == 1 and dict(enums['FisicaContractKind'])['SPECIAL'] == 2
    from fisica_sdk import FisicaErrorCode, codes
    assert all(FisicaErrorCode[member.name] == member.value for member in codes.FisicaStatus)


def test_python_transport_uses_the_generated_ble_profiles():
    """No UUID or device name is written into the Python transport; both come from codes.py."""
    import re
    ROOT = Path(__file__).resolve().parents[1]
    for name, generated in (('bluetooth_handler.py', 'codes.BLE_PROFILES'), ('port_scanner.py', 'codes.BLE_NAME_CONTAINS')):
        source = (ROOT / 'fisica_sdk' / name).read_text(encoding='utf-8')
        assert generated in source and not re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-', source), name
