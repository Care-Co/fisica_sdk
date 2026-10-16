"""License: SDK keys as seats, full and basic mode, and no usage tracking. Real C ABI and a mock server."""
import base64
import hashlib
import json
from pathlib import Path
import time
import warnings
import pytest
from fisica_sdk import (Entitlement, FisicaEntitlementWarning, FisicaLicenseError, FisicaSDK, MockTransport,
                        NativeCore, NativeEntitlement, NativeSession)
from tools.entitlement_dev import MockEntitlementServer, host_machine_id, issue_token, pc_target, public_key, sign

NOW = 1_800_000_000
DAY = 86400
ACCEPTED, IGNORED_OLDER, REJECTED_FORMAT, REJECTED_KEY, REJECTED_SIGNATURE, REJECTED_TARGET = 1, 2, 3, 4, 5, 6
BASIC, FULL = 1, 2
ALL_FEATURES = 15  # raw_data, export, advanced, full_rate


def here():
    """This test process's target: a Python script is bound to the PC."""
    with NativeEntitlement() as context:
        return context.status(NOW)['target']


def key(verified_at=NOW, bound=True, **fields):
    """A trial seat key, activated for this PC unless bound=False."""
    seat = {'key': 'key_1', 'track': 'trial', 'full_until': NOW + 14 * DAY}
    if bound:
        seat.update(bind_type='pc', bind=here(), key_until=NOW + 90 * DAY)
    seat.update(fields)
    return issue_token('acme', 'ctr-1', verified_at, **seat)


def server_check(prefix, body):
    """The developer portal's check value (app/services.py _check)."""
    material = (b'fisica-release-code-v1|' if prefix == 'FRC1' else b'') + f'{prefix}.{body}'.encode()
    return hashlib.sha256(material).hexdigest()[:8]


def read_code(code, prefix):
    head, body, check = code.split('.')
    assert head == prefix and check == server_check(prefix, body)
    return json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))


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
    mock.add_contract('acme', 'ctr-1', track='trial', full_until=NOW + 14 * DAY)
    mock.add_key('key_1', 'acme', 'ctr-1', days=30)
    url = mock.start()
    yield mock, url
    mock.stop()


def test_test_signer_matches_rfc8032_vectors():
    secret = bytes.fromhex('9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60')
    assert public_key(secret).hex() == 'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a'
    assert sign(secret, b'').hex().startswith('e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155')


def test_core_trusts_the_production_portal_key():
    """developer.fisicahealthcare.com signs with this kid. Its private key is not here, so a forged
    signature must reach the signature check (REJECTED_SIGNATURE), not fail as an unknown key."""
    import os
    production = [entry['kid'] for entry in json.loads(
        (Path(__file__).resolve().parents[1] / 'native' / 'entitlement-keys.json').read_text())['keys']
        if entry['environment'] == 'production']
    assert 'prod-2026-10-15' in production
    with NativeEntitlement() as context:
        forged = issue_token('acme', 'ctr-1', NOW, kid='prod-2026-10-15', secret=os.urandom(32))
        assert context.import_token(forged, NOW) == REJECTED_SIGNATURE
        assert context.import_token(issue_token('acme', 'ctr-1', NOW, kid='retired-key'), NOW) == REJECTED_KEY


def test_core_reads_this_pc_itself():
    """The PC hash is SHA-256 of the platform machine id; the host never passes it in."""
    with NativeEntitlement() as context:
        status = context.status(NOW)
        assert (status['mode'], status['reason'], status['target_type']) == (BASIC, 1, 2)
        assert status['target'] == pc_target(host_machine_id())
        assert json.loads(context.activation_request()) == {'bind_type': 'pc', 'bind': status['target']}
        assert context.activation_code() is None  # no key loaded
        assert context.export_state() == b'FSS1\nT %d\n' % NOW


