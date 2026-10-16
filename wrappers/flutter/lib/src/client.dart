part of '../fisica_core.dart';

/// Application/platform BLE or USB adapter; no protocol code here.
///
/// [connect] may be called again after the link was lost or failed; it links
/// the same Scale again. [close] is final.
abstract interface class FisicaTransport {
  Future<void> connect();
  Stream<Uint8List> get received;
  Stream<void> get disconnected;
  Future<void> send(Uint8List bytes);
  Future<void> close();
}

/// A transport that knows which Scale link it carries: [FisicaLink.ble] or
/// [FisicaLink.usb]. [FisicaClient.connect] uses it as the default link and
/// rejects a different one before anything is sent.
abstract interface class FisicaLinkTransport implements FisicaTransport {
  int get link;
}

/// One isolate drives the shared core. Futures/Streams adapt native events only.
///
/// After a link loss the core keeps the session (RECOVERING). Call [connect]
/// again, with the same or a replacement transport: the core resumes a
/// measurement it still wants, with its original deadline and the frames kept.
class FisicaClient {
  final FisicaSession session;
  FisicaTransport _transport;
  final _clock = Stopwatch();
  final _events = StreamController<FisicaSessionEvent>.broadcast();
  final _waiters = <int, Completer<FisicaSessionEvent>>{};
  StreamSubscription<Uint8List>? _input;
  StreamSubscription<void>? _lost;
  Timer? _timer;
  Future<void> _writes = Future.value();
  bool _closed = false, _linked = false, _connecting = false;
  // Bumped whenever a link starts or ends so late callbacks of an earlier link are ignored.
  int _generation = 0;
  Future<void>? _closing;

  /// Optional company/contract context. It only produces status warnings;
  /// nothing is recorded and nothing is gated.
  final FisicaEntitlement? entitlement;
  final _frames = ListQueue<DecodedMeasurement>();
  int _maxFrames = FisicaLimit.analysisFrames;
  int _droppedFrames = 0;
  int? _battery;
  FisicaClient(FisicaCore core, FisicaTransport transport, {this.entitlement})
    : session = FisicaSession(core),
      _transport = transport;
  Stream<FisicaSessionEvent> get events => _events.stream;

  /// Transport of the current or most recent link.
  FisicaTransport get transport => _transport;

  /// Frames of the current measurement. [start] clears them; a reconnect keeps
  /// them. Only the latest [maxFrames] frames are kept.
  List<DecodedMeasurement> get frames => List.unmodifiable(_frames);

  /// Oldest frames dropped beyond [maxFrames] since the last [start] or [clearFrames].
  int get droppedFrames => _droppedFrames;

  /// Battery level in percent from the last [readBattery], or null.
  /// Measurement packets carry no battery field.
  int? get battery => _battery;

  /// How many recent frames to keep; at most [FisicaLimit.analysisFrames], the
  /// most one analysis accepts. This also bounds memory in long sessions.
  int get maxFrames => _maxFrames;
  set maxFrames(int value) {
    RangeError.checkValueInInterval(
      value,
      1,
      FisicaLimit.analysisFrames,
      'maxFrames',
    );
    _maxFrames = value;
    _trimFrames();
  }

  void clearFrames() {
    _frames.clear();
    _droppedFrames = 0;
  }

  void _trimFrames() {
    while (_frames.length > _maxFrames) {
      _frames.removeFirst();
      _droppedFrames++;
    }
  }

  /// Analyze the collected frames in the shared core: weight and left/right
  /// foot width and length in nominal centimetres, or null without a stable
  /// measurement. [battery] is the last [readBattery] reading, or null.
  ({
    double weight,
    double leftWidth,
    double leftLength,
    double rightWidth,
    double rightLength,
    List<DecodedMeasurement> frames,
    int? battery,
  })?
  report() {
    if (_frames.isEmpty) return null;
    entitlement?.checkStatus();
    final result = session.core.analyzePackets(
      _frames.map((frame) => frame.rawData).toList(),
    );
    if (result == null) return null;
    return (
      weight: result[0],
      leftWidth: result[1],
      leftLength: result[2],
      rightWidth: result[3],
      rightLength: result[4],
      frames: frames,
      battery: _battery,
    );
  }

