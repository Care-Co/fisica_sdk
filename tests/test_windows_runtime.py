"""Windows transport regressions exercised without hardware on any host."""
import json
import time
import warnings
import asyncio
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from fisica_sdk import FisicaSDK, NativeCore, MockTransport, FisicaFirmwareWarning
from fisica_sdk import FisicaCommandError, FisicaError, FisicaErrorCode, FisicaTransportError
from fisica_sdk.receiver import Receiver
from fisica_sdk.serial_uart import SerialUART
from fisica_sdk import port_scanner

FIXTURES = Path(__file__).parent / 'fixtures'


def licensed_sdk():
    """An SDK in full mode (test key for this PC): these tests check every frame and raw data."""
    from tests.conftest import full_key
    sdk = FisicaSDK()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        sdk.configure_entitlement(token=full_key(), storage=None)
    return sdk

def protocol():
    return json.loads((FIXTURES / 'protocol.json').read_text())


def packet():
    fixtures = json.loads((FIXTURES / 'legacy-processing.json').read_text())
    return bytes.fromhex(next(f['packet_hex'] for f in fixtures if f.get('id') == 'synthetic-recorded-layout'))


def firmware_transport(core, response=None):
    response = response if response is not None else bytes.fromhex(protocol()['responses'][0]['hex'])
    exchanges = {core.command('GET_FIRMWARE_VERSION'): [response[:5], response[5:]]}
    for name, value in [('SET_DEVICE_USB',0), ('START_CONTINUOUS_MEASUREMENT',0),
                        ('STOP_CONTINUOUS_MEASUREMENT',0), ('SET_DEVICE_STOP',0),
                        ('SET_WEIGHT_ZERO',0), ('create_weight_scale',100)]:
        command = core.command(name,value=value)
        exchanges[command] = [bytes([0x24,command[1],command[2],1,0,0,0xff,0xf0])]
    return MockTransport(exchanges)


def wait_until(predicate):
    deadline = time.monotonic() + 4
    while not predicate():
        assert time.monotonic() < deadline, 'SDK operation did not complete'
        time.sleep(0.01)


def test_windows_com_ports_do_not_depend_on_english_usb_description(monkeypatch):
    import serial.tools.list_ports
    port = SimpleNamespace(device='COM7', description='串行桥接器', vid=4292,
                           pid=60000, serial_number='scale-test')
    monkeypatch.setattr(serial.tools.list_ports, 'comports', lambda: [port])
    assert port_scanner.find_serial_com_ports() == ['COM7']
    assert port_scanner.find_serial_devices()[0]['serial_number'] == 'scale-test'


def test_usb_discovery_does_not_hide_bluetooth(monkeypatch):
    wired = {'id': 'COM7', 'name': 'Fisica Scale', 'type': 'Serial'}
    wireless = {'id': 'ble-test', 'name': 'Fisica Scale', 'type': 'Bluetooth'}
    monkeypatch.setattr(port_scanner, 'find_serial_devices', lambda: [wired])
    monkeypatch.setattr(port_scanner, 'find_bluetooth_com_ports', lambda **kwargs: [wireless])
    assert {d['type'] for d in port_scanner.find_all_com_ports()} == {'Serial', 'Bluetooth'}
    assert port_scanner.find_all_com_ports(transport='Serial')[0]['id'] == 'COM7'


def test_ble_startup_timeout_cleans_up_without_replacing_host_event_loop(monkeypatch):
    import bleak
    stopped = []
    class StalledScanner:
        def __init__(self, callback):
            pass
        async def start(self):
            await asyncio.sleep(10)
        async def stop(self):
            stopped.append(True)
    monkeypatch.setattr(bleak, 'BleakScanner', StalledScanner)
    monkeypatch.setattr(port_scanner, 'BLE_START_TIMEOUT', 0.02)
    async def app():
        loop = asyncio.get_running_loop()
        with pytest.raises(FisicaTransportError, match='did not finish'):  # not "no Scale nearby"
            port_scanner.find_bluetooth_com_ports(timeout=0.01)
        assert asyncio.get_running_loop() is loop
    asyncio.run(app())
    assert stopped == [True]


