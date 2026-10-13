part of '../fisica_core.dart';

/// Application/platform BLE or USB adapter; no protocol code here.
abstract interface class FisicaTransport {
  Future<void> connect();
  Stream<Uint8List> get received;
  Stream<void> get disconnected;
  Future<void> send(Uint8List bytes);
  Future<void> close();
}

/// One isolate drives the shared core. Futures/Streams adapt native events only.
class FisicaClient {
  final FisicaSession session;
  final FisicaTransport transport;
  final _clock = Stopwatch();
  final _events = StreamController<FisicaSessionEvent>.broadcast();
  final _waiters = <int, Completer<FisicaSessionEvent>>{};
  StreamSubscription<Uint8List>? _input;
  StreamSubscription<void>? _lost;
  Timer? _timer;
  Future<void> _writes = Future.value();
  bool _closed = false;
  Future<void>? _closing;
  FisicaClient(FisicaCore core, this.transport) : session = FisicaSession(core);
  Stream<FisicaSessionEvent> get events => _events.stream;
  int get now => _clock.elapsedMilliseconds;
  void _failure(Object error, {bool sendFailure = false}) {
    if (_closed) return;
    session.action(5, value: sendFailure ? 1 : 0, nowMs: now);
    _events.addError(error);
    _drain();
    for (final waiter in _waiters.values) {
      waiter.completeError(error);
    }
    _waiters.clear();
  }

  void _drain() {
    for (final event in session.drain()) {
      if (event.kind == 1) {
        _writes = _writes
            .then((_) async {
              if (!_closed) await transport.send(event.payload);
            })
            .catchError((Object error) {
              _failure(error, sendFailure: true);
            });
      }
      if (event.kind == 3) _waiters.remove(event.operation)?.complete(event);
      _events.add(event);
    }
  }

  Future<FisicaSessionEvent> _action(int action, int value) {
    if (_closed) throw StateError('Client is closed');
    if (_waiters.containsKey(action) || (_waiters.isNotEmpty && action != 4))
      throw FisicaStateError(7, 'Another operation is pending');
    final waiter = Completer<FisicaSessionEvent>();
    _waiters[action] = waiter;
    try {
      session.action(action, value: value, nowMs: now);
      _drain();
    } catch (error, stack) {
      _waiters.remove(action);
      waiter.completeError(error, stack);
    }
    return waiter.future;
  }

  Future<FisicaSessionEvent> connect({int profile = 1}) async {
    if (_input != null) throw StateError('Already connected');
    await transport.connect();
    _clock.start();
    _input = transport.received.listen((data) {
      try {
        session.feed(data, nowMs: now);
        _drain();
      } catch (error) {
        _failure(error);
      }
    }, onError: (Object error) => _failure(error));
    _lost = transport.disconnected.listen(
      (_) => _failure(StateError('Link lost')),
    );
    _timer = Timer.periodic(const Duration(milliseconds: 20), (_) {
      try {
        session.tick(now);
        _drain();
      } catch (error) {
        _failure(error, sendFailure: true);
      }
    });
    return _action(
      1,
      profile,
    ); // Probe result may warn; core permits continuation.
  }

  Future<FisicaSessionEvent> _checked(
    Future<FisicaSessionEvent> pending,
  ) async {
    final event = await pending;
    if (event.status != 0) throw FisicaCommandError(event);
    return event;
  }

  Future<FisicaSessionEvent> start({Duration? duration}) =>
      _checked(_action(2, duration?.inMilliseconds ?? -1));
  Future<FisicaSessionEvent> stop() => _checked(_action(3, 0));
  Future<void> close() => _closing ??= _close();
  Future<void> _close() async {
    if (_closed) return;
    try {
      if (_input != null) await _checked(_action(4, 0));
    } finally {
      _closed = true;
      _timer?.cancel();
      await _input?.cancel();
      await _lost?.cancel();
      try {
        await transport.close();
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
