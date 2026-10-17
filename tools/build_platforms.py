"""Trusted multi-platform binary builder. Never compiles on a customer's device.

One run builds one target into --output (default .build/platforms):

  apple     apple/CFisica.xcframework, apple/release.json, apple/fisica-swift-<v>.zip
  android   android/jni/<abi>/*.so, android/classes.jar, android/release.json,
            android/fisica-scale-<v>.aar, android/fisica-kotlin-<v>.zip
  desktop   <target>/<core library>, <target>/release.json, fisica-core-<core>-<target>.zip

A run first empties its target's output, so nothing from an earlier build is packaged.
tools/package_platforms.py makes the partner packages from these; both write every archive
with tools/release_packages.py.
"""
import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess

from native_build import ROOT, build_native, prepare_release, release_metadata, require_release_keys
from audit_artifacts import validate_android_library
import release_packages as packages

TARGETS = ('apple', 'android', *packages.DESKTOP_TARGETS)
CORE_SOURCES = ROOT / 'native' / 'src'
PUBLIC_INCLUDE = ROOT / 'native' / 'include'


def run(command):
    subprocess.run([str(part) for part in command], check=True)


def fresh(directory):
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    return directory


def static_core(generated, name, target, sdk):
    """The core as a static library for one Apple clang target."""
    objects = []
    for source in sorted(CORE_SOURCES.glob('*.cpp')):
        obj = generated / f'{name}-{source.stem}.o'
        run(['c++', '-std=c++17', '-O2', '-ffp-contract=off', '-fvisibility=hidden', '-fvisibility-inlines-hidden',
             '-DFISICA_CORE_STATIC', '-DFISICA_CORE_BUILD', '-target', target, '-isysroot', sdk,
             '-I' + str(PUBLIC_INCLUDE), '-I' + str(generated), '-c', source, '-o', obj])
        objects.append(obj)
    # One relocatable object first: `ld -r` turns the hidden symbols (Monocypher, internal helpers) into local
    # ones. In an archive of separate objects they stay linkable, so an app that defines crypto_ed25519_check
    # itself would be linked instead of the core's signature check, and an app with its own Monocypher would
    # clash. Only the fisica_core_* API stays global.
    merged = generated / f'{name}-core.o'
    run(['c++', '-target', target, '-isysroot', sdk, '-r', '-nostdlib', '-o', merged, *objects])
    # Weak definitions of the C++ standard library (vtables, type info) may stay: any copy is the same one.
    exported = [line for line in subprocess.check_output(['nm', '-g', '-U', '-m', str(merged)], text=True).splitlines()
                if line.strip()]
    stray = [line.split()[-1] for line in exported if not line.split()[-1].startswith('_fisica_core_')
             and not ('weak external' in line and 'NSt3__' in line.split()[-1])]
    if stray:
        raise RuntimeError(f'{merged.name} exports more than the core API: {", ".join(stray[:5])}')
    library = generated / f'{name}.a'
    run(['libtool', '-static', '-o', library, merged])
    return library


def apple(output, validation):
    output = fresh(output)
    generated = output / 'generated'
    release = prepare_release(generated)
    xcframework = output / 'CFisica.xcframework'
    xcframework.mkdir()
    developer = Path(os.environ.get('FISICA_XCODE_DEVELOPER', '/Applications/Xcode.app/Contents/Developer'))
    macos_sdk = Path(subprocess.check_output(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], text=True).strip())
    platforms = developer / 'Platforms'
    # (library identifier, platform, variant, SDK, clang targets)
    variants = [
        ('macos-arm64_x86_64', 'macos', None, macos_sdk, ['arm64-apple-macos11', 'x86_64-apple-macos11']),
        ('ios-arm64', 'ios', None, platforms / 'iPhoneOS.platform/Developer/SDKs/iPhoneOS.sdk', ['arm64-apple-ios13']),
        ('ios-arm64_x86_64-simulator', 'ios', 'simulator', platforms / 'iPhoneSimulator.platform/Developer/SDKs/iPhoneSimulator.sdk',
         ['arm64-apple-ios13-simulator', 'x86_64-apple-ios13-simulator']),
    ]
    libraries = []
    for identifier, platform, variant, sdk, targets in variants:
        if not sdk.is_dir():
            raise RuntimeError(f'Apple SDK missing: {sdk}')
        directory = xcframework / identifier
        directory.mkdir()
        thin = [static_core(generated, identifier + '-' + target.split('-')[0], target, sdk) for target in targets]
        library = directory / 'libfisica_core.a'
        if len(thin) > 1:
            run(['lipo', '-create', *thin, '-output', library])
        else:
            shutil.copyfile(thin[0], library)
        headers = directory / 'Headers'
        headers.mkdir()
        shutil.copyfile(packages.HEADER, headers / 'fisica_core.h')
        (headers / 'module.modulemap').write_text('module CFisica { header "fisica_core.h" export * }\n')
        item = {'LibraryIdentifier': identifier, 'LibraryPath': library.name, 'HeadersPath': 'Headers',
                'SupportedArchitectures': [target.split('-')[0] for target in targets], 'SupportedPlatform': platform}
        if variant:
            item['SupportedPlatformVariant'] = variant
        libraries.append(item)
    (xcframework / 'Info.plist').write_bytes(plistlib.dumps(
        {'CFBundlePackageType': 'XFWK', 'XCFrameworkFormatVersion': '1.0', 'AvailableLibraries': libraries}))
    packages.write_release(output / 'release.json', release)
    # swift test (tools/verify_bindings.py swift) builds the wrapper against this XCFramework.
    local = packages.WRAPPERS / 'swift' / 'CFisica.xcframework'
    shutil.rmtree(local, ignore_errors=True)
    shutil.copytree(xcframework, local)
    packages.swift_package(output, xcframework, release, validation)


