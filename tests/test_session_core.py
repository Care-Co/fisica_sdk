"""No hardware, real C ABI: session states, ACK failures and lifecycle policy."""
import ctypes as ct
import json
from pathlib import Path
import pytest
from fisica_sdk import NativeSession, FisicaError

VECTORS = Path(__file__).parent / 'fixtures' / 'sessions.json'


def drain(session):
    return list(session.events())


def feed(session, hex_bytes, now=0):
    session.feed(bytes.fromhex(hex_bytes), now_ms=now)
    return drain(session)


def ready(session):
    session.action(1, 2, now_ms=0)
    drain(session)
    feed(session, '24434701322e302e31fff0')


def measuring(session, duration=-1):
    ready(session)
    session.action(2, duration, now_ms=0)
    drain(session)
    feed(session, '242253010000fff0')
    feed(session, '245153010000fff0')


def test_shared_session_vectors():
    for vector in json.loads(VECTORS.read_text()):
        with NativeSession() as session:
            for step in vector['steps']:
                now = step['now_ms']
                if 'action' in step:
                    session.action(step['action'], step.get('value', 0), now_ms=now)
                elif 'feed_hex' in step:
                    session.feed(bytes.fromhex(step['feed_hex']), now_ms=now)
                elif 'command' in step:
                    session.command(step['command'], now_ms=now)
                else:
                    session.tick(now_ms=now)
                events = drain(session)
                actual = [[e['kind'],e['state'],e['status'],e['device_error'],e['operation'],e['payload'].hex()]
                          for e in events]
                assert actual == step['events'], (vector['id'], step, actual)


def test_busy_unmatched_ack_and_timeout_no_retry():
    with NativeSession() as session:
        ready(session)
        session.action(2, -1, now_ms=0)
        drain(session)
        with pytest.raises(FisicaError) as error:
            session.command('SET_WEIGHT_ZERO', now_ms=1)
        assert error.value.code == 7
        assert feed(session, '243147010050fff0', 10)[0]['kind'] == 5
        session.tick(now_ms=2000)
        events = drain(session)
        assert [(e['kind'],e['status']) for e in events] == [(2,0),(3,8)]
        assert session.state == 6
        assert all(e['kind'] != 1 for e in events)
        assert feed(session, '242253010000fff0', 2001)[0]['kind'] == 5
        with pytest.raises(FisicaError) as error:
            session.action(2, -1, now_ms=2002)
        assert error.value.code == 6


@pytest.mark.parametrize('error_code',[1,2,3,4,5,6,7,8,9,513])
def test_nack_code_is_preserved_and_not_assumed_success(error_code):
    with NativeSession() as session:
        ready(session)
        session.action(2, -1, now_ms=0)
        drain(session)
        events = feed(session, '24225300'+error_code.to_bytes(2,'big').hex()+'fff0')
        result = next(e for e in events if e['kind'] == 3)
        assert result['status'] == 9 and result['device_error'] == error_code
        assert session.state == 6


def test_duration_waits_for_start_ack_and_stop_ack():
    with NativeSession() as session:
        ready(session)
        session.action(2, 100, now_ms=10)
        drain(session)
        feed(session,'242253010000fff0',50)
        feed(session,'245153010000fff0',100)
        session.tick(now_ms=199)
        assert drain(session) == []
        session.tick(now_ms=200)
        assert any(e['payload'].hex()=='245153000000fff0' for e in drain(session))
        assert session.state == 4
        feed(session,'245153010000fff0',250)
        assert session.state == 1


def test_stop_during_start_does_not_send_concurrent_command():
    with NativeSession() as session:
        ready(session)
        session.action(2,-1,now_ms=0)
        drain(session)
        session.action(3,now_ms=1)
        assert drain(session) == []
        events=feed(session,'242253010000fff0',2)
        assert [e['operation'] for e in events if e['kind']==3] == [2,3]
        assert all(e['kind'] != 1 for e in events)
        assert session.state == 1


def test_disconnect_waits_for_stop_then_release_ack():
    with NativeSession() as session:
        measuring(session)
        session.action(4,now_ms=1)
        events=drain(session)
        assert events[-1]['payload'].hex()=='245153000000fff0'
        events=feed(session,'245153010000fff0',2)
        assert events[-1]['payload'].hex()=='242253000000fff0'
        assert session.state == 5
        events=feed(session,'242253010000fff0',3)
        assert events[-1]['operation']==4 and events[-1]['status']==0 and session.state==0


def test_link_recovery_resumes_in_core_and_preserves_duration():
    with NativeSession() as session:
        measuring(session,1000)
        session.action(5,now_ms=100)
        assert drain(session)[-1]['kind']==6 and session.state==7
        session.action(1,2,now_ms=200)
        drain(session)
        events=feed(session,'24434701322e302e31fff0',201)
        assert events[-1]['payload'].hex()=='242253000002fff0'
        feed(session,'242253010000fff0',202)
        feed(session,'245153010000fff0',203)
        session.tick(now_ms=1000)
        assert drain(session)[-1]['payload'].hex()=='245153000000fff0'


def test_cancel_recovery_prevents_resume():
    with NativeSession() as session:
        measuring(session)
        session.action(5,now_ms=10)
        drain(session)
        session.action(3,now_ms=11)
        drain(session)
        session.action(1,2,now_ms=12)
        drain(session)
        events=feed(session,'24434701322e302e31fff0',13)
        assert not any(e['kind']==1 for e in events)
        assert session.state==1


