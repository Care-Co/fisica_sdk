"""Publish audited customer SDK packages to the CDN (S3 carenco-prod/public/sdk, served by cdn.carencoinc.com).

Takes the packages of one release: the multi-platform packages of tools/package_platforms.py (with
their platform-manifest.json) and the Python wheels of tools/build_release.py. Every file is audited
as a customer artifact first; QA (validation) builds are refused. Without --publish it only prints
what it would upload. With --publish each file goes to

    s3://carenco-prod/public/sdk/<release>/<category>/<file>  ->  https://cdn.carencoinc.com/public/sdk/...

cached for a year as immutable. A file already there is skipped when it is the same (a rerun) and is an
error when it differs: published files are never replaced (the bucket keeps old versions, so
overwrites would also cost storage). Then <release>/manifest.json lists the files with size and
SHA-256, and public/sdk/latest.json (cached 5 minutes) points to it; the developer portal reads both.

    python tools/publish_packages.py --release 3.0.0 --packages .build/multiplatform-releases/3.0.0 \
        --wheels .build/releases/3.0.0 ...
    python tools/publish_packages.py ... --publish   # needs AWS credentials (boto3)

Run by .github/workflows/release.yml on demand only, never on a push.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_artifacts import audit_archive, validation_named  # noqa: E402

BUCKET = 'carenco-prod'
PREFIX = 'public/sdk'
CDN = 'https://cdn.carencoinc.com/public/sdk'
REGION = 'ap-northeast-2'
IMMUTABLE = 'public, max-age=31536000, immutable'
LATEST_CACHE = 'public, max-age=300'
RELEASE = re.compile(r'^\d+\.\d+\.\d+$')
PLATFORMS = ('macosx_11_0_universal2', 'win_amd64', 'win_arm64')
CONTENT_TYPES = {'.zip': 'application/zip', '.json': 'application/json'}


def category(name):
    """Folder of a package on the CDN, by its file name."""
    if name.endswith('.whl'):
        return 'python'
    rules = (('fisica-core-', 'core'), ('fisica-swift-', 'swift'), ('fisica-scale-', 'android'),
             ('fisica-kotlin-', 'android'), ('fisica-dotnet-', 'dotnet'), ('fisica-flutter-', 'flutter'),
             ('fisica-partner-samples-', 'samples'))
    for prefix, folder in rules:
        if name.startswith(prefix):
            return folder
    raise ValueError(f'{name}: not an SDK package')


def platform(name):
    found = [target for target in PLATFORMS if target in name]
    return found[0] if found else None


def collect(packages, wheel_directories):
    """The archives of package_platforms (listed in its platform-manifest.json) and the wheels of build_release
    (its core ZIPs are left out: package_platforms makes the published ones)."""
    packages = Path(packages)
    listed = json.loads((packages / 'platform-manifest.json').read_text())
    if listed.get('validation'):
        raise SystemExit(f'{packages}: a QA (validation) build is never published')
    files = {name: packages / name for name in listed['artifacts']}
    for directory in wheel_directories:
        for path in sorted(Path(directory).rglob('*.whl')):
            if path.name in files:
                raise SystemExit(f'{path.name} appears twice: {files[path.name]} and {path}')
            files[path.name] = path
    missing = [str(path) for path in files.values() if not path.is_file()]
    if missing:
        raise SystemExit('Missing: ' + ', '.join(missing))
    return files


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as file:
        for chunk in iter(lambda: file.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def plan(release, packages, wheel_directories=()):
    """Audit every package as a customer artifact and say where it goes."""
    if not RELEASE.match(release):
        raise SystemExit(f'--release must look like 3.0.0, not {release!r}')
    entries = []
    for name, path in collect(packages, wheel_directories).items():
        if validation_named(path):
            raise SystemExit(f'{name}: a QA (validation) build is never published')
        audit_archive(path, validation=False)  # raises on anything a customer must not get
        folder = category(name)
        entries.append({'category': folder, 'name': name, 'path': path,
                        'key': f'{PREFIX}/{release}/{folder}/{name}',
                        'url': f'{CDN}/{release}/{folder}/{name}',
                        'size': path.stat().st_size, 'sha256': sha256(path), 'platform': platform(name)})
    return entries


def commit():
    try:
        return subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True, cwd=Path(__file__).resolve().parents[1]).strip()
    except (OSError, subprocess.CalledProcessError):
        return os.environ.get('GITHUB_SHA', '')


def manifest(release, entries, now=None):
    from native_build import release_metadata
    meta = release_metadata()
    return {'release': release, 'sdk_version': meta['sdk_version'], 'core_version': meta['core_version'],
            'commit': commit(), 'published_at': (now or datetime.now(timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'base_url': f'{CDN}/{release}/',
            'files': [{key: entry[key] for key in ('category', 'name', 'url', 'size', 'sha256', 'platform')}
                      for entry in sorted(entries, key=lambda e: (e['category'], e['name']))]}


class Bucket:
    """The few S3 calls publishing needs, on a boto3 client (tests pass a fake)."""

    def __init__(self, client, bucket=BUCKET):
        self.client, self.bucket = client, bucket

    def existing_sha256(self, key):
        """The sha256 recorded on an existing object, '' when it has none, None when there is no object."""
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as error:  # botocore ClientError: 404 when there is no object (ListBucket allowed)
            if getattr(error, 'response', {}).get('Error', {}).get('Code') in ('404', 'NoSuchKey', 'NotFound'):
                return None
            raise
        return head.get('Metadata', {}).get('sha256', '')

    def put(self, key, body, content_type, cache_control, digest, replace=False):
        options = {} if replace else {'IfNoneMatch': '*'}  # never replace a published file
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type,
                               CacheControl=cache_control, Metadata={'sha256': digest}, **options)


def upload_once(bucket, key, body, content_type, digest):
    """Upload unless the same content is there; refuse different content under a published key."""
    existing = bucket.existing_sha256(key)
    if existing == digest:
        return 'same'
    if existing is not None:
        raise SystemExit(f'{key} is already published with different content; publish a new release instead')
    bucket.put(key, body, content_type, IMMUTABLE, digest)
    return 'uploaded'


def publish(release, entries, bucket, now=None):
    results = {}
    for entry in entries:
        content_type = CONTENT_TYPES.get(Path(entry['name']).suffix, 'application/octet-stream')
        with open(entry['path'], 'rb') as body:
            results[entry['key']] = upload_once(bucket, entry['key'], body, content_type, entry['sha256'])
    document = manifest(release, entries, now)
    key = f'{PREFIX}/{release}/manifest.json'
    existing = bucket.existing_sha256(key)
    if existing is None:
        data = (json.dumps(document, indent=2) + '\n').encode()
        bucket.put(key, data, 'application/json', IMMUTABLE, hashlib.sha256(data).hexdigest())
        results[key] = 'uploaded'
    else:
        # A rerun of the same release: the manifest stays as first published, and must list the same files.
        current = json.loads(bucket.client.get_object(Bucket=bucket.bucket, Key=key)['Body'].read())
        if {(f['name'], f['sha256']) for f in current['files']} != {(f['name'], f['sha256']) for f in document['files']}:
            raise SystemExit(f'{key} lists other files than this run; publish a new release instead')
        document = current
        results[key] = 'same'
    latest = {'release': release, 'manifest': f'{CDN}/{release}/manifest.json', 'published_at': document['published_at']}
    data = (json.dumps(latest, indent=2) + '\n').encode()
    bucket.put(f'{PREFIX}/latest.json', data, 'application/json', LATEST_CACHE, hashlib.sha256(data).hexdigest(), replace=True)
    results[f'{PREFIX}/latest.json'] = 'updated'
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--release', required=True, help='release folder on the CDN, e.g. 3.0.0')
    parser.add_argument('--publish', action='store_true', help='upload (default: print the plan only)')
    parser.add_argument('--packages', required=True, type=Path,
                        help='tools/package_platforms.py output for this SDK version (has platform-manifest.json)')
    parser.add_argument('--wheels', nargs='*', default=[], type=Path, help='tools/build_release.py outputs with the wheels')
    args = parser.parse_args()
    entries = plan(args.release, args.packages, args.wheels)
    for entry in entries:
        print(f"{entry['category']:8} {entry['size'] / 1e6:7.2f} MB  {entry['url']}")
    if not args.publish:
        print(f'{len(entries)} packages audited; dry run, nothing uploaded (add --publish)')
        return
    import boto3
    results = publish(args.release, entries, Bucket(boto3.client('s3', region_name=REGION)))
    for key, outcome in results.items():
        print(f'{outcome:9} s3://{BUCKET}/{key}')
    print(f'Release {args.release}: {CDN}/{args.release}/manifest.json')


if __name__ == '__main__':
    main()
