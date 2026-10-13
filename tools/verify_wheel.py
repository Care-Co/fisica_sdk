"""Install a release wheel into a temporary environment and test the shipped SDK."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
import venv
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
from fisica_sdk import NativeCore,FisicaSDK,FisicaDeprecationWarning
from fisica_sdk.models import MeasurementFrame
from fisica_sdk.device import ATCommands
from fisica_sdk import MockTransport,FisicaFirmwareWarning
core=NativeCore()
sdk=FisicaSDK()
assert 'PyQt5' not in sys.modules
packet=bytearray(645)
packet[639:641]=packet[643:645]=b'\\xf0\\x0f'
packet[641:643]=(705).to_bytes(2,'big')
measurement=core.decode_packet(packet)
assert measurement.weight==70.5 and not measurement.battery_valid
assert len(measurement.raw_grid)==29 and len(measurement.raw_grid[0])==22
assert len(measurement.sensor_matrix[0])==29
assert core.command('START_CONTINUOUS_MEASUREMENT').hex()=='245153000001fff0'
assert core.command('create_weight_scale',value=-32768).hex()=='241053008000fff0'
assert core.next_packet(packet)[1]==1
assert core.version_info()['license']=='Fisica Scale SDK License'
firmware=bytes.fromhex('24434701322e302e31fff0')
assert core.decode_response(firmware)['payload']==b'2.0.1'
exchanges={core.command('GET_FIRMWARE_VERSION'):[firmware]}
for name in ('SET_DEVICE_USB','START_CONTINUOUS_MEASUREMENT','STOP_CONTINUOUS_MEASUREMENT','SET_DEVICE_STOP'):
    command=core.command(name)
    exchanges[command]=[bytes([0x24,command[1],command[2],1,0,0,0xff,0xf0])]
exchanges[core.command('START_CONTINUOUS_MEASUREMENT')][0]+=bytes(packet)*12
transport=MockTransport(exchanges)
with warnings.catch_warnings(record=True) as notices:
    warnings.simplefilter('always')
    sdk.connect({'id':'installed-mock','type':'Serial'},transport=transport)
    assert any(issubclass(n.category,FisicaFirmwareWarning) for n in notices)
assert sdk.get_device_info()['firmware_version']=='2.0.1'
sdk.start_measurement()
deadline=time.monotonic()+5
while len(sdk._frames)<12:
    assert time.monotonic()<deadline
    time.sleep(0.02)
sdk.stop_measurement()
sdk.wait()
assert sdk.get_session_frames()[0]['raw_data']==list(packet)
sdk.disconnect()
sdk.wait()
grid=[[0.0]*29 for _ in range(29)]
grid[4][3]=grid[4][20]=100
frames=[MeasurementFrame('',grid,70.5,99)]*12
assert core.analyze(frames)==(70.5,1.0,1.0,1.0,1.0)
sdk.set_metadata(id='installed-wheel-smoke',name='fixture')
sdk._session.frames=frames
assert sdk.analyze().weight==70.5
with warnings.catch_warnings(record=True) as notices:
    warnings.simplefilter('always')
    ATCommands.build_command(0x22,0x53,0,1)
    assert len(notices)==1 and issubclass(notices[0].category,FisicaDeprecationWarning)
print('Installed wheel verified:',core.version_info())
if os.environ.get('FISICA_VERIFY_GUI')=='1':
    import numpy as np
    from fisica_sdk import Visualizer
    image=Visualizer().render(np.asarray(grid),mode='PIXEL',bbox=True)
    assert image.shape==(512,512,3)
    from fisica_sdk.gui_viewer import setup_gui,update_gui
    app=setup_gui()
    update_gui(image)
    app.processEvents()
    app.quit()
    print('GUI extras render and Qt initialization verified')
'''


def verify_wheel(path, extras=None):
    path = Path(path).resolve()
    audit_archive(path)
    with tempfile.TemporaryDirectory(prefix='fisica-wheel-') as temporary:
        root = Path(temporary)
        venv.EnvBuilder(with_pip=True).create(root / 'venv')
        python = root / 'venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
        requirement = str(path) + (f'[{extras}]' if extras else '')
        subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check',requirement],check=True,cwd=root)
        import os
        environment = dict(os.environ)
        if extras == 'gui':
            environment.update(FISICA_VERIFY_GUI='1', QT_QPA_PLATFORM='offscreen')
        subprocess.run([str(python),'-I','-c',SMOKE],check=True,cwd=root,env=environment,timeout=60)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('wheel',type=Path)
    parser.add_argument('--extras', choices=['visualization', 'gui'])
    args = parser.parse_args()
    verify_wheel(args.wheel, extras=args.extras)