def test_handles_are_independent_and_time_cannot_go_backwards():
    with NativeSession() as first, NativeSession() as second:
        measuring(first)
        ready(second)
        assert (first.state,second.state)==(3,1)
        first.tick(now_ms=50)
        with pytest.raises(FisicaError) as error:
            first.tick(now_ms=49)
        assert error.value.code==1
        first.close()
        with pytest.raises(FisicaError):
            first.tick(now_ms=60)
        second.tick(now_ms=60)


def test_event_capacity_error_keeps_event_and_canary():
    with NativeSession() as session:
        session.action(1,2,now_ms=0)
        assert session.poll()['kind']==2
        lib=session.core._lib
        meta=(ct.c_uint32*9)(*[99]*9)
        output=(ct.c_uint8*9)(*[99]*9)
        status=lib.fisica_core_session_poll(session._handle,meta,8,output,7)
        assert status==2 and meta[7]==8 and meta[8]==99 and list(output)==[99]*9
        assert session.poll()['payload'].hex()=='244347000000fff0'


def test_stop_queued_behind_probe_or_idle_command_completes():
    with NativeSession() as session:
        session.action(1,2,now_ms=0)
        drain(session)
        session.action(3,now_ms=1)
        drain(session)
        events=feed(session,'24434701322e302e31fff0',2)
        assert [e['operation'] for e in events if e['kind']==3] == [1,3]
        session.command('GET_BATTERY_LEVEL',now_ms=3)
        drain(session)
        session.action(3,now_ms=4)
        drain(session)
        events=feed(session,'243147010050fff0',5)
        assert [e['operation'] for e in events if e['kind']==3] == [5,3]


def test_stop_timeout_and_release_nack_are_not_success():
    with NativeSession() as session:
        measuring(session)
        session.action(4,now_ms=1)
        drain(session)
        session.tick(now_ms=2001)
        events=drain(session)
        assert [(e['operation'],e['status']) for e in events if e['kind']==3] == [(3,8),(4,8)]
        assert session.state==6
    with NativeSession() as session:
        measuring(session)
        session.action(4,now_ms=1)
        drain(session)
        feed(session,'245153010000fff0',2)
        events=feed(session,'242253000006fff0',3)
        assert events[-1]['operation']==4 and events[-1]['status']==9 and events[-1]['device_error']==6


def test_failed_initial_start_does_not_resume_after_link_loss():
    with NativeSession() as session:
        ready(session)
        session.action(2,-1,now_ms=1)
        drain(session)
        session.action(5,now_ms=2)
        events=drain(session)
        assert next(e for e in events if e['kind']==3)['status']==11
        session.action(1,2,now_ms=3)
        drain(session)
        events=feed(session,'24434701322e302e31fff0',4)
        assert not any(e['kind']==1 for e in events)


def test_measurement_only_after_start_ack_and_response_payload_preserved():
    vector=json.loads((VECTORS.parent/'legacy-processing.json').read_text())
    packet=bytes.fromhex(next(v['packet_hex'] for v in vector if v.get('id')=='synthetic-recorded-layout'))
    with NativeSession() as session:
        ready(session)
        session.feed(packet,now_ms=1)
        assert drain(session)==[]
        session.action(2,-1,now_ms=2)
        drain(session)
        feed(session,'242253010000fff0',3)
        session.feed(bytes.fromhex('245153010000fff0')+packet*12,now_ms=4)
        events=drain(session)
        frames=[e for e in events if e['kind']==4]
        assert len(frames)==12 and all(e['payload']==packet for e in frames)


@pytest.mark.parametrize('name',['SET_DEVICE_USB','SET_DEVICE_BLE','SET_DEVICE_STOP',
                                 'START_CONTINUOUS_MEASUREMENT','STOP_CONTINUOUS_MEASUREMENT',
                                 'GET_FIRMWARE_VERSION','STOP_MEASUREMENT_V1'])
def test_managed_session_commands_cannot_bypass_state_machine(name):
    with NativeSession() as session:
        ready(session)
        with pytest.raises(FisicaError) as error:
            session.command(name,now_ms=1)
        assert error.value.code in (1,4)
        assert drain(session)==[]


def test_stop_or_expiry_during_link_loss_cannot_confirm_hardware_stop():
    for timed in (False,True):
        with NativeSession() as session:
            measuring(session,100 if timed else -1)
            session.action(5,now_ms=10)
            drain(session)
            if timed:
                session.tick(now_ms=100)
            else:
                session.action(3,now_ms=20)
            events=drain(session)
            assert events[-1]['operation']==3 and events[-1]['status']==11
            session.action(4,now_ms=101)
            events=drain(session)
            assert events[-1]['operation']==4 and events[-1]['status']==11


def test_stop_cannot_override_release_and_duplicate_pending_requests_are_busy():
    with NativeSession() as session:
        measuring(session)
        session.action(4,now_ms=1)
        drain(session)
        for action in (3,4):
            with pytest.raises(FisicaError) as error:
                session.action(action,now_ms=2)
            assert error.value.code==7
        feed(session,'245153010000fff0',3)
        events=feed(session,'242253010000fff0',4)
        assert events[-1]['operation']==4 and events[-1]['status']==0
