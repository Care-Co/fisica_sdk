import 'dart:async';
import 'dart:io';
import 'dart:typed_data';
import 'package:fisica_core/fisica_core.dart';
import 'package:flutter_libserialport/flutter_libserialport.dart';

/// USB virtual serial port on Windows/macOS, matching Python's 115200 8N1
/// with DTR and RTS asserted (see [configure]). Mobile USB is deliberately
/// unsupported.
///
/// After a link loss or a failed connect, [connect] opens the port again;
/// called while open it closes the port first. [close] is final.
class FisicaUsbTransport implements FisicaLinkTransport {
  final String portName;
  final _received = StreamController<Uint8List>.broadcast();
  final _disconnected = StreamController<void>.broadcast();
  SerialPort? _port;
  SerialPortReader? _reader;
  StreamSubscription<Uint8List>? _input;
  bool _closed = false, _lostSent = false, _connecting = false;
  FisicaUsbTransport(this.portName);

  static void _requireDesktop() {
    if (!Platform.isWindows && !Platform.isMacOS) {
      throw UnsupportedError(
        'Scale USB is supported on Windows and macOS only',
      );
    }
  }

  /// Line settings the Scale needs: 115200 8N1, no flow control, DTR and RTS
  /// on. The Scale's ESP32 is wired for auto-reset: with DTR off it reboots
  /// (DTR and RTS both off) or is held in reset (only RTS on), so DTR and RTS
  /// are set explicitly instead of being left to the OS.
  static void configure(SerialPortConfig config) {
    config.baudRate = 115200;
    config.bits = 8;
    config.parity = SerialPortParity.none;
    config.stopBits = 1;
    // A new config leaves DTR and RTS as they are; no flow control keeps it so.
    config.setFlowControl(SerialPortFlowControl.none);
    config.dtr = SerialPortDtr.on;
    config.rts = SerialPortRts.on;
  }

  static List<String> ports() {
    _requireDesktop();
    return SerialPort.availablePorts;
  }

  @override
  int get link => FisicaLink.usb;
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

  /// Close the port of the previous link, if any.
  Future<void> _release() async {
    final input = _input, reader = _reader, port = _port;
    _input = null;
    _reader = null;
    _port = null;
    await input?.cancel();
    reader?.close();
    try {
      if (port?.isOpen == true) port!.close();
    } finally {
      port?.dispose();
    }
  }

  @override
  Future<void> connect() async {
    _requireDesktop();
    if (_closed) throw StateError('Transport is closed');
    if (_connecting) throw StateError('Transport is already connecting');
    _connecting = true;
    try {
      await _release();
      _lostSent = false;
      await _open();
    } finally {
      _connecting = false;
    }
  }

  Future<void> _open() async {
    try {
      final port = _port = SerialPort(portName);
      if (!port.openReadWrite())
        throw StateError('Cannot open $portName: ${SerialPort.lastError}');
      final config = SerialPortConfig();
      try {
        configure(config);
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
      await _release();
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
    try {
      await _release();
    } finally {
      await _received.close();
      await _disconnected.close();
    }
  }
}