  int get now => _clock.elapsedMilliseconds;

  Future<void> _unlink() {
    _linked = false;
    _generation++;
    final input = _input, lost = _lost;
    _input = null;
    _lost = null;
    return Future.wait([
      if (input != null) input.cancel(),
      if (lost != null) lost.cancel(),
    ]);
  }

  void _failure(Object error, {bool sendFailure = false, int? generation}) {
    if (_closed || (generation != null && generation != _generation)) return;
    if (_linked) {
      unawaited(_unlink());
      session.action(
        FisicaSessionAction.lost,
        value: sendFailure ? 1 : 0,
        nowMs: now,
      );
    }
    _events.addError(error);
    _drain();
    for (final waiter in _waiters.values) {
      waiter.completeError(error);
    }
    _waiters.clear();
  }

  void _drain() {
    for (final event in session.drain()) {
      if (event.kind == FisicaSessionEventKind.transmit) {
        final generation = _generation;
        final link = _transport;
        _writes = _writes
            .then((_) async {
              if (!_closed && generation == _generation) {
                await link.send(event.payload);
              }
            })
            .catchError((Object error) {
              _failure(error, sendFailure: true, generation: generation);
            });
      }
      if (event.kind == FisicaSessionEventKind.result) {
        _waiters.remove(event.operation)?.complete(event);
      }
      if (event.kind == FisicaSessionEventKind.measurement) {
        _frames.add(session.core.decodePacket(event.payload));
        _trimFrames();
      }
      _events.add(event);
    }
  }

  Future<FisicaSessionEvent> _action(
    int action,
    int value, {
    void Function()? accepted,
  }) {
    if (_closed) throw StateError('Client is closed');
    if (_waiters.containsKey(action) ||
        (_waiters.isNotEmpty && action != FisicaSessionAction.disconnect)) {
      throw FisicaStateError(FisicaStatus.busy, 'Another operation is pending');
    }
    final waiter = Completer<FisicaSessionEvent>();
    _waiters[action] = waiter;
    try {
      session.action(action, value: value, nowMs: now);
      accepted?.call();
      _drain();
    } catch (error, stack) {
      _waiters.remove(action);
      waiter.completeError(error, stack);
    }
    return waiter.future;
  }

  static int _linkFor(FisicaTransport transport, int? requested) {
    final carried = transport is FisicaLinkTransport ? transport.link : null;
    final link = requested ?? carried ?? FisicaLink.ble;
    String name(int value) => value == FisicaLink.usb ? 'USB' : 'BLE';
    if (link != FisicaLink.ble && link != FisicaLink.usb) {
      throw FisicaArgumentError(
        FisicaStatus.invalidArgument,
        'Use FisicaLink.ble or FisicaLink.usb',
      );
    }
    if (carried != null && carried != link) {
      throw FisicaArgumentError(
        FisicaStatus.invalidArgument,
        'This transport carries ${name(carried)}, not ${name(link)}',
      );
    }
    return link;
  }

