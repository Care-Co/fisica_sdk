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
