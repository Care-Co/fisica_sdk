import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:fisica_core/fisica_core.dart';
import 'package:test/test.dart';

class MockBytes implements FisicaTransport {
  final input = StreamController<Uint8List>.broadcast();
  final lost = StreamController<void>.broadcast();
  bool closed = false;
  int closes = 0;
  final sent = <Uint8List>[];
  @override
  Stream<Uint8List> get received => input.stream;
  @override
  Stream<void> get disconnected => lost.stream;
  @override
  Future<void> connect() async {}
  @override
  Future<void> send(Uint8List bytes) async {
    sent.add(bytes);
    final ack = bytes[1] == 0x43
        ? [0x24, 0x43, 0x47, 1, 50, 46, 48, 46, 49, 255, 240]
        : [0x24, bytes[1], bytes[2], 1, 0, 0, 255, 240];
    input.add(Uint8List.fromList(ack));
  }

  @override
  Future<void> close() async {
    closes++;
    closed = true;
    await input.close();
    await lost.close();
  }
}

class LinkedBytes extends MockBytes implements FisicaLinkTransport {
  @override
  final int link;
  LinkedBytes(this.link);
}

Uint8List fixturePacket() {
  final fixtures =
      jsonDecode(
            File(
              '../../tests/fixtures/legacy-processing.json',
            ).readAsStringSync(),
          )
          as List<dynamic>;
  final hex =
      fixtures.firstWhere(
            (f) => f['id'] == 'synthetic-recorded-layout',
          )['packet_hex']
          as String;
  return Uint8List.fromList([
    for (var i = 0; i < hex.length; i += 2)
      int.parse(hex.substring(i, i + 2), radix: 16),
  ]);
}

Future<void> settle() => Future<void>.delayed(const Duration(milliseconds: 60));

/// Never answers [silent] (for example the release, SET_DEVICE_STOP).
class SilentBytes extends MockBytes {
  final Uint8List silent;
  SilentBytes(this.silent);
  @override
  Future<void> send(Uint8List bytes) async {
    if (bytes.toString() != silent.toString()) return super.send(bytes);
    sent.add(bytes);
  }
}

/// Answers GET_BATTERY_LEVEL with [battery] percent, or refuses it (NACK) when null.
class BatteryBytes extends MockBytes {
  int? battery = 80;
  @override
  Future<void> send(Uint8List bytes) async {
    if (bytes[1] != 0x31) return super.send(bytes);
    sent.add(bytes);
    final level = battery;
    input.add(Uint8List.fromList(level == null
        ? [0x24, 0x31, 0x47, 0, 0, 1, 255, 240]
        : [0x24, 0x31, 0x47, 1, 0, level, 255, 240]));
  }
}

