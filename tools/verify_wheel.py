"""Install a wheel into a temporary environment and test the shipped SDK.

A release wheel trusts production keys only: a test-signed key for this PC must be rejected and
basic mode stay. A --validation wheel (tools/build_release.py --validation, version
<v>+validation) also trusts the test key, so the same key must open full mode; pass --validation
to verify one. The installed version must say which of the two it is. A universal2 wheel runs
with this Python's architecture only (arm64 on Apple Silicon unless started under Rosetta).
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import venv
import zipfile
from audit_artifacts import audit_archive

SMOKE = '''
import socket,sys,warnings,time,os
def no_network(*args,**kwargs):
    raise AssertionError('SDK attempted network access')
socket.create_connection=no_network
original_connect=socket.socket.connect
def offline_connect(sock,address):
    # Windows asyncio uses a loopback socketpair for process-local wakeups.
    if sock.family==getattr(socket,'AF_UNIX',None) or address[0] in ('127.0.0.1','::1','localhost'):
        return original_connect(sock,address)
    raise AssertionError('SDK attempted external network access')
socket.socket.connect=offline_connect
from fisica_sdk import NativeCore,FisicaSDK,FisicaLicenseError
from fisica_sdk.models import MeasurementFrame
from fisica_sdk import MockTransport,FisicaFirmwareWarning
from importlib.metadata import version as installed_version
# pip list shows a validation (QA) wheel as <v>+validation, a customer wheel as <v>.
installed=installed_version('fisica')
assert installed.endswith('+validation')==(os.environ['FISICA_VERIFY_FULL']=='1'),installed
core=NativeCore()
sdk=FisicaSDK()
assert 'cv2' not in sys.modules and 'PIL' not in sys.modules
packet=bytearray(645)
packet[639:641]=packet[643:645]=b'\\xf0\\x0f'
packet[641:643]=(705).to_bytes(2,'big')
packet[1:300]=bytes(10+i%7 for i in range(299))
try:
    core.decode_packet(packet)
    raise AssertionError('raw grids need full mode')
except FisicaLicenseError:
    pass
assert core.command('START_CONTINUOUS_MEASUREMENT').hex()=='245153000001fff0'
assert core.command('create_weight_scale',value=-32768).hex()=='241053008000fff0'
assert core.next_packet(packet)[1]==1
assert core.version_info()['license']=='Fisica Scale SDK License'
firmware=bytes.fromhex('24434701322e302e31fff0')
assert core.decode_response(firmware)['payload']==b'2.0.1'
def scale():
    exchanges={core.command('GET_FIRMWARE_VERSION'):[firmware]}
    for name in ('SET_DEVICE_USB','START_CONTINUOUS_MEASUREMENT','STOP_CONTINUOUS_MEASUREMENT','SET_DEVICE_STOP'):
        command=core.command(name)
        exchanges[command]=[bytes([0x24,command[1],command[2],1,0,0,0xff,0xf0])]
    exchanges[core.command('START_CONTINUOUS_MEASUREMENT')][0]+=bytes(packet)*12
    return MockTransport(exchanges)
def measure(expected):
    sdk.start_measurement()
    deadline=time.monotonic()+5
    while len(sdk._frames)<expected:
        assert time.monotonic()<deadline
        time.sleep(0.02)
    time.sleep(0.3)
    sdk.stop_measurement()
    sdk.wait()
    assert len(sdk.get_session_frames())==expected
with warnings.catch_warnings(record=True) as notices:
    warnings.simplefilter('always')
    sdk.connect({'id':'installed-mock','type':'Serial'},transport=scale())
    assert any(issubclass(n.category,FisicaFirmwareWarning) for n in notices)
assert sdk.get_device_info()['firmware_version']=='2.0.1'
# No key: basic mode. One frame a second without raw data; weight and foot size from every packet.
assert (sdk.entitlement_status()['mode'],sdk.entitlement_status()['reason'])==('basic','no_key')
measure(1)
assert sdk.get_session_frames()[0]['raw_data'] is None
assert sdk.analyze().weight==70.5
# A test-signed key activated for this PC (offline file). No network.
with warnings.catch_warnings():
    warnings.simplefilter('ignore')
    assert sdk.configure_entitlement(token='not a token',storage=None)['last_import']=='rejected_format'
    status=sdk.configure_entitlement(token=os.environ['FISICA_VERIFY_TOKEN'],storage=None)
if os.environ['FISICA_VERIFY_FULL']=='1':
    # Validation wheel: the core trusts the test key, so full mode, every frame with raw data.
    assert (status['mode'],status['company'],status['contract_state'])==('full','acme','active')
    measurement=core.decode_packet(packet,sdk._entitlement.native)
    assert measurement.weight==70.5 and not measurement.battery_valid
    assert len(measurement.raw_grid)==29 and len(measurement.raw_grid[0])==22 and len(measurement.sensor_matrix[0])==29
    measure(12)
    assert sdk.get_session_frames()[0]['raw_data']==list(packet)
else:
    # Release wheel: production keys only. The test signing key is unknown to the core.
    assert (status['mode'],status['last_import'])==('basic','rejected_key'),status
    try:
        core.decode_packet(packet,sdk._entitlement.native)
        raise AssertionError('a test-signed key opened full mode in a release wheel')
    except FisicaLicenseError:
        pass
sdk.disconnect()
sdk.wait()
grid=[[0.0]*29 for _ in range(29)]
grid[4][3]=grid[4][20]=100
frames=[MeasurementFrame('',grid,70.5,99)]*12
assert core.analyze(frames)==(70.5,1.0,1.0,1.0,1.0)
sdk.set_metadata(id='installed-wheel-smoke',name='fixture')
sdk._session.frames=frames
assert sdk.analyze().weight==70.5
assert 'distribution' not in core.version_info()
print('Installed wheel verified:',installed,core.version_info())
print('Test-signed key:',status['mode'],'mode',status['last_import'])
if os.environ.get('FISICA_VERIFY_VISUALIZATION')=='1':
    from fisica_sdk import VisualOptions
    for mode in ('PIXEL','BLUR','BINARY','CONTOUR'):
        assert sdk.render(grid_data=grid,mode=mode,bbox=True).shape==(512,512,3)
    assert len(sdk.render(grid_data=grid,mode=VisualOptions.ALL))==6
    print('Visualization extra renders every mode')
'''


def wheel_trusts_test_key(path):
    """Whether the wheel's core trusts a non-production key, from its release metadata."""
    with zipfile.ZipFile(path) as archive:
        release = json.loads(archive.read('fisica_sdk/_release.json'))
    return any(key.get('environment') != 'production' for key in release.get('entitlement', {}).get('keys', []))


