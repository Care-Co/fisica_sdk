import json
from pathlib import Path
import zipfile
import pytest
from tools.audit_artifacts import audit_archive
from tools.partner_samples import PARTNER_SAMPLE_FILES

ROOT = Path(__file__).resolve().parents[1]


def sample_archive(path, extra=None):
    files = {name: (ROOT / 'examples/partner' / name).read_bytes() for name in PARTNER_SAMPLE_FILES}
    files.update({'INSTALL.md': (ROOT / 'doc/partner-quickstart.md').read_bytes(),
                  'LICENSE': (ROOT / 'LICENSE').read_bytes(),
                  'quickstart.json': json.dumps({'type': 'fisica-partner-samples', 'schema_version': 1}).encode()})
    files.update(extra or {})
    with zipfile.ZipFile(path, 'w') as archive:
        for name, contents in files.items(): archive.writestr(name, contents)


def test_public_c_samples_do_not_require_shipping_private_core(tmp_path):
    archive = tmp_path / 'samples.zip'
    sample_archive(archive)
    assert audit_archive(archive) == len(PARTNER_SAMPLE_FILES) + 3


@pytest.mark.parametrize('name', ['native/src/core.cpp', 'private.cpp', 'flutter/lib/core.pdb', 'tools/build.py'])
def test_sample_allowlist_rejects_private_files_even_with_sample_marker(tmp_path, name):
    archive = tmp_path / 'samples.zip'
    sample_archive(archive, {name: b'internal'})
    with pytest.raises(ValueError, match='allowlist'): audit_archive(archive)


def test_c_sources_are_rejected_without_the_explicit_sample_manifest(tmp_path):
    archive = tmp_path / 'unmarked.zip'
    with zipfile.ZipFile(archive, 'w') as stream:
        stream.writestr('pubspec.yaml', 'name: fisica_flutter')
        stream.writestr('lib/fisica_flutter.dart', '// public transport')
        stream.writestr('c/main.c', '// sample')
        stream.writestr('LICENSE', (ROOT / 'LICENSE').read_bytes())
    with pytest.raises(ValueError, match='Private source'): audit_archive(archive)


def test_synthetic_data_matches_all_sample_consumers():
    root = ROOT / 'examples/partner'
    script = json.loads((root / 'scale-mock.json').read_text())
    assert (root / 'scale-mock.json').read_bytes() == (root / 'flutter/assets/scale-mock.json').read_bytes()
    assert (root / 'sample-frame.hex').read_text().strip() == script['measurement_hex']
    from fisica_sdk import NativeCore
    packet = bytes.fromhex(script['measurement_hex'])
    core = NativeCore()
    assert core.decode_packet(packet).weight == script['expected_weight']
    assert script['frame_count'] == 12
