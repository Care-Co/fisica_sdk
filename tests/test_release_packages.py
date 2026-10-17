"""Packaging tools: production keys by default, complete inputs, tracked files only, one recipe per package."""
import json
import os
import plistlib
import runpy
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tools import release_packages as packages
from tools.audit_artifacts import audit_archive
from tools.native_build import KEY_MODE, ROOT, entitlement_keys, prepare_release, release_metadata

pe_dll = runpy.run_path(str(Path(__file__).with_name('test_windows_build.py')))['pe_dll']
android_elf = runpy.run_path(str(Path(__file__).with_name('test_artifacts.py')))['_android_elf']
ELF = {'arm64-v8a': dict(machine=183), 'armeabi-v7a': dict(wide=False, machine=40), 'x86_64': dict(machine=62)}
TEST_KEY = next(bytes.fromhex(key['public_key']) for key in entitlement_keys(include_test=True) if key['environment'] == 'test')


def tool(*arguments, **options):
    environment = {key: value for key, value in os.environ.items() if key != KEY_MODE}
    return subprocess.run([sys.executable, *map(str, arguments)], cwd=ROOT, env=environment,
                          capture_output=True, text=True, **options)


def release_for(tmp_path, monkeypatch, validation):
    monkeypatch.setenv(KEY_MODE, 'validation' if validation else 'release')
    return prepare_release(tmp_path / ('generated-validation' if validation else 'generated'))


def zip_of(path, files):
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return path


def core_zip(path, release, library=b'\xca\xfe\xba\xbe core'):
    files = {'libfisica_core.dylib': library, 'fisica_core.h': packages.HEADER.read_bytes(), 'LICENSE': packages.LICENSE.read_bytes()}
    if release is not None:
        files['release.json'] = json.dumps(release)
    return zip_of(path, files)


def test_customer_archives_reject_cores_that_trust_the_test_key(tmp_path, monkeypatch):
    release = release_for(tmp_path, monkeypatch, validation=False)
    qa = release_for(tmp_path, monkeypatch, validation=True)
    assert audit_archive(core_zip(tmp_path / 'release.zip', release)) == 4
    archive = core_zip(tmp_path / 'qa.zip', qa)
    with pytest.raises(ValueError, match='trusts test entitlement keys'):
        audit_archive(archive)
    assert audit_archive(archive, validation=True) == 4
    # The compiled key table counts, whatever release.json says.
    archive = core_zip(tmp_path / 'hidden.zip', release, library=b'\xca\xfe\xba\xbe' + TEST_KEY)
    with pytest.raises(ValueError, match='trusts the repository test entitlement key'):
        audit_archive(archive)
    with pytest.raises(ValueError, match='missing release.json'):
        audit_archive(core_zip(tmp_path / 'bare.zip', None))


def test_cli_audit_needs_validation_for_qa_archives(tmp_path, monkeypatch):
    archive = core_zip(tmp_path / 'qa.zip', release_for(tmp_path, monkeypatch, validation=True))
    result = tool('tools/audit_artifacts.py', archive)
    assert result.returncode != 0 and 'trusts test entitlement keys' in result.stderr
    result = tool('tools/audit_artifacts.py', '--validation', archive)
    assert result.returncode == 0 and 'test key allowed' in result.stdout


def swift_zip(path, release, xcframework=True):
    files = {'Package.swift': '// swift-tools-version: 5.9', 'LICENSE': packages.LICENSE.read_bytes(), 'release.json': json.dumps(release)}
    if xcframework:
        for name, data in xcframework_entries().items():
            files['CFisica.xcframework/' + name] = data
    return zip_of(path, files)


def xcframework_entries():
    libraries = [{'LibraryIdentifier': identifier, 'LibraryPath': 'libfisica_core.a', 'HeadersPath': 'Headers',
                  'SupportedPlatform': platform, **({'SupportedPlatformVariant': variant} if variant else {})}
                 for identifier, platform, variant in (('macos-arm64_x86_64', 'macos', None), ('ios-arm64', 'ios', None),
                                                       ('ios-arm64_x86_64-simulator', 'ios', 'simulator'))]
    entries = {'Info.plist': plistlib.dumps({'AvailableLibraries': libraries})}
    for library in libraries:
        base = library['LibraryIdentifier'] + '/'
        entries.update({base + 'libfisica_core.a': b'!<arch>\n', base + 'Headers/fisica_core.h': packages.HEADER.read_bytes(),
                        base + 'Headers/module.modulemap': b'module CFisica { header "fisica_core.h" export * }\n'})
    return entries


