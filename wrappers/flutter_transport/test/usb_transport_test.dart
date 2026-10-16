import 'package:fisica_flutter/fisica_flutter.dart';
import 'package:flutter_libserialport/flutter_libserialport.dart';
import 'package:flutter_test/flutter_test.dart';

/// Records settings like libserialport's config, where -1 means "leave as is".
class FakeConfig implements SerialPortConfig {
  @override
  int baudRate = -1;
  @override
  int bits = -1;
  @override
  int parity = -1;
  @override
  int stopBits = -1;
  @override
  int dtr = -1;
  @override
  int rts = -1;
  int? flowControl;
  @override
  void setFlowControl(int value) => flowControl = value;
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

void main() {
  // Checked on a real Scale: nothing answers at 9600, and with DTR off its
  // ESP32 reboots (DTR and RTS off) or is held in reset (only RTS on).
  test('USB line settings are 115200 8N1 with DTR and RTS on', () {
    final config = FakeConfig();
    FisicaUsbTransport.configure(config);
    expect(config.baudRate, 115200);
    expect(config.bits, 8);
    expect(config.parity, SerialPortParity.none);
    expect(config.stopBits, 1);
    expect(config.flowControl, SerialPortFlowControl.none);
    expect(config.dtr, SerialPortDtr.on);
    expect(config.rts, SerialPortRts.on);
  });
}
