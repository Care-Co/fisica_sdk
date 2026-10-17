"""SDK command worker, sleep(), BLE transport, license replacement and logging, without hardware."""
import asyncio
import json
import logging
import subprocess
import sys
import threading
import time
import warnings
from pathlib import Path
import pytest
from fisica_sdk import FisicaSDK, NativeCore, FisicaCommandError, FisicaTransportError, codes
from fisica_sdk import bluetooth_handler, port_scanner
from fisica_sdk.bluetooth_handler import BluetoothHandler
from fisica_sdk.receiver import Receiver
from conftest import full_key
from tools.entitlement_dev import issue_token
from test_windows_runtime import firmware_transport, licensed_sdk, packet, protocol, wait_until

ROOT = Path(__file__).resolve().parents[1]
MOCK = {'id': 'mock', 'type': 'Serial'}


def connect(sdk, transport):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        sdk.connect(MOCK, transport=transport)


def wait_within(sdk, seconds=6):
    """sdk.wait() with a deadline: a hang fails the test instead of stalling the suite."""
    outcome = {}

    def run():
        try:
            sdk.wait()
        except Exception as error:
            outcome['error'] = error
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    assert not thread.is_alive(), 'wait() hung'
    if 'error' in outcome:
        raise outcome['error']


def measuring_transport(core, frames=0):
    transport = firmware_transport(core)
    transport.exchanges[core.command('START_CONTINUOUS_MEASUREMENT')][0] += packet() * frames
    return transport


# Command worker ------------------------------------------------------------------------------

def test_start_followed_at_once_by_stop_and_repeated_cycles_finish():
    core = NativeCore()
    transport = measuring_transport(core, frames=3)
    sdk = licensed_sdk()
    connect(sdk, transport)
    try:
        for _ in range(5):
            sdk.start_measurement()
            sdk.stop_measurement()
            wait_within(sdk)
            assert not (sdk._starting or sdk._stopping or sdk._working)
        assert transport.sent.count(core.command('START_CONTINUOUS_MEASUREMENT')) == 5
        assert transport.sent.count(core.command('STOP_CONTINUOUS_MEASUREMENT')) == 5
        sdk.start_measurement(duration=0.2)
        wait_within(sdk)
        assert sdk.analyze().weight == 70.5
    finally:
        sdk.disconnect()
        wait_within(sdk)
    assert not transport.connected


def test_a_second_start_reports_an_error_and_keeps_the_running_measurement():
    core = NativeCore()
    transport = measuring_transport(core)
    sdk = FisicaSDK()
    connect(sdk, transport)
    try:
        sdk.start_measurement()
        sdk.start_measurement()
        wait_until(lambda: not sdk._starting)
        assert sdk._working and core.command('STOP_CONTINUOUS_MEASUREMENT') not in transport.sent
        sdk.stop_measurement()
        with pytest.raises(RuntimeError, match='already running'):
            wait_within(sdk)
    finally:
        sdk.disconnect()
        wait_within(sdk)


def test_overlapping_sleeps_do_not_clear_each_other():
    sdk, done = FisicaSDK(), []
    sdk.sleep(1.0, after=lambda: done.append('long'))
    sdk.sleep(0.05, after=lambda: done.append('short'))
    assert sdk._sleeping == 2  # counted before the threads start
    wait_until(lambda: done == ['short'])
    assert sdk._sleeping == 1
    wait_within(sdk)
    assert done == ['short', 'long'] and sdk._sleeping == 0


def test_a_failing_sleep_callback_is_raised_by_wait_once():
    sdk = FisicaSDK()

    def broken():
        raise ValueError('callback broke')
    sdk.sleep(0.01, after=broken)
    with pytest.raises(ValueError, match='callback broke'):
        wait_within(sdk)
    assert sdk._sleeping == 0
    wait_within(sdk)
    sdk.sleep(0.01, after=sdk.wait)  # would wait for itself
    with pytest.raises(RuntimeError, match='callback'):
        wait_within(sdk)


