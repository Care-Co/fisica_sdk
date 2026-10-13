import zipfile
import pytest
from tools.audit_artifacts import audit_archive
from tools.native_build import release_metadata


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


def test_partner_distribution_metadata_requires_both_identifiers(monkeypatch):
    monkeypatch.setenv('FISICA_PARTNER_ID', 'partner-test')
    monkeypatch.delenv('FISICA_DISTRIBUTION_ID', raising=False)
    with pytest.raises(ValueError, match='together'):
        release_metadata()
    monkeypatch.setenv('FISICA_DISTRIBUTION_ID', 'delivery-test')
    metadata = release_metadata()
    assert metadata['distribution'] == {'partner_id': 'partner-test', 'distribution_id': 'delivery-test'}
    monkeypatch.setenv('FISICA_PARTNER_ID', '../../private')
    with pytest.raises(ValueError, match='ASCII'):
        release_metadata()


@pytest.mark.parametrize('identifier', ['.', '..', 'CON', 'COM1.txt', 'trailing.'])
def test_distribution_identifier_is_a_safe_windows_directory_component(monkeypatch, identifier):
    monkeypatch.setenv('FISICA_PARTNER_ID', 'partner-test')
    monkeypatch.setenv('FISICA_DISTRIBUTION_ID', identifier)
    with pytest.raises(ValueError, match='ASCII'):
        release_metadata()
