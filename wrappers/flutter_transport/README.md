# Fisica Flutter transports

Public Flutter adapters for the `fisica_core` binary package. Version 0.1.0.
Keep this package at `flutter_transport/` beside the extracted core package at
`flutter/`. Add `fisica_flutter` as a path dependency in the host app.

```dart
import 'package:fisica_flutter/fisica_flutter.dart';

// Grant Bluetooth permissions, scan and let the user select a Scale first.
final devices = await FisicaBleTransport.scan();
final transport = FisicaBleTransport(selectedDeviceId);
final client = FisicaClient(FisicaCore.platform(), transport);
await client.connect(link: FisicaLink.ble);
// await client.start(); collect frames; await client.stop(); client.report();
// Always await client.close(), including on errors.
```

- BLE: Android/iOS/macOS/Windows through pinned `universal_ble 0.21.1`.
- USB serial: Windows/macOS through `flutter_libserialport 0.6.0`, 9600 8N1.
  Select a port from `FisicaUsbTransport.ports()` and connect with `link: FisicaLink.usb`.
- Each adapter declares its link (`FisicaLinkTransport`), so the client rejects a
  mismatched `link` before anything is sent.
- Native binaries are supplied by the sibling core package. There is no customer
  build of private Fisica code. No license token or network activation is required.
- Permissions and choosing a device remain app responsibilities. The public
  sample prepares host settings and requests Android permissions.
- No mobile USB, Wi-Fi, Web or automatic reconnect. After a link failure call
  `client.connect()` again when the user reconnects: the same adapter links the Scale
  again (a failed connect can also be retried) and the client resumes the session.
  `close()` is final for both the adapter and the client.
- GATT write completion is not a device ACK. Framing, command state, timeouts and
  analysis remain in the common core. MTU failure falls back to 20-byte payloads.
- The default Scale BLE profile and continuous command are not yet verified with
  production firmware 2.0.1. No command bytes or firmware were changed.

See the provided sample `INSTALL.md` or `doc/partner-quickstart.md` for OS setup,
app integration and validation limits. Keep `THIRD_PARTY_NOTICES.md` with your
app distribution; the serial C dependency is LGPL-3.0-or-later.
