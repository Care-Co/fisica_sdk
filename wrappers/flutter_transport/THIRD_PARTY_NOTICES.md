# Flutter transport dependencies

The Fisica wrapper and private compiled core use Fisica Scale SDK License 2.0.
The independent dependencies below retain their own licenses. This file does
not replace their license texts or other redistribution obligations.

| Dependency | Version | License/source |
|---|---|---|
| universal_ble | **0.21.1, exact pin** | BSD-3-Clause, Copyright 2024 Navideck Labs OÜ; Copyright 2020 Woodemi Co,Ltd. https://pub.dev/packages/universal_ble/versions/0.21.1/license |
| flutter_libserialport | **0.6.0** | MIT, J-P Nurmi. https://pub.dev/packages/flutter_libserialport/versions/0.6.0/license |
| libserialport Dart wrapper | dependency of flutter_libserialport | MIT. https://github.com/jpnurmi/libserialport.dart |
| libserialport C library | bundled/built by flutter_libserialport | LGPL-3.0-or-later. https://sigrok.org/wiki/Libserialport ; https://github.com/sigrokproject/libserialport |

The BSD/MIT and LGPL/GPL license texts are included in `licenses/`. For the serial
C library, retain its notices and applicable LGPL/GPL license texts and provide
its corresponding source and replacement/relinking facilities as required by its
license when redistributing. Do not merge it into the private Fisica core.
Review the actual resolved native dependency version in the consuming app.

universal_ble major versions after the pinned BSD release use different terms.
Do not widen this dependency to a later major without reviewing those terms.
Windows ARM64 plugin execution and hardware I/O remain unverified.
