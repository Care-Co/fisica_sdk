"""Assemble the partner packages from trusted prebuilt cores (tools/build_platforms.py output).

--input needs every target's build: apple, android, macosx_11_0_universal2, win_arm64 and
win_amd64. All inputs are checked before anything is written: each must exist, its
release.json must match this checkout's core build, and without --validation trust production
keys only. --output/<sdk-version>/ is emptied first and then holds exactly the packages listed
in platform-manifest.json. --validation packages QA builds (tools/build_platforms.py
--validation): from .build/platforms-validation to .build/multiplatform-releases-validation,
every archive named -validation and the manifest saying validation: true.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from native_build import ROOT, release_metadata, require_release_keys
import release_packages as packages


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--input', type=Path, help='tools/build_platforms.py --output of every target '
                        '(default: .build/platforms, with --validation .build/platforms-validation)')
    parser.add_argument('--output', type=Path, help='default: .build/multiplatform-releases, '
                        'with --validation .build/multiplatform-releases-validation')
    parser.add_argument('--validation', action='store_true', help='QA packages whose cores may trust the test entitlement key')
    args = parser.parse_args()
    suffix = '-validation' if args.validation else ''
    args.input = args.input or ROOT / '.build' / ('platforms' + suffix)
    args.output = args.output or ROOT / '.build' / ('multiplatform-releases' + suffix)
    require_release_keys(args.validation)
    release = release_metadata()
    names = packages.package_names(release)
    source = args.input
    xcframework = source / 'apple' / 'CFisica.xcframework'
    jni = source / 'android' / 'jni'
    aar = source / 'android' / names['aar']
    cores = [source / packages.core_name(release, target) for target in packages.DESKTOP_TARGETS]
    windows = {target: source / target / 'fisica_core.dll' for target in packages.WINDOWS_ARCHITECTURES}
    metadata = [source / name / 'release.json' for name in ('apple', 'android', *packages.DESKTOP_TARGETS)]
    packages.require_build_kind(metadata, args.validation)
    packages.require([xcframework / 'Info.plist', aar, *cores, *windows.values(), *metadata,
                      *(jni / abi / 'libfisica_core.so' for abi in packages.ANDROID_ABIS)],
                     f'run tools/build_platforms.py{" --validation" if args.validation else ""} --target TARGET for every target')
    built = packages.prebuilt_release(metadata, args.validation)

    output = args.output / release['sdk_version']
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True)
    artifacts = [packages.copy_package(path, output, built, args.validation) for path in (*cores, aar)]
    artifacts += [
        packages.swift_package(output, xcframework, built, args.validation),
        packages.kotlin_sources(output, built, args.validation),
        packages.dotnet_sources(output, built, args.validation),
        packages.flutter_package(output, xcframework, jni, windows, built, args.validation),
        packages.flutter_transport_package(output, built, args.validation),
        packages.partner_samples_package(output, release, args.validation),
    ]
    manifest = {'sdk_version': release['sdk_version'], 'core_version': release['core_version'],
                'abi_minor': release['abi_minor'], 'entitlement': built['entitlement'],
                'validation': built['validation'],
                'artifacts': {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(artifacts)}}
    (output / 'platform-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('Platform packages:', output)


if __name__ == '__main__':
    main()
