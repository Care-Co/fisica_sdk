part of '../fisica_core.dart';

typedef _CreateNative = Int32 Function(Pointer<Pointer<Void>>);
typedef _CreateDart = int Function(Pointer<Pointer<Void>>);
typedef _DestroyNative = Void Function(Pointer<Void>);
typedef _DestroyDart = void Function(Pointer<Void>);
typedef _ActionNative = Int32 Function(Pointer<Void>, Uint32, Int64, Uint64);
typedef _ActionDart = int Function(Pointer<Void>, int, int, int);
typedef _TickNative = Int32 Function(Pointer<Void>, Uint64);
typedef _TickDart = int Function(Pointer<Void>, int);
typedef _FeedNative =
    Int32 Function(Pointer<Void>, Pointer<Uint8>, Uint32, Uint64);
typedef _FeedDart = int Function(Pointer<Void>, Pointer<Uint8>, int, int);
typedef _SessionCommandNative =
    Int32 Function(
      Pointer<Void>,
      Pointer<Utf8>,
      Double,
      Pointer<Uint8>,
      Uint32,
      Uint64,
    );
typedef _SessionCommandDart =
    int Function(
      Pointer<Void>,
      Pointer<Utf8>,
      double,
      Pointer<Uint8>,
      int,
      int,
    );
typedef _PollNative =
    Int32 Function(
      Pointer<Void>,
      Pointer<Uint32>,
      Uint32,
      Pointer<Uint8>,
      Uint32,
    );
typedef _PollDart =
    int Function(Pointer<Void>, Pointer<Uint32>, int, Pointer<Uint8>, int);
typedef _LicenseNative = Int32 Function(Pointer<Void>, Pointer<Void>, Uint64, Uint64);
typedef _LicenseDart = int Function(Pointer<Void>, Pointer<Void>, int, int);
typedef _SessionAnalyzeNative = Int32 Function(Pointer<Void>, Pointer<Double>, Uint32);
typedef _SessionAnalyzeDart = int Function(Pointer<Void>, Pointer<Double>, int);

/// Codes and payload match fisica_core.h. No platform/device policy here.
/// A measurement event's payload is read with [FisicaCore.frameFromEvent].
class FisicaSessionEvent {
  final int kind, state, status, deviceError, operation, header, type;
  final Uint8List payload;
  FisicaSessionEvent(List<int> meta, this.payload)
    : kind = meta[0],
      state = meta[1],
      status = meta[2],
      deviceError = meta[3],
      operation = meta[4],
      header = meta[5],
      type = meta[6];
}

/// Sans-IO native session, owned by one host/isolate. Always close explicitly.
/// Supply elapsed monotonic milliseconds, tick regularly and drain events.
/// The host sends TRANSMIT bytes, feeds received bytes and handles RECONNECT.
///
/// Until [license] copies a full-mode license into it, the session is in
/// basic mode: it hands out at most one measurement frame per
/// [FisicaLimit.basicFrameIntervalMs], without the raw grid and packet.
/// Every packet still counts toward [analyze].
class FisicaSession {
  final FisicaCore core;
  Pointer<Void> _handle = nullptr;
  int state = 0;
  FisicaSession(this.core) {
    final output = calloc<Pointer<Void>>();
    try {
      core._check(
        core._library.lookupFunction<_CreateNative, _CreateDart>(
          'fisica_core_session_create',
        )(output),
      );
      _handle = output.value;
    } finally {
      calloc.free(output);
    }
  }

  void close() {
    if (_handle == nullptr) return;
    core._library.lookupFunction<_DestroyNative, _DestroyDart>(
      'fisica_core_session_destroy',
    )(_handle);
    _handle = nullptr;
  }

  void _validateTime(int nowMs) {
    if (_handle == nullptr) throw FisicaStateError(6, 'Session is closed');
    if (nowMs < 0)
      throw FisicaArgumentError(1, 'Expected monotonic milliseconds');
  }

  /// 1 connected (value [FisicaLink.ble] or [FisicaLink.usb]), 2 start (duration ms, -1 unlimited),
  /// 3 stop, 4 disconnect, 5 lost (value 1 send failure, 0 link loss).
  void action(int action, {int value = 0, required int nowMs}) {
    _validateTime(nowMs);
    if (action < 1 || action > 5)
      throw FisicaArgumentError(1, 'Invalid action');
    core._check(
      core._library.lookupFunction<_ActionNative, _ActionDart>(
        'fisica_core_session_action',
      )(_handle, action, value, nowMs),
    );
  }