void main() {
  final core = FisicaCore.open(
    Platform.environment['FISICA_CORE_TEST_LIBRARY']!,
  );
  // Shared vectors plus keys activated for this PC, from tools/verify_bindings.py.
  final vectors =
      jsonDecode(
            File(
              Platform.environment['FISICA_LICENSE_VECTORS']!,
            ).readAsStringSync(),
          )
          as Map<String, dynamic>;
  final contexts = <FisicaEntitlement>[];
  tearDown(() {
    for (final entitlement in contexts) {
      entitlement.close();
    }
    contexts.clear();
  });
  FisicaEntitlement license([String? name]) {
    final entitlement = FisicaEntitlement(
      core,
      clock: () => vectors['now'] as int,
      storageDirectory: null, // memory only: tests never touch the user's folder
    );
    contexts.add(entitlement);
    if (name != null) entitlement.importToken(vectors[name] as String);
    return entitlement;
  }

  /// Connect, start, receive 12 packets at once, stop.
  Future<FisicaClient> measure(FisicaEntitlement? entitlement) async {
    final transport = MockBytes();
    final client = FisicaClient(core, transport, entitlement: entitlement);
    await client.connect(link: FisicaLink.usb);
    await client.start();
    for (var i = 0; i < 12; i++) {
      transport.input.add(fixturePacket());
    }
    await settle();
    await client.stop();
    return client;
  }
  test('battery comes from GET_BATTERY_LEVEL, idle or measuring', () async {
    final transport = BatteryBytes();
    final client = FisicaClient(core, transport);
    await client.connect(link: FisicaLink.usb);
    expect(client.battery, isNull);
    expect(transport.sent.any((s) => s[1] == 0x31), isFalse);
    expect(await client.readBattery(), 80);
    await client.start();
    expect(await client.readBattery(), 80);
    expect(client.battery, 80);
    await client.stop();
    transport.battery = null;
    await expectLater(client.readBattery(), throwsA(isA<FisicaCommandError>()));
    expect(client.battery, 80);
    expect((await client.start()).state, 3); // the refusal did not fault the session
    await client.close();
  });
  test('async client uses core for start stop release', () async {
    final transport = MockBytes();
    final client = FisicaClient(core, transport);
    expect((await client.connect(link: FisicaLink.usb)).status, 0);
    expect((await client.start()).state, 3);
    expect((await client.stop()).state, 1);
    await client.close();
    expect(transport.closed, isTrue);
  });
  test('duration completes from core event', () async {
    final client = FisicaClient(core, MockBytes());
    await client.connect(link: FisicaLink.usb);
    final stopped = client.events.firstWhere(
      (e) => e.kind == 3 && e.operation == 3,
    );
    await client.start(duration: const Duration(milliseconds: 30));
    expect((await stopped.timeout(const Duration(seconds: 2))).status, 0);
    await client.close();
  });
  test('basic mode: one frame a second, the report uses every packet', () async {
    final client = await measure(null);
    // The 12 packets arrive within one second: the core hands out one frame
    // without raw data, and analyzes all 12.
    expect(client.frames, hasLength(1));
    expect(client.frames.single.rawGrid, isNull);
    expect(client.frames.single.rawData, isNull);
    expect(client.frames.single.sensorMatrix.length, 29);
    expect(client.frames.single.weight, 70.5);
    final report = client.report()!;
    expect(report.weight, 70.5);
    expect(report.frames, hasLength(1));
    expect(client.session.analyze(), core.analyzePackets(List.filled(12, fixturePacket())));
    await client.close();
  });
  test('full mode: every packet as a frame with raw data', () async {
    final packet = fixturePacket();
    final client = await measure(license('full'));
    expect(client.frames, hasLength(12));
    final report = client.report()!;
    expect(report.weight, 70.5);
    expect(report.frames.first.sensorMatrix.length, 29);
    expect(report.frames.first.rawGrid!.first.length, 22);
    expect(report.frames.first.rawData, packet);
    expect(
      core.analyzePackets(List.filled(12, packet)),
      core.analyzeMatrices(
        List.filled(12, report.frames.first.sensorMatrix),
        List.filled(12, report.frames.first.weight),
      ),
    );
    await client.close();
  });
  test('measurement and analysis work in every license state', () async {
    for (final name in [
      null,
      'unbound',
      'tampered',
      'other_target',
      'legacy',
      'limited',
      'full',
    ]) {
      final entitlement = name == null ? null : license(name);
      final client = await measure(entitlement);
      final everyFrame = name == 'full' || name == 'limited';
      expect(client.frames, hasLength(everyFrame ? 12 : 1), reason: name);
      // 'limited' opens export only: every frame, but no raw data.
      expect(client.frames.first.rawData != null, name == 'full', reason: name);
      expect(client.report()!.weight, 70.5, reason: name);
      await client.close();
    }
  });
  test('a license change is applied to the open session', () async {
    final entitlement = license();
    final transport = MockBytes();
    final client = FisicaClient(core, transport, entitlement: entitlement);
    Future<void> receive() async {
      for (var i = 0; i < 12; i++) {
        transport.input.add(fixturePacket());
      }
      await settle();
    }

    await client.connect(link: FisicaLink.usb);
    await client.start();
    await receive();
    expect(client.frames, hasLength(1));
    // Importing (or activating online) a key changes the license; the client
    // copies it into its session during the measurement.
    entitlement.importToken(vectors['full'] as String);
    await settle();
    await receive();
    expect(client.frames, hasLength(13));
    expect(client.frames.last.rawData, isNotNull);
    await client.stop();
    // Part of the measurement ran in basic mode: the session analyzes all 24 packets.
    expect(client.report()!.weight, 70.5);
    // Releasing the key returns the session to basic mode.
    entitlement.release();
    await settle();
    await client.start();
    await receive();
    await client.stop();
    expect(client.frames, hasLength(1));
    expect(client.report()!.weight, 70.5);
    await client.close();
  });
  test(
    'same client reconnects after link loss and resumes measuring',
    () async {
      final packet = fixturePacket();
      final transport = MockBytes();
      final client = FisicaClient(
        core,
        transport,
        entitlement: license('full'),
      );
      final errors = <Object>[];
      final reconnects = <FisicaSessionEvent>[];
      client.events.listen((e) {
        if (e.kind == FisicaSessionEventKind.reconnect) reconnects.add(e);
      }, onError: errors.add);
      await client.connect(link: FisicaLink.usb);
      await client.start(duration: const Duration(seconds: 30));
      transport.input.add(packet);
      transport.input.add(packet);
      await settle();
      transport.lost.add(null);
      await settle();
      expect(client.session.state, FisicaSessionState.recovering);
      expect(reconnects, hasLength(1));
      expect(errors, hasLength(1));
      // Same transport: the core probes, sets the mode again and resumes.
      expect(
        (await client.connect(link: FisicaLink.usb)).status,
        FisicaStatus.ok,
      );
      await settle();
      expect(client.session.state, FisicaSessionState.measuring);
      transport.input.add(packet);
      await settle();
      expect(client.frames, hasLength(3));
      expect((await client.stop()).state, FisicaSessionState.idle);
      expect(client.report()!.weight, 70.5);
      await client.close();
    },
  );
  test(
    'reconnect can replace the transport and closes the previous one',
    () async {
      final first = MockBytes();
      final second = MockBytes();
      final client = FisicaClient(core, first);
      client.events.handleError((_) {}).listen((_) {});
      await client.connect(link: FisicaLink.ble);
      await client.start();
      first.lost.add(null);
      await settle();
      await client.connect(link: FisicaLink.ble, transport: second);
      await settle();
      expect(first.closed, isTrue);
      expect(identical(client.transport, second), isTrue);
      expect(client.session.state, FisicaSessionState.measuring);
      expect(second.sent, isNotEmpty);
      await client.close();
      expect(second.closed, isTrue);
    },
  );
  test('close after link loss frees resources without an error', () async {
    final transport = MockBytes();
    final client = FisicaClient(core, transport);
    client.events.handleError((_) {}).listen((_) {});
    await client.connect(link: FisicaLink.ble);
    await client.start();
    transport.lost.add(null);
    await settle();
    await client.close();
    expect(transport.closed, isTrue);
    expect(() => client.connect(), throwsStateError);
  });
  test('link comes from the transport; a different one is rejected', () async {
    final usb = LinkedBytes(FisicaLink.usb);
    final client = FisicaClient(core, usb);
    await expectLater(
      client.connect(link: FisicaLink.ble),
      throwsA(isA<FisicaArgumentError>()),
    );
    expect(usb.sent, isEmpty);
    await expectLater(
      client.connect(link: 3),
      throwsA(isA<FisicaArgumentError>()),
    );
    await client.connect();
    await client.start();
    expect(
      usb.sent.any(
        (bytes) =>
            bytes.toString() == core.command('SET_DEVICE_USB').toString(),
      ),
      isTrue,
    );
    await client.stop();
    await client.close();
  });
  test('a transport without a declared link must be given one', () async {
    final transport = MockBytes();
    final client = FisicaClient(core, transport);
    await expectLater(
      client.connect(),
      throwsA(
        isA<FisicaArgumentError>().having(
          (e) => e.code,
          'code',
          FisicaStatus.invalidArgument,
        ),
      ),
    );
    expect(transport.sent, isEmpty);
    await client.connect(link: FisicaLink.ble);
    await client.start();
    expect(
      transport.sent.any(
        (bytes) =>
            bytes.toString() == core.command('SET_DEVICE_BLE').toString(),
      ),
      isTrue,
    );
    await client.stop();
    await client.close();
  });
  test('report after close comes from the core analysis, in basic mode too', () async {
    final client = await measure(null);
    client.clearFrames();
    await client.close();
    expect(client.frames, isEmpty);
    expect(client.report()!.weight, 70.5);
    expect(client.report()!.frames, isEmpty);
  });
  test('only the same operation is busy; the core orders the rest', () async {
    final client = FisicaClient(core, MockBytes());
    await client.connect(link: FisicaLink.usb);
    final start = client.start();
    expect(
      () => client.start(),
      throwsA(
        isA<FisicaStateError>().having((e) => e.code, 'code', FisicaStatus.busy),
      ),
    );
    // A stop while the start waits for its ACK is the core's to order: it
    // cancels the start and stops.
    final stop = client.stop();
    await expectLater(
      start,
      throwsA(
        isA<FisicaCommandError>().having(
          (e) => e.code,
          'code',
          FisicaStatus.cancelled,
        ),
      ),
    );
    expect((await stop).state, FisicaSessionState.idle);
    await client.close();
  });
  test('deprecated profile is still accepted as the link', () async {
    final transport = MockBytes();
    final client = FisicaClient(core, transport);
    // ignore: deprecated_member_use_from_same_package
    await client.connect(profile: FisicaLink.usb);
    await client.start();
    expect(
      transport.sent.any(
        (bytes) =>
            bytes.toString() == core.command('SET_DEVICE_USB').toString(),
      ),
      isTrue,
    );
    await client.stop();
    await client.close();
  });
  test('start clears the previous measurement; frames stay bounded', () async {
    final packet = fixturePacket();
    final transport = MockBytes();
    final client = FisicaClient(core, transport, entitlement: license('full'));
    expect(
      () => client.maxFrames = FisicaLimit.analysisFrames + 1,
      throwsRangeError,
    );
    client.maxFrames = 5;
    await client.connect(link: FisicaLink.usb);
    await client.start();
    for (var i = 0; i < 8; i++) {
      transport.input.add(packet);
    }
    await settle();
    await client.stop();
    expect(client.frames, hasLength(5));
    expect(client.droppedFrames, 3);
    expect(client.report()!.weight, 70.5);
    await client.start();
    expect(client.frames, isEmpty);
    expect(client.droppedFrames, 0);
    await client.stop();
    await client.close();
  });
  final busy = throwsA(
    isA<FisicaStateError>().having((e) => e.code, 'code', FisicaStatus.busy),
  );
  test('close during start cancels pending future and disposes once', () async {
    final transport = MockBytes();
    final client = FisicaClient(core, transport);
    await client.connect(link: FisicaLink.usb);
    final start = client.start();
    final canceled = expectLater(start, throwsA(isA<FisicaCommandError>()));
    final close = client.close();
    await expectLater(client.close(), busy);
    await canceled;
    await close;
    await client.close(); // closed: completes at once and does nothing
    expect(transport.closes, 1);
  });
  test('a second close while one is pending is busy; once closed, close does nothing', () async {
    final release = core.command('SET_DEVICE_STOP');
    final transport = SilentBytes(release); // the Scale never confirms the release
    final client = FisicaClient(core, transport);
    await client.connect(link: FisicaLink.usb);
    await client.start();
    for (var i = 0; i < 12; i++) {
      transport.input.add(fixturePacket());
    }
    await settle();
    await client.stop();
    final first = client.close();
    await expectLater(client.close(), busy);
    expect(transport.closed, isFalse); // the busy close left the first alone
    // Unconfirmed: the first close fails, and still releases everything.
    await expectLater(first, throwsA(isA<FisicaCommandError>()));
    expect(transport.closes, 1);
    expect(client.report()!.weight, 70.5);
    await client.close();
    expect(transport.closes, 1);
    expect(
      transport.sent.where((s) => s.toString() == release.toString()),
      hasLength(1),
    );
  });
}
