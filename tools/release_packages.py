"""Customer packages: one function per artifact, so every package has exactly one recipe.

tools/build_release.py (Python wheel and this host's core), tools/build_platforms.py (one
platform's binaries) and tools/package_platforms.py (the partner packages made from those
binaries) all write their archives here. Every package

- holds the license and the release.json of the core it was built with (the partner samples,
  which contain no core, carry quickstart.json instead),
- lists only files git tracks plus the binaries it needs, never whatever else is on disk
  (local tool settings with absolute paths, lock files, .DS_Store, stale build output),
- is audited by tools/audit_artifacts.py as it is written; with validation=False an archive
  whose core trusts the repository test key fails,
- is named as a validation build ('-validation' before the extension) when its release metadata
  says validation: true, so a QA package cannot pass for a customer one.
"""
import json
import plistlib
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

if __package__:  # imported as tools.release_packages (tests)
    from .audit_artifacts import ANDROID_ABIS, audit_archive, validation_name
    from .native_build import ROOT, release_metadata
    from .partner_samples import PARTNER_SAMPLE_FILES
else:  # imported by the scripts in tools/
    from audit_artifacts import ANDROID_ABIS, audit_archive, validation_name
    from native_build import ROOT, release_metadata
    from partner_samples import PARTNER_SAMPLE_FILES

LICENSE = ROOT / 'LICENSE'
HEADER = ROOT / 'native' / 'include' / 'fisica_core.h'
WRAPPERS = ROOT / 'wrappers'
SAMPLES = ROOT / 'examples' / 'partner'
DESKTOP_TARGETS = ('macosx_11_0_universal2', 'win_arm64', 'win_amd64')
WINDOWS_ARCHITECTURES = {'win_amd64': 'x64', 'win_arm64': 'arm64'}
TEST_DIRECTORIES = ('test', 'tests', 'Tests')


# File names. They come from the release metadata, so no tool, workflow or document hardcodes a version,
# and a validation build (release['validation'], its core trusts the test key) is named so:
# fisica-swift-0.3.0-validation.zip, fisica-core-2.0.0-win_amd64-validation.zip.

def transport_version():
    match = re.search(r'^version:\s*(\S+)', (WRAPPERS / 'flutter_transport' / 'pubspec.yaml').read_text(), re.MULTILINE)
    if not match:
        raise ValueError('wrappers/flutter_transport/pubspec.yaml has no version')
    return match[1]


def _named(name, release):
    return validation_name(name) if release.get('validation') else name


def package_names(release):
    wrappers = release['wrappers']
    names = {
        'swift': f"fisica-swift-{wrappers['swift']['version']}.zip",
        'aar': f"fisica-scale-{wrappers['kotlin']['version']}.aar",
        'kotlin': f"fisica-kotlin-{wrappers['kotlin']['version']}.zip",
        'dotnet': f"fisica-dotnet-{wrappers['dotnet']['version']}.zip",
        'flutter': f"fisica-flutter-{wrappers['flutter']['version']}-platforms.zip",
        'flutter_transport': f'fisica-flutter-transports-{transport_version()}.zip',
        'samples': f"fisica-partner-samples-{release['sdk_version']}.zip",
    }
    return {kind: _named(name, release) for kind, name in names.items()}


def core_name(release, target):
    return _named(f"fisica-core-{release['core_version']}-{target}.zip", release)


def core_library(target):
    """File name of the core library for a platform tag (tools/native_build.py platform_tag())."""
    if target.startswith('win_'):
        return 'fisica_core.dll'
    return 'libfisica_core.dylib' if target.startswith('macosx_') else 'libfisica_core.so'


# Inputs.