def test_serial_open_is_explicit_and_command_lists_are_written_as_bytes(monkeypatch):
    import serial
    writes = []
    fake = SimpleNamespace(is_open=False, open=lambda: None,
                           write=lambda data: writes.append(data) or len(data), close=lambda: None)
    kwargs_seen = []
    monkeypatch.setattr(serial, 'Serial', lambda **kwargs: kwargs_seen.append(kwargs) or fake)
    uart = SerialUART('COM7')
    assert 'port' not in kwargs_seen[0]
    # A real Scale answers only at 115200, and only while DTR is asserted.
    assert kwargs_seen[0]['baudrate'] == 115200
    assert fake.dtr is True and fake.rts is True
    uart.connect()
    uart.send([0x24, 0xff, 0xf0])
    assert writes == [b'\x24\xff\xf0']
    assert isinstance(writes[0], bytes)
    fake.write = lambda data: 0
    with pytest.raises(FisicaTransportError) as error:
        uart.send([1])
    assert error.value.code == FisicaErrorCode.TRANSPORT_ERROR


def test_recovery_never_selects_another_scale_and_tracks_usb_serial_number():
    selected = {'id': 'COM7', 'type': 'Serial', 'serial_number': 'original', 'vid': 4292, 'pid': 60000}
    receiver = Receiver(selected, lambda frame: None)
    other = dict(selected, id='COM8', serial_number='other')
    moved = dict(selected, id='COM9')
    assert receiver._find_original_device([other]) is None
    assert receiver._find_original_device([other, moved]) == moved
    assert receiver._find_original_device([moved, dict(moved, id='COM10')]) is None
    without_identity = Receiver({'id': 'COM7', 'type': 'Serial'}, lambda frame: None)
    assert without_identity._find_original_device([{'id': 'COM8', 'type': 'Serial'}]) is None


def test_stop_cleans_up_even_when_device_write_fails():
    core = NativeCore()
    class FailedStop(MockTransport):
        def send(self, data):
            if data == core.command('STOP_CONTINUOUS_MEASUREMENT'):
                raise FisicaTransportError('Disconnected')
            super().send(data)
    receiver = Receiver({'id': 'mock', 'type': 'Serial'}, lambda frame: None,
                        transport=FailedStop(firmware_transport(core).exchanges))
    with pytest.warns(FisicaFirmwareWarning):
        receiver.conn()
    receiver.start()
    with pytest.raises(FisicaTransportError):
        receiver.stop()
    with pytest.raises(FisicaError) as error:
        receiver.discon()
    assert error.value.code == 6
    assert not receiver._transport.connected


def test_pending_recovery_cannot_be_reactivated_by_a_new_start():
    receiver = Receiver({'id': 'mock', 'type': 'Serial'}, lambda frame: None)
    receiver.check_thread = SimpleNamespace(is_alive=lambda: True)
    receiver._check_stop_event.set()
    with pytest.raises(FisicaTransportError, match='still stopping'):
        receiver.start()
    assert receiver._check_stop_event.is_set()


def test_uart_is_closed_even_if_handler_cleanup_fails():
    receiver = Receiver({'id': 'mock', 'type': 'Serial'}, lambda frame: None)
    closed = []
    def fail():
        raise FisicaTransportError('Handler cleanup failed')
    receiver._handler = SimpleNamespace(close=fail)
    receiver._uart = SimpleNamespace(close=lambda: closed.append(True))
    with pytest.raises(FisicaTransportError):
        receiver._close_transport()
    assert closed == [True] and receiver._uart is None and receiver._handler is None


def test_disconnect_error_clears_sdk_connection_state():
    sdk = FisicaSDK()
    def fail():
        raise FisicaTransportError('Device unplugged')
    sdk._receiver = SimpleNamespace(discon=fail)
    sdk._connected_device = 'mock'
    sdk._connected = True
    sdk.disconnect()
    with pytest.raises(FisicaTransportError):
        sdk.wait()
    assert sdk.get_device_info() is None and not sdk._connected


