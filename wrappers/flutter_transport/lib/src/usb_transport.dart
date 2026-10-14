import 'dart:async';
import 'dart:io';
import 'dart:typed_data';
import 'package:fisica_core/fisica_core.dart';
import 'package:flutter_libserialport/flutter_libserialport.dart';

/// USB virtual serial port on Windows/macOS, matching Python's 9600 8N1.
/// Mobile USB is deliberately unsupported.
class FisicaUsbTransport implements FisicaTransport {
  final String portName;
  final _received = StreamController<Uint8List>.broadcast();
  final _disconnected = StreamController<void>.broadcast();
  SerialPort? _port;
  SerialPortReader? _reader;
  StreamSubscription<Uint8List>? _input;
  bool _closed = false, _lostSent = false;
  FisicaUsbTransport(this.portName);

  static void _requireDesktop() {
    if (!Platform.isWindows && !Platform.isMacOS) {
      throw UnsupportedError(
        'Scale USB is supported on Windows and macOS only',
      );
    }
  }

  static List<String> ports() {
    _requireDesktop();
    return SerialPort.availablePorts;
  }

  @override
  Stream<Uint8List> get received => _received.stream;
  @override
  Stream<void> get disconnected => _disconnected.stream;

  void _lost(Object error) {
    if (_closed || _lostSent) return;
    _lostSent = true;
    _received.addError(error);
    _disconnected.add(null);
  }

  @override
  Future<void> connect() async {
    _requireDesktop();
    if (_closed || _port != null) throw StateError('Transport already used');
    try {
      final port = _port = SerialPort(portName);
      if (!port.openReadWrite())
        throw StateError('Cannot open $portName: ${SerialPort.lastError}');
      final config = SerialPortConfig();
      try {
        config.baudRate = 9600;
        config.bits = 8;
        config.parity = SerialPortParity.none;
        config.stopBits = 1;
        config.setFlowControl(SerialPortFlowControl.none);
        port.config = config;
      } finally {
        config.dispose();
      }
      _reader = SerialPortReader(port);
      _input = _reader!.stream.listen(
        (bytes) => _received.add(Uint8List.fromList(bytes)),
        onError: (Object error) => _lost(error),
        onDone: () {
          if (!_closed) _lost(StateError('USB reader closed'));
        },
      );
    } catch (_) {
      await close();
      rethrow;
    }
  }

  @override
  Future<void> send(Uint8List bytes) async {
    final port = _port;
    if (_closed || _lostSent || port == null || !port.isOpen) {
      throw StateError('USB transport is not ready');
    }
    var offset = 0;
    final deadline = Stopwatch()..start();
    while (offset < bytes.length) {
      final remaining = 1000 - deadline.elapsedMilliseconds;
      if (remaining <= 0) throw TimeoutException('USB write timed out');
      final count = port.write(
        Uint8List.sublistView(bytes, offset),
        timeout: remaining,
      );
      if (count <= 0)
        throw StateError('USB write failed: ${SerialPort.lastError}');
      offset += count;
    }
  }

  @override
  Future<void> close() async {
    if (_closed) return;
    _closed = true;
    await _input?.cancel();
    _reader?.close();
    final port = _port;
    _port = null;
    try {
      if (port?.isOpen == true) port!.close();
    } finally {
      port?.dispose();
      await _received.close();
      await _disconnected.close();
    }
  }
}