def verify_wheel(path, extras=None, validation=False):
    path = Path(path).resolve()
    audit_archive(path, validation=validation)
    full = wheel_trusts_test_key(path)
    if validation and not full:
        raise SystemExit(f'{path.name}: --validation was given but the wheel does not trust the test key')
    with tempfile.TemporaryDirectory(prefix='fisica-wheel-') as temporary:
        root = Path(temporary)
        venv.EnvBuilder(with_pip=True).create(root / 'venv')
        python = root / 'venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
        requirement = str(path) + (f'[{extras}]' if extras else '')
        # Binary wheels first: a dependency built from source tests the host's compiler, not this wheel.
        subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','--prefer-binary',requirement],check=True,cwd=root)
        from entitlement_dev import host_pc_vectors
        environment = dict(os.environ)
        # Keys stored for this PC (a paired development Mac) must not leak into the smoke test.
        home = root / 'home'
        home.mkdir()
        environment.update(HOME=str(home), USERPROFILE=str(home), XDG_STATE_HOME=str(home / 'state'),
                           LOCALAPPDATA=str(home / 'AppData' / 'Local'), APPDATA=str(home / 'AppData' / 'Roaming'))
        # The same test-signed key for this PC opens full mode in a validation wheel and nothing in a release wheel.
        environment['FISICA_VERIFY_TOKEN'] = host_pc_vectors(int(time.time()))['full']
        environment['FISICA_VERIFY_FULL'] = '1' if full else '0'
        if extras == 'visualization':
            environment.update(FISICA_VERIFY_VISUALIZATION='1')
        subprocess.run([str(python),'-I','-c',SMOKE],check=True,cwd=root,env=environment,timeout=60)
    print(f'{path.name}: ' + ('validation wheel, test key trusted (full mode checked)' if full
                              else 'release wheel, production keys only (test key rejected)'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('wheel',type=Path)
    parser.add_argument('--extras', choices=['visualization'])
    parser.add_argument('--validation', action='store_true', help='accept a QA wheel that also trusts the test key')
    args = parser.parse_args()
    verify_wheel(args.wheel, extras=args.extras, validation=args.validation)
