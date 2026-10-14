import zipfile
import pytest
from tools.audit_artifacts import audit_archive
from tools.native_build import prepare_release, release_metadata, require_release_keys


def make_archive(path,extras=None):
    files = {
        'fisica_sdk/_native/libfisica_core.so':b'binary',
        'fisica_sdk/native.py':b'# public binding',
        'fisica_sdk/_release.json':b'{}',
        'LICENSE':b'Fisica Scale SDK License\n',
        'fisica-2.1.0.dist-info/METADATA':b'License-Expression: LicenseRef-Fisica-Scale-SDK\n',
    }
    files.update(extras or {})
    with zipfile.ZipFile(path,'w') as archive:
        for name,data in files.items():
            archive.writestr(name,data)


@pytest.mark.parametrize('file,data',[
    ('native/src/core.cpp',b'private implementation'),
    ('hidden.cpp',b'private implementation'),
    ('core.pdb',b'debug data'),
    ('fisica_sdk/analyzer.py',b'STABILITY_THRESHOLD = 0.5'),
])
def test_reject_source_and_debug_artifacts(tmp_path,file,data):
    archive = tmp_path / 'test.whl'
    make_archive(archive,{file:data})
    with pytest.raises(ValueError):
        audit_archive(archive)


def test_accept_public_adapters_and_binary(tmp_path):
    archive = tmp_path / 'test.whl'
    make_archive(archive)
    assert audit_archive(archive) == 5


def test_wrapper_metadata_stays_synchronized():
    # Also validates pubspec and generated Dart version metadata at build time.
    release = release_metadata()
    from fisica_sdk import __version__
    assert release['wrappers']['python']['version'] == __version__


def test_archive_without_license_is_rejected(tmp_path):
    archive = tmp_path / 'test.whl'
    make_archive(archive, {'LICENSE': b'wrong license'})
    with pytest.raises(ValueError, match='License'):
        audit_archive(archive)


@pytest.mark.parametrize('name', ['hidden.hpp', 'core.rs', 'algorithm.h', '../core.dll'])
def test_private_headers_other_core_languages_and_unsafe_paths_are_rejected(tmp_path, name):
    archive = tmp_path / 'test.whl'
    make_archive(archive, {name: b'internal'})
    with pytest.raises(ValueError):
        audit_archive(archive)


def test_core_metadata_is_identical_for_every_company(monkeypatch, tmp_path):
    """No company, contract or delivery identifier may reach the compiled core."""
    baseline = prepare_release(tmp_path / 'a')
    monkeypatch.setenv('FISICA_PARTNER_ID', 'partner-test')
    monkeypatch.setenv('FISICA_DISTRIBUTION_ID', 'delivery-test')
    assert prepare_release(tmp_path / 'b') == baseline
    assert (tmp_path / 'a' / 'fisica_release.h').read_bytes() == (tmp_path / 'b' / 'fisica_release.h').read_bytes()
    assert 'distribution' not in baseline and b'partner' not in (tmp_path / 'a' / 'fisica_release.h').read_bytes()
    assert baseline['entitlement']['max_refresh_days'] == 30


def test_customer_packages_refuse_test_entitlement_keys():
    keys = release_metadata()['entitlement']['keys']
    if any(key['environment'] == 'test' for key in keys):
        with pytest.raises(SystemExit, match='test entitlement keys'):
            require_release_keys(validation=False)
        assert require_release_keys(validation=True) is True
    else:
        assert require_release_keys(validation=False) is False


def test_native_binary_is_byte_identical_whoever_it_is_built_for(monkeypatch, tmp_path):
    """Same platform, architecture and core version: one binary, no build timestamp or company data."""
    from tools.native_build import build_native
    monkeypatch.delenv('FISICA_PREBUILT_CORE', raising=False)
    first = build_native(tmp_path / 'a').read_bytes()
    monkeypatch.setenv('FISICA_PARTNER_ID', 'partner-test')
    monkeypatch.setenv('FISICA_DISTRIBUTION_ID', 'delivery-test')
    second = build_native(tmp_path / 'b').read_bytes()
    assert first == second
    assert b'partner-test' not in second and b'delivery-test' not in second


def _android_elf(alignment, wide=True, machine=183, relro_end=None):
    """Smallest ELF shared object header: one LOAD and optionally a GNU_RELRO program header."""
    import struct
    programs = [(1, 0, 0x1000, alignment)] + ([(0x6474e552, 0x3000, relro_end - 0x3000, 1)] if relro_end else [])
    header = bytearray(64 if wide else 52)
    header[:6] = b'\x7fELF' + bytes([2 if wide else 1, 1])
    struct.pack_into('<HH', header, 16, 3, machine)
    if wide:
        struct.pack_into('<Q', header, 0x20, 64)
        struct.pack_into('<HH', header, 0x36, 56, len(programs))
        table = b''.join(struct.pack('<IIQQQQQQ', kind, 4, vaddr, vaddr, vaddr, memsz, memsz, align) for kind, vaddr, memsz, align in programs)
    else:
        struct.pack_into('<I', header, 0x1c, 52)
        struct.pack_into('<HH', header, 0x2a, 32, len(programs))
        table = b''.join(struct.pack('<IIIIIIII', kind, vaddr, vaddr, vaddr, memsz, memsz, 4, align) for kind, vaddr, memsz, align in programs)
    return bytes(header) + table


def test_android_libraries_must_be_16kb_aligned():
    from tools.audit_artifacts import validate_android_library
    validate_android_library(_android_elf(0x4000), 'arm64-v8a')
    validate_android_library(_android_elf(0x4000, relro_end=0x8000), 'arm64-v8a')
    validate_android_library(_android_elf(0x4000, wide=False, machine=40, relro_end=0x8000), 'armeabi-v7a')
    for data, abi in ((_android_elf(0x1000), 'arm64-v8a'), (_android_elf(0x1000, machine=62), 'x86_64'),
                      (_android_elf(0x1000, wide=False, machine=40), 'armeabi-v7a')):
        with pytest.raises(ValueError, match='LOAD segments are not 16 KB'):
            validate_android_library(data, abi)
    # Official example: a RELRO ending on a 4 KB (not 16 KB) boundary faults on 16 KB page devices.
    for data, abi in ((_android_elf(0x4000, relro_end=0xe1000), 'arm64-v8a'),
                      (_android_elf(0x4000, machine=62, relro_end=0x5000), 'x86_64'),
                      (_android_elf(0x4000, wide=False, machine=40, relro_end=0x5000), 'armeabi-v7a')):
        with pytest.raises(ValueError, match='RELRO'):
            validate_android_library(data, abi)
    with pytest.raises(ValueError, match='program headers'):
        validate_android_library(_android_elf(0x4000)[:64], 'arm64-v8a')
    from pathlib import Path
    build = (Path(__file__).resolve().parents[1] / 'tools/build_platforms.py').read_text(encoding='utf-8')
    assert '-Wl,-z,max-page-size=16384' in build and '-Wl,-z,common-page-size=16384' in build