def test_stop_requested_during_sleep_takes_effect_after_it():
    core = NativeCore()
    transport = measuring_transport(core)
    sdk = FisicaSDK()
    connect(sdk, transport)
    stop = core.command('STOP_CONTINUOUS_MEASUREMENT')
    try:
        sdk.start_measurement()
        sdk.sleep(0.4)
        sdk.stop_measurement()
        time.sleep(0.2)
        assert sdk._working and stop not in transport.sent
        wait_within(sdk)
        assert not sdk._working and transport.sent.count(stop) == 1
        sdk.start_measurement()
        sdk.sleep(0.1, after=sdk.stop_measurement)  # the GUI pattern
        wait_within(sdk)
        assert not sdk._working and transport.sent.count(stop) == 2
    finally:
        sdk.disconnect()
        wait_within(sdk)


def test_settings_need_a_connected_scale():
    sdk = FisicaSDK()
    for call in (sdk.set_zero, lambda: sdk.set_scale(100), sdk.get_battery):
        with pytest.raises(RuntimeError, match='Connect a Scale'):
            call()


def test_frames_are_bounded_by_the_analysis_limit(monkeypatch):
    import fisica_sdk.fisica_sdk as module
    monkeypatch.setattr(module, '_ANALYSIS_FRAMES', 5)
    core = NativeCore()
    transport = measuring_transport(core, frames=12)
    sdk = licensed_sdk()
    connect(sdk, transport)
    try:
        callbacks = []
        sdk.on_data(callbacks.append)
        for expected in (12, 24):  # a second measurement without analyze() keeps the bound
            sdk.start_measurement()
            wait_until(lambda: len(callbacks) == expected)
            sdk.stop_measurement()
            wait_within(sdk)
            assert len(sdk._frames) == 5 and len(sdk.get_session_frames()) == 5
        report = sdk.analyze()
        assert report.frame_count == 5 and report.weight == 70.5
        assert report.battery is None and not report.battery_valid  # packets carry no battery field
        assert sdk.get_report()[0]['total_frame'][0]['battery'] is None
    finally:
        sdk.disconnect()
        wait_within(sdk)


def test_disconnect_during_basic_mode_measurement_uses_the_core_analysis(monkeypatch):
    import fisica_sdk.fisica_sdk as module
    monkeypatch.setattr(module, 'global_analyzer', lambda frames: pytest.fail('analyzed the 1-fps frames'))
    core = NativeCore()
    transport = measuring_transport(core, frames=12)
    sdk = FisicaSDK()  # basic mode: one frame a second
    connect(sdk, transport)
    sdk.start_measurement()
    wait_until(lambda: len(sdk._frames) == 1)
    time.sleep(0.2)
    sdk.disconnect()
    wait_within(sdk)
    assert len(sdk.get_session_frames()) == 1
    assert sdk.analyze().weight == 70.5  # every packet, analyzed by the core


def test_a_failing_on_data_callback_is_logged_and_measuring_continues(caplog):
    core = NativeCore()
    transport = measuring_transport(core, frames=3)
    sdk = licensed_sdk()
    connect(sdk, transport)
    calls = []

    def broken(frame):
        calls.append(frame)
        raise ValueError('app bug')
    try:
        sdk.on_data(broken)
        with caplog.at_level(logging.ERROR, logger='fisica_sdk'):
            sdk.start_measurement()
            wait_until(lambda: len(calls) == 3)
            sdk.stop_measurement()
            wait_within(sdk)
        failures = [r for r in caplog.records if r.getMessage() == 'Measurement callback failed']
        assert len(failures) == 3 and all(r.exc_info for r in failures)
        assert len(sdk.get_session_frames()) == 3
    finally:
        sdk.disconnect()
        wait_within(sdk)


# License -------------------------------------------------------------------------------------

def test_configure_entitlement_for_the_stored_key_renews_with_the_developer_portal(monkeypatch):
    from fisica_sdk.entitlement import DEFAULT_ENDPOINT, Entitlement
    sdk = FisicaSDK()
    with warnings.catch_warnings(), monkeypatch.context() as offline:
        warnings.simplefilter('ignore')
        offline.setattr(Entitlement, '_post', lambda *args: pytest.fail('no server may be contacted'))
        assert sdk.configure_entitlement()['reason'] == 'no_key'  # nothing stored in the test storage
        assert sdk._entitlement._endpoint == DEFAULT_ENDPOINT  # as without configure_entitlement()
        assert sdk.sync_entitlement() == {'refreshed': False, 'activated': False, 'error': None}
        sdk.configure_entitlement(online=False)
        assert sdk._entitlement._endpoint == DEFAULT_ENDPOINT and sdk.sync_entitlement() is None
        sdk.configure_entitlement(token=full_key(), storage=None)
        assert sdk._entitlement._endpoint is None and sdk.sync_entitlement() is None  # explicit key: no default
    unbound = issue_token('acme', 'ctr-1', int(time.time()), key='key_test_1', track='trial',
                          full_until=int(time.time()) + 14 * 86400)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        sdk.configure_entitlement(token=unbound, endpoint='http://127.0.0.1:9', storage=None, timeout=0.2)
        result = sdk.sync_entitlement(wait=True)  # activation is due; the server cannot be reached
    assert result['error'].startswith('network') and not result['activated']  # reported, never raised


