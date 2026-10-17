import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:crypto/crypto.dart';
import 'package:fisica_core/fisica_core.dart';
import 'package:test/test.dart';

const day = 86400;
const allFeatures =
    FisicaFeature.rawData |
    FisicaFeature.export |
    FisicaFeature.advanced |
    FisicaFeature.fullRate;

Uint8List fromHex(String value) => Uint8List.fromList([
  for (var i = 0; i < value.length; i += 2)
    int.parse(value.substring(i, i + 2), radix: 16),
]);

dynamic fixture(String name) =>
    jsonDecode(File('../../tests/fixtures/$name').readAsStringSync());

/// Body of a FAC1/FRC1 code, after checking its check value as the
/// developer portal does.
Map<String, dynamic> readCode(String code, String prefix) {
  final parts = code.split('.');
  expect(parts, hasLength(3));
  expect(parts[0], prefix);
  final material =
      '${prefix == 'FRC1' ? 'fisica-release-code-v1|' : ''}$prefix.${parts[1]}';
  expect(
    parts[2],
    sha256.convert(utf8.encode(material)).toString().substring(0, 8),
  );
  return jsonDecode(utf8.decode(base64Url.decode(base64Url.normalize(parts[1]))))
      as Map<String, dynamic>;
}

void main() {
  final core = FisicaCore.open(
    Platform.environment['FISICA_CORE_TEST_LIBRARY']!,
  );
  // Shared vectors plus keys activated for this PC (a command-line test
  // program is bound to the PC), written by tools/verify_bindings.py.
  final vectorsPath = Platform.environment['FISICA_LICENSE_VECTORS'];
  if (vectorsPath == null)
    throw StateError('Set FISICA_LICENSE_VECTORS to the license vectors JSON');
  final vectors =
      jsonDecode(File(vectorsPath).readAsStringSync()) as Map<String, dynamic>;
  final now = vectors['now'] as int;
  final target = vectors['target'] as String;
  if (vectors['target_type'] != 'pc')
    throw StateError('A command-line test program is bound to the PC');
  final packet = fromHex(
    (fixture('legacy-processing.json') as List<dynamic>).firstWhere(
          (f) => f['id'] == 'synthetic-recorded-layout',
        )['packet_hex']
        as String,
  );
  final contexts = <FisicaEntitlement>[];
  FisicaEntitlement license({
    int Function()? clock,
    String? storage,
    String? endpoint,
  }) {
    final entitlement = FisicaEntitlement(
      core,
      clock: clock ?? () => now,
      storageDirectory: storage,
      endpoint: endpoint,
      timeout: const Duration(seconds: 2),
    );
    contexts.add(entitlement);
    return entitlement;
  }

  tearDown(() {
    for (final entitlement in contexts) {
      entitlement.close();
    }
    contexts.clear();
  });
  Directory temporary() {
    final directory = Directory.systemTemp.createTempSync('fisica-license-');
    addTearDown(() => directory.deleteSync(recursive: true));
    return directory;
  }

  test('without a key the core reads this PC and stays in basic mode', () {
    final entitlement = license();
    final status = entitlement.status();
    expect(status.mode, FisicaMode.basic);
    expect(status.isFull, isFalse);
    expect(status.reason, FisicaModeReason.noKey);
    expect(status.features, 0);
    expect(status.company, isNull);
    expect(status.key, isNull);
    expect(status.activationCode, isNull);
    expect(status.targetType, FisicaBindType.pc);
    expect(status.target, target);
    expect(status.message, contains('Basic mode'));
    expect(entitlement.activationCode(), isNull);
    // The stored state is the latest time seen: no record of SDK use.
    expect(ascii.decode(entitlement.exportState()), 'FSS1\nT $now\n');
  });
  test('an unactivated key is basic mode with an activation code', () {
    final entitlement = license();
    expect(
      entitlement.importToken(vectors['unbound'] as String),
      FisicaEntitlementOutcome.accepted,
    );
    final status = entitlement.status();
    expect(status.mode, FisicaMode.basic);
    expect(status.reason, FisicaModeReason.notActivated);
    expect(status.bindType, FisicaBindType.none);
    expect(status.company, 'acme');
    expect(status.contract, 'ctr-1');
    expect(status.key, 'key_test_1');
    expect(status.track, FisicaTrack.trial);
    expect(status.fullUntil, now + 14 * day);
    // The developer portal's FAC1 parser accepts it: key, this PC and check.
    expect(readCode(status.activationCode!, 'FAC1'), {
      'k': 'key_test_1',
      't': 'pc',
      'b': target,
    });
    expect(entitlement.activationCode(), status.activationCode);
    expect(
      entitlement.importToken(vectors['unbound_older'] as String),
      FisicaEntitlementOutcome.ignoredOlder,
    );
    // Nothing is activated here to release.
    expect(
      entitlement.release,
      throwsA(isA<FisicaStateError>().having((e) => e.code, 'code', 6)),
    );
  });
  test('a key activated for this PC is full mode until the trial ends', () {
    var clock = now;
    final entitlement = license(clock: () => clock);
    final noticed = <int>[];
    entitlement.onWarning = (bits, _) => noticed.add(bits);
    expect(
      entitlement.importToken(vectors['full'] as String),
      FisicaEntitlementOutcome.accepted,
    );
    var status = entitlement.status();
    expect(status.isFull, isTrue);
    expect(status.reason, FisicaModeReason.none);
    expect(status.message, isNull);
    expect(status.features, allFeatures);
    expect(status.hasFeature(FisicaFeature.rawData), isTrue);
    expect(entitlement.fullRate, isTrue);
    expect(status.bindType, FisicaBindType.pc);
    expect(status.bind, target);
    expect(status.key, 'key_test_1');
    expect(status.keyUntil, now + 90 * day);
    expect(status.fullUntil, now + 14 * day);
    expect(status.keyEnvironment, FisicaKeyEnvironment.test);
    expect(status.activationCode, isNull);
    expect(noticed, [FisicaEntitlementWarning.testKey]);
    expect(
      entitlement.importToken(vectors['full_older'] as String),
      FisicaEntitlementOutcome.ignoredOlder,
    );
    clock = now + 8 * day;
    entitlement.checkStatus();
    expect(noticed.last & FisicaEntitlementWarning.fullEnding, isNonZero);
    expect(
      entitlement.status().warningMessages,
      anyElement(contains('7 days')),
    );
    clock = now + 14 * day;
    status = entitlement.status();
    expect(status.mode, FisicaMode.basic);
    expect(status.reason, FisicaModeReason.fullEnded);
    expect(status.features, 0);
    expect(entitlement.fullRate, isFalse);
  });
  test('rejected keys never replace the key activated here', () {
    final entitlement = license();
    entitlement.importToken(vectors['full'] as String);
    expect(
      entitlement.importToken(vectors['other_target'] as String),
      FisicaEntitlementOutcome.rejectedTarget,
    );
    expect(
      entitlement.importToken(vectors['tampered'] as String),
      FisicaEntitlementOutcome.rejectedSignature,
    );
    expect(
      entitlement.importToken(vectors['unknown_key'] as String),
      FisicaEntitlementOutcome.rejectedKey,
    );
    expect(
      entitlement.importToken('not a key'),
      FisicaEntitlementOutcome.rejectedFormat,
    );
    final status = entitlement.status();
    expect(status.isFull, isTrue);
    expect(status.key, 'key_test_1');
    expect(status.lastImport, FisicaEntitlementOutcome.rejectedFormat);
    expect(status.warnings & FisicaEntitlementWarning.importRejected, isNonZero);
  });
  test('a contract confirmation and a released key are basic mode', () {
    final legacy = license();
    expect(
      legacy.importToken(vectors['legacy'] as String),
      FisicaEntitlementOutcome.accepted,
    );
    expect(legacy.status().reason, FisicaModeReason.legacyToken);
    expect(legacy.status().contractState, FisicaContract.active);
    final released = license(clock: () => now + day);
    released.importToken(vectors['revoked'] as String);
    expect(released.status().mode, FisicaMode.basic);
    expect(released.status().reason, FisicaModeReason.revoked);
  });
  test('decoding grids needs full mode with raw data', () {
    expect(
      () => core.decodePacket(packet),
      throwsA(isA<FisicaLicenseError>().having((e) => e.code, 'code', 13)),
    );
    final basic = license()..importToken(vectors['unbound'] as String);
    expect(
      () => core.decodePacket(packet, license: basic),
      throwsA(isA<FisicaLicenseError>()),
    );
    final limited = license()..importToken(vectors['limited'] as String);
    expect(
      limited.status().features,
      FisicaFeature.export | FisicaFeature.fullRate,
    );
    expect(
      () => core.decodePacket(packet, license: limited),
      throwsA(isA<FisicaLicenseError>()),
    );
    final full = license()..importToken(vectors['full'] as String);
    final decoded = core.decodePacket(packet, license: full);
    expect(decoded.weight, 70.5);
    expect(decoded.rawGrid!.first.length, 22);
  });
  test('a session hands out frames by mode; analysis uses every packet', () {
    final full = license()..importToken(vectors['full'] as String);
    final reference = core.decodePacket(packet, license: full);
    final firmware = fromHex(
      (fixture('protocol.json')['responses'] as List<dynamic>).first['hex']
          as String,
    );
    for (final entitlement in [null, full]) {
      final session = FisicaSession(core);
      try {
        var time = 1000;
        session.action(
          FisicaSessionAction.connected,
          value: FisicaLink.usb,
          nowMs: time,
        );
        session.feed(firmware, nowMs: time);
        session.license(entitlement, nowMs: time);
        session.action(FisicaSessionAction.start, value: -1, nowMs: time);
        for (final name in ['SET_DEVICE_USB', 'START_CONTINUOUS_MEASUREMENT']) {
          final command = core.command(name);
          session.feed(
            Uint8List.fromList([0x24, command[1], command[2], 1, 0, 0, 255, 240]),
            nowMs: time,
          );
        }
        session.drain();
        final frames = <DecodedMeasurement>[];
        final times = <int>[];
        for (var i = 0; i < 10; i++) {
          time += 300;
          session.feed(packet, nowMs: time);
          for (final event in session.drain()) {
            if (event.kind != FisicaSessionEventKind.measurement) continue;
            frames.add(core.frameFromEvent(event));
            times.add(time);
          }
        }
        expect(frames, hasLength(entitlement == null ? 3 : 10));
        for (final frame in frames) {
          expect(frame.weight, reference.weight);
          expect(frame.sensorMatrix, reference.sensorMatrix);
        }
        if (entitlement == null) {
          // Basic mode: at most one frame per second of session time, no raw data.
          for (var i = 1; i < times.length; i++) {
            expect(
              times[i] - times[i - 1],
              greaterThanOrEqualTo(FisicaLimit.basicFrameIntervalMs),
            );
          }
          expect(frames.every((f) => f.rawGrid == null && f.rawData == null), isTrue);
        } else {
          expect(frames.first.rawData, packet);
          expect(frames.first.rawGrid, reference.rawGrid);
        }
        expect(session.analyze()!.first, reference.weight);
      } finally {
        session.close();
      }
    }
  });
  test('release gives the FRC1 code, forgets the key and its stored state', () {
    final directory = temporary();
    final entitlement = license(storage: directory.path);
    entitlement.importToken(vectors['full'] as String);
    expect(directory.listSync(), hasLength(1));
    final code = entitlement.release();
    expect(readCode(code, 'FRC1'), {
      'k': 'key_test_1',
      't': 'pc',
      'b': target,
    });
    final status = entitlement.status();
    expect(status.mode, FisicaMode.basic);
    expect(status.reason, FisicaModeReason.noKey);
    expect(status.key, isNull);
    expect(directory.listSync(), isEmpty);
    expect(entitlement.release, throwsA(isA<FisicaStateError>()));
  });
  test('the stored activated key wins over the original key file', () {
    final directory = temporary();
    license(storage: directory.path).importToken(vectors['full'] as String);
    // A later start loads the activated key by itself.
    final reopened = license(storage: directory.path);
    expect(reopened.status().isFull, isTrue);
    expect(
      reopened.importToken(vectors['unbound_older'] as String),
      FisicaEntitlementOutcome.ignoredOlder,
    );
    expect(reopened.status().isFull, isTrue);
    expect(reopened.status().bind, target);
  });
  test('only a state file of this PC is loaded', () {
    final directory = temporary();
    File('${directory.path}/entitlement-0000000000000000.state')
        .writeAsStringSync('FSS1\nC ${vectors['other_target']}\nT ${now + 400 * day}\n');
    File('${directory.path}/entitlement-1111111111111111.state')
        .writeAsStringSync('not a state');
    final empty = license(storage: directory.path);
    expect(empty.status().reason, FisicaModeReason.noKey);
    // The other file's later time is not taken over: no clock rollback here.
    empty.importToken(vectors['full'] as String);
    expect(empty.status().isFull, isTrue);
  });
  test('a failed exchange changes nothing and never throws', () async {
    final entitlement = license(endpoint: 'http://127.0.0.1:9');
    // Importing a new key starts its activation in the background.
    entitlement.importToken(vectors['unbound'] as String);
    final result = await entitlement.sync();
    expect(result!.error, startsWith('network:'));
    expect(result.activated, isFalse);
    final status = entitlement.status();
    expect(status.reason, FisicaModeReason.notActivated);
    expect(status.lastSyncError, startsWith('network:'));
    // Without an endpoint, or offline, nothing leaves the device.
    expect(await license().sync(), isNull);
  });
  test('a renewal reporting the key released deletes the stored state', () async {
    final requests = <(String, String?, String)>[];
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    addTearDown(server.close);
    server.listen((request) async {
      requests.add((
        request.uri.path,
        request.headers.value(HttpHeaders.authorizationHeader),
        await utf8.decoder.bind(request).join(),
      ));
      request.response.headers.contentType = ContentType.json;
      request.response.write(jsonEncode({'token': vectors['revoked']}));
      await request.response.close();
    });
    final directory = temporary();
    var clock = now;
    final entitlement = license(
      clock: () => clock,
      storage: directory.path,
      endpoint: 'http://127.0.0.1:${server.port}/',
    );
    entitlement.importToken(vectors['full'] as String);
    // Full mode with nothing due: the background check sends nothing.
    expect(await entitlement.sync(), (
      activated: false,
      refreshed: false,
      error: null,
    ));
    expect(requests, isEmpty);
    clock = now + 15 * day; // the trial ended: renew
    expect(await entitlement.sync(), (
      activated: false,
      refreshed: true,
      error: null,
    ));
    expect(requests, [
      ('/sdk/v1/entitlement/refresh', 'Bearer ${vectors['full']}', '{}'),
    ]);
    expect(entitlement.status().reason, FisicaModeReason.revoked);
    expect(directory.listSync(), isEmpty);
    // A released key is not renewed again.
    expect((await entitlement.sync())!.refreshed, isFalse);
    expect(requests, hasLength(1));
  });
  test('online activation for this PC against the mock developer portal', () async {
    // The reference server started by tools/verify_bindings.py holds the
    // unactivated `unbound` key and checks every request it receives.
    final environment = Platform.environment;
    final endpoint = environment['FISICA_ENTITLEMENT_ENDPOINT'];
    if (endpoint == null) {
      markTestSkipped('Mock entitlement server not configured');
      return;
    }
    final storage = environment['FISICA_ENTITLEMENT_STORAGE']!;
    final serverNow = int.parse(environment['FISICA_ENTITLEMENT_NOW']!);
    final entitlement = license(
      clock: () => serverNow,
      endpoint: endpoint,
      storage: storage,
    );
    final changes = <EntitlementStatus>[];
    entitlement.changes.listen(changes.add);
    expect(
      entitlement.importToken(vectors['unbound'] as String),
      FisicaEntitlementOutcome.accepted,
    );
    // The activation runs in the background; sync() completes after it.
    await entitlement.sync();
    final status = entitlement.status();
    expect(status.isFull, isTrue);
    expect(status.bindType, FisicaBindType.pc);
    expect(status.bind, target);
    expect(status.verifiedAt, serverNow);
    expect(status.keyUntil, serverNow + 90 * day);
    expect(status.lastSyncError, isNull);
    await Future<void>.delayed(Duration.zero);
    expect(changes.last.isFull, isTrue);
    // A forced renewal sends an empty body.
    expect((await entitlement.sync(force: true))!.error, isNull);
    // A later start finds the activated key; the original key file does not replace it.
    final reopened = license(clock: () => serverNow, storage: storage);
    expect(reopened.status().isFull, isTrue);
    expect(
      reopened.importToken(vectors['unbound'] as String),
      FisicaEntitlementOutcome.ignoredOlder,
    );
    expect(reopened.status().bind, target);
    // Pairing without a key: the mock portal approves every code at once
    // with an unbound key of the contract.
    final paired = '$storage/paired';
    final fresh = license(clock: () => serverNow, endpoint: endpoint, storage: paired);
    expect(fresh.status().reason, FisicaModeReason.noKey);
    PairingCode? shown;
    expect(await fresh.pair((code) => shown = code), PairingResult.approved);
    expect(shown!.userCode, hasLength(9));
    expect(fresh.status().key, 'key_pair_1');
    expect(fresh.status().bind, target);
    final later = license(clock: () => serverNow, storage: paired);
    expect(later.status().isFull, isTrue);
    expect(later.status().key, 'key_pair_1');
    await expectLater(license().pair((_) {}), throwsA(isA<FisicaStateError>()));
  });
}
