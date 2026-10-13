"""Internal native build tools. Never included in customer wheels/bundles."""
import json
import os
import platform
import re
import runpy
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
validate_windows_dll = runpy.run_path(str(ROOT / 'tools' / 'audit_artifacts.py'))['validate_windows_dll']


def release_metadata(validate_flutter=True):
    release = json.loads((ROOT / 'sdk-release.json').read_text())
    wrappers = {}
    for path in sorted((ROOT / 'wrappers').glob('*/wrapper.json')):
        wrapper = json.loads(path.read_text())
        if wrapper['name'] != path.parent.name or wrapper['name'] in wrappers:
            raise ValueError('Wrapper manifest name must match its unique directory')
        wrappers[wrapper['name']] = wrapper
    flutter_pubspec = ROOT / 'wrappers' / 'flutter' / 'pubspec.yaml'
    if flutter_pubspec.exists():
        match = re.search(r'^version:\s*(\S+)', flutter_pubspec.read_text(), re.MULTILINE)
        if not match or match[1] != wrappers['flutter']['version']:
            raise ValueError('Flutter pubspec version must match wrappers/flutter/wrapper.json')
        if validate_flutter:
            text = (flutter_pubspec.parent / 'lib' / 'src' / 'version.dart').read_text()
            sdk_match = re.search(r"const sdkVersion\s*=\s*'([^']+)';",text)
            manifest_match = re.search(r"const wrapperManifestJson\s*=\s*r'''(.*?)''';",text,re.DOTALL)
            if (not sdk_match or sdk_match[1] != release['sdk_version'] or not manifest_match or
                    json.loads(manifest_match[1]) != wrappers['flutter']):
                raise ValueError('Flutter generated metadata is stale; run python tools/sync_flutter_metadata.py')
    release['policy_schema_version'] = 1
    release['wrappers'] = wrappers
    partner = os.environ.get('FISICA_PARTNER_ID')
    distribution = os.environ.get('FISICA_DISTRIBUTION_ID')
    if bool(partner) != bool(distribution):
        raise ValueError('Partner and distribution identifiers must be provided together')
    for value in (partner, distribution):
        reserved = {'CON', 'PRN', 'AUX', 'NUL', *('COM'+str(i) for i in range(1,10)), *('LPT'+str(i) for i in range(1,10))}
        if value and (not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,78}[A-Za-z0-9])?', value)
                      or value.split('.')[0].upper() in reserved):
            raise ValueError('Distribution identifiers must be opaque ASCII identifiers of 1-80 characters')
    release['license'] = 'Fisica Scale SDK License'
    release['distribution'] = {'partner_id': partner, 'distribution_id': distribution}
    return release


def library_name():
    if platform_tag().startswith('win_'):
        return 'fisica_core.dll'
    return 'libfisica_core.dylib' if sys.platform == 'darwin' else 'libfisica_core.so'


def platform_tag():
    target = os.environ.get('FISICA_BUILD_PLATFORM')
    if target:
        if target not in ('win_amd64', 'win_arm64', 'macosx_11_0_universal2'):
            raise ValueError('Unsupported explicit desktop build target')
        return target
    if sys.platform == 'darwin':
        minimum = os.environ.get('MACOSX_DEPLOYMENT_TARGET', '11.0').replace('.', '_')
        return 'macosx_' + minimum + '_' + platform.machine()
    import sysconfig
    tag = sysconfig.get_platform().replace('-', '_').replace('.', '_')
    if sys.platform == 'win32' and tag not in ('win_amd64','win_arm64'):
        raise ValueError('Windows builds require x64 or ARM64 Python and a matching compiler')
    return tag