def test_unverified_firmware_warns_and_keeps_connection_usable():
    core = NativeCore()
    transport = firmware_transport(core)
    sdk = FisicaSDK()
    try:
        with pytest.warns(FisicaFirmwareWarning) as notices:
            sdk.connect({'id': 'mock', 'type': 'Serial'}, transport=transport)
        assert next(n for n in notices if n.category is FisicaFirmwareWarning).message.code == 'FIRMWARE_UNVERIFIED'
        assert sdk.get_device_info()['firmware_version'] == '2.0.1'
        assert not sdk.get_device_info()['firmware_verified']
        sdk.set_zero()
        sdk.set_scale(100)
        assert core.command('SET_WEIGHT_ZERO') in transport.sent
        assert core.command('create_weight_scale', value=100) in transport.sent
    finally:
        sdk.disconnect()
        sdk.wait()
    assert not transport.connected


def test_firmware_timeout_does_not_block_connection():
    sdk, transport = FisicaSDK(), MockTransport()
    try:
        with pytest.warns(FisicaFirmwareWarning, match='unknown'):
            sdk.connect({'id': 'mock', 'type': 'Serial'}, transport=transport)
        assert sdk.get_device_info()['firmware_version'] is None
        assert transport.connected
    finally:
        sdk.disconnect()
        sdk.wait()


def test_battery_is_read_from_the_scale_and_carried_into_reports(tmp_path):
    """Measurement packets have no battery field; GET_BATTERY_LEVEL is the real reading."""
    core = NativeCore()
    transport = firmware_transport(core)
    battery = core.command('GET_BATTERY_LEVEL')
    transport.exchanges[battery] = [bytes.fromhex('243147010050fff0')]  # 0x50 = 80 %
    transport.exchanges[core.command('START_CONTINUOUS_MEASUREMENT')] = [bytes.fromhex('245153010000fff0') + packet() * 12]
    sdk = licensed_sdk()
    try:
        with pytest.warns(FisicaFirmwareWarning):
            sdk.connect({'id': 'mock', 'type': 'Serial'}, transport=transport)
        # An injected transport is never queried automatically: it may not answer.
        assert battery not in transport.sent and sdk.get_device_info()['battery'] is None
        assert sdk.get_battery() == 80
        assert sdk.get_device_info()['battery'] == 80 and sdk.get_device_info()['battery_valid'] is True
        frames = []
        sdk.on_data(frames.append)
        sdk.start_measurement()
        wait_until(lambda: len(frames) == 12)
        assert sdk.get_battery() == 80  # also answers during a measurement
        sdk.stop_measurement()
        sdk.wait()
        report = sdk.analyze()
        assert (report.battery, report.battery_valid) == (80, True)
        assert all(frame.battery_valid is False for frame in report.total_frame)  # packets carry no battery
        sdk.export_report(report, output=str(tmp_path / 'report.json'))
        exported = json.loads((tmp_path / 'report.json').read_text())
        assert (exported[0]['battery'], exported[0]['battery_valid']) == (80, True)
    finally:
        sdk.disconnect()
        sdk.wait()
    assert transport.sent.count(battery) == 2


def test_battery_refused_by_the_scale_raises_and_keeps_no_reading():
    core = NativeCore()
    transport = firmware_transport(core)
    transport.exchanges[core.command('GET_BATTERY_LEVEL')] = [bytes.fromhex('243147000001fff0')]  # NACK
    sdk = FisicaSDK()
    try:
        with pytest.warns(FisicaFirmwareWarning):
            sdk.connect({'id': 'mock', 'type': 'Serial'}, transport=transport)
        with pytest.raises(FisicaCommandError):
            sdk.get_battery()
        assert sdk.get_device_info()['battery_valid'] is False
    finally:
        sdk.disconnect()
        sdk.wait()
    with pytest.raises(RuntimeError, match='Connect a Scale'):
        sdk.get_battery()