def tracked_files(directory, exclude=TEST_DIRECTORIES):
    """Files under `directory` that git tracks, as (path, name in the archive), without `exclude` directories."""
    directory = Path(directory).resolve()
    relative = directory.relative_to(ROOT).as_posix()
    # The checkout may belong to another user than the build (CI containers run as root);
    # git then refuses to read it unless it is named a safe directory.
    command = ['git', '-c', f'safe.directory={ROOT.as_posix()}', '-C', str(ROOT), 'ls-files', '-z', '--', relative]
    try:
        listing = subprocess.run(command, check=True, capture_output=True).stdout.decode()
    except (OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(f'Packages take their file lists from git ls-files; run this in the SDK checkout ({error})')
    files = []
    for name in sorted(filter(None, listing.split('\0'))):
        path = ROOT / name
        inner = path.relative_to(directory)
        if any(part in exclude for part in inner.parts[:-1]):
            continue
        if not path.is_file():
            raise SystemExit(f'{name} is tracked but missing: restore it or commit its removal')
        files.append((path, inner.as_posix()))
    if not files:
        raise SystemExit(f'git tracks no files under {relative}')
    return files


def require(paths, hint):
    """Stop before writing anything when an input is missing, naming every missing file at once."""
    missing = [str(path) for path in paths if not Path(path).is_file()]
    if missing:
        raise SystemExit(f'Missing inputs ({hint}):\n  ' + '\n  '.join(missing))


def _policy(release):
    """Everything the core compiles in, except what differs between release and validation builds (keys, validation)."""
    policy = json.loads(json.dumps(release))
    policy.get('entitlement', {}).pop('keys', None)
    policy.pop('validation', None)
    return policy


def trusts_test_key(release):
    return any(key.get('environment') != 'production' for key in release.get('entitlement', {}).get('keys', []))


def require_build_kind(paths, validation):
    """Stop unless every existing prebuilt release.json is the kind of build asked for: no test key
    without validation, and its validation flag (which names the packages) the same as this run's.
    Checked before the inputs' file names, which differ between the two kinds."""
    expected = release_metadata()['validation']
    for path in paths:
        if not Path(path).is_file():
            continue
        release = read_release(path)
        if trusts_test_key(release) and not validation:
            raise SystemExit(f'{path} trusts test entitlement keys; rebuild the platforms without --validation')
        kind = release.get('validation')
        if kind is not expected:
            raise SystemExit(f'{path} ' + ('is a validation build; package it with --validation' if kind is True else
                                           'is a customer build; rebuild the platforms with --validation for QA packages'
                                           if kind is False else 'predates the validation flag; rebuild it'))


def prebuilt_release(paths, validation):
    """The release.json shared by prebuilt inputs.

    Every input must come from this checkout's core build: the same core and ABI version and
    the same compiled policy as release_metadata(). Without validation none may trust the test
    key, with validation all must be validation builds (their packages are named so), and all of
    them must be identical (one build, not a mix of release and QA cores).
    """
    require_build_kind(paths, validation)
    current = _policy(release_metadata())
    releases = {}
    for path in paths:
        release = json.loads(Path(path).read_text())
        if _policy(release) != current:
            fields = sorted(key for key in set(current) | set(release) if current.get(key) != _policy(release).get(key))
            versions = ', '.join(f"{key} {release.get(key)}" for key in ('core_version', 'abi_major', 'abi_minor'))
            raise SystemExit(f'{path} is from another core build ({versions}; differs in {", ".join(fields)}); '
                             'rebuild it with tools/build_platforms.py')
        releases[str(path)] = release
    if len({json.dumps(release, sort_keys=True) for release in releases.values()}) > 1:
        raise SystemExit('Prebuilt inputs trust different entitlement keys (release and --validation builds mixed): '
                         + ', '.join(releases))
    return next(iter(releases.values()))


def read_release(path):
    return json.loads(Path(path).read_text())


def write_release(path, release):
    Path(path).write_text(json.dumps(release, indent=2) + '\n')


# Archives.

def _common(release):
    return [(LICENSE, 'LICENSE'), ((json.dumps(release, indent=2) + '\n').encode(), 'release.json')]


def write_package(path, files, validation):
    """Write one archive from (source path or bytes, archive name) pairs and audit it; a failed audit removes it."""
    path = Path(path)
    names = [name for _, name in files]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f'{path.name}: duplicate entries {duplicates}')
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for source, name in sorted(files, key=lambda item: item[1]):
                if isinstance(source, bytes):
                    archive.writestr(name, source)
                else:
                    archive.write(source, name)
        audit_archive(path, validation=validation)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def copy_package(source, directory, release, validation):
    """Copy a package an earlier build wrote, after auditing it and checking it holds `release`."""
    source = Path(source)
    audit_archive(source, validation=validation)
    with zipfile.ZipFile(source) as archive:
        if json.loads(archive.read('release.json')) != release:
            raise SystemExit(f'{source} holds another release.json than the other inputs; rebuild it')
    target = Path(directory) / source.name
    shutil.copyfile(source, target)
    return target


def xcframework_files(xcframework, prefix):
    """Info.plist plus, for each library it lists, the static core and its public headers."""
    xcframework = Path(xcframework)
    plist = xcframework / 'Info.plist'
    require([plist], 'CFisica.xcframework: python tools/build_platforms.py --target apple')
    files = [(plist, prefix + 'Info.plist')]
    for library in plistlib.loads(plist.read_bytes())['AvailableLibraries']:
        base = library['LibraryIdentifier'] + '/'
        names = [library['LibraryPath'], library['HeadersPath'] + '/fisica_core.h', library['HeadersPath'] + '/module.modulemap']
        require([xcframework / base / name for name in names], 'incomplete CFisica.xcframework')
        files += [(xcframework / base / name, prefix + base + name) for name in names]
    return files


