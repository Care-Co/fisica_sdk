import json
import os
from pathlib import Path
import re
import subprocess
import sys
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
    assert core.analyze_packets([packet] * script['frame_count'])[0] == script['expected_weight']  # basic mode
    assert script['frame_count'] == 12


def test_samples_use_the_current_wrapper_versions():
    """The samples name the packages of this release: AAR file name and C# package version."""
    version = {name: json.loads((ROOT / 'wrappers' / name / 'wrapper.json').read_text())['version'] for name in ('kotlin', 'dotnet')}
    samples = ROOT / 'examples/partner'
    for app in ('app-kotlin', 'app-java'):
        gradle = (samples / 'android' / app / 'build.gradle').read_text()
        assert re.findall(r'fisica-scale-([^"/]+)\.aar', gradle) == [version['kotlin']], app
    project = (samples / 'dotnet/Quickstart.csproj').read_text()
    assert re.findall(r'Include="CareCo\.Fisica\.Scale" Version="([^"]+)"', project) == [version['dotnet']]
    assert re.findall(r'<Version>([^<]+)</Version>', (ROOT / 'wrappers/dotnet/Fisica.Scale.csproj').read_text()) == [version['dotnet']]
    # Until the NuGet package exists the sample builds the wrapper source from the C# ZIP.
    assert 'Include="$(FisicaSdk)dotnet/Fisica.Scale.csproj"' in project


SAMPLES = ROOT / 'examples/partner'
CONSOLE_SAMPLES = ('python/main.py', 'dotnet/Program.cs', 'c/main.c')
APP_SAMPLES = ('swift/ScaleApp.swift', 'android/app-kotlin/src/main/kotlin/example/fisica/kotlin/MainActivity.kt',
               'android/app-java/src/main/java/example/fisica/java/MainActivity.java', 'flutter/lib/main.dart')
REPORT_LABELS = ('Weight: ', 'Left foot: ', 'Right foot: ', 'Frames: ', 'Battery: ')
NO_RESULT = 'No stable result; check the load on the Scale and the received frames'


@pytest.mark.parametrize('name', CONSOLE_SAMPLES + APP_SAMPLES)
def test_samples_share_one_flow_and_wording(name):
    """A partner sees the same steps and labels in every language (examples/partner/README.md)."""
    source = (SAMPLES / name).read_text()
    version = 'Fisica core ' if name == 'c/main.c' else 'Fisica SDK '
    for text in (version, 'License: ', 'full mode', 'basic mode', 'Measuring for 5 seconds', 'Left foot: length ',
                 'Right foot: length ', 'not read', NO_RESULT, 'Error: '):
        assert text in source, (name, text)
    position = 0
    for label in REPORT_LABELS:  # the report fields in this order
        position = source.find(label, position)
        assert position >= 0, (name, label)
    if name in APP_SAMPLES:
        for text in ('Scan for Scales', 'Demo without hardware', 'Connect', 'Measure (5 s)', 'Disconnect',
                     'Activation code: ', 'scale-mock.json', 'readBattery'):
            assert text in source, (name, text)
    else:
        assert 'Disconnected' in source, name


def test_c_sample_answers_with_the_replies_of_the_mock_script():
    script = json.loads((SAMPLES / 'scale-mock.json').read_text())
    replies = dict(re.findall(r'\{ "([A-Z_]+)", "([0-9a-f]+)" \}', (SAMPLES / 'c/main.c').read_text()))
    assert {'GET_FIRMWARE_VERSION', 'GET_BATTERY_LEVEL', 'SET_DEVICE_USB', 'START_CONTINUOUS_MEASUREMENT'} <= set(replies)
    assert all(script['replies'][name] == reply for name, reply in replies.items())


def test_python_sample_runs_the_common_flow_against_the_mock(tmp_path):
    # A fresh user profile: no stored SDK key, and nothing is written outside tmp_path.
    profile = {name: str(tmp_path) for name in ('HOME', 'USERPROFILE', 'LOCALAPPDATA', 'XDG_STATE_HOME')}
    result = subprocess.run([sys.executable, str(SAMPLES / 'python/main.py'), '--mock', str(SAMPLES / 'scale-mock.json')],
                            cwd=tmp_path, env={**os.environ, **profile, 'PYTHONPATH': str(ROOT)},
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert re.fullmatch(r'Fisica SDK \S+ \(core \S+\)', lines[0]), lines
    assert lines[1] == 'License: basic mode (no_key)'
    assert lines[2].startswith('No SDK key.')
    assert lines[3:] == ['Connected to mock (firmware 2.0.1)', 'Measuring for 5 seconds...',
                         'Weight: 70.5 kg', 'Left foot: length 30.2 cm, width 11.8 cm',
                         'Right foot: length 30.2 cm, width 11.7 cm', 'Frames: 1', 'Battery: 80%', 'Disconnected']
    assert 'Traceback' not in result.stderr