def test_public_sdk_measurement_reports_preserve_raw_data_and_validity(tmp_path):
    core = NativeCore()
    transport = firmware_transport(core)
    transport.exchanges[core.command('START_CONTINUOUS_MEASUREMENT')] = [bytes.fromhex('245153010000fff0') + packet() * 12]
    sdk = licensed_sdk()
    try:
        with pytest.warns(FisicaFirmwareWarning):
            sdk.connect({'id': 'mock', 'type': 'Serial'}, transport=transport)
        sdk.set_metadata(id='offline-test', name='synthetic')
        callbacks = []
        sdk.on_data(callbacks.append)
        sdk.start_measurement()
        wait_until(lambda: len(callbacks) == 12)
        sdk.stop_measurement()
        sdk.wait()
        assert core.command('SET_DEVICE_USB') in transport.sent
        assert core.command('SET_DEVICE_BLE') not in transport.sent
        frames = sdk.get_session_frames()
        assert frames[0]['raw_data'] == list(packet())
        assert np.shape(frames[0]['raw_grid']) == (29, 22)
        assert frames[0]['battery_valid'] is False
        report = sdk.analyze()
        assert report.weight == 70.5 and not report.battery_valid
        assert sdk.get_report()[0]['battery_valid'] is False
        sdk.export_report(output=str(tmp_path / 'report.json'))
        exported = json.loads((tmp_path / 'report.json').read_text())
        assert exported[0]['total_frame'][0]['raw_data'] == list(packet())
        sdk.reset_session()
        sdk.reset_reports()
        assert sdk.get_all_reports() == []
    finally:
        sdk.stop_measurement()
        sdk.wait()
        sdk.disconnect()
        sdk.wait()
    assert core.command('SET_DEVICE_STOP') in transport.sent


def test_start_failure_is_reported_by_wait_instead_of_hanging():
    core = NativeCore()
    class FailingStart(MockTransport):
        def send(self, data):
            if bytes(data) == core.command('START_CONTINUOUS_MEASUREMENT'):
                raise FisicaTransportError('Simulated write failure')
            return super().send(data)
    transport = FailingStart(firmware_transport(core).exchanges)
    sdk = FisicaSDK()
    try:
        with pytest.warns(FisicaFirmwareWarning):
            sdk.connect({'id': 'mock', 'type': 'Serial'}, transport=transport)
        sdk.start_measurement()
        wait_until(lambda: not sdk._starting)
        with pytest.raises(FisicaTransportError):
            sdk.wait()
    finally:
        sdk.disconnect()
        with pytest.raises(FisicaError) as close_error:
            sdk.wait()
        assert close_error.value.code == 6


def test_native_error_code_survives_python_exception_mapping():
    with pytest.raises(FisicaError) as error:
        NativeCore().command('no-such-command')
    assert isinstance(error.value, ValueError)
    assert error.value.code == FisicaErrorCode.UNKNOWN_COMMAND
    with pytest.raises(FisicaError) as error:
        NativeCore().display_grid([[1]])
    assert error.value.code == FisicaErrorCode.INVALID_ARGUMENT


def test_duration_measurement_and_repeated_sessions_finish():
    core = NativeCore()
    transport = firmware_transport(core)
    transport.exchanges[core.command('START_CONTINUOUS_MEASUREMENT')] = [bytes.fromhex('245153010000fff0') + packet() * 4]
    sdk = FisicaSDK()
    try:
        with pytest.warns(FisicaFirmwareWarning):
            sdk.connect({'id': 'mock', 'type': 'Serial'}, transport=transport)
        for index in range(2):
            sdk.set_metadata(id=f'timed-{index}')
            sdk.start_measurement(duration=0.6)
            sdk.wait()
            assert sdk.analyze().weight == 70.5
    finally:
        sdk.disconnect()
        sdk.wait()


