import json
from pathlib import Path
import pytest
from fisica_sdk import NativeCore
from tests.test_native_core import split_stream

ROOT = Path(__file__).parent / 'fixtures'


def test_shared_command_and_response_vectors():
    core = NativeCore()
    vectors = json.loads((ROOT / 'protocol.json').read_text())
    for command in vectors['commands']:
        assert core.command(command['name']).hex() == command['hex']
    for response in vectors['responses']:
        decoded = core.decode_response(bytes.fromhex(response['hex']))
        assert (decoded['header'], decoded['type'], decoded['success']) == (response['header'], response['type'], response['success'])
        assert decoded['payload'].hex() == response['payload_hex']


@pytest.mark.parametrize('chunk_size', [1, 7, 244, 644, 645, 1300])
def test_recorded_footer_layout_and_variable_response_in_fragmented_stream(chunk_size, full_license):
    fixtures = json.loads((ROOT / 'legacy-processing.json').read_text())
    packet = bytes.fromhex(next(f['packet_hex'] for f in fixtures if f.get('id') == 'synthetic-recorded-layout'))
    response = bytes.fromhex(json.loads((ROOT / 'protocol.json').read_text())['responses'][0]['hex'])
    stream = response + packet + packet + response
    frames, responses, buffer = split_stream(NativeCore(), [stream[start:start+chunk_size] for start in range(0, len(stream), chunk_size)],
                                             full_license)
    assert not buffer
    assert len(frames) == 2 and len(responses) == 2
    assert frames[0].weight == 70.5
    assert responses[0]['payload'] == b'2.0.1'


def test_long_response_does_not_overflow_wrapper_packet_buffer():
    packet = bytes.fromhex('24424701') + b'A' * 900 + bytes.fromhex('fff0')
    consumed, kind, output = NativeCore().next_packet(packet)
    assert (consumed, kind, output) == (len(packet), 2, packet)


def test_truncated_variable_response_waits_for_more_bytes():
    consumed, kind, packet = NativeCore().next_packet(bytes.fromhex('24434701322e302e31ff'))
    assert (consumed, kind, packet) == (0, 0, b'')