def test_replacing_the_entitlement_mid_measurement_keeps_measuring():
    core = NativeCore()
    transport = measuring_transport(core)
    sdk = licensed_sdk()
    connect(sdk, transport)
    frames = []
    sdk.on_data(frames.append)
    try:
        sdk.start_measurement()
        wait_until(lambda: sdk._working)
        transport.feed(packet() * 3)
        wait_until(lambda: len(frames) == 3)
        previous = sdk._entitlement
        closed = []
        original_close = previous.close
        previous.close = lambda: (closed.append(sdk._receiver._license() is not previous), original_close())
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            sdk.configure_entitlement(token=full_key(), storage=None)
        assert closed == [True]  # the new license was in place before the old context closed
        transport.feed(packet() * 3)
        wait_until(lambda: len(frames) == 6)  # still full rate
        sdk.stop_measurement()
        wait_within(sdk)
        assert sdk.analyze().weight == 70.5
    finally:
        sdk.disconnect()
        wait_within(sdk)


def test_a_license_that_cannot_be_applied_falls_back_to_basic_mode():
    class Broken:
        def license_session(self, session):
            raise RuntimeError('license context closed')
    core = NativeCore()
    transport = measuring_transport(core, frames=6)
    frames = []
    receiver = Receiver(MOCK, frames.append, transport=transport, license=Broken)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        receiver.conn()
    try:
        receiver.refresh_license()
        assert isinstance(receiver.license_error, RuntimeError)
        receiver.start()
        wait_until(lambda: frames)
        receiver._license_applied = 0  # the pump's periodic refresh fails too
        time.sleep(0.2)
        assert receiver.check_thread.is_alive() and receiver._worker_error is None
        assert len(frames) == 1  # basic mode: one frame a second
        receiver.stop()
        assert receiver.analyze()[0] == 70.5
    finally:
        receiver.discon()


# Scanning ------------------------------------------------------------------------------------

def test_bluetooth_scan_failure_is_an_error_not_an_empty_list(monkeypatch, caplog):
    import bleak

    class Off:
        def __init__(self, callback):
            pass

        async def start(self):
            raise OSError('Bluetooth is turned off')

        async def stop(self):
            pass
    monkeypatch.setattr(bleak, 'BleakScanner', Off)
    monkeypatch.setattr(port_scanner, 'find_serial_devices', lambda: [{'id': 'COM7', 'name': 'Fisica Scale', 'type': 'Serial'}])
    with pytest.raises(FisicaTransportError, match='turned off'):
        FisicaSDK().scan_devices(transport='Bluetooth')
    with caplog.at_level(logging.ERROR, logger='fisica_sdk'):
        assert [device['id'] for device in port_scanner.find_all_com_ports()] == ['COM7']
    assert 'Bluetooth scan failed' in caplog.text


# BLE transport with a fake BleakClient -------------------------------------------------------

class FakeCharacteristic:
    def __init__(self, uuid):
        self.uuid = uuid


class FakeService:
    def __init__(self, *uuids):
        self.uuid = '0000fe00-0000-1000-8000-00805f9b34fb'
        self.characteristics = [FakeCharacteristic(uuid) for uuid in uuids]


NORDIC = codes.BLE_PROFILES['nordic_uart']