def test_binary_packages_must_hold_their_binaries(tmp_path, monkeypatch):
    release = release_for(tmp_path, monkeypatch, validation=False)
    assert audit_archive(swift_zip(tmp_path / 'swift.zip', release)) > 3
    with pytest.raises(ValueError, match='Swift package is missing CFisica.xcframework/Info.plist'):
        audit_archive(swift_zip(tmp_path / 'empty-swift.zip', release, xcframework=False))
    jni = {f'jni/{abi}/{name}': android_elf(0x4000, **ELF[abi]) for abi in packages.ANDROID_ABIS
           for name in ('libfisica_core.so', 'libfisica_jni.so')}
    files = {'classes.jar': b'PK', 'AndroidManifest.xml': b'<manifest/>', 'LICENSE': packages.LICENSE.read_bytes(),
             'release.json': json.dumps(release), **jni}
    assert audit_archive(zip_of(tmp_path / 'full.aar', files)) == len(files)
    del files['jni/x86_64/libfisica_jni.so']
    with pytest.raises(ValueError, match='missing jni/x86_64/libfisica_jni.so'):
        audit_archive(zip_of(tmp_path / 'partial.aar', files))


def test_metadata_only_trusts_production_keys_unless_validation(tmp_path):
    for flags, expected in (((), '0'), (('--validation',), '1')):
        output = tmp_path / ('qa' if flags else 'release')
        result = tool('tools/native_build.py', '--metadata-only', *flags, '--output', output)
        assert result.returncode == 0, result.stderr
        header = (output / 'fisica_release.h').read_text()
        assert f'#define FISICA_VALIDATION_BUILD {expected}\n' in header
        environments = {key['environment'] for key in json.loads((output / 'release.json').read_text())['entitlement']['keys']}
        assert environments == ({'production', 'test'} if flags else {'production'})
    assert tool('tools/native_build.py', '--validation', '--output', tmp_path / 'x').returncode == 2


@pytest.mark.skipif(not shutil.which('cmake'), reason='cmake not installed')
def test_cmake_build_refuses_test_key_metadata_without_validation(tmp_path):
    def configure(metadata, *options):
        return subprocess.run(['cmake', '-S', ROOT / 'native', '-B', tmp_path / ('build-' + metadata.name + '-'.join(options)),
                               f'-DFISICA_RELEASE_DIR={metadata}', *options], capture_output=True, text=True)
    release, qa = tmp_path / 'release', tmp_path / 'qa'
    assert tool('tools/native_build.py', '--metadata-only', '--output', release).returncode == 0
    assert tool('tools/native_build.py', '--metadata-only', '--validation', '--output', qa).returncode == 0
    assert configure(release).returncode == 0
    refused = configure(qa)
    assert refused.returncode != 0 and 'trusts the test entitlement key' in refused.stderr
    assert configure(qa, '-DFISICA_VALIDATION=ON').returncode == 0


def test_package_file_lists_come_from_git(tmp_path, monkeypatch):
    repository = (tmp_path / 'repository').resolve()
    package = repository / 'wrappers' / 'demo'
    for name in ('lib/a.dart', 'test/a_test.dart', 'README.md', 'gone.txt'):
        (package / name).parent.mkdir(parents=True, exist_ok=True)
        (package / name).write_text(name)
    subprocess.run(['git', 'init', '-q', repository], check=True)
    subprocess.run(['git', '-C', repository, 'add', '.'], check=True)
    for name in ('.DS_Store', '.flutter-plugins-dependencies', 'build/output.txt'):
        (package / name).parent.mkdir(parents=True, exist_ok=True)
        (package / name).write_text('/Users/someone/local')
    (package / 'gone.txt').unlink()
    monkeypatch.setattr(packages, 'ROOT', repository)
    with pytest.raises(SystemExit, match='gone.txt is tracked but missing'):
        packages.tracked_files(package)
    subprocess.run(['git', '-C', repository, 'rm', '-q', '--cached', 'wrappers/demo/gone.txt'], check=True)
    assert [name for _, name in packages.tracked_files(package)] == ['README.md', 'lib/a.dart']


@pytest.fixture
def platform_inputs(tmp_path, monkeypatch):
    """Synthetic tools/build_platforms.py output for every target (validation or release keys)."""
    def build(validation, stale_core=False):
        release = release_for(tmp_path, monkeypatch, validation)
        source = tmp_path / ('input-validation' if validation else 'input')
        shutil.rmtree(source, ignore_errors=True)
        xcframework = source / 'apple' / 'CFisica.xcframework'
        for name, data in xcframework_entries().items():
            (xcframework / name).parent.mkdir(parents=True, exist_ok=True)
            (xcframework / name).write_bytes(data)
        jni = source / 'android' / 'jni'
        for abi in packages.ANDROID_ABIS:
            (jni / abi).mkdir(parents=True)
            for name in ('libfisica_core.so', 'libfisica_jni.so'):
                (jni / abi / name).write_bytes(android_elf(0x4000, **ELF[abi]))
        (source / 'android' / 'classes.jar').write_bytes(b'PK')
        packages.kotlin_aar(source / 'android', source / 'android' / 'classes.jar', jni, release, validation)
        for target in packages.DESKTOP_TARGETS:
            directory = source / target
            directory.mkdir()
            library = directory / packages.core_library(target)
            library.write_bytes(pe_dll(machine=0xaa64 if target == 'win_arm64' else 0x8664) if target.startswith('win_') else b'\xca\xfe\xba\xbe')
            core = dict(release, core_version='0.0.1') if stale_core and target == 'win_amd64' else release
            packages.write_release(directory / 'release.json', core)
            packages.core_package(source, library, release, target, validation)
        for name in ('apple', 'android'):
            packages.write_release(source / name / 'release.json', release)
        (source / 'android' / 'fisica-scale-0.2.0.aar').write_bytes(b'stale package from an older build')
        return source
    return build