def jni_files(jni, prefix, libraries):
    """The libraries of every Android ABI."""
    paths = [(Path(jni) / abi / library, f'{prefix}{abi}/{library}') for abi in ANDROID_ABIS for library in libraries]
    require([path for path, _ in paths], 'Android libraries: python tools/build_platforms.py --target android')
    return paths


def core_package(directory, core, release, target, validation):
    """fisica-core-<core>-<target>.zip: one compiled core, its public C header, release.json and the license."""
    core = Path(core)
    if core.name != core_library(target):
        raise ValueError(f'{core.name} is not the {target} core library')
    require([core], target + ' core')
    return write_package(Path(directory) / core_name(release, target),
                         [(core, core.name), (HEADER, 'fisica_core.h'), *_common(release)], validation)


def swift_package(directory, xcframework, release, validation):
    """fisica-swift-<v>.zip: the public Swift package with CFisica.xcframework beside Package.swift."""
    files = tracked_files(WRAPPERS / 'swift') + xcframework_files(xcframework, 'CFisica.xcframework/') + _common(release)
    return write_package(Path(directory) / package_names(release)['swift'], files, validation)


def kotlin_aar(directory, classes, jni, release, validation):
    """fisica-scale-<v>.aar: compiled Kotlin classes, manifest, consumer ProGuard rules, core and JNI library of every ABI."""
    require([classes], 'classes.jar: python tools/build_platforms.py --target android')
    kotlin = WRAPPERS / 'kotlin'
    files = [(Path(classes), 'classes.jar'), (kotlin / 'AndroidManifest.xml', 'AndroidManifest.xml'),
             (kotlin / 'consumer-rules.pro', 'proguard.txt'), *_common(release),
             *jni_files(jni, 'jni/', ('libfisica_core.so', 'libfisica_jni.so'))]
    return write_package(Path(directory) / package_names(release)['aar'], files, validation)


def kotlin_sources(directory, release, validation):
    """fisica-kotlin-<v>.zip: public Kotlin sources, JNI bridge and C header, for apps that build the AAR themselves."""
    files = tracked_files(WRAPPERS / 'kotlin') + [(HEADER, 'fisica_core.h')] + _common(release)
    return write_package(Path(directory) / package_names(release)['kotlin'], files, validation)


def dotnet_sources(directory, release, validation):
    """fisica-dotnet-<v>.zip: the public C# wrapper project.

    Apps reference the project and take each platform's core from its core ZIP. A NuGet package
    with the cores inside follows once a Windows runner can build and test it.
    """
    files = tracked_files(WRAPPERS / 'dotnet') + _common(release)
    return write_package(Path(directory) / package_names(release)['dotnet'], files, validation)


def flutter_package(directory, xcframework, jni, windows_cores, release, validation):
    """fisica-flutter-<v>-platforms.zip: the fisica_core Dart package with the compiled core of every Flutter platform."""
    files = tracked_files(WRAPPERS / 'flutter') + _common(release)
    files += jni_files(jni, 'android/src/main/jniLibs/', ('libfisica_core.so',))
    for platform in ('ios', 'macos'):
        files += xcframework_files(xcframework, platform + '/CFisica.xcframework/')
    require(windows_cores.values(), 'Windows cores: python tools/build_platforms.py --target win_amd64 / win_arm64')
    files += [(Path(windows_cores[target]), f'windows/lib/{architecture}/fisica_core.dll')
              for target, architecture in WINDOWS_ARCHITECTURES.items()]
    return write_package(Path(directory) / package_names(release)['flutter'], files, validation)


def flutter_transport_package(directory, release, validation):
    """fisica-flutter-transports-<v>.zip: the fisica_flutter BLE and desktop USB adapters with third-party notices."""
    files = tracked_files(WRAPPERS / 'flutter_transport') + _common(release)
    return write_package(Path(directory) / package_names(release)['flutter_transport'], files, validation)


def partner_samples_package(directory, release, validation):
    """fisica-partner-samples-<sdk>.zip: the explicit public sample allowlist and the installation guide."""
    quickstart = {'type': 'fisica-partner-samples', 'schema_version': 1, 'sdk_version': release['sdk_version']}
    files = [(SAMPLES / name, name) for name in PARTNER_SAMPLE_FILES]
    files += [(ROOT / 'doc' / 'partner-quickstart.md', 'INSTALL.md'), (LICENSE, 'LICENSE'),
              ((json.dumps(quickstart) + '\n').encode(), 'quickstart.json')]
    return write_package(Path(directory) / package_names(release)['samples'], files, validation)
