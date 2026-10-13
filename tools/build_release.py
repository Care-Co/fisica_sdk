"""Build and audit binary-only Python/native SDK artifacts on a trusted host."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile
import json
import os
import hashlib
import uuid
from native_build import ROOT, build_native, release_metadata, platform_tag
from audit_artifacts import audit_archive


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default=str(ROOT / '.build' / 'releases'))
    parser.add_argument('--partner-id', help='Opaque partner identifier; no personal information')
    parser.add_argument('--distribution-id', help='Delivery identifier; defaults to a new UUID for a partner build')
    args = parser.parse_args()
    if args.distribution_id and not args.partner_id:
        parser.error('--distribution-id requires --partner-id')
    if args.partner_id:
        os.environ['FISICA_PARTNER_ID'] = args.partner_id
        os.environ['FISICA_DISTRIBUTION_ID'] = args.distribution_id or str(uuid.uuid4())
    release = release_metadata()
    output = Path(args.output).resolve() / release['sdk_version']
    if release['distribution']['distribution_id']:
        output /= release['distribution']['distribution_id']
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, '-m', 'build', '--wheel', '--outdir', str(output)], check=True, cwd=ROOT)
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        library = build_native(directory)
        archive_path = output / f"fisica-core-{release['core_version']}-{platform_tag()}.zip"
        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.write(library, library.name)
            archive.write(directory / 'release.json', 'release.json')
            archive.write(ROOT / 'native' / 'include' / 'fisica_core.h', 'fisica_core.h')
            archive.write(ROOT / 'LICENSE', 'LICENSE')
        audit_archive(archive_path)
    flutter = ROOT / 'wrappers' / 'flutter'
    flutter_archive = output / ("fisica-flutter-" + release['wrappers']['flutter']['version'] + '.zip')
    with zipfile.ZipFile(flutter_archive, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.write(ROOT / 'LICENSE', 'LICENSE')
        for file in [flutter / 'pubspec.yaml',flutter / 'wrapper.json',flutter / 'README.md',*sorted((flutter / 'lib').rglob('*.dart'))]:
            archive.write(file,file.relative_to(flutter).as_posix())
    audit_archive(flutter_archive)
    for wheel in output.glob('*.whl'):
        audit_archive(wheel)
    artifacts = sorted([*output.glob('*.whl'), *output.glob('*.zip')])
    manifest = {'sdk_version': release['sdk_version'], 'license': release['license'],
                'core_version': release['core_version'],
                'abi': {'major': release['abi_major'], 'minor': release['abi_minor']},
                'distribution': release['distribution'],
                'artifacts': {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in artifacts}}
    (output / 'delivery-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Binary artifacts audited: {output}')


if __name__ == '__main__':
    main()
