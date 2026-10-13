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

@pytest.mark.parametrize('name,version',[('python','2.3.0'),('flutter','0.4.0'),('swift','0.1.0'),('kotlin','0.1.0'),('dotnet','0.1.0')])
def test_shared_native_wrapper_policy(name,version):
    lib=NativeCore()._lib
    fn=lib.fisica_core_check_wrapper;fn.argtypes=[ct.c_char_p,ct.c_char_p,ct.c_char_p,ct.POINTER(ct.c_uint32)];fn.restype=ct.c_int32
    warnings=ct.c_uint32()
    assert fn(name.encode(),b'2.3.0',version.encode(),ct.byref(warnings))==0 and warnings.value==0
    assert fn(name.encode(),b'1.5.0',version.encode(),ct.byref(warnings))==0 and warnings.value==1
    for sdk in [b'0.9.0',b'3.0.0',b'2.3.0-rc1',b'01.2.3',b'1.2',b'42949672960.0.0']:
        assert fn(name.encode(),sdk,version.encode(),ct.byref(warnings))==12


def test_ble_handler_has_native_input_queue():
    from fisica_sdk.bluetooth_handler import BluetoothHandler
    handler=BluetoothHandler('unused')
    assert handler.raw_queue.empty() and not handler.session_input
