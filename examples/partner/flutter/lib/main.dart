import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:device_info_plus/device_info_plus.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:fisica_flutter/fisica_flutter.dart';

void main() => runApp(const MaterialApp(home: ScalePage()));

class ScalePage extends StatefulWidget {
  const ScalePage({super.key});
  @override
  State<ScalePage> createState() => _ScalePageState();
}

class _ScalePageState extends State<ScalePage> {
  String status = 'Choose USB, BLE or the hardware-free demo';
  bool busy = false;
  List<({String id, String name, bool ble})> devices = [];
  FisicaClient? client;
  void show(String message) {
    if (mounted) setState(() => status = message);
  }

  Future<void> run(Future<void> Function() task) async {
    if (busy) return;
    setState(() => busy = true);
    try {
      await task();
    } catch (error) {
      show('$error');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> scanBle() async {
    if (Platform.isAndroid) {
      final sdk = (await DeviceInfoPlugin().androidInfo).version.sdkInt;
      final permissions = sdk >= 31
          ? [Permission.bluetoothScan, Permission.bluetoothConnect]
          : [Permission.locationWhenInUse];
      final results = await permissions.request();
      if (results.values.any((result) => !result.isGranted)) {
        throw StateError(
          'Bluetooth permission denied; allow it in app settings',
        );
      }
    }
    show('Scanning for 5 seconds');
    final found = await FisicaBleTransport.scan();
    if (!mounted) return;
    setState(
      () => devices = [
        for (final d in found)
          (id: d.deviceId, name: d.name ?? d.deviceId, ble: true),
      ],
    );
    show(
      found.isEmpty
          ? 'No Scale found; check power and permissions'
          : 'Choose a Scale',
    );
  }

  Future<void> measure(
    FisicaTransport transport,
    int link, {
    bool mock = false,
  }) async {
    final core = FisicaCore.platform();
    final current = client = FisicaClient(core, transport);
    final events = current.events.listen(
      (_) {},
      onError: (Object error) => show('$error'),
    );
    try {
      await current.connect(link: link);
      show(mock ? 'Synthetic demo measurement' : 'Measuring for 5 seconds');
      await current.start();
      await Future<void>.delayed(const Duration(seconds: 5));
      await current.stop();
      final report = current.report();
      show(
        report == null
            ? 'No stable result; check load and received frames'
            : '${mock ? "Demo: " : ""}${report.weight} kg; ${report.frames.length} frames',
      );
    } finally {
      try {
        await current.close();
      } finally {
        await events.cancel();
        if (identical(client, current)) client = null;
      }
    }
  }

  @override
  void dispose() {
    final current = client;
    if (current != null) unawaited(current.close().catchError((Object _) {}));
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Fisica Scale sample')),
    body: Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        children: [
          Text(status),
          Wrap(
            spacing: 8,
            children: [
              ElevatedButton(
                onPressed: busy ? null : () => run(scanBle),
                child: const Text('Scan BLE'),
              ),
              if (Platform.isWindows || Platform.isMacOS)
                ElevatedButton(
                  onPressed: busy
                      ? null
                      : () => run(() async {
                          final ports = FisicaUsbTransport.ports();
                          setState(
                            () => devices = [
                              for (final port in ports)
                                (id: port, name: port, ble: false),
                            ],
                          );
                          show('Choose the Scale serial port');
                        }),
                  child: const Text('List USB ports'),
                ),
              ElevatedButton(
                onPressed: busy
                    ? null
                    : () => run(() async {
                        final script =
                            jsonDecode(
                                  await rootBundle.loadString(
                                    'assets/scale-mock.json',
                                  ),
                                )
                                as Map<String, dynamic>;
                        await measure(
                          DemoTransport(FisicaCore.platform(), script),
                          FisicaLink.usb,
                          mock: true,
                        );
                      }),
                child: const Text('Demo without hardware'),
              ),
            ],
          ),
          Expanded(
            child: ListView(
              children: [
                for (final d in devices)
                  ListTile(
                    title: Text(d.name),
                    subtitle: Text(d.id),
                    onTap: busy
                        ? null
                        : () => run(
                            () => measure(
                              d.ble
                                  ? FisicaBleTransport(d.id)
                                  : FisicaUsbTransport(d.id),
                              d.ble ? FisicaLink.ble : FisicaLink.usb,
                            ),
                          ),
                  ),
              ],
            ),
          ),
        ],
      ),
    ),
  );
}

/// Recorded synthetic replies only; no measurement/protocol algorithms.
class DemoTransport implements FisicaTransport {
  final FisicaCore core;
  final Map<String, dynamic> script;
  final input = StreamController<Uint8List>.broadcast();
  final lost = StreamController<void>.broadcast();
  DemoTransport(this.core, this.script);
  Uint8List decode(String hex) => Uint8List.fromList([
    for (var i = 0; i < hex.length; i += 2)
      int.parse(hex.substring(i, i + 2), radix: 16),
  ]);
  @override
  Stream<Uint8List> get received => input.stream;
  @override
  Stream<void> get disconnected => lost.stream;
  @override
  Future<void> connect() async {}
  @override
  Future<void> send(Uint8List bytes) async {
    for (final entry in (script['replies'] as Map<String, dynamic>).entries) {
      final request = core.command(entry.key);
      if (bytes.length != request.length ||
          !List.generate(
            bytes.length,
            (i) => bytes[i] == request[i],
          ).every((same) => same))
        continue;
      input.add(decode(entry.value as String));
      if (entry.key == 'START_CONTINUOUS_MEASUREMENT') {
        for (var i = 0; i < script['frame_count']; i++)
          input.add(decode(script['measurement_hex'] as String));
      }
      return;
    }
  }

  @override
  Future<void> close() async {
    await input.close();
    await lost.close();
  }
}