  /// Link the Scale and probe its firmware.
  ///
  /// [link] is [FisicaLink.ble] or [FisicaLink.usb]. By default it is the
  /// transport's own link ([FisicaLinkTransport]), otherwise BLE. A link that
  /// differs from the transport's is rejected before anything is sent.
  ///
  /// Also reconnects after a link loss: omit [transport] to connect the same
  /// one again, or pass a replacement (the previous one is closed).
  Future<FisicaSessionEvent> connect({
    int? link,
    @Deprecated('Use link: FisicaLink.ble or FisicaLink.usb') int? profile,
    FisicaTransport? transport,
  }) async {
    if (_closed) throw StateError('Client is closed');
    if (_linked || _connecting) throw StateError('Already connected');
    if (link != null && profile != null && link != profile) {
      throw FisicaArgumentError(
        FisicaStatus.invalidArgument,
        'Pass link only; profile is its deprecated name',
      );
    }
    final next = transport ?? _transport;
    final selected = _linkFor(next, link ?? profile);
    _connecting = true;
    try {
      entitlement?.checkStatus();
      if (!identical(next, _transport)) {
        final previous = _transport;
        _transport = next;
        try {
          await previous.close();
        } catch (_) {
          // The replaced link is already gone; nothing else depends on it.
        }
      }
      await next.connect();
      if (_closed) throw StateError('Client is closed');
      _clock.start();
      final generation = ++_generation;
      _input = next.received.listen((data) {
        try {
          for (
            var offset = 0;
            offset < data.length;
            offset += FisicaLimit.feedBytes
          ) {
            final end = data.length - offset > FisicaLimit.feedBytes
                ? offset + FisicaLimit.feedBytes
                : data.length;
            session.feed(Uint8List.sublistView(data, offset, end), nowMs: now);
            _drain();
          }
        } catch (error) {
          _failure(error, generation: generation);
        }
      }, onError: (Object error) => _failure(error, generation: generation));
      _lost = next.disconnected.listen(
        (_) => _failure(StateError('Link lost'), generation: generation),
      );
      _timer ??= Timer.periodic(const Duration(milliseconds: 20), (_) {
        try {
          session.tick(now);
          _drain();
        } catch (error) {
          _failure(error, sendFailure: true);
        }
      });
      _linked = true;
    } finally {
      _connecting = false;
    }
    // Probe result may warn; core permits continuation.
    return _action(FisicaSessionAction.connected, selected);
  }

  Future<FisicaSessionEvent> _checked(
    Future<FisicaSessionEvent> pending,
  ) async {
    final event = await pending;
    if (event.status != 0) throw FisicaCommandError(event);
    return event;
  }

  /// Start measuring; [duration] null measures until [stop]. Frames of the
  /// previous measurement are cleared once the core accepts the start.
  Future<FisicaSessionEvent> start({Duration? duration}) {
    entitlement?.checkStatus();
    return _checked(
      _action(
        FisicaSessionAction.start,
        duration?.inMilliseconds ?? -1,
        accepted: clearFrames,
      ),
    );
  }

  Future<FisicaSessionEvent> stop() =>
      _checked(_action(FisicaSessionAction.stop, 0));

  /// Ask the Scale for its battery level (GET_BATTERY_LEVEL), idle or during
  /// a measurement, without interrupting it. A refused or unanswered query
  /// fails only this call; the session carries on.
  Future<int> readBattery() async {
    if (_closed) throw StateError('Client is closed');
    if (_waiters.isNotEmpty) {
      throw FisicaStateError(FisicaStatus.busy, 'Another operation is pending');
    }
    final waiter = Completer<FisicaSessionEvent>();
    _waiters[FisicaSessionOperation.request] = waiter;
    try {
      session.command('GET_BATTERY_LEVEL', nowMs: now);
      _drain();
    } catch (error, stack) {
      _waiters.remove(FisicaSessionOperation.request);
      waiter.completeError(error, stack);
    }
    final event = await _checked(waiter.future);
    final data = session.core.decodeResponse(event.payload)['payload'] as Uint8List;
    final percent = data.length == 2 ? (data[0] << 8) | data[1] : -1;
    if (percent < 0 || percent > 100) throw FisicaCommandError(event);
    return _battery = percent;
  }

  /// Stop if needed, release the Scale's measurement mode and close the
  /// transport. Without a link (after a loss) it only frees resources.
  Future<void> close() => _closing ??= _close();
  Future<void> _close() async {
    if (_closed) return;
    try {
      if (_linked) await _checked(_action(FisicaSessionAction.disconnect, 0));
    } finally {
      _closed = true;
      _timer?.cancel();
      await _unlink();
      for (final waiter in _waiters.values) {
        waiter.completeError(
          FisicaStateError(FisicaStatus.cancelled, 'Client closed'),
        );
      }
      _waiters.clear();
      try {
        await _transport.close();
      } finally {
        session.close();
        await _events.close();
      }
    }
  }
}

class FisicaCommandError extends FisicaStateError {
  final FisicaSessionEvent event;
  int get deviceCode => event.deviceError;
  FisicaCommandError(this.event)
    : super(
        event.status,
        'Command rejected or unconfirmed; device code ${event.deviceError}',
      );
}
