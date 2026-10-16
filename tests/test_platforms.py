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
    assert handler.raw_queue.empty() and not handler.session_input


def test_generated_language_codes_match_the_public_header():
    """One definition for every language: regenerate with tools/sync_binding_metadata.py."""
    from tools.sync_binding_metadata import generated, header_enums, podspecs
    stale = [path.name for path, text in {**generated(), **podspecs()}.items() if not path.is_file() or path.read_text() != text]
    assert not stale, 'Run python tools/sync_binding_metadata.py: ' + ', '.join(stale)
    enums = header_enums()
    assert dict(enums['FisicaSessionAction'])['CONNECTED'] == 1 and dict(enums['FisicaContractKind'])['SPECIAL'] == 2
    from fisica_sdk import FisicaErrorCode, codes
    assert all(FisicaErrorCode[member.name] == member.value for member in codes.FisicaStatus)


def test_default_ble_profile_covers_the_python_transport_characteristics():
    import re
    from fisica_sdk import codes
    ROOT = Path(__file__).resolve().parents[1]
    source = (ROOT / 'fisica_sdk' / 'bluetooth_handler.py').read_text(encoding='utf-8')
    block = source[source.index('known_uuids = ['):source.index(']', source.index('known_uuids = ['))]
    known = set(re.findall(r'"([0-9a-f-]{36})"', block))
    generated = {value for profile in codes.BLE_PROFILES.values() for value in profile.values()}
    assert known and known == generated
    assert codes.BLE_NAME_CONTAINS.lower() in (ROOT / 'fisica_sdk' / 'port_scanner.py').read_text(encoding='utf-8').lower()
