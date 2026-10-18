"""Build and audit this host's binary-only Python wheel and core ZIP on a trusted host.

--output/<sdk-version>/ is emptied first and then holds exactly the wheel, the core ZIP
(tools/release_packages.py) and artifact-manifest.json with their SHA-256. Build each
platform into its own --output. The Flutter, Swift, Kotlin and C# packages come from
tools/build_platforms.py and tools/package_platforms.py.

On macOS the wheel and core are universal2 (arm64 and x86_64 in one core); --platform
macosx_11_0_arm64 or macosx_11_0_x86_64 builds one architecture, --platform win_amd64 or
win_arm64 cross-builds Windows (with CXX). --validation builds trust the repository test key
too: they go to .build/releases-validation/ and are named so (fisica-<v>+validation-*.whl,
fisica-core-<core>-<platform>-validation.zip).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from native_build import ROOT, build_native, build_targets, platform_tag, release_metadata, require_release_keys
from audit_artifacts import audit_archive
import release_packages as packages


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--output', type=Path, help='default: .build/releases, with --validation .build/releases-validation')
    parser.add_argument('--platform', choices=build_targets(),
                        help='wheel platform tag (default: this host; universal2 on macOS)')
    parser.add_argument('--validation', action='store_true', help='QA-only build that may trust test entitlement keys')
    args = parser.parse_args()
    if args.platform:
        # Also reaches the wheel build setup.py runs in another process.
        os.environ['FISICA_BUILD_PLATFORM'] = args.platform
    target = platform_tag()
    validation = require_release_keys(args.validation)
    release = release_metadata()
    output = (args.output or ROOT / '.build' / ('releases-validation' if args.validation else 'releases'))
    output = output.resolve() / release['sdk_version']
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True)
    # setup.py reads the key choice from the environment require_release_keys set.
    subprocess.run([sys.executable, '-m', 'build', '--wheel', '--outdir', str(output)], check=True, cwd=ROOT)
    wheels = sorted(output.glob('*.whl'))
    if len(wheels) != 1:
        raise SystemExit(f'Expected one wheel in {output}, found {[path.name for path in wheels]}')
    if not wheels[0].name.endswith(f'-py3-none-{target}.whl'):
        raise SystemExit(f'{wheels[0].name} is not tagged {target}')
    audit_archive(wheels[0], validation=args.validation)
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        library = build_native(directory)
        core = packages.core_package(output, library, packages.read_release(directory / 'release.json'),
                                     target, args.validation)
    artifacts = [wheels[0], core]
    manifest = {'sdk_version': release['sdk_version'], 'license': release['license'],
                'core_version': release['core_version'], 'platform': target,
                'abi': {'major': release['abi_major'], 'minor': release['abi_minor']},
                'entitlement': release['entitlement'], 'validation': validation,
                'artifacts': {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in artifacts}}
    (output / 'artifact-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Binary artifacts audited: {output}')


if __name__ == '__main__':
    main()