def test_sdk_has_no_usage_tracking_surface():
    """Nothing records or reports whether, when or how often the SDK is used."""
    library = NativeCore()._lib
    for name in ('mark_used', 'report', 'report_done'):
        assert not hasattr(library, 'fisica_core_entitlement_' + name)
    for name in ('mark_used', 'report', 'report_done', 'pending_report'):
        assert not hasattr(NativeEntitlement, name) and not hasattr(Entitlement, name)
    header = (Path(__file__).resolve().parents[1] / 'native' / 'include' / 'fisica_core.h').read_text(encoding='utf-8')
    assert 'mark_used' not in header and 'entitlement_report' not in header
    token = key()
    with NativeEntitlement() as context:
        context.import_token(token, NOW)
        for later in (NOW + 5, NOW + 9 * DAY):
            context.status(later)
        # The stored state is the signed key and the latest time seen: no counters, no history.
        assert context.export_state() == b'FSS1\nC ' + token.encode() + b'\nT %d\n' % (NOW + 9 * DAY)


def test_activated_key_is_full_mode_until_the_trial_ends():
    with NativeEntitlement() as context:
        assert context.import_token(key(), NOW) == ACCEPTED
        status = context.status(NOW)
        assert (status['mode'], status['reason'], status['features'], status['track']) == (FULL, 0, ALL_FEATURES, 1)
        assert (status['company'], status['contract'], status['key'], status['bind']) == ('acme', 'ctr-1', 'key_1', here())
        assert status['key_until'] == NOW + 90 * DAY and status['full_until'] == NOW + 14 * DAY
        assert context.status(NOW + 8 * DAY)['warning_bits'] & 4  # full mode ends within 7 days
        ended = context.status(NOW + 14 * DAY)
        assert (ended['mode'], ended['reason'], ended['features']) == (BASIC, 10, 0)


def test_unactivated_key_is_basic_and_gives_an_activation_code():
    with NativeEntitlement() as context:
        assert context.import_token(key(bound=False), NOW) == ACCEPTED
        status = context.status(NOW)
        assert (status['mode'], status['reason'], status['bind_type']) == (BASIC, 3, 0)
        # The portal's FAC1 parser accepts the code: key, this target and its check value.
        assert read_code(context.activation_code(), 'FAC1') == {'k': 'key_1', 't': 'pc', 'b': here()}
        with pytest.raises(Exception):
            context.release()  # nothing activated here to release


def test_key_activated_elsewhere_never_replaces_this_pc_key():
    with NativeEntitlement() as context:
        context.import_token(key(), NOW)
        other = key(key='key_2', bind=pc_target('another-pc'))
        assert context.import_token(other, NOW) == REJECTED_TARGET
        status = context.status(NOW)
        assert (status['mode'], status['key'], status['warning_bits'] & 8) == (FULL, 'key_1', 8)


def test_legacy_contract_token_is_not_a_seat_key():
    with NativeEntitlement() as context:
        assert context.import_token(issue_token('acme', 'ctr-1', NOW), NOW) == ACCEPTED
        status = context.status(NOW)
        assert (status['mode'], status['reason'], status['contract_state'], status['company']) == (BASIC, 2, 1, 'acme')


@pytest.mark.parametrize('fields,reason', [
    ({'revoked_at': NOW}, 6), ({'key_until': NOW - 1}, 8), ({'contract_start': NOW + DAY}, 9),
    ({'full_until': 0}, 10), ({'full_until': NOW - 1}, 10),
])
def test_every_basic_reason_comes_from_signed_dates(fields, reason):
    with NativeEntitlement() as context:
        context.import_token(key(**fields), NOW)
        status = context.status(NOW)
        assert (status['mode'], status['reason'], status['features']) == (BASIC, reason, 0)


def test_contract_grace_and_limited_features():
    with NativeEntitlement() as context:
        context.import_token(key(track='contract', kind='special', contract_end=NOW - DAY, full_until=NOW + 29 * DAY,
                                 features='export'), NOW)
        status = context.status(NOW)
        assert (status['mode'], status['features'], status['warning_bits'] & 2) == (FULL, 2 | 8, 2)
        with pytest.raises(FisicaLicenseError):
            NativeCore().decode_packet(bytes(645), context, NOW)  # no raw data in this contract


