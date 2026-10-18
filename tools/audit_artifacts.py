"""Reject customer archives that contain implementation sources or debug artifacts, lack their
binaries, license or release metadata, or hold a core that trusts the repository test key.

    python tools/audit_artifacts.py [--validation] ARCHIVE...

--validation accepts QA archives whose core also trusts the test key (tools/*.py --validation).
Those must be named as validation builds (validation_named), and without --validation any
archive named so is refused, whatever it holds.
"""
from pathlib import Path
import plistlib
import re
import zipfile
import json
import runpy
PARTNER_SAMPLE_FILES = runpy.run_path(str(Path(__file__).with_name('partner_samples.py')))['PARTNER_SAMPLE_FILES']
import struct

FORBIDDEN_SUFFIXES = {'.cpp', '.cc', '.c', '.cxx', '.hpp', '.hxx', '.hh', '.rs', '.pyx', '.pdb', '.o', '.obj', '.pyc', '.lib', '.exp', '.ilk', '.idb', '.pch'}
INTERNAL_MARKERS = ('STABILITY_THRESHOLD', 'np.linalg.eigh', '_HEADER_WEIGHT_SCALE', '_STX =')


def validate_windows_dll(data, expected_machine=0x8664):
    """Check PE signature, x64 machine, PE32+ format and DLL characteristic."""
    if len(data) < 64 or data[:2] != b'MZ':
        raise ValueError('Windows core is not a PE DLL')
    offset = struct.unpack_from('<I',data,0x3c)[0]
    if offset + 26 > len(data) or data[offset:offset+4] != b'PE\x00\x00':
        raise ValueError('Windows core has an invalid PE header')
    machine = struct.unpack_from('<H',data,offset+4)[0]
    characteristics = struct.unpack_from('<H',data,offset+22)[0]
    optional_size = struct.unpack_from('<H',data,offset+20)[0]
    if optional_size < 2 or offset+24+optional_size > len(data):
        raise ValueError('Windows core has an invalid optional header')
    magic = struct.unpack_from('<H',data,offset+24)[0]
    if machine != expected_machine or magic != 0x20b:
        raise ValueError('Windows requires a matching x64 or ARM64 PE32+ DLL')
    if not characteristics & 0x2000:
        raise ValueError('Windows core is an executable, not a DLL')


# The LICENSE opens with the Korean original's title; the English title is accepted as well.
LICENSE_HEADINGS = ('Fisica Scale SDK 라이선스\n'.encode(), b'Fisica Scale SDK License\n')
ANDROID_PAGE_SIZE = 16384
ANDROID_PAGE_FLAGS = '-Wl,-z,max-page-size=16384 -Wl,-z,common-page-size=16384'


def validate_android_library(data, abi):
    machines = {'arm64-v8a': (2,183), 'armeabi-v7a': (1,40), 'x86_64': (2,62)}
    if len(data)<20 or data[:4]!=b'\x7fELF' or data[5]!=1 or abi not in machines:
        raise ValueError('Invalid Android ELF library or ABI')
    if (data[4],struct.unpack_from('<H',data,18)[0])!=machines[abi]:
        raise ValueError('Android library architecture does not match its ABI directory')
    # Every LOAD segment must be 16 KB aligned so the library loads on 16 KB page devices.
    wide = data[4] == 2
    if len(data) < (64 if wide else 52):
        raise ValueError('Android library has a truncated ELF header')
    offset = struct.unpack_from('<Q' if wide else '<I', data, 0x20 if wide else 0x1c)[0]
    size, count = struct.unpack_from('<HH', data, 0x36 if wide else 0x2a)
    if not count or size < (56 if wide else 32) or offset + size * count > len(data):
        raise ValueError('Android library has no readable program headers')
    word = '<Q' if wide else '<I'
    # (type, vaddr, memsz, align) of each program header.
    headers = [(struct.unpack_from('<I', data, base)[0], struct.unpack_from(word, data, base + (16 if wide else 8))[0],
                struct.unpack_from(word, data, base + (40 if wide else 20))[0], struct.unpack_from(word, data, base + (48 if wide else 28))[0])
               for base in (offset + i * size for i in range(count))]
    alignments = [align for kind, _, _, align in headers if kind == 1]
    if not alignments or min(alignments) < ANDROID_PAGE_SIZE:
        raise ValueError(f'Android library LOAD segments are not 16 KB aligned ({abi}); rebuild with {ANDROID_PAGE_FLAGS}')
    # The loader makes every page the RELRO segment touches read-only, so it must end on a
    # 16 KB boundary or writable data after it faults on 16 KB page devices.
    for kind, vaddr, memsz, _ in headers:
        if kind == 0x6474e552 and (vaddr + memsz) % ANDROID_PAGE_SIZE:
            raise ValueError(f'Android library RELRO segment does not end on a 16 KB boundary ({abi}); rebuild with {ANDROID_PAGE_FLAGS}')


