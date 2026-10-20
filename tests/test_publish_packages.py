"""Publishing SDK packages to the CDN: customer builds only, never replacing a published file."""
from datetime import datetime, timezone
import io
import json

import pytest

from tools import publish_packages as publisher

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


class Missing(Exception):
    response = {'Error': {'Code': '404'}}


class Exists(Exception):
    response = {'Error': {'Code': 'PreconditionFailed'}}


class FakeS3:
    def __init__(self):
        self.objects = {}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise Missing()
        return {'Metadata': self.objects[Key]['Metadata']}

    def put_object(self, Bucket, Key, Body, ContentType, CacheControl, Metadata, IfNoneMatch=None):
        if IfNoneMatch == '*' and Key in self.objects:
            raise Exists()
        data = Body if isinstance(Body, bytes) else Body.read()
        self.objects[Key] = {'Body': data, 'ContentType': ContentType, 'CacheControl': CacheControl, 'Metadata': Metadata}

    def get_object(self, Bucket, Key):
        return {'Body': io.BytesIO(self.objects[Key]['Body'])}


@pytest.fixture
def release(tmp_path, monkeypatch):
    monkeypatch.setattr(publisher, 'audit_archive', lambda path, validation=False: None)  # audits are tested elsewhere
    monkeypatch.setattr(publisher, 'commit', lambda: 'abc123')
    packages, wheels = tmp_path / 'packages', tmp_path / 'wheels'
    packages.mkdir()
    (wheels / '3.0.0').mkdir(parents=True)
    names = ['fisica-core-2.0.0-win_amd64.zip', 'fisica-swift-0.3.0.zip', 'fisica-scale-0.3.0.aar',
             'fisica-flutter-0.6.0-platforms.zip', 'fisica-partner-samples-3.0.0.zip']
    for name in names:
        (packages / name).write_bytes(name.encode())
    (packages / 'platform-manifest.json').write_text(json.dumps({'validation': False, 'artifacts': {n: 'x' for n in names}}))
    (wheels / '3.0.0' / 'fisica-3.0.0-py3-none-win_amd64.whl').write_bytes(b'wheel')
    (wheels / '3.0.0' / 'fisica-core-2.0.0-win_amd64.zip').write_bytes(b'not published from here')
    return packages, wheels


def test_the_plan_puts_each_package_in_its_folder(release):
    entries = {e['name']: e for e in publisher.plan('3.0.0', *[release[0], [release[1]]])}
    assert entries['fisica-3.0.0-py3-none-win_amd64.whl']['url'] == \
        'https://cdn.carencoinc.com/public/sdk/3.0.0/python/fisica-3.0.0-py3-none-win_amd64.whl'
    assert entries['fisica-3.0.0-py3-none-win_amd64.whl']['platform'] == 'win_amd64'
    assert entries['fisica-scale-0.3.0.aar']['key'] == 'public/sdk/3.0.0/android/fisica-scale-0.3.0.aar'
    assert entries['fisica-swift-0.3.0.zip']['platform'] is None
    assert entries['fisica-core-2.0.0-win_amd64.zip']['path'].parent == release[0]  # build_release's core ZIP is left out


def test_publishing_uploads_once_and_points_latest_to_the_manifest(release):
    s3 = FakeS3()
    entries = publisher.plan('3.0.0', release[0], [release[1]])
    results = publisher.publish('3.0.0', entries, publisher.Bucket(s3), NOW)
    assert all(outcome in ('uploaded', 'updated') for outcome in results.values())
    wheel = s3.objects['public/sdk/3.0.0/python/fisica-3.0.0-py3-none-win_amd64.whl']
    assert wheel['CacheControl'] == 'public, max-age=31536000, immutable' and len(wheel['Metadata']['sha256']) == 64
    manifest = json.loads(s3.objects['public/sdk/3.0.0/manifest.json']['Body'])
    assert manifest['release'] == '3.0.0' and manifest['commit'] == 'abc123' and len(manifest['files']) == 6
    assert manifest['published_at'] == '2026-10-20T12:00:00Z'
    latest = s3.objects['public/sdk/latest.json']
    assert latest['CacheControl'] == 'public, max-age=300'
    assert json.loads(latest['Body'])['manifest'] == 'https://cdn.carencoinc.com/public/sdk/3.0.0/manifest.json'

    # Running the same release again changes nothing published.
    again = publisher.publish('3.0.0', entries, publisher.Bucket(s3), datetime(2026, 10, 21, tzinfo=timezone.utc))
    assert again['public/sdk/3.0.0/manifest.json'] == 'same'
    assert json.loads(s3.objects['public/sdk/latest.json']['Body'])['published_at'] == '2026-10-20T12:00:00Z'
    assert {outcome for key, outcome in again.items() if key.endswith(('.zip', '.whl', '.aar'))} == {'same'}


def test_a_published_file_is_never_replaced(release):
    s3 = FakeS3()
    publisher.publish('3.0.0', publisher.plan('3.0.0', release[0], [release[1]]), publisher.Bucket(s3), NOW)
    (release[0] / 'fisica-swift-0.3.0.zip').write_bytes(b'rebuilt')
    with pytest.raises(SystemExit, match='different content'):
        publisher.publish('3.0.0', publisher.plan('3.0.0', release[0], [release[1]]), publisher.Bucket(s3), NOW)


def test_qa_builds_and_bad_release_names_are_refused(release):
    packages, wheels = release
    with pytest.raises(SystemExit, match='3.0.0'):
        publisher.plan('latest', packages, [wheels])
    (wheels / '3.0.0' / 'fisica-3.0.0+validation-py3-none-win_arm64.whl').write_bytes(b'qa')
    with pytest.raises(SystemExit, match='validation'):
        publisher.plan('3.0.0', packages, [wheels])
    (packages / 'platform-manifest.json').write_text(json.dumps({'validation': True, 'artifacts': {}}))
    with pytest.raises(SystemExit, match='validation'):
        publisher.plan('3.0.0', packages, [])