class FakeBleakClient:
    """Scripted Scale behind bleak: replies to known commands through the notify callback."""
    instances = []
    services = [FakeService('6e400001-b5a3-f393-e0a9-e50e24dcca9e', NORDIC['write'], NORDIC['notify'])]
    replies = {}
    write_delay = 0

    def __init__(self, address, disconnected_callback=None, **kwargs):
        self.address, self.disconnected_callback = address, disconnected_callback
        self.is_connected, self.writes, self.notify = False, [], None
        FakeBleakClient.instances.append(self)

    async def connect(self):
        self.is_connected = True

    async def disconnect(self):
        self.is_connected = False

    async def start_notify(self, characteristic, callback):
        self.notify = (characteristic, callback)

    async def stop_notify(self, characteristic):
        self.notify = None

    async def write_gatt_char(self, characteristic, data, response=None):
        if self.write_delay:
            await asyncio.sleep(self.write_delay)
        self.writes.append((characteristic.uuid, bytes(data)))
        reply = self.replies.get(bytes(data))
        if reply and self.notify:
            self.notify[1](self.notify[0], bytearray(reply))

    def drop(self):
        """The radio link drops: bleak reports it through disconnected_callback."""
        self.is_connected = False
        self.disconnected_callback(self)


@pytest.fixture
def fake_ble(monkeypatch):
    FakeBleakClient.instances = []
    monkeypatch.setattr(bluetooth_handler, 'BleakClient', FakeBleakClient)
    monkeypatch.setattr(BluetoothHandler, 'settle_delay', 0)
    return FakeBleakClient


def test_nordic_profile_writes_to_rx_and_listens_on_tx(fake_ble):
    handler = BluetoothHandler('AA:BB')
    try:
        handler.connect()
        handler.start_listening()
        client = fake_ble.instances[-1]
        assert client.disconnected_callback is not None and handler.is_alive()
        assert (handler.write_char.uuid, client.notify[0].uuid) == (NORDIC['write'], NORDIC['notify'])
        handler.send(b'\x24\x01')
        assert client.writes == [(NORDIC['write'], b'\x24\x01')]
        client.notify[1](client.notify[0], bytearray(b'\x24\x02'))
        assert handler.raw_queue.get_nowait() == b'\x24\x02'
    finally:
        handler.close()
    assert not handler._loop_thread.is_alive()


def test_scale_profile_and_missing_profile(fake_ble, monkeypatch):
    scale = codes.BLE_PROFILES['scale']
    monkeypatch.setattr(fake_ble, 'services', [FakeService(scale['write'])])
    handler = BluetoothHandler('AA:BB')
    try:
        handler.connect()
        assert handler.write_char.uuid == handler.notify_char.uuid == scale['write']
    finally:
        handler.close()
    monkeypatch.setattr(fake_ble, 'services', [FakeService('00002a19-0000-1000-8000-00805f9b34fb')])
    handler = BluetoothHandler('AA:BB')
    try:
        with pytest.raises(FisicaTransportError, match='none of the Fisica Scale BLE profiles'):
            handler.connect()
        assert len(fake_ble.instances) == 2  # not retried: the device simply lacks the profile
    finally:
        handler.close()


def test_connect_waits_longer_than_every_attempt_together():
    handler = BluetoothHandler('AA:BB')
    try:
        inner = handler.max_retries * (handler.cleanup_timeout + handler.connect_timeout + 1) + \
            (handler.max_retries - 1) * handler.retry_delay
        assert handler.connect_budget() > inner
    finally:
        handler.close()


def test_a_timed_out_command_never_runs_later(fake_ble, monkeypatch):
    handler = BluetoothHandler('AA:BB')
    try:
        handler.connect()
        client = fake_ble.instances[-1]
        handler.command_timeout = 0.05
        # Started but too slow: cancelled.
        monkeypatch.setattr(fake_ble, 'write_delay', 0.3)
        with pytest.raises(FisicaTransportError, match='timed out'):
            handler.send(b'\x01')
        # Not started yet because the BLE loop is busy: dropped, not run once the loop is free.
        monkeypatch.setattr(fake_ble, 'write_delay', 0)
        asyncio.run_coroutine_threadsafe(_block(0.3), handler._loop)
        with pytest.raises(FisicaTransportError, match='timed out'):
            handler.send(b'\x02')
        time.sleep(0.6)
        assert client.writes == []
        handler.command_timeout = 1
        handler.send(b'\x03')
        assert client.writes == [(NORDIC['write'], b'\x03')]
    finally:
        handler.close()


async def _block(seconds):
    time.sleep(seconds)  # blocks the loop on purpose