ANDROID_TARGETS = {'arm64-v8a': 'aarch64-linux-android23', 'armeabi-v7a': 'armv7a-linux-androideabi23',
                   'x86_64': 'x86_64-linux-android23'}


def kotlin_classes(jar):
    """Compile the public Kotlin sources to classes.jar with Gradle's Kotlin compiler; no build toolchain ships."""
    gradle = Path(os.environ.get('FISICA_GRADLE_LIB', '/opt/homebrew/opt/gradle/libexec/lib'))
    stdlib = next(gradle.glob('kotlin-stdlib-[0-9]*.jar'))
    android_jar = Path(os.environ.get('ANDROID_SDK_ROOT', str(Path.home() / 'Library/Android/sdk'))) / 'platforms/android-35/android.jar'
    sources = [path for path, _ in packages.tracked_files(ROOT / 'wrappers/kotlin/src/main/kotlin') if path.suffix == '.kt']
    run(['java', '-cp', str(gradle / '*'), 'org.jetbrains.kotlin.cli.jvm.K2JVMCompiler', '-no-stdlib', '-no-reflect',
         '-classpath', str(stdlib) + os.pathsep + str(android_jar), '-jvm-target', '1.8', '-d', jar, *sources])


def android(output, ndk, validation):
    output = fresh(output)
    generated = output / 'generated'
    release = prepare_release(generated)
    compiler = next((ndk / 'toolchains/llvm/prebuilt').iterdir()) / 'bin'
    jni = output / 'jni'
    sources = sorted(CORE_SOURCES.glob('*.cpp'))
    for abi, target in ANDROID_TARGETS.items():
        directory = jni / abi
        directory.mkdir(parents=True)
        # 16 KB page support (Google Play, Android 15+), also fine on 4 KB devices: LOAD segments
        # aligned to 16 KB and the RELRO segment ending on a 16 KB boundary. NDK r27 and older need
        # both flags; r28+ defaults to them.
        common = [compiler / (target + '-clang++'), '-std=c++17', '-O2', '-shared', '-fPIC', '-fvisibility=hidden',
                  '-ffp-contract=off', '-static-libstdc++', '-Wl,--exclude-libs,ALL',
                  '-Wl,-z,max-page-size=16384', '-Wl,-z,common-page-size=16384', '-I' + str(PUBLIC_INCLUDE)]
        run([*common, '-DFISICA_CORE_BUILD', '-I' + str(generated), *sources,
             '-Wl,-soname,libfisica_core.so', '-o', directory / 'libfisica_core.so'])
        run([*common, ROOT / 'wrappers/kotlin/bridge/fisica_jni.cpp', '-L' + str(directory), '-lfisica_core',
             '-Wl,-soname,libfisica_jni.so', '-o', directory / 'libfisica_jni.so'])
        for library in ('libfisica_core.so', 'libfisica_jni.so'):
            validate_android_library((directory / library).read_bytes(), abi)
    classes = output / 'classes.jar'
    kotlin_classes(classes)
    packages.write_release(output / 'release.json', release)
    packages.kotlin_aar(output, classes, jni, release, validation)
    packages.kotlin_sources(output, release, validation)


def desktop(output, target, validation):
    directory = fresh(output / target)
    (output / packages.core_name(release_metadata(), target)).unlink(missing_ok=True)
    previous = os.environ.get('FISICA_BUILD_PLATFORM')
    os.environ['FISICA_BUILD_PLATFORM'] = target
    try:
        core = build_native(directory)
    finally:
        if previous is None:
            os.environ.pop('FISICA_BUILD_PLATFORM', None)
        else:
            os.environ['FISICA_BUILD_PLATFORM'] = previous
    packages.core_package(output, core, packages.read_release(directory / 'release.json'), target, validation)


def artifact_paths(output, targets):
    """The packages each target writes. Names come from release metadata so CI never hardcodes a version."""
    release = release_metadata()
    names = packages.package_names(release)
    paths = []
    for target in targets:
        if target == 'apple':
            paths.append(output / 'apple' / names['swift'])
        elif target == 'android':
            paths += [output / 'android' / names['aar'], output / 'android' / names['kotlin']]
        else:
            paths.append(output / packages.core_name(release, target))
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--target', choices=TARGETS)
    group.add_argument('--print-artifacts', nargs='+', choices=TARGETS, metavar='TARGET')
    parser.add_argument('--output', type=Path, default=ROOT / '.build/platforms')
    parser.add_argument('--ndk', type=Path, default=Path(os.environ.get(
        'FISICA_ANDROID_NDK', Path.home() / 'Library/Android/sdk/ndk/26.3.11579264')))
    parser.add_argument('--validation', action='store_true', help='QA-only build that may trust test entitlement keys')
    args = parser.parse_args()
    if args.print_artifacts:
        cwd = Path.cwd()
        print(' '.join(path.relative_to(cwd).as_posix() if path.is_relative_to(cwd) else str(path)
                       for path in artifact_paths(args.output, args.print_artifacts)))
        return
    require_release_keys(args.validation)
    args.output.mkdir(parents=True, exist_ok=True)
    if args.target == 'apple':
        apple(args.output / 'apple', args.validation)
    elif args.target == 'android':
        android(args.output / 'android', args.ndk, args.validation)
    else:
        desktop(args.output, args.target, args.validation)


if __name__ == '__main__':
    main()