def test_clock_set_back_more_than_an_hour_is_basic_mode():
    with NativeEntitlement() as context:
        context.import_token(key(), NOW)
        assert context.status(NOW + 2 * DAY)['mode'] == FULL
        assert context.status(NOW + 2 * DAY - 1800)['mode'] == FULL  # a clock correction
        assert context.status(NOW + DAY)['reason'] == 7
        # The latest time seen is kept in the state and survives a restart.
        with NativeEntitlement() as restarted:
            restarted.restore_state(context.export_state())
            assert restarted.status(NOW + DAY)['reason'] == 7
            # Importing the same key file again does not forget it; a newer key does.
            assert restarted.import_token(key(), NOW + DAY) == ACCEPTED
            assert restarted.status(NOW + DAY)['reason'] == 7
            restarted.import_token(key(verified_at=NOW + DAY), NOW + DAY)
            assert restarted.status(NOW + DAY)['mode'] == FULL


def test_server_cannot_grant_more_than_thirty_days_between_renewals():
    with NativeEntitlement() as context:
        context.import_token(key(refresh_by=NOW + 365 * DAY, key_until=0), NOW)
        status = context.status(NOW)
        assert status['refresh_by'] == NOW + 30 * DAY and status['mode'] == FULL
        assert context.status(NOW + 31 * DAY)['warning_bits'] & 1  # renew due; key without expiry stays full
        assert context.status(NOW + 31 * DAY)['mode'] == BASIC  # trial over


@pytest.mark.parametrize('mutate,expected', [
    (lambda t: t[:-4] + ('AAAA' if not t.endswith('AAAA') else 'BBBB'), REJECTED_SIGNATURE),
    (lambda t: t.replace('.', '.e', 1), REJECTED_FORMAT),
    (lambda t: 'FSE2' + t[4:], REJECTED_FORMAT),
    (lambda t: t + '.extra', REJECTED_FORMAT),
    (lambda t: '', REJECTED_FORMAT),
])
def test_tampered_or_malformed_keys_are_rejected_not_errors(mutate, expected):
    with NativeEntitlement() as context:
        assert context.import_token(mutate(key()), NOW) == expected
        status = context.status(NOW)
        assert (status['mode'], status['reason'], status['company'], status['warning_bits'] & 8) == (BASIC, 1, '', 8)


def test_payload_swap_and_invalid_seat_members_are_rejected():
    first, second = key(), key(key='key_2')
    forged = second.rsplit('.', 1)[0] + '.' + first.rsplit('.', 1)[1]
    with NativeEntitlement() as context:
        assert context.import_token(forged, NOW) == REJECTED_SIGNATURE
        for fields in ({'bind_type': 'pc', 'bind': 'not-a-hash'}, {'bind_type': 'app', 'bind': 'noDots'},
                       {'bind_type': 'usb'}, {'key': '', 'bind_type': 'pc'}, {'track': 'forever'}, {'features': 'RAW'}):
            assert context.import_token(key(**fields), NOW) == REJECTED_FORMAT, fields
        assert context.import_token(key(version=2), NOW) == REJECTED_FORMAT


def test_older_key_file_is_ignored_and_state_is_verified_again():
    with NativeEntitlement() as context, NativeEntitlement() as restored, NativeEntitlement() as forged:
        context.import_token(key(verified_at=NOW + DAY), NOW + DAY)
        assert context.import_token(key(), NOW + DAY) == IGNORED_OLDER
        assert context.import_token(key(bound=False), NOW + DAY) == IGNORED_OLDER  # the original download
        state = context.export_state()
        restored.restore_state(state)
        assert restored.status(NOW + DAY)['mode'] == FULL
        forged.restore_state(state.replace(b'C FSE1.', b'C FSE1.x', 1))
        assert forged.status(NOW + DAY)['reason'] == 1
        # Usage lines written by earlier development builds are ignored and not kept.
        restored.restore_state(state + b'U ' + key().encode() + b' 1800000007 0 1\n')
        assert b'\nU ' not in restored.export_state()


def test_release_on_the_device_gives_the_portal_code_and_forgets_the_key(tmp_path):
    entitlement = quiet(token=key(), storage=tmp_path / 'state', clock=Clock())
    assert (tmp_path / 'state').is_file()
    code = entitlement.release()
    assert read_code(code, 'FRC1') == {'k': 'key_1', 't': 'pc', 'b': here()}
    status = entitlement.status()
    assert (status['mode'], status['reason']) == ('basic', 'no_key')
    assert not (tmp_path / 'state').exists()