def scale_replies(core, frames=0):
    replies = {core.command('GET_FIRMWARE_VERSION'): bytes.fromhex(protocol()['responses'][0]['hex']),
               core.command('GET_BATTERY_LEVEL'): bytes.fromhex('243147010050fff0')}
    for name in ('SET_DEVICE_BLE', 'START_CONTINUOUS_MEASUREMENT', 'STOP_CONTINUOUS_MEASUREMENT', 'SET_DEVICE_STOP'):
        command = core.command(name)
        replies[command] = bytes([0x24, command[1], command[2], 1, 0, 0, 0xff, 0xf0])
    replies[core.command('START_CONTINUOUS_MEASUREMENT')] += packet() * frames
    return replies


@pytest.mark.filterwarnings('ignore::fisica_sdk.FisicaFirmwareWarning')  # the reconnect probes the firmware again
def test_a_dropped_ble_link_is_recovered_and_the_measurement_resumes(fake_ble, monkeypatch):
    core = NativeCore()
    monkeypatch.setattr(fake_ble, 'replies', scale_replies(core, frames=2))
    device = {'id': 'AA:BB', 'type': 'Bluetooth', 'name': 'Fisica Scale'}
    monkeypatch.setattr('fisica_sdk.receiver.scan', lambda transport=None: [device])
    start = core.command('START_CONTINUOUS_MEASUREMENT')
    receiver = Receiver(device, lambda frame: None)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        receiver.conn()
    try:
        assert receiver.device_info['battery'] == 80
        receiver.start()
        first = fake_ble.instances[-1]
        assert (NORDIC['write'], start) in first.writes
        first.drop()
        wait_until(lambda: len(fake_ble.instances) == 2 and not receiver._reconnect)
        second = fake_ble.instances[-1]
        wait_until(lambda: (NORDIC['write'], start) in second.writes)  # the core resumed the measurement
        assert not first.is_connected and receiver._handler.is_alive()
        receiver.stop()
    finally:
        receiver.discon()
    assert fake_ble.instances[-1].writes[-1] == (NORDIC['write'], core.command('SET_DEVICE_STOP'))


def test_a_slow_reconnect_scan_does_not_block_the_session(fake_ble, monkeypatch):
    core = NativeCore()
    monkeypatch.setattr(fake_ble, 'replies', scale_replies(core))
    device = {'id': 'AA:BB', 'type': 'Bluetooth', 'name': 'Fisica Scale'}
    scanning = threading.Event()

    def slow_scan(transport=None):
        scanning.set()
        time.sleep(1.5)
        return []
    monkeypatch.setattr('fisica_sdk.receiver.scan', slow_scan)
    receiver = Receiver(device, lambda frame: None)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        receiver.conn()
    try:
        receiver.start()
        fake_ble.instances[-1].drop()
        assert scanning.wait(2)
        began = time.monotonic()
        with pytest.raises(FisicaCommandError):  # the link is gone: the stop cannot be confirmed
            receiver.stop()
        assert time.monotonic() - began < 0.5
    finally:
        receiver.discon()


# Logging -------------------------------------------------------------------------------------

def test_importing_the_library_prints_nothing():
    script = ("import logging, fisica_sdk, fisica_sdk.fisica_sdk\n"
              "logger = logging.getLogger('fisica_sdk')\n"
              "print(json.dumps([[type(h).__name__ for h in logger.handlers], logger.propagate, logger.level]))\n")
    result = subprocess.run([sys.executable, '-c', 'import json\n' + script], cwd=ROOT, capture_output=True,
                            text=True, check=True)
    assert json.loads(result.stdout) == [['NullHandler'], True, logging.NOTSET]


def test_debug_adds_a_console_handler_for_this_library_only(monkeypatch):
    import fisica_sdk.utils as utils
    logger, root = logging.getLogger('fisica_sdk'), logging.getLogger()
    handlers, level, root_level = list(logger.handlers), logger.level, root.level
    FisicaSDK()
    assert logger.handlers == handlers and logger.level == level
    monkeypatch.setattr(utils, '_console', None)
    try:
        FisicaSDK(debug=True)
        FisicaSDK(debug=True)
        consoles = [h for h in logger.handlers if isinstance(h, logging.StreamHandler)]
        assert len(consoles) == 1 and consoles[0].level == logging.DEBUG and logger.isEnabledFor(logging.DEBUG)
        assert root.level == root_level and logger.propagate
    finally:
        for handler in logger.handlers[:]:
            if handler not in handlers:
                logger.removeHandler(handler)
        logger.setLevel(level)