# A validation (QA) build trusts the repository test key, whose secret is committed: shipped by
# mistake, it would let anyone sign full-mode keys. So its file names say what it is. Archives
# end in '-validation' before the extension (fisica-core-2.0.0-win_amd64-validation.zip,
# fisica-scale-0.3.0-validation.aar). A wheel's name is name-version-python-abi-platform, so its
# version carries the local label '+validation' instead (fisica-3.0.0+validation-py3-none-win_amd64.whl):
# pip installs it, `pip list` shows it and PyPI refuses to host it. The build tools also write
# validation builds to their own default directories (.build/*-validation).
VALIDATION_MARK = '-validation'
WHEEL_VALIDATION_LABEL = 'validation'


def wheel_version(path):
    """The version field of a wheel file name (name-version[-build]-python-abi-platform.whl), or None."""
    path = Path(path)
    parts = path.stem.split('-')
    return parts[1] if path.suffix == '.whl' and len(parts) in (5, 6) else None


def validation_named(path):
    """Whether the file name marks a validation build: a '+validation' wheel version, a '-validation' archive name."""
    path = Path(path)
    if path.suffix == '.whl':
        version = wheel_version(path)
        return version is not None and version.partition('+')[2] == WHEEL_VALIDATION_LABEL
    return path.stem.endswith(VALIDATION_MARK)


def validation_name(name):
    """The file name of the validation build of the package `name`."""
    path = Path(name)
    if path.suffix == '.whl':
        parts = path.stem.split('-')
        parts[1] += '+' + WHEEL_VALIDATION_LABEL
        return '-'.join(parts) + path.suffix
    return path.stem + VALIDATION_MARK + path.suffix


# Mach-O CPU types (mach/machine.h) of the macOS cores, and what each macOS wheel tag must hold.
MACHO_CPU_TYPES = {0x0100000c: 'arm64', 0x01000007: 'x86_64'}
MACOS_WHEEL_ARCHITECTURES = {'universal2': ('arm64', 'x86_64'), 'arm64': ('arm64',), 'x86_64': ('x86_64',)}


