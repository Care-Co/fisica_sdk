import 'dart:io';
import 'dart:convert';
import 'dart:typed_data';
import 'package:fisica_flutter/fisica_flutter.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:universal_ble/universal_ble.dart';

class FakeBle extends UniversalBlePlatform {
  AvailabilityState availability = AvailabilityState.poweredOn;
  int negotiatedMtu = 23, disconnects = 0, stopScans = 0;
  bool missingUuid = false, refuseMtu = false, writeFailure = false;
  bool subscribed = false;
  bool hangConnect = false;
  Uint8List? frame, startCommand;
  final writes = <Uint8List>[];
  @override
  Future<AvailabilityState> getBluetoothAvailabilityState() async =>
      availability;
  @override
  Future<void> startScan({
    ScanFilter? scanFilter,
    PlatformConfig? platformConfig,
  }) async {
    updateScanResult(BleDevice(deviceId: 'unrelated', name: 'Other device'));
    updateScanResult(BleDevice(deviceId: 'scale', name: 'Fisica Scale'));
  }

  @override
  Future<void> stopScan() async {
    stopScans++;
  }

  @override
  Future<void> connect(String id, {Duration? connectionTimeout}) async {
    if (!hangConnect) updateConnection(id, true);
  }

  @override
  Future<void> disconnect(String id) async {
    disconnects++;
    updateConnection(id, false);
  }

  @override
  Future<int> requestMtu(String id, int expectedMtu) async {
    if (refuseMtu) throw StateError('MTU rejected');
    return negotiatedMtu;
  }

  @override
  Future<List<BleService>> discoverServices(String id) async => [
    BleService(
      '180a',
      missingUuid
          ? []
          : [
              BleCharacteristic(FisicaBleProfile.scale.write, [
                CharacteristicProperty.write,
                CharacteristicProperty.notify,
              ]),
            ],
    ),
  ];
  @override
  Future<void> setNotifiable(
    String id,
    String service,
    String characteristic,
    BleInputProperty property,
  ) async {
    subscribed = true;
  }