def test_offline_activation_file_on_a_pc_that_is_never_online(tmp_path):
    clock = Clock()
    original = quiet(token=key(bound=False), storage=tmp_path / 'state', clock=clock)
    status = original.status()
    assert (status['mode'], status['reason']) == ('basic', 'not_activated') and status['activation_code']
    # The portal turns the code into a key file activated for this PC.
    file = tmp_path / 'activated.fse'
    file.write_text(key(verified_at=NOW + 60) + '\n')
    clock.now = NOW + 60
    assert original.import_file(file) == 'accepted'
    assert original.status()['mode'] == 'full'
    assert original.sync(wait=True) is None  # no endpoint: nothing leaves the PC
    # A later start with the original key file still finds the activated key.
    reopened = quiet(token=key(bound=False), storage=tmp_path / 'state', clock=clock)
    assert reopened.status()['mode'] == 'full'


def test_online_activation_and_renewal_send_only_the_key_and_this_target(server, tmp_path):
    mock, url = server
    clock = Clock()
    mock.clock = clock
    # Importing a new key starts its activation in the background; wait for it.
    entitlement = quiet(token=key(bound=False), endpoint=url, storage=tmp_path / 'state', clock=clock)
    entitlement.sync(wait=True)
    assert entitlement.status()['mode'] == 'full'
    assert mock.keys['key_1']['bind'] == here()
    assert entitlement.sync(wait=True) == {'refreshed': False, 'activated': False, 'error': None}  # nothing due
    clock.now = NOW + 25 * DAY  # inside the renewal window of a 30-day key
    mock.contracts[('acme', 'ctr-1')]['full_until'] = NOW + 60 * DAY  # e.g. a contract after the trial
    assert 'renew_due' in entitlement.status()['warnings']
    assert entitlement.sync(wait=True)['refreshed']
    assert entitlement.status()['key_until'] == NOW + 55 * DAY and 'renew_due' not in entitlement.status()['warnings']
    # Exactly the core's activation request, then empty refreshes: no usage, time or count.
    assert mock.requests == [('/sdk/v1/entitlement/activate', {'bind_type': 'pc', 'bind': here()}),
                             ('/sdk/v1/entitlement/refresh', {})]
    # The activated key is stored; a later start with the original key file uses it.
    reopened = quiet(token=key(bound=False), storage=tmp_path / 'state', clock=clock)
    assert reopened.status()['key_until'] == NOW + 55 * DAY


def test_network_failure_is_not_expiry(server, tmp_path):
    mock, url = server
    mock.fail_requests = 100
    entitlement = quiet(token=key(bound=False), endpoint=url, storage=tmp_path / 'state', clock=Clock())
    result = entitlement.sync(wait=True)
    status = entitlement.status()
    assert result['error'] and status['last_sync_error'] and status['reason'] == 'not_activated'
    mock.fail_requests = 0
    assert entitlement.sync(wait=True)['activated']


def test_key_released_in_the_portal_is_let_go_at_the_next_renewal(server, tmp_path):
    mock, url = server
    mock.add_key('key_1', 'acme', 'ctr-1', bind_type='pc', bind=here())
    entitlement = quiet(token=key(), endpoint=url, storage=tmp_path / 'state', clock=Clock())
    mock.keys['key_1']['released'] = True
    assert entitlement.sync(wait=True, force=True)['refreshed']
    status = entitlement.status()
    assert (status['mode'], status['reason']) == ('basic', 'revoked')
    assert not (tmp_path / 'state').exists()


def test_server_accepts_nothing_but_activation_targets_and_empty_refreshes(server):
    mock, _ = server
    token = key(bound=False)
    assert mock.handle('/sdk/v1/entitlement/refresh', token, {})[0] == 200
    assert mock.handle('/sdk/v1/entitlement/refresh', token, {'client_last_used_at': NOW})[0] == 400
    assert mock.handle('/sdk/v1/entitlement/activate', token, {'bind_type': 'pc', 'bind': here(), 'count': 3})[0] == 400
    assert mock.handle('/sdk/v1/usage', token, {})[0] == 404  # There is no usage endpoint.
    assert mock.handle('/sdk/v1/entitlement/refresh', None, {})[0] == 401
    assert mock.handle('/sdk/v1/entitlement/activate', token, {'bind_type': 'pc', 'bind': here()})[0] == 200
    assert mock.handle('/sdk/v1/entitlement/activate', token, {'bind_type': 'pc', 'bind': pc_target('x')})[0] == 409


