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
    closed = true;
    await input.close();
    await lost.close();
  }
}

void main() {
  final core = FisicaCore.open(
    Platform.environment['FISICA_CORE_TEST_LIBRARY']!,
  );
  test('async client uses core for start stop release', () async {
    final transport = MockBytes();
    final client = FisicaClient(core, transport);
    expect((await client.connect(profile: 2)).status, 0);
    expect((await client.start()).state, 3);
    expect((await client.stop()).state, 1);
    await client.close();
    expect(transport.closed, isTrue);
  });
  test('duration completes from core event', () async {
    final client = FisicaClient(core, MockBytes());
    await client.connect();
    final stopped = client.events.firstWhere(
      (e) => e.kind == 3 && e.operation == 3,
    );
    await client.start(duration: const Duration(milliseconds: 30));
    expect((await stopped.timeout(const Duration(seconds: 2))).status, 0);
    await client.close();
  });
  test('client collects frames and reports through the shared core', () async {
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
    final packet = Uint8List.fromList([
      for (var i = 0; i < hex.length; i += 2)
        int.parse(hex.substring(i, i + 2), radix: 16),
    ]);
    final transport = MockBytes();
    final client = FisicaClient(core, transport);
    await client.connect(profile: 2);
    await client.start();
    for (var i = 0; i < 12; i++) {
      transport.input.add(packet);
    }
    await Future<void>.delayed(const Duration(milliseconds: 60));
    await client.stop();
    expect(client.frames.length, 12);
    final report = client.report()!;
    expect(report.weight, 70.5);
    expect(report.frames.first.sensorMatrix.length, 29);
    expect(report.frames.first.rawGrid.first.length, 22);
    expect(
      core.analyzePackets(List.filled(12, packet)),
      core.analyzeMatrices(
        List.filled(12, report.frames.first.sensorMatrix),
        List.filled(12, report.frames.first.weight),
      ),
    );
    await client.close();
  });
  test('entitlement is status only and never blocks measurement', () async {
    final vectors =
        jsonDecode(
              File('../../tests/fixtures/entitlement.json').readAsStringSync(),
            )
            as Map<String, dynamic>;
    final now = vectors['now'] as int;
    var clock = now;
    final entitlement = FisicaEntitlement(core, clock: () => clock);
    var seen = 0;
    entitlement.onWarning = (bits, _) => seen |= bits;
    expect(entitlement.status().contract, FisicaContract.unverified);
    expect(
      entitlement.importToken(vectors['tampered'] as String),
      FisicaEntitlementOutcome.rejectedSignature,
    );
    expect(entitlement.status().company, '');
    expect(
      entitlement.importToken(vectors['valid'] as String),
      FisicaEntitlementOutcome.accepted,
    );
    var status = entitlement.status();
    expect(status.baseLicense, FisicaBaseLicense.granted);
    expect(status.verification, FisicaVerification.fresh);
    expect(status.contract, FisicaContract.active);
    expect(status.company, 'acme');
    expect(status.keyEnvironment, FisicaKeyEnvironment.test);
    expect(
      entitlement.importToken(vectors['older'] as String),
      FisicaEntitlementOutcome.ignoredOlder,
    );
    clock = now + 31 * 86400;
    status = entitlement.status();
    expect(status.baseLicense, FisicaBaseLicense.granted);
    expect(status.verification, FisicaVerification.refreshDue);
    expect(status.contract, FisicaContract.active);
    entitlement.importToken(vectors['revoked'] as String);
    expect(entitlement.status().contract, FisicaContract.revoked);
    // Measurement still runs with a revoked contract and outdated confirmation.
    final client = FisicaClient(core, MockBytes(), entitlement: entitlement);
    await client.connect(profile: 2);
    expect((await client.start()).state, FisicaSessionState.measuring);
    await client.stop();
    await client.close();
    // Using the SDK leaves no trace: the stored state is still only the
    // signed token.
    expect(
      ascii.decode(entitlement.exportState()),
      'FSS1\nC ${vectors['revoked']}\n',
    );
    final restored = FisicaEntitlement(core, clock: () => clock);
    restored.restoreState(entitlement.exportState());
    expect(restored.status().company, 'acme');
    final expected =
        FisicaEntitlementWarning.importRejected |
        FisicaEntitlementWarning.testKey |
        FisicaEntitlementWarning.contractRevoked;
    expect(seen & expected, expected);
    entitlement.close();
    restored.close();
  });
  test('online confirmation and storage', () async {
    // Runs against the reference mock server started by tools/verify_bindings.py.
    final environment = Platform.environment;
    final endpoint = environment['FISICA_ENTITLEMENT_ENDPOINT'];
    if (endpoint == null) {
      markTestSkipped('Mock entitlement server not configured');
      return;
    }
    final directory = environment['FISICA_ENTITLEMENT_STORAGE']!;
    final now = int.parse(environment['FISICA_ENTITLEMENT_NOW']!);
    final valid =
        (jsonDecode(
                  File(
                    '../../tests/fixtures/entitlement.json',
                  ).readAsStringSync(),
                )
                as Map<String, dynamic>)['valid']
            as String;
    final entitlement = FisicaEntitlement(
      core,
      clock: () => now,
      endpoint: endpoint,
      storageDirectory: directory,
    );
    expect(
      entitlement.importToken(valid),
      FisicaEntitlementOutcome.accepted,
    );
    expect(await entitlement.sync(), isNull);
    // Server-signed confirmation time.
    expect(entitlement.status().verifiedAt, now);
    entitlement.checkStatus();
    expect(await entitlement.sync(), isNull);
    // A later start with the app's older token still finds the newer stored
    // confirmation.
    final reopened = FisicaEntitlement(
      core,
      clock: () => now,
      storageDirectory: directory,
    );
    reopened.importToken(valid);
    expect(reopened.status().verifiedAt, now);
    // A communication failure is recorded, raises nothing and changes no
    // state.
    final offline = FisicaEntitlement(
      core,
      clock: () => now,
      endpoint: 'http://127.0.0.1:9',
      storageDirectory: '$directory/offline',
    );
    offline.importToken(valid);
    offline.checkStatus();
    expect(await offline.sync(), startsWith('network:'));
    expect(offline.status().contract, FisicaContract.active);
    expect(offline.status().verification, FisicaVerification.fresh);
    entitlement.close();
    offline.close();
    reopened.close();
  });
  test('close during start cancels pending future and disposes once', () async {
    final client = FisicaClient(core, MockBytes());
    await client.connect();
    final start = client.start();
    final canceled = expectLater(start, throwsA(isA<FisicaCommandError>()));
    final close = client.close();
    expect(identical(close, client.close()), isTrue);
    await canceled;
    await close;
  });
}
