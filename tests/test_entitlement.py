"""Entitlement: status and warnings only, and no usage tracking. Real C ABI and a mock server."""
import json
from pathlib import Path
import time
import warnings
import pytest
from fisica_sdk import (Entitlement, FisicaEntitlementWarning, FisicaSDK, MockTransport, NativeCore,
                        NativeEntitlement)
from tools.entitlement_dev import MockEntitlementServer, issue_token, public_key, sign

NOW = 1_800_000_000
DAY = 86400
ACCEPTED, IGNORED_OLDER, REJECTED_FORMAT, REJECTED_KEY, REJECTED_SIGNATURE = 1, 2, 3, 4, 5


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


def quiet(**options):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', FisicaEntitlementWarning)
        return Entitlement(**options)


@pytest.fixture
def server():
    mock = MockEntitlementServer(clock=Clock())
    mock.add_contract('acme', 'ctr-1')
    url = mock.start()
    yield mock, url
    mock.stop()


def test_test_signer_matches_rfc8032_vectors():
    secret = bytes.fromhex('9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60')
    assert public_key(secret).hex() == 'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a'
    assert sign(secret, b'').hex().startswith('e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155')


def test_unverified_context_keeps_base_license_and_attributes_nothing():
    with NativeEntitlement() as context:
        status = context.status(NOW)
        assert (status['base_license'], status['verification'], status['contract_state']) == (1, 0, 0)
        assert status['warning_bits'] == 1 and status['company'] == ''
        assert context.export_state() == b'FSS1\n'


def test_sdk_has_no_usage_tracking_surface():
    """Policy of 2026-10-14: nothing records or reports whether, when or how often the SDK is used."""
    library = NativeCore()._lib
    for name in ('mark_used', 'report', 'report_done'):
        assert not hasattr(library, 'fisica_core_entitlement_' + name)
    for name in ('mark_used', 'report', 'report_done', 'pending_report'):
        assert not hasattr(NativeEntitlement, name) and not hasattr(Entitlement, name)
    header = (Path(__file__).resolve().parents[1] / 'native' / 'include' / 'fisica_core.h').read_text(encoding='utf-8')
    assert 'mark_used' not in header and 'entitlement_report' not in header
    token = issue_token('acme', 'ctr-1', NOW)
    with NativeEntitlement() as context:
        context.import_token(token, NOW)
        for later in (NOW + 5, NOW + 90 * DAY):
            context.status(later)
        # The only stored state is the signed token itself: no times of use, no counters.
        assert context.export_state() == b'FSS1\nC ' + token.encode() + b'\n'


def test_valid_token_is_fresh_and_three_states_are_independent():
    with NativeEntitlement() as context:
        assert context.import_token(issue_token('acme', 'ctr-1', NOW, kind='special', contract_end=NOW + 10 * DAY, grants='oem,rebrand'), NOW) == ACCEPTED
        status = context.status(NOW)
        assert (status['verification'], status['contract_state'], status['contract_kind']) == (1, 1, 2)
        assert (status['company'], status['contract'], status['grants']) == ('acme', 'ctr-1', 'oem,rebrand')
        assert status['refresh_by'] == NOW + 30 * DAY and status['contract_end'] == NOW + 10 * DAY
        # Contract ended, confirmation still fresh: base license is untouched.
        ended = context.status(NOW + 11 * DAY)
        assert (ended['base_license'], ended['verification'], ended['contract_state']) == (1, 1, 3)
        # Confirmation older than 30 days is "refresh due", not an expired license or contract.
        stale = NativeEntitlement()
        stale.import_token(issue_token('acme', 'ctr-2', NOW), NOW)
        late = stale.status(NOW + 31 * DAY)
        assert (late['base_license'], late['verification'], late['contract_state'], late['warning_bits'] & 2) == (1, 2, 1, 2)


def test_server_cannot_grant_more_than_thirty_days():
    with NativeEntitlement() as context:
        context.import_token(issue_token('acme', 'ctr-1', NOW, refresh_by=NOW + 365 * DAY), NOW)
        assert context.status(NOW)['refresh_by'] == NOW + 30 * DAY


