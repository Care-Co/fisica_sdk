"""Build and audit this host's binary-only Python wheel and core ZIP on a trusted host.

--output/<sdk-version>/ is emptied first and then holds exactly the wheel, the core ZIP
(tools/release_packages.py) and artifact-manifest.json with their SHA-256. Build each
platform into its own --output. The Flutter, Swift, Kotlin and C# packages come from
tools/build_platforms.py and tools/package_platforms.py.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from native_build import ROOT, build_native, platform_tag, release_metadata, require_release_keys
from audit_artifacts import audit_archive
import release_packages as packages


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--output', default=str(ROOT / '.build' / 'releases'))
    parser.add_argument('--validation', action='store_true', help='QA-only build that may trust test entitlement keys')
    args = parser.parse_args()
    validation = require_release_keys(args.validation)
    release = release_metadata()
    output = Path(args.output).resolve() / release['sdk_version']
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True)
    # setup.py reads the key choice from the environment require_release_keys set.
    subprocess.run([sys.executable, '-m', 'build', '--wheel', '--outdir', str(output)], check=True, cwd=ROOT)
    wheels = sorted(output.glob('*.whl'))
    if len(wheels) != 1:
        raise SystemExit(f'Expected one wheel in {output}, found {[path.name for path in wheels]}')
    audit_archive(wheels[0], validation=args.validation)
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        library = build_native(directory)
        core = packages.core_package(output, library, packages.read_release(directory / 'release.json'),
                                     platform_tag(), args.validation)
    artifacts = [wheels[0], core]
    manifest = {'sdk_version': release['sdk_version'], 'license': release['license'],
                'core_version': release['core_version'],
                'abi': {'major': release['abi_major'], 'minor': release['abi_minor']},
                'entitlement': release['entitlement'], 'validation_build': validation,
                'artifacts': {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in artifacts}}
    (output / 'artifact-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Binary artifacts audited: {output}')


if __name__ == '__main__':
    main()
