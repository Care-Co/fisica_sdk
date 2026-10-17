import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:device_info_plus/device_info_plus.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:fisica_flutter/fisica_flutter.dart';

// The same steps as every partner sample: SDK and core version, the license,
// pick a Scale (or the synthetic demo), Connect (reads the battery), Measure
// (5 s), the report, Disconnect.

void main() => runApp(const MaterialApp(home: ScalePage()));

/// SDK key from the developer portal, e.g. `flutter run
/// --dart-define=FISICA_SDK_KEY=FSE1...`. Without one the SDK runs in basic mode.
/// On iOS and Android the key is bound to the app ID, one seat for every
/// install, so it ships inside the app (pairing with a code is for PCs).
const sdkKey = String.fromEnvironment('FISICA_SDK_KEY');
const licenseEndpoint = String.fromEnvironment(
  'FISICA_LICENSE_ENDPOINT',
  defaultValue: FisicaEntitlement.defaultEndpoint,
);
const noResult =
    'No stable result; check the load on the Scale and the received frames';

/// Reason names as the other samples show them ([FisicaModeReason] codes).
const reasonNames = [
  '',
  'no_key',
  'legacy_token',
  'not_activated',
  'no_target',
  'other_target',
  'revoked',
  'clock_rollback',
  'key_expired',
  'not_started',
  'full_ended',
];
const emptyReport = [
  'Weight: -',
  'Left foot: -',
  'Right foot: -',
  'Frames: -',
  'Battery: -',
];

/// The Scale to connect: a BLE device, a desktop USB serial port, or the
/// synthetic demo Scale (no hardware).
typedef ScaleChoice = ({String id, String name, int link, bool demo});

class ScalePage extends StatefulWidget {
  const ScalePage({super.key});
  @override
  State<ScalePage> createState() => _ScalePageState();
}

class _ScalePageState extends State<ScalePage> {
  String version = '';
  String license = 'License: not loaded';
  String status = 'Scan for Scales, or try the demo';
  List<ScaleChoice> devices = [];
  ScaleChoice? selected;
  Map<String, dynamic>? demoScript;
  bool connected = false;
  bool busy = false;
  List<String> report = emptyReport;
  FisicaCore? core;
  FisicaEntitlement? entitlement;
  FisicaClient? client;
  StreamSubscription<FisicaSessionEvent>? events;
  StreamSubscription<EntitlementStatus>? licenseChanges;

  void update(VoidCallback change) {
    if (mounted) setState(change);
  }

  /// "full mode", or "basic mode (no_key)" with the SDK's explanation of the
  /// reason.
  static String describeLicense(EntitlementStatus current) => [
    current.isFull
        ? 'License: full mode'
        : 'License: basic mode (${current.reason < reasonNames.length ? reasonNames[current.reason] : 'unknown'})',
    if (current.message != null) current.message!,
    if (current.activationCode != null)
      'Activation code: ${current.activationCode}',
  ].join('\n');

  static String describe(Object error) => error is StateError
      ? error.message
      : error is ArgumentError
      ? '${error.message}'
      : '$error';

