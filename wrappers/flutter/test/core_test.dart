import 'dart:io';
import 'dart:convert';
import 'dart:typed_data';
import 'package:crypto/crypto.dart';
import 'package:fisica_core/fisica_core.dart';
import 'package:test/test.dart';

void main() {
  final path = Platform.environment['FISICA_CORE_TEST_LIBRARY'];
  if (path == null)
    throw StateError(
      'Set FISICA_CORE_TEST_LIBRARY to a trusted native test binary',
    );
  final core = FisicaCore.open(path);
  // Shared vectors plus keys activated for this PC, from tools/verify_bindings.py.
  final vectorsPath = Platform.environment['FISICA_LICENSE_VECTORS'];
  if (vectorsPath == null)
    throw StateError('Set FISICA_LICENSE_VECTORS to the license vectors JSON');
  final vectors =
      jsonDecode(File(vectorsPath).readAsStringSync()) as Map<String, dynamic>;
  // Decoding grids is raw data: it needs full mode.
  final full = FisicaEntitlement(core, clock: () => vectors['now'] as int);
  full.importToken(vectors['full'] as String);
  tearDownAll(full.close);
  test('API deprecation lifecycle uses the wrapper version', () {
    final manifest = Map<String, dynamic>.from(core.wrapper);
    // Fixed version: this lifecycle must not depend on the current release.
    manifest['version'] = '0.3.0';
    manifest['deprecated_apis'] = {
      'oldApi': {
        'since': '0.2.0',
        'removed_in': '0.5.0',
        'replacement': 'newApi',
      },
    };
    final warnings = <String>[];
    warnDeprecatedApi(manifest, 'oldApi', warnings.add);
    expect(warnings.single, contains('Use newApi'));
    manifest['version'] = '0.5.0';
    expect(
      () => warnDeprecatedApi(manifest, 'oldApi', warnings.add),
      throwsStateError,
    );
  });
  test('unknown result schema fails before decoding', () {
    final policy = Map<String, dynamic>.from(core.policy)
      ..['result_schema_version'] = 2;
    expect(
      () => verifyCompatibility(
        policy: policy,
        wrapper: core.wrapper,
        sdkVersion: core.sdkVersion,
        coreVersion: core.coreVersion,
        abiVersion: core.abiVersion,
        onDeprecation: (_) {},
      ),
      throwsStateError,
    );
  });
  test('independent wrapper versions and native ABI', () {
    expect(core.versionInfo['wrapper_name'], 'flutter');
    // Expected values come from the release manifests, not from this test.
    final sdk = jsonDecode(File('../../sdk-release.json').readAsStringSync());
    final manifest = jsonDecode(File('wrapper.json').readAsStringSync());
    expect(core.versionInfo['wrapper_version'], manifest['version']);
    expect(core.versionInfo['core_version'], sdk['core_version']);
    expect(core.versionInfo.containsKey('distribution'), isFalse);
    expect(core.versionInfo['entitlement']['max_refresh_days'], 30);
    expect(core.versionInfo['abi_major'], 2);
  });
  test('same core decodes measurement and commands', () {
    final packet = Uint8List(645);
    packet[638] = packet[643] = 0xf0;
    packet[639] = packet[644] = 0x0f;
    packet[641] = 2;
    packet[642] = 193;
    expect(
      () => core.decodePacket(packet),
      throwsA(isA<FisicaLicenseError>().having((e) => e.code, 'code', 13)),
    );
    final result = core.decodePacket(packet, license: full);
    expect(result.weight, 70.5);
    expect(result.rawGrid!.length, 29);
    expect(result.rawGrid!.first.length, 22);
    expect(result.rawData, packet);
    expect(result.sensorMatrix.first.length, 29);
    expect(result.batteryValid, isFalse);
    expect(core.command('START_CONTINUOUS_MEASUREMENT'), [
      0x24,
      0x51,
      0x53,
      0,
      0,
      1,
      0xff,
      0xf0,
    ]);
    final partial = core.nextPacket(Uint8List.fromList(packet.sublist(0, 200)));
    expect(partial.consumed, 0);
    final complete = core.nextPacket(packet);
    expect(complete.kind, 1);
    expect(complete.bytes, packet);
  });
  test('analysis executes native implementation', () {
    final grid = List.generate(29, (_) => List.filled(29, 0.0));
    grid[4][3] = grid[4][20] = 100;
    expect(core.analyzeMatrices(List.filled(12, grid), List.filled(12, 70.5)), [
      70.5,
      1,
      1,
      1,
      1,
    ]);
  });
  test('invalid input fails', () {
    expect(() => core.decodePacket(Uint8List(644)), throwsArgumentError);
    expect(
      () => core.command('create_weight_scale', value: 32768),
      throwsArgumentError,
    );
  });
  Uint8List fromHex(String value) => Uint8List.fromList([
    for (var i = 0; i < value.length; i += 2)
      int.parse(value.substring(i, i + 2), radix: 16),
  ]);
  test('shared Python and Dart processing golden vectors', () {
    final fixtures =
        jsonDecode(
              File(
                '../../tests/fixtures/legacy-processing.json',
              ).readAsStringSync(),
            )
            as List<dynamic>;
    for (final fixture in fixtures) {
      final result = core.decodePacket(
        fromHex(fixture['packet_hex'] as String),
        license: full,
      );
      final values = result.sensorMatrix.expand((row) => row).toList();
      final data = ByteData(values.length * 8);
      for (var i = 0; i < values.length; i++) {
        data.setFloat64(i * 8, values[i], Endian.little);
      }
      expect(
        sha256.convert(data.buffer.asUint8List()).toString(),
        fixture['display_sha256'],
      );
      expect(result.weight, fixture['weight']);
    }
  });
  test('shared command and response golden vectors', () {
    final fixtures =
        jsonDecode(
              File('../../tests/fixtures/protocol.json').readAsStringSync(),
            )
            as Map<String, dynamic>;
    for (final fixture in fixtures['commands'] as List<dynamic>) {
      expect(
        core.command(fixture['name'] as String),
        fromHex(fixture['hex'] as String),
      );
    }
    for (final fixture in fixtures['responses'] as List<dynamic>) {
      final packet = fromHex(fixture['hex'] as String);
      final result = core.decodeResponse(packet);
      expect(result['header'], fixture['header']);
      expect(result['success'], fixture['success']);
      expect(result['payload'], fromHex(fixture['payload_hex'] as String));
      expect(core.nextPacket(packet).bytes, packet);
    }
    expect(
      () => core.command('no-such-command'),
      throwsA(isA<FisicaArgumentError>().having((e) => e.code, 'code', 4)),
    );
  });
  test('wrapper deprecation and unsupported ABI are separate', () {
    final warnings = <String>[];
    final oldWrapper = Map<String, dynamic>.from(core.wrapper)
      ..['version'] = '0.0.9';
    expect(
      () => verifyCompatibility(
        policy: core.policy,
        wrapper: oldWrapper,
        sdkVersion: core.sdkVersion,
        coreVersion: core.coreVersion,
        abiVersion: core.abiVersion,
        onDeprecation: warnings.add,
      ),
      throwsStateError,
    );
    // A deprecation window like the ones minor releases open (3.0.0 has none).
    final window = jsonDecode(jsonEncode(core.policy)) as Map<String, dynamic>;
    window['sdk_support']['minimum'] = '1.0.0';
    verifyCompatibility(
      policy: window,
      wrapper: core.wrapper,
      sdkVersion: '1.5.0',
      coreVersion: core.coreVersion,
      abiVersion: core.abiVersion,
      onDeprecation: warnings.add,
    );
    expect(warnings.single, contains('SDK 1.5.0 is deprecated'));
    expect(
      () => verifyCompatibility(
        policy: core.policy,
        wrapper: core.wrapper,
        sdkVersion: core.sdkVersion,
        coreVersion: core.coreVersion,
        abiVersion: 3 << 16,
        onDeprecation: warnings.add,
      ),
      throwsStateError,
    );
  });
  test('shared Sans-IO session state and ACK golden vectors', () {
    final vectors =
        jsonDecode(
              File('../../tests/fixtures/sessions.json').readAsStringSync(),
            )
            as List<dynamic>;
    for (final vector in vectors) {
      final session = FisicaSession(core);
      try {
        for (final step in vector['steps']) {
          final now = step['now_ms'] as int;
          if (step.containsKey('action')) {
            session.action(
              step['action'] as int,
              value: step['value'] as int,
              nowMs: now,
            );
          } else if (step.containsKey('feed_hex')) {
            session.feed(fromHex(step['feed_hex'] as String), nowMs: now);
          } else if (step.containsKey('command')) {
            session.command(step['command'] as String, nowMs: now);
          } else {
            session.tick(now);
          }
          final events = session
              .drain()
              .map(
                (e) => [
                  e.kind,
                  e.state,
                  e.status,
                  e.deviceError,
                  e.operation,
                  e.payload
                      .map((b) => b.toRadixString(16).padLeft(2, '0'))
                      .join(),
                ],
              )
              .toList();
          expect(events, step['events'], reason: vector['id'] as String);
        }
      } finally {
        session.close();
      }
      expect(
        () => session.tick(0),
        throwsA(isA<FisicaStateError>().having((e) => e.code, 'code', 6)),
      );
    }
  });
}
