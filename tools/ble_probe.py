"""Record how a real Scale behaves over BLE: GATT table, MTU, ACK/NACK and measurement data.

Three descriptions of BLE measurement disagree (doc/ble-measurement-path.md):
the protocol document (continuous 0x51 is USB-only; BLE uses the single/general
measurement 0x50), the published 1.0.1 SDK (BLE mode + continuous on every link) and
the current core (link mode + continuous). This probe runs both candidate sequences
against one device and writes what actually happened, so the BLE path can be decided
from evidence per firmware (planned production 2.0.1, interim 2.0.2).

  continuous: the core's current session sequence (mode BLE, 0x51 start/stop, release)
  single:     the documented sequence (mode BLE, 0x50 start with CTRL=1/UTYPE=1 in the
              8-byte form of the 2025-03-10 revision, 0x50 stop, release)

It sends only firmware query, mode, measurement start/stop and mode release. No zero,
calibration, Wi-Fi or OTA command. Run it from a terminal that has Bluetooth
permission, once per firmware version, with someone standing on the Scale:

    python tools/ble_probe.py --seconds 5 --out ble-2.0.1.json
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fisica_sdk import NativeSession, codes  # noqa: E402
from fisica_sdk.native import get_core  # noqa: E402

EVENT = {member.value: member.name for member in codes.FisicaSessionEvent}
OPERATION = {member.value: member.name for member in codes.FisicaSessionOperation}
STATE = {member.value: member.name for member in codes.FisicaSessionState}
STATUS = {member.value: member.name for member in codes.FisicaStatus}


def test_license():
    """A full-mode key for this PC signed with the TEST key, so every frame is counted.

    Basic mode hands out one frame a second. This development tool needs a core that trusts
    test keys (any build without --release keys)."""
    from fisica_sdk import NativeEntitlement
    from tools.entitlement_dev import host_pc_vectors
    context = NativeEntitlement()
    context.import_token(host_pc_vectors(int(time.time()))['full'])
    return context


async def drive(write, incoming, seconds, clock=time.monotonic, license=None):
    """Run connect -> start -> wait -> stop -> release through the core and log every step."""
    log, frames = [], []
    origin = clock()
    now = lambda: int((clock() - origin) * 1000)
    results = {}

    async def pump(session):
        for event in session.events():
            kind = EVENT[event['kind']]
            entry = {'t_ms': now(), 'event': kind, 'state': STATE[event['state']]}
            if kind == 'TRANSMIT':
                entry['bytes'] = event['payload'].hex()
                await write(event['payload'])
            elif kind == 'MEASUREMENT':
                frames.append(now())
                continue  # Counted, not logged one by one.
            elif kind == 'RESULT':
                entry.update(operation=OPERATION[event['operation']], status=STATUS[event['status']],
                             device_code=event['device_error'], response=event['payload'].hex())
                results[event['operation']] = event
            elif kind == 'RESPONSE':
                entry['bytes'] = event['payload'].hex()
            log.append(entry)

    async def until(session, operation, limit):
        deadline = clock() + limit
        while operation not in results and clock() < deadline:
            try:
                session.feed(await asyncio.wait_for(incoming.get(), 0.02), now_ms=now())
            except asyncio.TimeoutError:
                session.tick(now_ms=now())
            await pump(session)
        return results.pop(operation, None)

    with NativeSession() as session:
        session.license(license, now_ms=now())
        session.action(codes.FisicaSessionAction.CONNECTED, 1, now_ms=now())  # 1 = BLE profile
        await pump(session)
        probe = await until(session, codes.FisicaSessionOperation.PROBE, 3)
        firmware = None
        if probe and not probe['status']:
            firmware = get_core().decode_response(probe['payload'])['payload'].decode('utf-8', 'replace').strip('\x00 \r\n')
        session.action(codes.FisicaSessionAction.START, -1, now_ms=now())
        await pump(session)
        begin = await until(session, codes.FisicaSessionOperation.BEGIN, 5)
        started = bool(begin and not begin['status'])
        if started:
            deadline = clock() + seconds
            while clock() < deadline:
                try:
                    session.feed(await asyncio.wait_for(incoming.get(), 0.02), now_ms=now())
                except asyncio.TimeoutError:
                    session.tick(now_ms=now())
                await pump(session)
            session.action(codes.FisicaSessionAction.STOP, 0, now_ms=now())
            await pump(session)
            await until(session, codes.FisicaSessionOperation.END, 3)
        if session.state not in (codes.FisicaSessionState.DISCONNECTED, codes.FisicaSessionState.FAULT):
            session.action(codes.FisicaSessionAction.DISCONNECT, 0, now_ms=now())
            await pump(session)
            await until(session, codes.FisicaSessionOperation.CLOSE, 3)
        final_state = STATE[session.state]
    intervals = [b - a for a, b in zip(frames, frames[1:])]
    return {'firmware': firmware, 'continuous_start_accepted': started, 'frames': len(frames),
            'frame_interval_ms': {'min': min(intervals), 'max': max(intervals)} if intervals else None,
            'final_state': final_state, 'log': log}


async def drive_single(write, incoming, seconds, clock=time.monotonic):
    """Documented BLE sequence with raw commands; the core has no session path for it yet."""
    core = get_core()
    log, frames, buffer = [], [], bytearray()
    origin = clock()
    now = lambda: int((clock() - origin) * 1000)

    async def read(limit, header=None):
        deadline = clock() + limit
        while clock() < deadline:
            try:
                buffer.extend(await asyncio.wait_for(incoming.get(), 0.02))
            except asyncio.TimeoutError:
                continue
            while buffer:
                consumed, kind, packet = core.next_packet(bytes(buffer))
                if not consumed:
                    break
                del buffer[:consumed]
                if kind == 1:
                    frames.append((now(), packet[0]))  # Byte 0 is the measurement count index.
                elif kind == 2:
                    response = core.decode_response(packet)
                    entry = {'t_ms': now(), 'event': 'RESPONSE', 'header': response['header'], 'accepted': response['success'],
                             'device_code': 0 if response['success'] else (response['data_high'] << 8) | response['data_low'], 'bytes': packet.hex()}
                    log.append(entry)
                    if header is not None and response['header'] == header:
                        return entry
        return None

    async def command(step, data, header):
        log.append({'t_ms': now(), 'event': 'TRANSMIT', 'step': step, 'bytes': data.hex()})
        await write(data)
        return await read(3, header)

    mode = await command('mode_ble', core.command('SET_DEVICE_BLE'), 0x22)
    start = await command('single_start', core.command('_legacy_fixed', payload=bytes([0x50, 0x53, 1, 1])), 0x50)
    started = bool(start and start['accepted'])
    if started:
        await read(seconds)
    stop = await command('single_stop', core.command('_legacy_fixed', payload=bytes([0x50, 0x53, 0, 0])), 0x50)
    release = await command('release', core.command('SET_DEVICE_STOP'), 0x22)
    intervals = [b[0] - a[0] for a, b in zip(frames, frames[1:])]
    return {'mode_accepted': bool(mode and mode['accepted']), 'single_start_accepted': started, 'frames': len(frames),
            'count_index_values': sorted({index for _, index in frames})[:64],
            'frame_interval_ms': {'min': min(intervals), 'max': max(intervals)} if intervals else None,
            'stop_accepted': bool(stop and stop['accepted']), 'release_accepted': bool(release and release['accepted']), 'log': log}


async def probe(args):
    from bleak import BleakClient, BleakScanner
    found = await BleakScanner.discover(timeout=args.scan_seconds, return_adv=True)
    scales = [(device, adv) for device, adv in found.values()
              if codes.BLE_NAME_CONTAINS.lower() in (device.name or adv.local_name or '').lower()]
    report = {'name_filter': codes.BLE_NAME_CONTAINS, 'devices_seen': len(found),
              'scales': [{'name': d.name or a.local_name, 'address': d.address, 'rssi': a.rssi, 'services': a.service_uuids} for d, a in scales]}
    target = next((d for d, _ in scales if not args.address or d.address == args.address), None)
    if target is None:
        report['error'] = 'No matching Scale found'
        return report
    incoming = asyncio.Queue()
    async with BleakClient(target) as client:
        table = [{'service': service.uuid, 'characteristic': c.uuid, 'properties': list(c.properties)}
                 for service in client.services for c in service.characteristics]
        report.update(address=target.address, mtu=client.mtu_size, gatt=table)
        uuids = {c['characteristic'] for c in table}
        profile = next((p for p in codes.BLE_PROFILES.values() if p['write'] in uuids and p['notify'] in uuids), None)
        report['default_profile_matches'] = profile is not None
        if profile is None:
            report['error'] = 'Known characteristic UUIDs not present; see gatt'
            return report
        report['profile'] = profile
        await client.start_notify(profile['notify'], lambda _, data: incoming.put_nowait(bytes(data)))
        write = lambda data: client.write_gatt_char(profile['write'], data)
        if args.sequence in ('continuous', 'both'):
            report['continuous'] = await drive(write, incoming, args.seconds, license=test_license())
        if args.sequence in ('single', 'both'):
            while not incoming.empty():
                incoming.get_nowait()
            report['single'] = await drive_single(write, incoming, args.seconds)
        await client.stop_notify(profile['notify'])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--seconds', type=float, default=5, help='Measurement time after the start ACK')
    parser.add_argument('--scan-seconds', type=float, default=8)
    parser.add_argument('--address', help='Probe this device only')
    parser.add_argument('--sequence', choices=['continuous', 'single', 'both'], default='both')
    parser.add_argument('--out', type=Path, default=Path('ble-probe.json'))
    args = parser.parse_args()
    report = asyncio.run(probe(args))
    args.out.write_text(json.dumps(report, indent=2) + '\n')
    continuous, single = report.get('continuous', {}), report.get('single', {})
    summary = {'firmware': continuous.get('firmware'), 'mtu': report.get('mtu'), 'default_profile_matches': report.get('default_profile_matches'),
               'continuous_over_ble': {key: continuous.get(key) for key in ('continuous_start_accepted', 'frames', 'final_state')},
               'documented_single': {key: single.get(key) for key in ('single_start_accepted', 'frames', 'count_index_values', 'stop_accepted')},
               'error': report.get('error')}
    print(json.dumps(summary, indent=2))
    print('Full record:', args.out)


if __name__ == '__main__':
    main()