def macho_architectures(data):
    """Architectures of a 64-bit Mach-O binary: every slice of a universal (fat) file, or the one of a thin file."""
    magic = data[:4]
    if magic in (b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'):
        entry = 20 if magic == b'\xca\xfe\xba\xbe' else 32
        count = struct.unpack_from('>I', data, 4)[0] if len(data) >= 8 else 0
        if not count or 8 + count * entry > len(data):
            raise ValueError('Universal Mach-O binary has no readable architecture table')
        return sorted(MACHO_CPU_TYPES.get(struct.unpack_from('>I', data, 8 + i * entry)[0], 'unknown') for i in range(count))
    if magic == b'\xcf\xfa\xed\xfe' and len(data) >= 8:
        return [MACHO_CPU_TYPES.get(struct.unpack_from('<I', data, 4)[0], 'unknown')]
    raise ValueError('macOS core is not a 64-bit Mach-O binary')


NATIVE_LIBRARIES = ('.dylib', '.so', '.dll', '.a')
CORE_LIBRARIES = ('fisica_core.dll', 'libfisica_core.dylib', 'libfisica_core.so')
ANDROID_ABIS = ('arm64-v8a', 'armeabi-v7a', 'x86_64')
KOTLIN_CORE = 'src/main/kotlin/com/carenco/fisica/Core.kt'


def repository_test_keys():
    """Public keys of the non-production entries in native/entitlement-keys.json (none outside the SDK checkout)."""
    try:
        keys = json.loads((Path(__file__).resolve().parents[1] / 'native' / 'entitlement-keys.json').read_text())['keys']
        return [bytes.fromhex(key['public_key']) for key in keys if key['environment'] != 'production']
    except (OSError, ValueError, KeyError):
        return []


def package_kind(path, names):
    """What an archive is, from its contents (file names only for wheels and AARs)."""
    if path.suffix == '.whl':
        return 'wheel'
    if 'quickstart.json' in names:
        return 'samples'
    if path.suffix == '.aar':
        return 'aar'
    if 'Package.swift' in names:
        return 'swift'
    if 'pubspec.yaml' in names and 'lib/fisica_core.dart' in names:
        return 'flutter'
    if 'pubspec.yaml' in names and 'lib/fisica_flutter.dart' in names:
        return 'flutter-transport'
    if KOTLIN_CORE in names:
        return 'kotlin'
    if any('/' not in name and name.endswith('.csproj') for name in names):
        return 'dotnet'
    return 'core'


def xcframework_entries(archive, names, prefix):
    """Entries an XCFramework in the archive needs: Info.plist and, for each library it lists, the core and its header."""
    plist = prefix + 'Info.plist'
    if plist not in names:
        return [plist]
    try:
        libraries = plistlib.loads(archive.read(plist))['AvailableLibraries']
        platforms = {(item['SupportedPlatform'], item.get('SupportedPlatformVariant')) for item in libraries}
        entries = [plist] + [prefix + item['LibraryIdentifier'] + '/' + name for item in libraries
                             for name in (item['LibraryPath'], item['HeadersPath'] + '/fisica_core.h')]
    except (plistlib.InvalidFileException, ValueError, KeyError, TypeError):
        raise ValueError(f'{plist} is not an XCFramework Info.plist')
    missing = {('ios', None), ('ios', 'simulator'), ('macos', None)} - platforms
    if missing:
        raise ValueError(f'{plist} has no library for ' + ', '.join(sorted('-'.join(filter(None, item)) for item in missing)))
    return entries


def require_binaries(kind, archive, names):
    """Packages that promise compiled cores must contain all of them."""
    def need(entries, package):
        missing = [entry for entry in entries if entry not in names]
        if missing:
            raise ValueError(f'{package} is missing ' + ', '.join(missing))
    if kind == 'aar':
        need(['classes.jar', 'AndroidManifest.xml'] + [f'jni/{abi}/{library}' for abi in ANDROID_ABIS
                                                        for library in ('libfisica_core.so', 'libfisica_jni.so')], 'Android library (AAR)')
    elif kind == 'swift':
        need(xcframework_entries(archive, names, 'CFisica.xcframework/'), 'Swift package')
    elif kind == 'flutter':
        need([f'android/src/main/jniLibs/{abi}/libfisica_core.so' for abi in ANDROID_ABIS] +
             [f'windows/lib/{architecture}/fisica_core.dll' for architecture in ('x64', 'arm64')], 'Flutter package')
        for platform in ('ios', 'macos'):
            need(xcframework_entries(archive, names, platform + '/CFisica.xcframework/'), 'Flutter package')
        for architecture, machine in (('x64', 0x8664), ('arm64', 0xaa64)):
            validate_windows_dll(archive.read(f'windows/lib/{architecture}/fisica_core.dll'), machine)
    elif kind == 'core':
        if len([name for name in names if name in CORE_LIBRARIES]) != 1 or 'fisica_core.h' not in names:
            raise ValueError('Core archive needs exactly one core library and fisica_core.h at its root')


def audit_release(path, kind, archive, names, validation):
    """The release.json a package carries, the entitlement keys its core trusts and whether its name says so."""
    if kind == 'samples':
        return
    name = 'fisica_sdk/_release.json' if kind == 'wheel' else 'release.json'
    if name not in names:
        raise ValueError(f'Archive is missing {name}')
    try:
        release = json.loads(archive.read(name))
        keys = release.get('entitlement', {}).get('keys')
    except (ValueError, AttributeError):
        raise ValueError(f'{name} is not release metadata')
    if kind != 'wheel' and (not isinstance(release.get('core_version'), str) or not isinstance(keys, list) or not keys):
        raise ValueError(f'{name} lacks the core version or the trusted entitlement keys')
    listed = any(not isinstance(key, dict) or key.get('environment') != 'production' for key in keys or [])
    # The compiled key table, whatever the metadata says.
    test_keys = repository_test_keys()
    compiled = next((entry for entry in names if entry.endswith(NATIVE_LIBRARIES)
                     and any(key in archive.read(entry) for key in test_keys)), None)
    flagged = release.get('validation') is True
    if not validation:
        advice = ('customer archives trust production keys only; a QA archive is named as a validation build '
                  'and audited with --validation')
        if listed:
            raise ValueError(f'{path.name} trusts test entitlement keys ({name}): {advice}')
        if compiled:
            raise ValueError(f'{compiled} in {path.name} trusts the repository test entitlement key: {advice}')
        if flagged:
            raise ValueError(f'{path.name} is a validation build ({name} says validation: true): {advice}')
        return
    marked = validation_named(path)
    if (listed or compiled or flagged) and not marked:
        reason = (f'trusts the test entitlement key ({name if listed else compiled})' if listed or compiled
                  else f'is a validation build ({name} says validation: true)')
        raise ValueError(f'{path.name} {reason}, but its name does not mark it as a validation build: '
                         f'QA packages are named like {validation_name(path.name)}')
    if marked and release.get('validation') is False:
        raise ValueError(f'{path.name} is named as a validation build, but {name} is a customer build (validation: false)')


def audit_archive(path, validation=False):
    """Audit one customer archive; validation=True accepts cores that also trust the test key (QA builds)."""
    path = Path(path)
    # Any name that says validation stays out of a release, also a copy a browser renamed to '... (1).zip'.
    if not validation and 'validation' in path.name.lower():
        raise ValueError(f'{path.name} is named as a validation (QA) build, whose core may trust the repository '
                         'test key: customer releases never include one; audit QA packages with --validation')
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        kind = package_kind(path, names)
        sample_package = kind == 'samples'
        if sample_package:
            metadata = json.loads(archive.read('quickstart.json'))
            if metadata.get('type') != 'fisica-partner-samples' or metadata.get('schema_version') != 1:
                raise ValueError('Unknown public sample package')
            expected = set(PARTNER_SAMPLE_FILES) | {'INSTALL.md','LICENSE','quickstart.json'}
            if set(names) != expected or len(names) != len(expected):
                raise ValueError('Public sample archive differs from the explicit source allowlist')
        if kind in ('wheel', 'core') and not any(name.endswith(NATIVE_LIBRARIES) for name in names):
            raise ValueError('Archive does not contain a native library')
        for name in names:
            parts = Path(name).parts
            if name.endswith('.so') and any(abi in parts for abi in ('arm64-v8a','armeabi-v7a','x86_64')):
                abi=next(abi for abi in ('arm64-v8a','armeabi-v7a','x86_64') if abi in parts)
                validate_android_library(archive.read(name),abi)
            if '..' in parts or Path(name).is_absolute():
                raise ValueError(f'Unsafe archive path: {name}')
            if Path(name).suffix.lower() == '.h' and name != 'fisica_core.h' and not (Path(name).name == 'fisica_core.h' and 'Headers' in parts):
                raise ValueError(f'Unexpected implementation header: {name}')
            runtime_asset = name in ('runtimes/win-x64/native/fisica_core.dll','runtimes/win-arm64/native/fisica_core.dll','runtimes/osx-x64/native/libfisica_core.dylib','runtimes/osx-arm64/native/libfisica_core.dylib')
            if runtime_asset and name.endswith('.dll'):
                validate_windows_dll(archive.read(name),0xaa64 if 'win-arm64' in parts else 0x8664)
            public_jni = name == 'bridge/fisica_jni.cpp' and 'wrapper.json' in names and 'src/main/kotlin/com/carenco/fisica/Core.kt' in names
            public_sample = sample_package and name in ('c/main.c','cpp/main.cpp')
            if (Path(name).suffix.lower() in FORBIDDEN_SUFFIXES and not (public_jni or public_sample)) or any(
                (part.lower() in ('tools', 'tests', '.build', 'examples', '.git') or (part.lower()=='native' and not runtime_asset)) or part.lower().endswith('.dsym')
                for part in parts
            ):
                raise ValueError(f'Private source/debug artifact in customer archive: {name}')
            if name.endswith('.py'):
                text = archive.read(name).decode('utf-8')
                if any(marker in text for marker in INTERNAL_MARKERS):
                    raise ValueError(f'Native implementation leaked into Python wrapper: {name}')
        if path.suffix == '.whl':
            required = ('fisica_sdk/_release.json', 'fisica_sdk/native.py')
            if any(name not in names for name in required):
                raise ValueError('Wheel is missing wrapper release metadata')
            if path.stem.endswith(('win_amd64','win_arm64')):
                libraries = [name for name in names if name.endswith(('.dll','.dylib','.so'))]
                if libraries != ['fisica_sdk/_native/fisica_core.dll']:
                    raise ValueError('Windows wheel must contain exactly the Windows core DLL')
                validate_windows_dll(archive.read(libraries[0]),0xaa64 if path.stem.endswith('win_arm64') else 0x8664)
                wheel_metadata = [name for name in names if name.endswith('.dist-info/WHEEL')]
                if len(wheel_metadata) != 1 or 'Tag: py3-none-' + ('win_arm64' if path.stem.endswith('win_arm64') else 'win_amd64') not in archive.read(wheel_metadata[0]).decode():
                    raise ValueError('Windows wheel metadata does not match its filename')
            if 'macosx_' in path.name:
                tag = path.stem.rsplit('-', 1)[-1]
                wanted = next((list(value) for key, value in MACOS_WHEEL_ARCHITECTURES.items() if tag.endswith('_' + key)), None)
                if wanted is None:
                    raise ValueError(f'Unsupported macOS wheel platform tag {tag}')
                for name in [name for name in names if name.endswith('.dylib')]:
                    data = archive.read(name)
                    if tag.endswith('_universal2') and data[:4] not in (b'\xca\xfe\xba\xbe',b'\xca\xfe\xba\xbf'):
                        raise ValueError('Universal wheel contains a thin Mach-O binary')
                    found = macho_architectures(data)
                    if found != wanted:
                        raise ValueError(f'Wheel architecture does not match its native binary ({tag}: {name} holds '
                                         + ', '.join(found) + ')')
        elif path.stem.endswith(('win_amd64','win_arm64')):
            if 'fisica_core.dll' not in names:
                raise ValueError('Windows native bundle is missing the core DLL')
            validate_windows_dll(archive.read('fisica_core.dll'),0xaa64 if path.stem.endswith('win_arm64') else 0x8664)
        licenses = [name for name in names if name == 'LICENSE' or name.endswith('/licenses/LICENSE')]
        if len(licenses) != 1 or not archive.read(licenses[0]).startswith(LICENSE_HEADINGS):
            raise ValueError('Archive is missing the Fisica Scale SDK License')
        if path.suffix == '.whl':
            metadata = [name for name in names if name.endswith('.dist-info/METADATA')]
            if len(metadata) != 1 or 'License-Expression: LicenseRef-Fisica-Scale-SDK' not in archive.read(metadata[0]).decode():
                raise ValueError('Wheel license metadata does not match the SDK license')
            # pip refuses such a wheel as well: a renamed validation wheel does not pass for a customer one.
            declared = re.search(r'^Version: *(\S+)', archive.read(metadata[0]).decode(), re.MULTILINE)
            if declared and wheel_version(path) and declared[1].replace('-', '_') != wheel_version(path):
                raise ValueError(f'Wheel metadata version {declared[1]} does not match its file name {path.name}')
        require_binaries(kind, archive, names)
        audit_release(path, kind, archive, names, validation)
    return len(names)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('archives', nargs='+', type=Path)
    parser.add_argument('--validation', action='store_true',
                        help='accept QA archives whose core also trusts the repository test key')
    args = parser.parse_args()
    for path in args.archives:
        trust = 'test key allowed (validation)' if args.validation else 'production keys only'
        print(f'{path}: {audit_archive(path, validation=args.validation)} files, audit passed ({trust})')