  @override
  Future<void> writeValue(
    String id,
    String service,
    String characteristic,
    Uint8List value,
    BleOutputProperty property,
  ) async {
    if (writeFailure) throw StateError('Write failed');
    if (!subscribed) throw StateError('Not subscribed');
    writes.add(Uint8List.fromList(value));
    if (value.length == 8 && value[0] == 0x24) {
      final reply = value[1] == 0x43
          ? [0x24, 0x43, 0x47, 1, 50, 46, 48, 46, 49, 255, 240]
          : [0x24, value[1], value[2], 1, 0, 0, 255, 240];
      updateCharacteristicValue(id, characteristic, Uint8List.fromList(reply));
      if (frame != null && value.toString() == startCommand.toString()) {
        for (var i = 0; i < 12; i++) {
          updateCharacteristicValue(
            id,
            characteristic,
            Uint8List.sublistView(frame!, 0, 200),
          );
          updateCharacteristicValue(
            id,
            characteristic,
            Uint8List.sublistView(frame!, 200),
          );
        }
      }
    }
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  late FakeBle backend;
  setUp(() {
    backend = FakeBle();
    UniversalBle.setInstance(backend);
  });
  test('scan filters Scale names and always stops scanning', () async {
    final found = await FisicaBleTransport.scan(
      duration: const Duration(milliseconds: 5),
    );
    expect(found.map((d) => d.deviceId), ['scale']);
    expect(backend.stopScans, 1);
  });
  test('powered off Bluetooth fails before connecting', () async {
    backend.availability = AvailabilityState.poweredOff;
    final transport = FisicaBleTransport('scale');
    await expectLater(transport.connect(), throwsStateError);
    await transport.close();
  });
  test('missing UUID fails and releases the connection', () async {
    backend.missingUuid = true;
    final transport = FisicaBleTransport('scale');
    await expectLater(transport.connect(), throwsStateError);
    expect(backend.disconnects, 1);
  });
  test('connection timeout cleans up rather than waiting forever', () async {
    backend.hangConnect = true;
    final transport = FisicaBleTransport(
      'scale',
      timeout: const Duration(milliseconds: 10),
    );
    await expectLater(transport.connect(), throwsA(isA<Exception>()));
    expect(backend.disconnects, 1);
  });
  test('writes are serialized and fragmented using negotiated MTU', () async {
    backend.negotiatedMtu = 30;
    final transport = FisicaBleTransport('scale');
    await transport.connect();
    final first = Uint8List.fromList(List.generate(70, (i) => i));
    await Future.wait([
      transport.send(first),
      transport.send(Uint8List.fromList([99])),
    ]);
    expect(backend.writes.map((b) => b.length), [27, 27, 16, 1]);
    expect(backend.writes.expand((b) => b).toList(), [...first, 99]);
    await transport.close();
  });
  test('failed MTU request falls back to 20 byte payloads', () async {
    backend.refuseMtu = true;
    final transport = FisicaBleTransport('scale');
    await transport.connect();
    await transport.send(Uint8List(41));
    expect(transport.mtu, 23);
    expect(backend.writes.map((b) => b.length), [20, 20, 1]);
    await transport.close();
  });
  test('maximum MTU still respects the 512 byte attribute limit', () async {
    backend.negotiatedMtu = 517;
    final transport = FisicaBleTransport('scale');
    await transport.connect();
    await transport.send(Uint8List(600));
    expect(backend.writes.map((b) => b.length), [512, 88]);
    await transport.close();
  });
  test('one transport cannot be connected twice concurrently', () async {
    final transport = FisicaBleTransport('scale');
    final first = transport.connect();
    await expectLater(transport.connect(), throwsStateError);
    await first;
    await transport.close();
  });
  test('link loss is reported once and prevents further writes', () async {
    final transport = FisicaBleTransport('scale');
    var losses = 0;
    final subscription = transport.disconnected.listen((_) => losses++);
    await transport.connect();
    backend.updateConnection('scale', false);
    backend.updateConnection('scale', false);
    await Future<void>.delayed(Duration.zero);
    expect(losses, 1);
    await expectLater(transport.send(Uint8List(8)), throwsStateError);
    await subscription.cancel();
    await transport.close();
  });
  test('write errors reach the caller; close is idempotent', () async {
    final transport = FisicaBleTransport('scale');
    await transport.connect();
    backend.writeFailure = true;
    await expectLater(transport.send(Uint8List(8)), throwsStateError);
    await transport.close();
    await transport.close();
    expect(backend.disconnects, 1);
  });
  test('BLE adapter drives the real shared session core', () async {
    final path = Platform.environment['FISICA_CORE_TEST_LIBRARY'];
    expect(
      path,
      isNotNull,
      reason: 'Set FISICA_CORE_TEST_LIBRARY to the built core',
    );
    final client = FisicaClient(
      FisicaCore.open(path!),
      FisicaBleTransport('scale'),
    );
    final script =
        jsonDecode(
              File('../../examples/partner/scale-mock.json').readAsStringSync(),
            )
            as Map<String, dynamic>;
    final hex = script['measurement_hex'] as String;
    backend.frame = Uint8List.fromList([
      for (var i = 0; i < hex.length; i += 2)
        int.parse(hex.substring(i, i + 2), radix: 16),
    ]);
    backend.startCommand = client.session.core.command(
      'START_CONTINUOUS_MEASUREMENT',
    );
    try {
      expect((await client.connect()).status, FisicaStatus.ok);
      expect((await client.start()).state, FisicaSessionState.measuring);
      await Future<void>.delayed(const Duration(milliseconds: 50));
      expect((await client.stop()).state, FisicaSessionState.idle);
      expect(client.report()!.weight, 70.5);
      expect(client.frames.length, 12);
    } finally {
      await client.close();
    }
    expect(backend.disconnects, 1);
  });
}