def prepare_release(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    metadata = release_metadata()
    text = json.dumps(metadata, separators=(',', ':'))
    # JSON string escaping also provides a valid C string literal here (ASCII metadata).
    header = '#pragma once\n#define FISICA_CORE_VERSION ' + json.dumps(metadata['core_version'])
    header += '\n#define FISICA_CORE_POLICY ' + json.dumps(text) + '\n'
    header += '#define FISICA_ABI_MAJOR ' + str(metadata['abi_major']) + '\n'
    header += '#define FISICA_ABI_MINOR ' + str(metadata['abi_minor']) + '\n'
    header += '#define FISICA_SDK_MIN ' + json.dumps(metadata['sdk_support']['minimum']) + '\n'
    header += '#define FISICA_SDK_MAX ' + json.dumps(metadata['sdk_support']['maximum_exclusive']) + '\n'
    header += '#define FISICA_SDK_DEPRECATED ' + json.dumps(metadata['sdk_support']['deprecated_before']) + '\n'
    header += 'struct FisicaWrapperPolicy { const char *name,*core_min,*core_max,*minimum,*maximum,*deprecated; unsigned major,minor; bool schema; };\n'
    header += 'static const FisicaWrapperPolicy fisica_wrapper_policies[] = {\n'
    for name, wrapper in metadata['wrappers'].items():
        fields = [name, wrapper['core']['minimum'], wrapper['core']['maximum_exclusive'],
                  wrapper['support']['minimum'], wrapper['support']['maximum_exclusive'],
                  wrapper['support']['deprecated_before']]
        header += '{' + ','.join(json.dumps(f) for f in fields) + ',' + str(wrapper['abi']['major']) + ',' + str(wrapper['abi']['minimum_minor']) + ',' + ('true' if metadata['result_schema_version'] in wrapper['result_schema_versions'] else 'false') + '},\n'
    header += '};\n'
    (directory / 'fisica_release.h').write_text(header)
    (directory / 'release.json').write_text(json.dumps(metadata, indent=2) + '\n')
    return metadata


def build_native(output_directory):
    output_directory = Path(output_directory).resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    generated = ROOT / '.build' / 'native-generated'
    target = platform_tag()
    windows = target.startswith('win_')
    machine = 0xaa64 if target == 'win_arm64' else 0x8664
    if windows:
        generated = generated / target
    metadata = prepare_release(generated)
    output = output_directory / library_name()
    sources = [str(path) for path in sorted((ROOT / 'native' / 'src').glob('*.cpp'))]
    includes = [ROOT / 'native' / 'include', generated]
    prebuilt = os.environ.get('FISICA_PREBUILT_CORE')
    if prebuilt:
        if not windows:
            raise ValueError('Prebuilt core packaging is currently supported only for win_amd64')
        validate_windows_dll(Path(prebuilt).read_bytes(), machine)
        shutil.copyfile(prebuilt, output)
        (output_directory / 'release.json').write_text(json.dumps(metadata, indent=2) + '\n')
        return output
    compiler = os.environ.get('CXX') or ('cl' if sys.platform == 'win32' else 'c++')
    if windows and sys.platform == 'win32':
        if not shutil.which(compiler):
            raise RuntimeError('MSVC compiler not found. Use an x64 Visual Studio Developer shell.')
        command = [compiler, '/nologo', '/LD', '/O2', '/MT', '/EHsc', '/std:c++17',
                   '/fp:strict', '/DFISICA_CORE_BUILD', '/Fo' + str(generated) + os.sep]
        command += ['/I' + str(path) for path in includes]
        command += [*sources, '/link', '/OUT:' + str(output),
                    '/IMPLIB:' + str(generated / 'fisica_core.lib'), '/INCREMENTAL:NO',
                    '/OPT:REF', '/OPT:ICF', '/DYNAMICBASE', '/NXCOMPAT', '/HIGHENTROPYVA', '/MACHINE:' + ('ARM64' if machine==0xaa64 else 'X64')]
    elif windows:
        if not os.environ.get('CXX'):
            raise RuntimeError('Cross-building Windows requires CXX pointing to a matching cross compiler')
        command = [compiler, '-std=c++17', '-O2', '-DNDEBUG', '-DFISICA_CORE_BUILD',
                   '-shared', '-static', '-s', '-ffp-contract=off']
        command += ['-I' + str(path) for path in includes]
        command += [*sources, '-o', str(output)]
    else:
        command = [compiler, '-std=c++17', '-O2', '-DNDEBUG', '-DFISICA_CORE_BUILD',
                   '-fvisibility=hidden', '-fvisibility-inlines-hidden', '-ffp-contract=off']
        command += ['-I' + str(path) for path in includes]
        if sys.platform == 'darwin':
            command += (['-arch','arm64','-arch','x86_64'] if target.endswith('universal2') else ['-arch',platform.machine()])
            command += ['-dynamiclib', '-Wl,-install_name,@rpath/' + output.name]
            command += ['-mmacosx-version-min=' + os.environ.get('MACOSX_DEPLOYMENT_TARGET', '11.0')]
        else:
            command += ['-shared', '-fPIC', '-Wl,--exclude-libs,ALL']
        command += [*sources, '-o', str(output)]
    subprocess.run(command, check=True, cwd=generated)
    if windows:
        validate_windows_dll(output.read_bytes(), machine)
    else:
        subprocess.run(['strip', '-x' if sys.platform == 'darwin' else '--strip-unneeded', str(output)], check=True)
    (output_directory / 'release.json').write_text(json.dumps(metadata, indent=2) + '\n')
    return output


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default=str(ROOT / 'fisica_sdk' / '_native'))
    parser.add_argument('--bundle', action='store_true', help='Include only public header and wrapper manifests')
    parser.add_argument('--metadata-only', action='store_true')
    args = parser.parse_args()
    if args.metadata_only:
        prepare_release(args.output)
    else:
        build_native(args.output)
        if args.bundle:
            output = Path(args.output)
            shutil.copyfile(ROOT / 'native' / 'include' / 'fisica_core.h', output / 'fisica_core.h')
