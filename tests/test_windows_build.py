import json
import struct
import zipfile
import subprocess
import sys
import pytest
from tools.audit_artifacts import audit_archive, validate_windows_dll
from tools import native_build


def pe_dll(machine=0x8664,magic=0x20b,characteristics=0x2000):
    data=bytearray(512)
    data[:2]=b'MZ'
    struct.pack_into('<I',data,0x3c,128)
    data[128:132]=b'PE\x00\x00'
    struct.pack_into('<H',data,132,machine)
    struct.pack_into('<H',data,148,240)
    struct.pack_into('<H',data,150,characteristics)
    struct.pack_into('<H',data,152,magic)
    return bytes(data)


@pytest.mark.parametrize('data',[
    b'not a DLL',pe_dll(machine=0x14c),pe_dll(machine=0xaa64),
    pe_dll(magic=0x10b),pe_dll(characteristics=2),pe_dll()[:160],
])
def test_windows_binary_validation_rejects_wrong_format_or_architecture(data):
    with pytest.raises(ValueError):
        validate_windows_dll(data)


def windows_wheel(path,dll=None,extra=None,tag='py3-none-win_amd64'):
    files={
        'fisica_sdk/_native/fisica_core.dll': pe_dll() if dll is None else dll,
        'fisica_sdk/native.py':b'# public binding',
        'fisica_sdk/_release.json':b'{}',
        'LICENSE':b'Fisica Scale SDK License\n',
        'fisica-2.0.0.dist-info/METADATA':b'License-Expression: LicenseRef-Fisica-Scale-SDK\n',
        'fisica-2.0.0.dist-info/WHEEL':('Wheel-Version: 1.0\nTag: '+tag+'\n').encode(),
    }
    files.update(extra or {})
    with zipfile.ZipFile(path,'w') as archive:
        for name,data in files.items():
            archive.writestr(name,data)


def test_windows_wheel_architecture_and_metadata_match(tmp_path):
    wheel=tmp_path / 'fisica-2.0.0-py3-none-win_amd64.whl'
    windows_wheel(wheel)
    assert audit_archive(wheel)==6
    windows_wheel(wheel,dll=pe_dll(machine=0x14c))
    with pytest.raises(ValueError,match='x64'):
        audit_archive(wheel)
    windows_wheel(wheel,tag='py3-none-win32')
    with pytest.raises(ValueError,match='metadata'):
        audit_archive(wheel)


@pytest.mark.parametrize('name',['core.PDB','core.lib','core.exp','hidden.CPP','core.obj','core.ilk'])
def test_windows_compiler_sidecars_and_sources_do_not_ship(tmp_path,name):
    wheel=tmp_path / 'fisica-2.0.0-py3-none-win_amd64.whl'
    windows_wheel(wheel,extra={name:b'internal data'})
    with pytest.raises(ValueError):
        audit_archive(wheel)


def test_cross_target_packaging_copies_only_validated_dll_and_metadata(tmp_path,monkeypatch):
    monkeypatch.setenv('FISICA_BUILD_PLATFORM','win_amd64')
    dll=tmp_path / 'trusted.dll'
    dll.write_bytes(pe_dll())
    (tmp_path / 'trusted.pdb').write_bytes(b'private debug data')
    monkeypatch.setenv('FISICA_PREBUILT_CORE',str(dll))
    output=tmp_path / 'package'
    library=native_build.build_native(output)
    assert library.name=='fisica_core.dll' and library.read_bytes()==dll.read_bytes()
    assert sorted(path.name for path in output.iterdir())==['fisica_core.dll','release.json']
    assert json.loads((output / 'release.json').read_text())['core_version']=='1.3.0'


def test_unvalidated_dll_cannot_enter_package(tmp_path,monkeypatch):
    monkeypatch.setenv('FISICA_BUILD_PLATFORM','win_amd64')
    dll=tmp_path / 'bad.dll'
    dll.write_bytes(pe_dll(machine=0xaa64))
    monkeypatch.setenv('FISICA_PREBUILT_CORE',str(dll))
    with pytest.raises(ValueError):
        native_build.build_native(tmp_path / 'package')
    assert not (tmp_path / 'package' / 'fisica_core.dll').exists()


def test_no_implicit_cross_compiler_fallback(tmp_path,monkeypatch):
    monkeypatch.setattr(native_build.sys,'platform','darwin')
    monkeypatch.setenv('FISICA_BUILD_PLATFORM','win_amd64')
    monkeypatch.delenv('CXX',raising=False)
    monkeypatch.delenv('FISICA_PREBUILT_CORE',raising=False)
    with pytest.raises(RuntimeError,match='cross compiler'):
        native_build.build_native(tmp_path)


def test_sdk_initialization_does_not_require_device_discovery_runtimes():
    script='''
import builtins,sys
original=builtins.__import__
def without_bluetooth(name,*args,**kwargs):
    if name=='bleak' or name.startswith('bleak.'):
        raise AssertionError('Bluetooth backend imported during SDK initialization')
    if name=='serial.tools.list_ports' or name.startswith('serial.tools.list_ports_'):
        raise AssertionError('Serial discovery imported during SDK initialization')
    return original(name,*args,**kwargs)
builtins.__import__=without_bluetooth
from fisica_sdk import FisicaSDK
sdk=FisicaSDK()
assert sdk.version_info()['core_version']=='1.3.0'
assert 'bleak' not in sys.modules
'''
    result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