  void tick(int nowMs) {
    _validateTime(nowMs);
    core._check(
      core._library.lookupFunction<_TickNative, _TickDart>(
        'fisica_core_session_tick',
      )(_handle, nowMs),
    );
  }

  void feed(Uint8List bytes, {required int nowMs}) {
    _validateTime(nowMs);
    if (bytes.length > FisicaLimit.feedBytes) {
      throw FisicaArgumentError(1, 'Feed is too large');
    }
    final input = calloc<Uint8>(bytes.isEmpty ? 1 : bytes.length);
    try {
      input.asTypedList(bytes.length).setAll(0, bytes);
      core._check(
        core._library.lookupFunction<_FeedNative, _FeedDart>(
          'fisica_core_session_feed',
        )(_handle, input, bytes.length, nowMs),
      );
    } finally {
      calloc.free(input);
    }
  }

  void command(
    String name, {
    double value = 0,
    Uint8List? payload,
    required int nowMs,
  }) {
    _validateTime(nowMs);
    if (name.contains('\u0000'))
      throw FisicaArgumentError(1, 'Command contains NUL');
    final bytes = payload ?? Uint8List(0);
    if (bytes.length > 4096)
      throw FisicaArgumentError(1, 'Payload is too large');
    final nativeName = name.toNativeUtf8(allocator: calloc);
    final input = calloc<Uint8>(bytes.isEmpty ? 1 : bytes.length);
    try {
      input.asTypedList(bytes.length).setAll(0, bytes);
      core._check(
        core._library
            .lookupFunction<_SessionCommandNative, _SessionCommandDart>(
              'fisica_core_session_command',
            )(_handle, nativeName, value, input, bytes.length, nowMs),
      );
    } finally {
      calloc.free(nativeName);
      calloc.free(input);
    }
  }

  FisicaSessionEvent? poll() {
    if (_handle == nullptr) throw FisicaStateError(6, 'Session is closed');
    final meta = calloc<Uint32>(8);
    final payload = calloc<Uint8>(FisicaLimit.eventBytes);
    try {
      final status = core._library.lookupFunction<_PollNative, _PollDart>(
        'fisica_core_session_poll',
      )(_handle, meta, 8, payload, FisicaLimit.eventBytes);
      if (status == 3) return null;
      core._check(status);
      state = meta[1];
      return FisicaSessionEvent(
        meta.asTypedList(8).toList(),
        Uint8List.fromList(payload.asTypedList(meta[7])),
      );
    } finally {
      calloc.free(meta);
      calloc.free(payload);
    }
  }

  /// Copy the license decision of [entitlement] into this session; null
  /// returns it to basic mode. A full-mode decision lasts until the key or
  /// contract would end, on the session's clock. Call it again after
  /// importing, activating or renewing a key ([FisicaClient] does this).
  void license(FisicaEntitlement? entitlement, {required int nowMs}) {
    _validateTime(nowMs);
    final context = entitlement?._open ?? nullptr;
    final nowS =
        entitlement?._now() ?? DateTime.now().millisecondsSinceEpoch ~/ 1000;
    core._check(
      core._library.lookupFunction<_LicenseNative, _LicenseDart>(
        'fisica_core_session_license',
      )(_handle, context, nowS, nowMs),
    );
  }

  /// Weight and foot size from every measurement packet since the last
  /// START, in any mode: `[weight_kg, left_width_cm, left_length_cm,
  /// right_width_cm, right_length_cm]`, or null without a stable measurement.
  List<double>? analyze() {
    if (_handle == nullptr) throw FisicaStateError(6, 'Session is closed');
    final output = calloc<Double>(5);
    try {
      final status = core._library
          .lookupFunction<_SessionAnalyzeNative, _SessionAnalyzeDart>(
            'fisica_core_session_analyze',
          )(_handle, output, 5);
      if (status == FisicaStatus.noResult) return null;
      core._check(status);
      return output.asTypedList(5).toList();
    } finally {
      calloc.free(output);
    }
  }

  List<FisicaSessionEvent> drain() {
    final events = <FisicaSessionEvent>[];
    while (true) {
      final event = poll();
      if (event == null) return events;
      events.add(event);
    }
  }
}
