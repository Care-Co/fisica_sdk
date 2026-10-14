"""Reject customer archives containing implementation sources or debug artifacts."""
from pathlib import Path
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


def audit_archive(path):
    path = Path(path)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        sample_package = 'quickstart.json' in names
        if sample_package:
            metadata = json.loads(archive.read('quickstart.json'))
            if metadata.get('type') != 'fisica-partner-samples' or metadata.get('schema_version') != 1:
                raise ValueError('Unknown public sample package')
            expected = set(PARTNER_SAMPLE_FILES) | {'INSTALL.md','LICENSE','quickstart.json'}
            if set(names) != expected or len(names) != len(expected):
                raise ValueError('Public sample archive differs from the explicit source allowlist')
        dart_wrapper = ('pubspec.yaml' in names and any(n in names for n in ('lib/fisica_core.dart','lib/fisica_flutter.dart'))) or any(n.endswith(('.csproj','Core.kt','Package.swift')) for n in names)
        if not dart_wrapper and not any(name.endswith(('.dylib', '.so', '.dll', '.a')) for name in names):
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
                libraries = [name for name in names if name.endswith('.dylib')]
                for name in libraries:
                    header = archive.read(name)[:8]
                    if 'universal2' in path.name and header[:4] not in (b'\xca\xfe\xba\xbe',b'\xca\xfe\xba\xbf'):
                        raise ValueError('Universal wheel contains a thin Mach-O binary')
                    if header[:4] == b'\xcf\xfa\xed\xfe':
                        cpu = struct.unpack('<I',header[4:8])[0]
                        expected = 0x100000c if path.stem.endswith('arm64') else 0x1000007
                        if cpu != expected:
                            raise ValueError('Wheel architecture does not match its native binary')
        elif path.stem.endswith(('win_amd64','win_arm64')):
            if 'fisica_core.dll' not in names:
                raise ValueError('Windows native bundle is missing the core DLL')
            validate_windows_dll(archive.read('fisica_core.dll'),0xaa64 if path.stem.endswith('win_arm64') else 0x8664)
        licenses = [name for name in names if name == 'LICENSE' or name.endswith('/licenses/LICENSE')]
        if len(licenses) != 1 or not archive.read(licenses[0]).startswith(b'Fisica Scale SDK License\n'):
            raise ValueError('Archive is missing the Fisica Scale SDK License')
        if path.suffix == '.whl':
            metadata = [name for name in names if name.endswith('.dist-info/METADATA')]
            if len(metadata) != 1 or 'License-Expression: LicenseRef-Fisica-Scale-SDK' not in archive.read(metadata[0]).decode():
                raise ValueError('Wheel license metadata does not match the SDK license')
    return len(names)


if __name__ == '__main__':
    import sys
    for path in sys.argv[1:]:
        print(f'{path}: {audit_archive(path)} files, source-exclusion audit passed')
