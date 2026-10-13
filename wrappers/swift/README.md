# Fisica Scale Swift wrapper

Use the generated Swift package ZIP containing `Package.swift`, public Swift sources
and `CFisica.xcframework`. macOS 11+ and iOS 13+; desktop universal, iOS arm64, simulator
arm64/x86_64. No private source is compiled in the consuming app. Link libc++.

`FisicaCore()` checks ABI 1.3 and the common offline version policy. Its `decode`,
`analyze`, `command` and `FisicaSession` APIs call the same C core as Python/Dart.
Call sessions on one executor with nondecreasing monotonic milliseconds. Drain
TRANSMIT (1) to the transport, feed bytes, tick regularly, handle RESULT (3) status
and deviceError, and decode MEASUREMENT (4) payloads. Wait for CLOSE result before
closing IO. Freeing the handle alone does not stop the device.

`BLETransport` scans/selects a peripheral and manages notification setup and chunked
writes. Supply the confirmed write/notify UUIDs; it does not guess a new Scale's
profile or embed protocol rules. Wire `onReady` to CONNECTED action 1/profile 1,
`onBytes` to feed, `onLost` to LOST action 5, and TRANSMIT to send. Host selects the
Scale, runs ticks and displays firmware/deprecation/command warnings. All callbacks
use the configured serial queue (default main). Add NSBluetoothAlwaysUsageDescription
to the application's Info.plist and enable the macOS Bluetooth entitlement when sandboxed.

Physical iOS/BLE execution remains required. Legacy BLE continuous mode needs firmware
validation; the protocol document describes continuous mode as USB-only.
