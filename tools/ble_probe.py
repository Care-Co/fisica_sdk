"""Record how a real Scale behaves over BLE: GATT table, MTU, ACK/NACK and measurement data.

The protocol document describes continuous measurement as USB-only while the core
sends the same continuous commands over BLE. This probe runs the core's current BLE
sequence against one device and writes what actually happened, so the BLE path can be
decided from evidence per firmware (planned production 2.0.1, interim 2.0.2).

It sends only: firmware query, BLE mode, start, stop, mode release. No zero,
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


async def drive(write, incoming, seconds, clock=time.monotonic):
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
        report.update(await drive(lambda data: client.write_gatt_char(profile['write'], data), incoming, args.seconds))
        await client.stop_notify(profile['notify'])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--seconds', type=float, default=5, help='Measurement time after the start ACK')
    parser.add_argument('--scan-seconds', type=float, default=8)
    parser.add_argument('--address', help='Probe this device only')
    parser.add_argument('--out', type=Path, default=Path('ble-probe.json'))
    args = parser.parse_args()
    report = asyncio.run(probe(args))
    args.out.write_text(json.dumps(report, indent=2) + '\n')
    summary = {key: report.get(key) for key in ('firmware', 'mtu', 'default_profile_matches', 'continuous_start_accepted', 'frames', 'final_state', 'error')}
    print(json.dumps(summary, indent=2))
    print('Full record:', args.out)


if __name__ == '__main__':
    main()
