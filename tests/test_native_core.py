import ctypes as ct
import hashlib
import json
from pathlib import Path
from queue import Queue
import numpy as np
import pytest
from fisica_sdk import NativeCore
from fisica_sdk.models import MeasurementFrame
from fisica_sdk.parser import DataParser


@pytest.fixture(scope='session')
def core():
    return NativeCore()


def make_packet(weight=705):
    packet = bytearray(645)
    packet[1:639] = bytes((i % 16 for i in range(638)))
    packet[638:640] = b'\xf0\x0f'
    packet[641:643] = weight.to_bytes(2, 'big')
    packet[643:645] = b'\xf0\x0f'
    return bytes(packet)


def test_decoded_data_contract(core):
    packet = make_packet()
    result = core.decode_packet(packet)
    assert result.weight == 70.5
    assert result.battery == 99 and not result.battery_valid
    assert np.shape(result.raw_grid) == (29,22)
    assert np.shape(result.sensor_matrix) == (29,29)
    assert np.array_equal(np.array(result.raw_grid).ravel(),np.frombuffer(packet[1:639],dtype=np.uint8))
    assert np.count_nonzero(np.array(result.sensor_matrix)[:,11:18]) == 0
    assert result.raw_data == list(packet)


def test_legacy_processing_regression_fixtures(core):
    fixtures = json.loads((Path(__file__).parent / 'fixtures' / 'legacy-processing.json').read_text())
    for fixture in fixtures:
        result = core.decode_packet(bytes.fromhex(fixture['packet_hex']))
        encoded = np.asarray(result.sensor_matrix,dtype='<f8').tobytes()
        assert hashlib.sha256(encoded).hexdigest() == fixture['display_sha256']
        assert result.weight == fixture['weight']


@pytest.mark.parametrize('size',[0,1,644,646,10000])
def test_reject_invalid_packet_length(core,size):
    with pytest.raises(ValueError):
        core.decode_packet(bytes(size))


@pytest.mark.parametrize('bad',[np.nan,np.inf,-1.0])
def test_reject_invalid_numeric_values(core,bad):
    grid = np.zeros((29,22))
    grid[0,0] = bad
    with pytest.raises(ValueError):
        core.preprocess(grid)


@pytest.mark.parametrize('chunk_size',[1,7,8,20,244,644,645,1300])
def test_fragmented_and_concatenated_frames_with_responses(core,chunk_size):
    first, second = make_packet(705), make_packet(719)
    response = bytes.fromhex('242253010000fff0')
    stream = response + first + second + response
    frames, responses = Queue(), Queue()
    parser = DataParser(frames,responses)
    buffer = bytearray()
    for start in range(0,len(stream),chunk_size):
        buffer.extend(stream[start:start+chunk_size])
        parser.process_all_data_by_terminator(buffer)
    assert buffer == b''
    assert [frames.get().weight for _ in range(frames.qsize())] == [70.5,71.9]
    assert responses.qsize() == 2
    assert responses.get()['success']


def test_command_bytes_inside_complete_grid_are_not_response(core):
    packet = bytearray(make_packet())
    packet[20:28] = bytes.fromhex('242253010000fff0')
    consumed,kind,data = core.next_packet(packet)
    assert (consumed,kind,data) == (645,1,bytes(packet))


@pytest.mark.parametrize('chunk_size',[1,37,64])
def test_command_bytes_inside_partial_grid_are_not_response(core,chunk_size):
    packet = bytearray(make_packet())
    packet[20:28] = bytes.fromhex('242253010000fff0')
    frames,responses = Queue(),Queue()
    parser = DataParser(frames,responses)
    buffer = bytearray()
    for start in range(0,len(packet),chunk_size):
        buffer.extend(packet[start:start+chunk_size])
        parser.process_all_data_by_terminator(buffer)
    assert frames.qsize() == 1 and responses.qsize() == 0
    assert bytes(frames.get().raw_data) == bytes(packet)


def test_noise_is_bounded_and_partial_frame_preserved(core):
    packet = make_packet()
    buffer = bytearray(b'noise'*2000 + packet[:500])
    consumed,kind,_ = core.next_packet(buffer)
    assert consumed > 0 and kind == 0
    del buffer[:consumed]
    buffer.extend(packet[500:])
    consumed,kind,data = core.next_packet(buffer)
    assert kind == 1 and data == packet


def test_analysis_and_cop(core):
    grid = np.zeros((29,29))
    grid[4,3] = 100
    grid[4,20] = 100
    frames = [MeasurementFrame('2026-10-13',grid.tolist(),70.5,99) for _ in range(12)]
    assert core.analyze(frames) == (70.5,1,1,1,1)
    assert core.cop([[0,0],[2,2]]) == (1,1.5)
    assert core.cop([[0,0]]) == (0,0)
    assert core.analyze(frames[:1]) is None
    assert core.analyze([MeasurementFrame('',[[0,0]],70,99)]*12) is None


def test_abi_capacity_guard_preserves_canary(core):
    packet = (ct.c_uint8*645).from_buffer_copy(make_packet())
    raw = (ct.c_double*639)(*([42]*639))
    display = (ct.c_double*841)()
    metrics = (ct.c_double*2)()
    status = core._lib.fisica_core_decode_packet(packet,645,raw,637,display,841,metrics,2)
    assert status == 2
    assert list(raw) == [42]*639


def test_named_command_and_input_validation(core):
    assert core.command('START_CONTINUOUS_MEASUREMENT').hex() == '245153000001fff0'
    assert core.command('create_weight_scale',value=-32768).hex() == '241053008000fff0'
    for value in (-32769,32768,1.5,np.nan):
        with pytest.raises(ValueError):
            core.command('create_weight_scale',value=value)
    with pytest.raises(ValueError):
        core.command('unknown')


def test_core_is_required_not_silently_replaced_by_python(tmp_path):
    from fisica_sdk import CoreCompatibilityError
    with pytest.raises(CoreCompatibilityError,match='Native core is missing'):
        NativeCore(tmp_path / 'missing.so')
