import 'dart:async';
import 'dart:typed_data';
import 'package:fisica_core/fisica_core.dart';
import 'package:universal_ble/universal_ble.dart';

/// BLE bytes only. Protocol framing, commands and ACKs remain in the core.
/// Grant OS Bluetooth permissions before scanning or connecting.
///
/// After a link loss or a failed connect, [connect] links the same Scale again;
/// called while linked it drops that link first. [close] is final.
class FisicaBleTransport implements FisicaLinkTransport {
  final String deviceId;
  final FisicaBleProfile profile;
  final Duration timeout;
  final _received = StreamController<Uint8List>.broadcast();
  final _disconnected = StreamController<void>.broadcast();
  StreamSubscription<bool>? _connection;
  StreamSubscription<Uint8List>? _notifications;
  String? _writeService, _notifyService;
  bool _withResponse = true, _ready = false, _closed = false, _linked = false;
  bool _lostSent = false, _connecting = false;
  Future<void> _writes = Future.value();
  int _mtu = 23;
  int get mtu => _mtu;

  FisicaBleTransport(
    this.deviceId, {
    this.profile = FisicaBleProfile.scale,
    this.timeout = const Duration(seconds: 15),
  });

  /// One bounded scan. The caller selects a device; no automatic connection.
  static Future<List<BleDevice>> scan({
    Duration duration = const Duration(seconds: 5),
  }) async {
    await _requireBluetooth();
    final devices = <String, BleDevice>{};
    final subscription = UniversalBle.scanStream.listen((device) {
      if (device.name?.contains(FisicaBleProfile.nameContains) == true) {
        devices[device.deviceId] = device;
      }
    });
    try {
      await UniversalBle.startScan();
      await Future<void>.delayed(duration);
      return devices.values.toList();
    } finally {
      await subscription.cancel();
      await UniversalBle.stopScan();
    }
  }

  static Future<void> _requireBluetooth() async {
    final state = await UniversalBle.getBluetoothAvailabilityState();
    if (state != AvailabilityState.poweredOn) {
      throw StateError('Bluetooth is unavailable: ${state.name}');
    }
  }

  @override
  int get link => FisicaLink.ble;
  @override
  Stream<Uint8List> get received => _received.stream;
  @override
  Stream<void> get disconnected => _disconnected.stream;

  void _lost() {
    _ready = false;
    if (!_closed && !_lostSent) {
      _lostSent = true;
      _disconnected.add(null);
    }
  }

  /// Drop the subscriptions of the previous link; [disconnect] also ends it.
  Future<void> _release({bool disconnect = false}) async {
    _ready = false;
    final notifications = _notifications, connection = _connection;
    _notifications = null;
    _connection = null;
    await notifications?.cancel();
    await connection?.cancel();
    if (disconnect && connection != null) {
      await UniversalBle.disconnect(deviceId, timeout: timeout);
    }
  }

  @override
  Future<void> connect() async {
    if (_closed) throw StateError('Transport is closed');
    if (_connecting) throw StateError('Transport is already connecting');
    _connecting = true;
    try {
      await _connect();
    } finally {
      _connecting = false;
    }
  }

  Future<void> _connect() async {
    await _release(disconnect: _ready);
    _lostSent = false;
    _linked = false;
    _mtu = 23;
    await _requireBluetooth();
    _connection = UniversalBle.connectionStream(deviceId).listen(
      (connected) {
        if (!connected && _linked) _lost();
        _linked = connected;
      },
      onError: (Object error) {
        _received.addError(error);
        _lost();
      },
    );
    try {
      await UniversalBle.connect(deviceId, timeout: timeout);
      _linked = true;
      try {
        _mtu = await UniversalBle.requestMtu(deviceId, 517, timeout: timeout);
        if (_mtu < 23) _mtu = 23;
      } catch (_) {
        _mtu = 23; // MTU requests are optional; use the safe ATT payload size.
      }
      final services = await UniversalBle.discoverServices(
        deviceId,
        timeout: timeout,
      );
      BleCharacteristic? write, notify;
      for (final service in services) {
        for (final characteristic in service.characteristics) {
          if (BleUuidParser.string(characteristic.uuid) ==
              BleUuidParser.string(profile.write)) {
            write = characteristic;
            _writeService = service.uuid;
          }
          if (BleUuidParser.string(characteristic.uuid) ==
              BleUuidParser.string(profile.notify)) {
            notify = characteristic;
            _notifyService = service.uuid;
          }
        }
      }
      if (write == null || notify == null) {
        throw StateError(
          'Scale write/notify UUIDs not found; check the BLE profile',
        );
      }
      _withResponse = write.properties.contains(CharacteristicProperty.write);
      if (!_withResponse &&
          !write.properties.contains(
            CharacteristicProperty.writeWithoutResponse,
          )) {
        throw StateError('Scale characteristic is not writable');
      }
      _notifications =
          UniversalBle.characteristicValueStream(
            deviceId,
            profile.notify,
          ).listen(
            (bytes) => _received.add(Uint8List.fromList(bytes)),
            onError: (Object error) {
              _received.addError(error);
              _lost();
            },
          );
      if (notify.properties.contains(CharacteristicProperty.notify)) {
        await UniversalBle.subscribeNotifications(
          deviceId,
          _notifyService!,
          profile.notify,
          timeout: timeout,
        );
      } else if (notify.properties.contains(CharacteristicProperty.indicate)) {
        await UniversalBle.subscribeIndications(
          deviceId,
          _notifyService!,
          profile.notify,
          timeout: timeout,
        );
      } else {
        throw StateError('Scale characteristic does not support notifications');
      }
      if (!_linked || _closed || _lostSent)
        throw StateError('BLE link lost while preparing');
      _ready = true;
    } catch (error, stack) {
      try {
        await _release(disconnect: true);
      } finally {
        Error.throwWithStackTrace(error, stack);
      }
    }
  }

  @override
  Future<void> send(Uint8List bytes) {
    final copy = Uint8List.fromList(bytes);
    final pending = _writes.then((_) async {
      if (!_ready || _closed) throw StateError('BLE transport is not ready');
      final size = (_mtu - 3).clamp(1, 512);
      for (var offset = 0; offset < copy.length; offset += size) {
        if (!_ready || _closed) throw StateError('BLE link lost during write');
        final end = (offset + size).clamp(0, copy.length);
        await UniversalBle.write(
          deviceId,
          _writeService!,
          profile.write,
          Uint8List.sublistView(copy, offset, end),
          withoutResponse: !_withResponse,
          timeout: timeout,
        );
      }
    });
    _writes = pending.catchError((Object _) {});
    return pending; // GATT completion is never converted to a Scale command ACK.
  }

  @override
  Future<void> close() async {
    if (_closed) return;
    _closed = true;
    try {
      await _release(disconnect: true);
    } finally {
      await _received.close();
      await _disconnected.close();
    }
  }
}