def test_notices_are_distinct_from_errors_and_issued_once():
    clock = Clock()
    with pytest.warns(FisicaEntitlementWarning, match='TEST key'):
        entitlement = Entitlement(token=key(), storage=None, clock=clock)
    clock.now = NOW + 15 * DAY
    with pytest.warns(FisicaEntitlementWarning, match='trial or contract') as caught:
        entitlement.check_status()
        entitlement.check_status()
    assert len(caught) == 1
    with pytest.warns(FisicaEntitlementWarning, match='rejected'):
        assert entitlement.import_token('not a key') == 'rejected_format'
    assert entitlement.status()['company'] == 'acme'


def test_session_hands_out_frames_by_mode():
    fixtures = Path(__file__).parent / 'fixtures'
    packet = bytes.fromhex(next(f['packet_hex'] for f in json.loads((fixtures / 'legacy-processing.json').read_text())
                                if f.get('id') == 'synthetic-recorded-layout'))
    core = NativeCore()
    reference = None
    with NativeEntitlement() as full:
        full.import_token(key(), NOW)
        reference = core.decode_packet(packet, full, NOW)
        for license, expected in ((None, 3), (full, 10)):
            with NativeSession() as session:
                now = 1000
                session.action(1, 2, now)
                session.feed(bytes.fromhex(json.loads((fixtures / 'protocol.json').read_text())['responses'][0]['hex']), now)
                session.license(license, NOW, now)
                session.action(2, -1, now)
                for name in ('SET_DEVICE_USB', 'START_CONTINUOUS_MEASUREMENT'):
                    command = core.command(name)
                    session.feed(bytes([0x24, command[1], command[2], 1, 0, 0, 0xff, 0xf0]), now)
                events = []
                for _ in range(10):
                    now += 300
                    session.feed(packet, now)
                    events += [event for event in session.events() if event['kind'] == 4]
                assert len(events) == expected
                assert all(event['weight'] == reference.weight for event in events)
                assert all(event['sensor_matrix'].tolist() == reference.sensor_matrix for event in events)
                if license:
                    assert events[0]['packet'] == packet and events[0]['raw_grid'].tolist() == reference.raw_grid
                else:
                    assert events[0]['packet'] is None and events[0]['raw_grid'] is None
                assert session.analyze()[0] == reference.weight
    with pytest.raises(FisicaLicenseError):
        core.decode_packet(packet)


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


@pytest.mark.parametrize('state', ['no_key', 'not_activated', 'tampered', 'ended', 'revoked', 'unreachable_server', 'full'])
def test_connect_measure_analyze_work_in_every_license_state(state, tmp_path):
    tokens = {
        'not_activated': key(bound=False),
        'tampered': key()[:-3] + 'zzz',
        'ended': key(full_until=NOW - DAY),
        'revoked': key(revoked_at=NOW - DAY),
        'unreachable_server': key(bound=False),
        'full': key(),
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
            expected = 12 if state == 'full' else 1  # the 12 packets arrive within one second
            deadline = time.monotonic() + 4
            while len(frames) < expected:
                assert time.monotonic() < deadline, 'measurement stalled'
                time.sleep(0.01)
            time.sleep(0.2)
            sdk.stop_measurement()
            sdk.wait()
            assert len(frames) == expected
            assert (frames[0].raw_data is not None) == (state == 'full')
            report = sdk.analyze()
            assert report.weight == 70.5  # every packet is analyzed in every mode
            exported = sdk.export_report(output=str(tmp_path / 'report'))
            saved = json.loads((tmp_path / 'report.json').read_text())[0]
            assert ('total_frame' in saved) == (state == 'full') and saved['weight'] == 70.5
        finally:
            sdk.disconnect()
            sdk.wait()
    status = sdk.entitlement_status()
    assert status['mode'] == ('full' if state == 'full' else 'basic')
    if state == 'unreachable_server':
        assert sdk._entitlement.sync(wait=True)['error']  # Failure is recorded, never raised.
        assert sdk.entitlement_status()['reason'] == 'not_activated'