@pytest.mark.parametrize('mutate,expected', [
    (lambda t: t[:-4] + ('AAAA' if not t.endswith('AAAA') else 'BBBB'), REJECTED_SIGNATURE),
    (lambda t: t.replace('.', '.e', 1), REJECTED_FORMAT),
    (lambda t: 'FSE2' + t[4:], REJECTED_FORMAT),
    (lambda t: t + '.extra', REJECTED_FORMAT),
    (lambda t: '', REJECTED_FORMAT),
])
def test_tampered_or_malformed_tokens_are_unverified_not_errors(mutate, expected):
    token = issue_token('acme', 'ctr-1', NOW)
    with NativeEntitlement() as context:
        assert context.import_token(mutate(token), NOW) == expected
        status = context.status(NOW)
        assert status['contract_state'] == 0 and status['company'] == '' and status['warning_bits'] & 33 == 33


def test_payload_swap_under_valid_signature_is_rejected():
    first, second = issue_token('acme', 'ctr-1', NOW), issue_token('other', 'ctr-9', NOW)
    forged = second.rsplit('.', 1)[0] + '.' + first.rsplit('.', 1)[1]
    with NativeEntitlement() as context:
        assert context.import_token(forged, NOW) == REJECTED_SIGNATURE


def test_unknown_key_and_future_version_are_rejected():
    secret = bytes(range(32))
    with NativeEntitlement() as context:
        assert context.import_token(issue_token('acme', 'ctr-1', NOW, kid='unknown-key', secret=secret), NOW) == REJECTED_KEY
        assert context.import_token(issue_token('acme', 'ctr-1', NOW, version=2), NOW) == REJECTED_FORMAT
        assert context.import_token(issue_token('bad company', 'ctr-1', NOW), NOW) == REJECTED_FORMAT


def test_rejected_or_older_import_preserves_verified_information():
    with NativeEntitlement() as context:
        newer = issue_token('acme', 'ctr-1', NOW + 5 * DAY)
        context.import_token(newer, NOW + 5 * DAY)
        # Re-importing an older file cannot move or extend the confirmation window.
        assert context.import_token(issue_token('acme', 'ctr-1', NOW), NOW + 5 * DAY) == IGNORED_OLDER
        assert context.import_token(newer[:-2] + 'zz', NOW + 5 * DAY) in (REJECTED_SIGNATURE, REJECTED_FORMAT)
        status = context.status(NOW + 5 * DAY)
        assert (status['company'], status['verified_at'], status['contract_state']) == ('acme', NOW + 5 * DAY, 1)
        # The same file imported again later is still judged by its signed time.
        assert context.import_token(newer, NOW + 40 * DAY) == ACCEPTED
        assert context.status(NOW + 40 * DAY)['verification'] == 2


def test_revocation_only_from_signed_server_information():
    with NativeEntitlement() as context:
        context.import_token(issue_token('acme', 'ctr-1', NOW), NOW)
        assert context.status(NOW + 400 * DAY)['contract_state'] == 1  # Silence is never a revocation.
        context.import_token(issue_token('acme', 'ctr-1', NOW + DAY, revoked_at=NOW + DAY), NOW + DAY)
        status = context.status(NOW + DAY)
        assert (status['base_license'], status['contract_state'], status['warning_bits'] & 8) == (1, 4, 8)


def test_instances_are_isolated():
    with NativeEntitlement() as first, NativeEntitlement() as second:
        first.import_token(issue_token('acme', 'ctr-1', NOW), NOW)
        assert second.status(NOW)['company'] == '' and second.status(NOW)['contract_state'] == 0
        second.import_token(issue_token('other', 'ctr-2', NOW), NOW)
        assert first.status(NOW)['company'] == 'acme' and second.status(NOW)['company'] == 'other'
        assert b'other' not in first.export_state() and first.token() != second.token()