def expected_packages(release):
    names = packages.package_names(release)
    return sorted([packages.core_name(release, target) for target in packages.DESKTOP_TARGETS] +
                  [names[kind] for kind in ('aar', 'swift', 'kotlin', 'dotnet', 'flutter', 'flutter_transport', 'samples')])


def test_package_platforms_writes_exactly_the_expected_packages(tmp_path, platform_inputs):
    source = platform_inputs(validation=False)
    output = tmp_path / 'packages'
    stale = output / release_metadata()['sdk_version'] / 'fisica-kotlin-0.2.0.zip'
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b'left from an earlier run')
    result = tool('tools/package_platforms.py', '--input', source, '--output', output)
    assert result.returncode == 0, result.stderr
    directory = stale.parent
    release = release_metadata()
    assert sorted(path.name for path in directory.iterdir()) == sorted(expected_packages(release) + ['platform-manifest.json'])
    manifest = json.loads((directory / 'platform-manifest.json').read_text())
    assert sorted(manifest['artifacts']) == expected_packages(release) and manifest['validation_build'] is False
    for name in manifest['artifacts']:
        with zipfile.ZipFile(directory / name) as archive:
            entries = archive.namelist()
            assert not any(part in ('.DS_Store', '.flutter-plugins-dependencies', 'pubspec.lock', 'test', 'Tests')
                           for entry in entries for part in Path(entry).parts), name
            if 'partner-samples' not in name:
                keys = json.loads(archive.read('release.json'))['entitlement']['keys']
                assert {key['environment'] for key in keys} == {'production'}, name
    with zipfile.ZipFile(directory / packages.package_names(release)['flutter']) as archive:
        entries = set(archive.namelist())
    assert {'android/src/main/jniLibs/x86_64/libfisica_core.so', 'ios/CFisica.xcframework/Info.plist',
            'macos/CFisica.xcframework/ios-arm64/libfisica_core.a', 'windows/lib/arm64/fisica_core.dll',
            'lib/fisica_core.dart', 'release.json', 'LICENSE'} <= entries


def test_package_platforms_checks_every_input_first(tmp_path, platform_inputs):
    qa = platform_inputs(validation=True)
    output = tmp_path / 'packages'
    refused = tool('tools/package_platforms.py', '--input', qa, '--output', output)
    assert refused.returncode != 0 and 'trusts test entitlement keys' in refused.stderr and not output.exists()
    accepted = tool('tools/package_platforms.py', '--validation', '--input', qa, '--output', output)
    assert accepted.returncode == 0, accepted.stderr
    manifest = json.loads((output / release_metadata()['sdk_version'] / 'platform-manifest.json').read_text())
    assert manifest['validation_build'] is True

    stale = platform_inputs(validation=False, stale_core=True)
    refused = tool('tools/package_platforms.py', '--input', stale, '--output', tmp_path / 'stale')
    assert refused.returncode != 0 and 'win_amd64/release.json is from another core build (core_version 0.0.1' in refused.stderr
    shutil.rmtree(stale / 'apple' / 'CFisica.xcframework')
    (stale / 'win_arm64' / 'fisica_core.dll').unlink()
    refused = tool('tools/package_platforms.py', '--input', stale, '--output', tmp_path / 'missing')
    assert refused.returncode != 0 and 'CFisica.xcframework/Info.plist' in refused.stderr and 'win_arm64/fisica_core.dll' in refused.stderr


def test_print_artifacts_names_every_package_from_the_release_metadata(tmp_path):
    result = tool('tools/build_platforms.py', '--print-artifacts', 'apple', 'android', 'win_amd64', '--output', tmp_path)
    assert result.returncode == 0, result.stderr
    release = release_metadata()
    names = packages.package_names(release)
    assert result.stdout.split() == [str(tmp_path / 'apple' / names['swift']), str(tmp_path / 'android' / names['aar']),
                                     str(tmp_path / 'android' / names['kotlin']), str(tmp_path / packages.core_name(release, 'win_amd64'))]
