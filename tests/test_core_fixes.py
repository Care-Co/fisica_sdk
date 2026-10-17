"""Core session fixes from the 2026-10-17 review: noise before an answer, a host that does not
poll, a lost link, closing twice, commands the session cannot recognize, and the lean analysis."""
import json
from pathlib import Path

import pytest

from fisica_sdk import FisicaError, NativeCore, NativeSession
from fisica_sdk.codes import FisicaSessionAction as Action, FisicaSessionEvent as Event
from fisica_sdk.codes import FisicaSessionOperation as Operation, FisicaSessionState as State

FIXTURES = Path(__file__).parent / 'fixtures'
PACKET = bytes.fromhex(next(item['packet_hex'] for item in json.loads((FIXTURES / 'legacy-processing.json').read_text())
                            if item.get('id') == 'synthetic-recorded-layout'))
PROBE_ACK = bytes.fromhex('24434701322e302e31fff0')


def events(session):
    return list(session.events())


def results(found, operation):
    return [e for e in found if e['kind'] == Event.RESULT and e['operation'] == operation]


def measuring(session):
    session.action(Action.CONNECTED, 2, now_ms=0)
    session.feed(PROBE_ACK, now_ms=0)
    session.action(Action.START, -1, now_ms=0)
    session.feed(bytes.fromhex('242253010000fff0'), now_ms=0)
    session.feed(bytes.fromhex('245153010000fff0'), now_ms=0)
    events(session)


def test_an_answer_after_noise_is_found_while_no_frames_stream():
    with NativeSession() as session:
        session.action(Action.CONNECTED, 2, now_ms=0)
        events(session)
        session.feed(b'\x00\x7f' + PROBE_ACK, now_ms=10)  # bytes left in the port before the answer
        probe = results(events(session), Operation.PROBE)
        assert len(probe) == 1 and probe[0]['status'] == 0


def test_a_lost_link_keeps_results_already_produced():
    with NativeSession() as session:
        session.action(Action.CONNECTED, 2, now_ms=0)
        session.feed(PROBE_ACK, now_ms=0)  # the probe's result waits, not polled yet
        session.action(Action.LOST, 0, now_ms=1)
        found = events(session)
        assert results(found, Operation.PROBE) and not [e for e in found if e['kind'] == Event.TRANSMIT]


def test_closing_during_the_probe_leaves_the_session_closable_again():
    with NativeSession() as session:
        session.action(Action.CONNECTED, 2, now_ms=0)
        session.action(Action.DISCONNECT, 0, now_ms=0)  # waits for the probe's answer
        session.feed(PROBE_ACK, now_ms=1)
        assert events(session)[-1]['state'] == State.DISCONNECTED
        session.action(Action.DISCONNECT, 0, now_ms=2)  # used to be refused as busy
        assert results(events(session), Operation.CLOSE)


def test_commands_whose_answer_cannot_be_recognized_are_refused():
    with NativeSession() as session:
        session.action(Action.CONNECTED, 2, now_ms=0)
        session.feed(PROBE_ACK, now_ms=0)
        events(session)
        with pytest.raises(FisicaError) as refused:
            session.command('_legacy_fixed', payload=bytes([0x31, 0x99, 0, 0]), now_ms=1)  # type 0x99
        assert refused.value.code == 1
        session.command('_legacy_fixed', payload=bytes([0x31, 0x47, 0, 0]), now_ms=1)  # a query is fine


def test_a_host_that_does_not_poll_loses_old_frames_not_the_session(full_license):
    with NativeSession() as session:
        session.license(full_license, now_ms=0)
        measuring(session)
        for i in range(1100):
            session.feed(PACKET, now_ms=1 + i)
        found = events(session)
        assert len(found) == 1024 and all(e['kind'] == Event.MEASUREMENT for e in found)
        session.action(Action.STOP, 0, now_ms=2000)
        assert events(session)[-1]['kind'] == Event.TRANSMIT  # still measuring and answering


def packets(weights):
    """The fixture packet with another weight (0.1 kg units at 641..642); None is an empty frame."""
    out = []
    for weight in weights:
        packet = bytearray(PACKET)
        if weight is None:
            packet[1:639] = bytes(638)
            weight = 0
        packet[641:643] = int(round(weight * 10)).to_bytes(2, 'big')
        out.append(bytes(packet))
    return out


@pytest.mark.parametrize('weights', [
    [10, 30, 30.2, None, 30.1, 50, 50.3, 50.1, 50.2, 50.4, 50.2, 50.1, 50.3, 50.2, 50.4, 50.3, 50.2, 70, 70.1],
    [10, 20, 20.3, 40, 40.2, 40.1, 60, 61, 61.2],          # no run of ten: the last stable run
    [5, 25, 45, 65],                                      # nothing stable
])
def test_lean_analysis_matches_decoding_every_packet(full_license, weights):
    core = NativeCore()
    sample = packets(weights)
    decoded = [core.decode_packet(packet, full_license) for packet in sample]
    expected = core.analyze_matrices([d.sensor_matrix for d in decoded], [d.weight for d in decoded])
    assert core.analyze_packets(sample) == expected
    assert (expected is None) == (weights == [5, 25, 45, 65])


def test_a_flood_of_unrequested_answers_drops_old_ones_not_the_session():
    with NativeSession() as session:
        session.action(Action.CONNECTED, 2, now_ms=0)
        session.feed(PROBE_ACK, now_ms=0)  # its result waits, not polled
        session.feed(bytes.fromhex('242253010000fff0') * 1100, now_ms=1)  # answers nobody asked for
        found = events(session)
        assert results(found, Operation.PROBE) and not [e for e in found if e['kind'] == Event.STATE and e['state'] == State.FAULT]
        assert len(found) == 1024


def test_a_stop_before_a_disconnect_still_gets_its_result():
    with NativeSession() as session:
        session.action(Action.CONNECTED, 2, now_ms=0)
        session.feed(PROBE_ACK, now_ms=0)
        events(session)
        session.command('_legacy_fixed', payload=bytes([0x31, 0x47, 0, 0]), now_ms=1)  # an answer pending
        session.action(Action.STOP, 0, now_ms=2)
        session.action(Action.DISCONNECT, 0, now_ms=3)
        session.feed(bytes.fromhex('243147010000fff0'), now_ms=4)
        found = events(session)
        assert results(found, Operation.END) and results(found, Operation.CLOSE)
        assert found[-1]['kind'] == Event.RESULT and session_state(found) == State.DISCONNECTED


def test_a_link_lost_while_closing_ends_the_close_without_a_reconnect():
    with NativeSession() as session:
        measuring(session)
        session.action(Action.DISCONNECT, 0, now_ms=10)  # waits for the measurement to stop
        events(session)
        session.action(Action.LOST, 0, now_ms=11)
        found = events(session)
        assert session_state(found) == State.DISCONNECTED and not [e for e in found if e['kind'] == Event.RECONNECT]
        assert results(found, Operation.CLOSE)
        session.action(Action.LOST, 0, now_ms=12)  # already closed: nothing happens
        assert events(session) == []


def session_state(found):
    return [e['state'] for e in found if e['kind'] == Event.STATE][-1]