def test_state_restore_verifies_tokens_again_and_cannot_forge_attribution():
    with NativeEntitlement() as context, NativeEntitlement() as restored, NativeEntitlement() as forged:
        token = issue_token('acme', 'ctr-1', NOW)
        context.import_token(token, NOW)
        state = context.export_state()
        restored.restore_state(state)
        assert restored.status(NOW)['company'] == 'acme' and restored.status(NOW)['verified_at'] == NOW
        tampered = state.replace(b'C FSE1.', b'C FSE1.x', 1)
        forged.restore_state(tampered)
        assert forged.status(NOW)['contract_state'] == 0 and forged.status(NOW)['company'] == ''
        # Usage lines written by earlier development builds are ignored and not kept.
        legacy = state + b'U ' + token.encode() + b' 1800000007 0 1\nR ' + b'05' * 16 + b' ' + token.encode() + b' 1800000007 0\n'
        restored.restore_state(legacy)
        assert restored.status(NOW)['company'] == 'acme' and restored.export_state() == state


def test_offline_file_on_a_pc_that_was_never_online(tmp_path):
    file = tmp_path / 'fisica-entitlement.fse'
    file.write_text(issue_token('acme', 'ctr-1', NOW) + '\n')
    clock = Clock()
    entitlement = quiet(file=file, storage=tmp_path / 'state', clock=clock)
    status = entitlement.status()
    assert (status['verification'], status['contract_state'], status['company']) == ('fresh', 'active', 'acme')
    assert status['key_environment'] == 'test' and 'test_key' in status['warnings']
    entitlement.check_status()
    # No endpoint: nothing leaves the PC.
    assert entitlement.sync(wait=True) is None
    clock.now = NOW + 31 * DAY
    assert entitlement.import_file(file) == 'accepted'  # Same file again: window is not extended.
    assert entitlement.status()['verification'] == 'refresh_due'
    # What is stored is the signed token, nothing about use.
    assert (tmp_path / 'state').read_bytes() == b'FSS1\nC ' + file.read_bytes().strip() + b'\n'


def test_online_refresh_sends_only_the_token_and_stores_the_newer_confirmation(server, tmp_path):
    mock, url = server
    clock = Clock(NOW + 29 * DAY)
    mock.clock.now = clock.now
    old = issue_token('acme', 'ctr-1', NOW)
    entitlement = quiet(token=old, endpoint=url, storage=tmp_path / 'state', clock=clock)
    entitlement.check_status()
    result = entitlement.sync(wait=True)
    assert result == {'refreshed': True, 'error': None}
    assert entitlement.status()['verified_at'] == NOW + 29 * DAY  # Server-signed confirmation time.
    # Every request is a refresh with an empty body: no usage, time or count is sent.
    assert mock.requests and all(path == '/sdk/v1/entitlement/refresh' and body == {} for path, body in mock.requests)
    assert not hasattr(mock, 'usage')
    # A later start with the app's older token still finds the newer stored confirmation.
    reopened = quiet(token=old, storage=tmp_path / 'state', clock=clock)
    assert reopened.status()['verified_at'] == NOW + 29 * DAY


def test_network_failure_is_not_expiry(server, tmp_path):
    mock, url = server
    clock = Clock()
    entitlement = quiet(token=issue_token('acme', 'ctr-1', NOW), endpoint=url, storage=tmp_path / 'state', clock=clock)
    entitlement.sync(wait=True)
    before = entitlement.status()
    mock.fail_requests = 100
    result = entitlement.sync(wait=True)
    after = entitlement.status()
    assert result['error'] and after['last_sync_error']
    assert (after['verification'], after['contract_state'], after['verified_at']) == ('fresh', 'active', before['verified_at'])
    mock.fail_requests = 0
    assert entitlement.sync(wait=True)['error'] is None


def test_server_signed_revocation_is_shown_without_blocking(server, tmp_path):
    mock, url = server
    mock.add_contract('acme', 'ctr-1', kind='special', revoked_at=NOW)
    entitlement = quiet(token=issue_token('acme', 'ctr-1', NOW - DAY, kind='special'), endpoint=url,
                        storage=tmp_path / 'state', clock=Clock())
    entitlement.sync(wait=True)
    status = entitlement.status()
    assert (status['base_license'], status['contract_state']) == ('granted', 'revoked')
    entitlement.check_status()  # Use after revocation raises a warning only and is not recorded anywhere.
    assert all(body == {} for _, body in mock.requests)


