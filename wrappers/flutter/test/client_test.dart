import 'dart:async';
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
