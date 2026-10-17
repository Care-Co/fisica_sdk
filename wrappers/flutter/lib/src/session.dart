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

/// The session functions of one loaded core, looked up once.
final class _SessionApi {
  _SessionApi(DynamicLibrary library)
    : create = library.lookupFunction<_CreateNative, _CreateDart>(
        'fisica_core_session_create',
      ),
      destroyPointer = library.lookup<NativeFunction<_DestroyNative>>(
        'fisica_core_session_destroy',
      ),
      action = library.lookupFunction<_ActionNative, _ActionDart>(
        'fisica_core_session_action',
      ),
      tick = library.lookupFunction<_TickNative, _TickDart>(
        'fisica_core_session_tick',
      ),
      feed = library.lookupFunction<_FeedNative, _FeedDart>(
        'fisica_core_session_feed',
      ),
      command = library
          .lookupFunction<_SessionCommandNative, _SessionCommandDart>(
            'fisica_core_session_command',
          ),
      poll = library.lookupFunction<_PollNative, _PollDart>(
        'fisica_core_session_poll',
      ),
      license = library.lookupFunction<_LicenseNative, _LicenseDart>(
        'fisica_core_session_license',
      ),
      analyze = library
          .lookupFunction<_SessionAnalyzeNative, _SessionAnalyzeDart>(
            'fisica_core_session_analyze',
          );
  final _CreateDart create;
  final Pointer<NativeFunction<_DestroyNative>> destroyPointer;
  late final _DestroyDart destroy = destroyPointer.asFunction<_DestroyDart>();
  // Destroys a session the app dropped without close().
  late final NativeFinalizer finalizer = NativeFinalizer(destroyPointer.cast());
  final _ActionDart action;
  final _TickDart tick;
  final _FeedDart feed;
  final _SessionCommandDart command;
  final _PollDart poll;
  final _LicenseDart license;
  final _SessionAnalyzeDart analyze;
}

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

/// Sans-IO native session, owned by one host/isolate. Close it explicitly; a
/// session dropped without [close] is freed once it is garbage collected.
/// Supply elapsed monotonic milliseconds, tick regularly and drain events.
/// The host sends TRANSMIT bytes, feeds received bytes and handles RECONNECT.
///
/// Until [license] copies a full-mode license into it, the session is in
/// basic mode: it hands out at most one measurement frame per
/// [FisicaLimit.basicFrameIntervalMs], without the raw grid and packet.
/// Every packet still counts toward [analyze].
class FisicaSession implements Finalizable {
  final FisicaCore core;
  final _SessionApi _api;
  // One native block per session, reused by every call: poll metadata,
  // analysis result, poll payload (EVENT_BYTES holds any event) and feed input.
  static const _meta = 0, _result = 32, _payload = 72;
  static const _input = _payload + FisicaLimit.eventBytes;
  static const _scratchBytes = _input + FisicaLimit.feedBytes;
  // Frees the scratch block of a session dropped without close().
  static final _release = NativeFinalizer(calloc.nativeFree);
  final Pointer<Uint8> _scratch;
  Pointer<Void> _handle = nullptr;
  int state = 0;
  FisicaSession(this.core)
    : _api = core._sessions,
      _scratch = calloc<Uint8>(_scratchBytes) {
    _release.attach(this, _scratch.cast(), detach: this);
    final output = _scratch.cast<Pointer<Void>>();
    core._check(_api.create(output));
    _handle = output.value;
    // The hint is what a session can hold: every packet of one analysis.
    _api.finalizer.attach(
      this,
      _handle,
      detach: this,
      externalSize: FisicaLimit.analysisFrames * 645,
    );
  }

  void close() {
    if (_handle == nullptr) return;
    _api.finalizer.detach(this);
    _api.destroy(_handle);
    _handle = nullptr;
    _release.detach(this);
    calloc.free(_scratch);
  }

  void _open() {
    if (_handle == nullptr) throw FisicaStateError(6, 'Session is closed');
  }

  void _validateTime(int nowMs) {
    _open();
    if (nowMs < 0)
      throw FisicaArgumentError(1, 'Expected monotonic milliseconds');
  }

  /// 1 connected (value [FisicaLink.ble] or [FisicaLink.usb]), 2 start (duration ms, -1 unlimited),
  /// 3 stop, 4 disconnect, 5 lost (value 1 send failure, 0 link loss).
  void action(int action, {int value = 0, required int nowMs}) {
    _validateTime(nowMs);
    if (action < 1 || action > 5)
      throw FisicaArgumentError(1, 'Invalid action');
    core._check(_api.action(_handle, action, value, nowMs));
  }

  void tick(int nowMs) {
    _validateTime(nowMs);
    core._check(_api.tick(_handle, nowMs));
  }

  void feed(Uint8List bytes, {required int nowMs}) {
    _validateTime(nowMs);
    if (bytes.length > FisicaLimit.feedBytes) {
      throw FisicaArgumentError(1, 'Feed is too large');
    }
    final input = _scratch + _input;
    input.asTypedList(bytes.length).setAll(0, bytes);
    core._check(_api.feed(_handle, input, bytes.length, nowMs));
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
    try {
      final input = _scratch + _input;
      input.asTypedList(bytes.length).setAll(0, bytes);
      core._check(
        _api.command(_handle, nativeName, value, input, bytes.length, nowMs),
      );
    } finally {
      calloc.free(nativeName);
    }
  }

  FisicaSessionEvent? poll() {
    _open();
    final meta = (_scratch + _meta).cast<Uint32>();
    final payload = _scratch + _payload;
    final status = _api.poll(_handle, meta, 8, payload, FisicaLimit.eventBytes);
    if (status == FisicaStatus.noResult) return null;
    core._check(status);
    state = meta[1];
    return FisicaSessionEvent(
      meta.asTypedList(8).toList(),
      Uint8List.fromList(payload.asTypedList(meta[7])),
    );
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
    core._check(_api.license(_handle, context, nowS, nowMs));
  }

  /// Weight and foot size from every measurement packet since the last
  /// START, in any mode: `[weight_kg, left_width_cm, left_length_cm,
  /// right_width_cm, right_length_cm]`, or null without a stable measurement.
  List<double>? analyze() {
    _open();
    final output = (_scratch + _result).cast<Double>();
    final status = _api.analyze(_handle, output, 5);
    if (status == FisicaStatus.noResult) return null;
    core._check(status);
    return output.asTypedList(5).toList();
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