def test_server_accepts_nothing_but_an_empty_refresh(server):
    mock, _ = server
    token = issue_token('acme', 'ctr-1', NOW)
    assert mock.handle('/sdk/v1/entitlement/refresh', token, {})[0] == 200
    assert mock.handle('/sdk/v1/entitlement/refresh', token, {'client_last_used_at': NOW})[0] == 400
    assert mock.handle('/sdk/v1/entitlement/refresh', token, {'company': 'other'})[0] == 400
    assert mock.handle('/sdk/v1/usage', token, {})[0] == 404  # There is no usage endpoint.
    assert mock.handle('/sdk/v1/entitlement/refresh', None, {})[0] == 401
    assert mock.handle('/sdk/v1/entitlement/refresh', token[:-2] + 'zz', {})[0] == 401


def test_warnings_are_distinct_from_errors_and_issued_once(tmp_path):
    clock = Clock()
    with pytest.warns(FisicaEntitlementWarning, match='TEST key'):
        entitlement = Entitlement(token=issue_token('acme', 'ctr-1', NOW), storage=None, clock=clock)
    clock.now = NOW + 31 * DAY
    with pytest.warns(FisicaEntitlementWarning, match='30 days') as caught:
        entitlement.check_status()
        entitlement.check_status()
    assert len(caught) == 1
    with pytest.warns(FisicaEntitlementWarning, match='rejected'):
        assert entitlement.import_token('not a token') == 'rejected_format'
    assert entitlement.status()['company'] == 'acme'


def _mock_scale():
    fixtures = Path(__file__).parent / 'fixtures'
    frame = bytes.fromhex(next(f['packet_hex'] for f in json.loads((fixtures / 'legacy-processing.json').read_text())
                               if f.get('id') == 'synthetic-recorded-layout'))
    core = NativeCore()
    firmware = bytes.fromhex(json.loads((fixtures / 'protocol.json').read_text())['responses'][0]['hex'])
    exchanges = {core.command('GET_FIRMWARE_VERSION'): [firmware]}
    for name in ('SET_DEVICE_USB', 'START_CONTINUOUS_MEASUREMENT', 'STOP_CONTINUOUS_MEASUREMENT', 'SET_DEVICE_STOP'):
        command = core.command(name)
        exchanges[command] = [bytes([0x24, command[1], command[2], 1, 0, 0, 0xff, 0xf0])]
    exchanges[core.command('START_CONTINUOUS_MEASUREMENT')][0] += frame * 12
    return MockTransport(exchanges)


@pytest.mark.parametrize('state,expected', [
    ('unconfigured', 'unverified'), ('tampered', 'unverified'), ('refresh_due', 'active'),
    ('ended', 'ended'), ('revoked', 'revoked'), ('unreachable_server', 'active'),
])
def test_connect_measure_analyze_work_in_every_entitlement_state(state, expected):
    tokens = {
        'tampered': issue_token('acme', 'ctr-1', NOW)[:-3] + 'zzz',
        'refresh_due': issue_token('acme', 'ctr-1', NOW - 60 * DAY),
        'ended': issue_token('acme', 'ctr-1', NOW, kind='special', contract_end=NOW - DAY),
        'revoked': issue_token('acme', 'ctr-1', NOW, kind='special', revoked_at=NOW - DAY),
        'unreachable_server': issue_token('acme', 'ctr-1', NOW),
    }
    sdk, frames = FisicaSDK(), []
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        if state in tokens:
            endpoint = 'http://127.0.0.1:9' if state == 'unreachable_server' else None
            sdk.configure_entitlement(token=tokens[state], endpoint=endpoint, storage=None, clock=Clock(), timeout=0.2)
        try:
            sdk.connect({'id': 'mock', 'type': 'Serial'}, transport=_mock_scale())
            sdk.on_data(frames.append)
            sdk.start_measurement()
            deadline = time.monotonic() + 4
            while len(frames) < 12:
                assert time.monotonic() < deadline, 'measurement stalled'
                time.sleep(0.01)
            sdk.stop_measurement()
            sdk.wait()
            assert sdk.analyze().weight == 70.5
        finally:
            sdk.disconnect()
            sdk.wait()
    status = sdk.entitlement_status()
    assert status['base_license'] == 'granted' and status['contract_state'] == expected
    if state == 'unreachable_server':
        assert sdk._entitlement.sync(wait=True)['error']  # Failure is recorded, never raised.
        assert sdk.entitlement_status()['verification'] == 'fresh'