def test_missing_start_ack_surfaces_stable_timeout_and_stops_wait():
    core=NativeCore()
    transport=firmware_transport(core)
    transport.exchanges.pop(core.command('START_CONTINUOUS_MEASUREMENT'))
    sdk=FisicaSDK()
    with pytest.warns(FisicaFirmwareWarning):
        sdk.connect({'id':'mock','type':'Serial'},transport=transport)
    sdk.start_measurement()
    wait_until(lambda: not sdk._starting)
    with pytest.raises(FisicaError) as error:
        sdk.wait()
    assert error.value.code==FisicaErrorCode.COMMAND_TIMEOUT and not sdk._working
    sdk.disconnect()
    with pytest.raises(FisicaError) as close_error:
        sdk.wait()
    assert close_error.value.code==FisicaErrorCode.INVALID_STATE and not transport.connected


def test_stop_nack_is_reported_by_public_wait_with_device_code():
    core=NativeCore()
    transport=firmware_transport(core)
    transport.exchanges[core.command('STOP_CONTINUOUS_MEASUREMENT')]=[bytes.fromhex('245153000005fff0')]
    sdk=FisicaSDK()
    with pytest.warns(FisicaFirmwareWarning):
        sdk.connect({'id':'mock','type':'Serial'},transport=transport)
    sdk.start_measurement()
    wait_until(lambda: sdk._working)
    sdk.stop_measurement()
    with pytest.raises(FisicaError) as error:
        sdk.wait()
    assert error.value.code==FisicaErrorCode.DEVICE_REJECTED and error.value.device_code==5
    sdk.disconnect()
    with pytest.raises(FisicaError):
        sdk.wait()
    assert not transport.connected


def test_disconnect_during_measurement_uses_core_stop_and_release():
    core=NativeCore()
    transport=firmware_transport(core)
    transport.exchanges[core.command('START_CONTINUOUS_MEASUREMENT')][0]+=packet()*3
    sdk = licensed_sdk()
    with pytest.warns(FisicaFirmwareWarning):
        sdk.connect({'id':'mock','type':'Serial'},transport=transport)
    sdk.start_measurement()
    wait_until(lambda: sdk._working and len(sdk._frames)==3)
    sdk.disconnect()
    wait_until(lambda: not sdk._disconnecting)
    sdk.wait()
    assert not sdk._working and not transport.connected
    assert len(sdk.get_session_frames())==3
    assert transport.sent[-2:]==[core.command('STOP_CONTINUOUS_MEASUREMENT'),core.command('SET_DEVICE_STOP')]


def test_slow_app_callback_cannot_drop_recorded_frames_when_stopped():
    import threading
    core=NativeCore()
    transport=firmware_transport(core)
    transport.exchanges[core.command('START_CONTINUOUS_MEASUREMENT')][0]+=packet()*12
    sdk = licensed_sdk()
    entered, release=threading.Event(), threading.Event()
    def callback(frame):
        entered.set()
        release.wait(3)
    with pytest.warns(FisicaFirmwareWarning):
        sdk.connect({'id':'mock','type':'Serial'},transport=transport)
    try:
        sdk.on_data(callback)
        sdk.start_measurement()
        assert entered.wait(2)
        wait_until(lambda: sdk._working)
        sdk.stop_measurement()
        sdk.wait()
        assert len(sdk.get_session_frames())==12
    finally:
        release.set()
        sdk.disconnect()
        sdk.wait()


def test_legacy_zero_duration_remains_unlimited_until_manual_stop():
    core=NativeCore()
    transport=firmware_transport(core)
    sdk=FisicaSDK()
    with pytest.warns(FisicaFirmwareWarning):
        sdk.connect({'id':'mock','type':'Serial'},transport=transport)
    try:
        sdk.start_measurement(duration=0)
        wait_until(lambda: sdk._working)
        time.sleep(.05)
        assert sdk._working and core.command('STOP_CONTINUOUS_MEASUREMENT') not in transport.sent
        sdk.stop_measurement()
        sdk.wait()
    finally:
        sdk.disconnect()
        sdk.wait()