  @override
  void initState() {
    super.initState();
    try {
      final platform = core = FisicaCore.platform();
      version =
          'Fisica SDK ${platform.sdkVersion} (core ${platform.coreVersion})';
      // One license for the app. Online, a new key is activated for this app
      // or PC and renewed automatically. Pass storageDirectory (for example
      // from path_provider) to keep the activated key across launches.
      final current = entitlement = FisicaEntitlement(
        platform,
        endpoint: licenseEndpoint,
      );
      licenseChanges = current.changes.listen(
        (status) => update(() => license = describeLicense(status)),
      );
      if (sdkKey.isNotEmpty) current.importToken(sdkKey);
      license = describeLicense(current.status());
    } catch (error) {
      status = 'Error: ${describe(error)}';
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
        update(
          () => status =
              'Permission denied; allow Bluetooth access in app settings',
        );
        return;
      }
    }
    update(() {
      devices = [];
      selected = null;
      status = 'Scanning for 5 seconds';
    });
    try {
      final found = await FisicaBleTransport.scan();
      update(() {
        devices = [
          for (final d in found)
            (
              id: d.deviceId,
              name: d.name ?? d.deviceId,
              link: FisicaLink.ble,
              demo: false,
            ),
        ];
        status = found.isEmpty
            ? 'No Scale found; check that it is powered on'
            : 'Choose a Scale';
      });
    } catch (error) {
      update(() => status = 'Error: ${describe(error)}');
    }
  }

  void listUsb() => update(() {
    devices = [
      for (final port in FisicaUsbTransport.ports())
        (id: port, name: port, link: FisicaLink.usb, demo: false),
    ];
    selected = null;
    status = devices.isEmpty
        ? 'No serial port found; check the USB cable'
        : 'Choose the Scale serial port';
  });

  void choose(ScaleChoice choice) => update(() {
    selected = choice;
    status = '${choice.name} selected; press Connect';
  });

  Future<void> chooseDemo() async {
    try {
      demoScript =
          jsonDecode(await rootBundle.loadString('assets/scale-mock.json'))
              as Map<String, dynamic>;
      update(() {
        selected = (
          id: 'demo',
          name: 'Demo without hardware',
          link: FisicaLink.usb,
          demo: true,
        );
        status = 'Demo without hardware selected; press Connect';
      });
    } catch (error) {
      update(() => status = 'Error: ${describe(error)}');
    }
  }

  Future<void> connect() async {
    final choice = selected;
    final platform = core;
    if (choice == null || platform == null || client != null || busy) return;
    final FisicaTransport transport = choice.demo
        ? DemoTransport(platform, demoScript!)
        : choice.link == FisicaLink.ble
        ? FisicaBleTransport(choice.id)
        : FisicaUsbTransport(choice.id);
    // The client applies the license to its session (full or basic mode).
    final current = client = FisicaClient(
      platform,
      transport,
      entitlement: entitlement,
    );
    events = current.events.listen(
      (_) {},
      onError: (Object error) => fail(error, current),
    );
    update(() {
      busy = true;
      report = emptyReport;
      status = 'Connecting';
    });
    try {
      // DemoTransport declares no link, so connect is always given one.
      final probe = await current.connect(link: choice.link);
      // Measurement packets carry no battery field: read it now; the report
      // shows this reading.
      try {
        await current.readBattery();
      } catch (_) {
        // The report then shows "Battery: not read"; measuring goes on.
      }
      if (!identical(client, current)) return;
      final firmware = probe.status == FisicaStatus.ok
          ? utf8
                .decode(
                  platform.decodeResponse(probe.payload)['payload']
                      as List<int>,
                  allowMalformed: true,
                )
                .replaceAll('\u0000', '')
                .trim()
          : '';
      update(() {
        connected = true;
        busy = false;
        status =
            'Connected (firmware ${firmware.isEmpty ? 'unknown' : firmware})';
      });
    } catch (error) {
      await fail(error, current);
    }
  }

  Future<void> measure() async {
    final current = client;
    if (current == null || !connected || busy) return;
    update(() {
      busy = true;
      report = emptyReport;
      status = 'Measuring for 5 seconds';
    });
    try {
      await current.start();
      await Future<void>.delayed(const Duration(seconds: 5));
      if (!identical(client, current)) return;
      await current.stop();
      // Weight and foot size come from every packet, in basic and full mode
      // alike.
      final result = current.report();
      if (!identical(client, current)) return;
      update(() {
        busy = false;
        status = result == null ? noResult : 'Measurement done';
        if (result != null) {
          String cm(double value) => '${value.toStringAsFixed(1)} cm';
          report = [
            'Weight: ${result.weight.toStringAsFixed(1)} kg',
            'Left foot: length ${cm(result.leftLength)}, width ${cm(result.leftWidth)}',
            'Right foot: length ${cm(result.rightLength)}, width ${cm(result.rightWidth)}',
            'Frames: ${result.frames.length}',
            'Battery: ${result.battery == null ? 'not read' : '${result.battery}%'}',
          ];
        }
      });
    } catch (error) {
      await fail(error, current);
    }
  }

  /// One clear line, and the client is always closed.
  Future<void> fail(Object error, FisicaClient failed) async {
    if (identical(client, failed)) await release('Error: ${describe(error)}');
  }

  /// Close the client (the Scale leaves measurement mode); resources are
  /// freed even when the Scale does not answer.
  Future<void> release(String message) async {
    final current = client;
    final listening = events;
    if (current == null) return;
    client = null;
    events = null;
    update(() {
      connected = false;
      busy = true;
      status = message;
    });
    try {
      await current.close();
    } catch (_) {
      // Closing frees the resources even when the Scale does not confirm.
    } finally {
      await listening?.cancel();
      update(() => busy = false);
    }
  }

  @override
  void dispose() {
    final current = client;
    final license = entitlement;
    client = null;
    unawaited(licenseChanges?.cancel());
    unawaited(events?.cancel());
    unawaited(
      (current?.close() ?? Future<void>.value())
          .catchError((Object _) {})
          .whenComplete(() => license?.close()),
    );
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final idle = client == null && !busy;
    return Scaffold(
      appBar: AppBar(title: const Text('Fisica Scale sample')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(version, style: Theme.of(context).textTheme.bodySmall),
            Text(license, style: Theme.of(context).textTheme.bodySmall),
            Text(status),
            Wrap(
              spacing: 8,
              children: [
                ElevatedButton(
                  onPressed: idle ? scanBle : null,
                  child: const Text('Scan for Scales'),
                ),
                if (Platform.isWindows || Platform.isMacOS)
                  ElevatedButton(
                    onPressed: idle ? listUsb : null,
                    child: const Text('List USB ports'),
                  ),
                ElevatedButton(
                  onPressed: idle ? chooseDemo : null,
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
                      selected: selected == d,
                      trailing: selected == d ? const Icon(Icons.check) : null,
                      onTap: idle ? () => choose(d) : null,
                    ),
                ],
              ),
            ),
            Wrap(
              spacing: 8,
              children: [
                ElevatedButton(
                  onPressed: selected != null && idle ? connect : null,
                  child: const Text('Connect'),
                ),
                ElevatedButton(
                  onPressed: connected && !busy ? measure : null,
                  child: const Text('Measure (5 s)'),
                ),
              ],
            ),
            for (final line in report) Text(line),
            ElevatedButton(
              onPressed: client != null && !(busy && connected)
                  ? () => release('Disconnected')
                  : null,
              child: const Text('Disconnect'),
            ),
          ],
        ),
      ),
    );
  }
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
