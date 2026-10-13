part of '../fisica_core.dart';

/// Three independent facts. Nothing here enables or disables SDK features.
/// Codes follow [FisicaVerification], [FisicaContract] and the other generated classes.
/// Times are Unix seconds, 0 when not set.
class EntitlementStatus {
  /// Granted with genuine hardware; it has no date expiry.
  final int baseLicense;

  /// How recently the server confirmed this information (30 days at most).
  final int verification;

  /// State of the verified company's contract, by server-signed dates only.
  final int contract;
  final int contractKind;
  final String company, contractReference;
  final List<String> grants;
  final int verifiedAt, refreshBy, contractStart, contractEnd, revokedAt;
  final int lastImport, keyEnvironment;

  /// Bit set of [FisicaEntitlementWarning].
  final int warnings;
  EntitlementStatus._(List<int> v, this.company, this.contractReference, this.grants)
    : baseLicense = v[0],
      verification = v[1],
      contract = v[2],
      contractKind = v[3],
      verifiedAt = v[4],
      refreshBy = v[5],
      contractStart = v[6],
      contractEnd = v[7],
      revokedAt = v[8],
      lastImport = v[9],
      keyEnvironment = v[10],
      warnings = v[11];
}

typedef _EntitlementBytesNative =
    Int32 Function(Pointer<Void>, Pointer<Uint8>, Uint32, Pointer<Uint32>);
typedef _EntitlementBytesDart =
    int Function(Pointer<Void>, Pointer<Uint8>, int, Pointer<Uint32>);

/// One SDK instance's verified company/contract context, owned by one isolate.
/// Import a server-signed token or file; status and warnings never block
/// connection, measurement or analysis. Always close explicitly.
class FisicaEntitlement {
  final FisicaCore core;
  final int Function() _clock;
  final String? _endpoint;
  final String? _storageDirectory;

  /// False keeps a configured endpoint unused.
  final bool online;
  Pointer<Void> _handle = nullptr;
  int _warned = 0;
  // One exchange with the server at a time.
  Future<String?> _exchange = Future.value(null);
  DateTime _nextSync = DateTime.fromMillisecondsSinceEpoch(0);

  /// Called with newly raised warning bits so the app can show or log them.
  void Function(int warnings, EntitlementStatus status)? onWarning;

  /// Last storage or network problem, or null. Never an entitlement state.
  String? lastSyncError;

  /// [endpoint]: server base URL for online confirmation; without it no network
  /// is used. [storageDirectory]: directory for the stored token (for example
  /// the app's support directory); null keeps it in memory only. SDK use is not
  /// tracked: nothing but the signed token is stored.
  FisicaEntitlement(
    this.core, {
    int Function()? clock,
    String? endpoint,
    String? storageDirectory,
    this.online = true,
  }) : _clock = clock ?? (() => DateTime.now().millisecondsSinceEpoch ~/ 1000),
       _endpoint = endpoint?.replaceAll(RegExp(r'/+$'), ''),
       _storageDirectory = storageDirectory {
    final output = calloc<Pointer<Void>>();
    try {
      core._check(
        core._library.lookupFunction<
          Int32 Function(Pointer<Pointer<Void>>),
          int Function(Pointer<Pointer<Void>>)
        >('fisica_core_entitlement_create')(output),
      );
      _handle = output.value;
    } finally {
      calloc.free(output);
    }
  }

  void close() {
    if (_handle == nullptr) return;
    core._library.lookupFunction<
      Void Function(Pointer<Void>),
      void Function(Pointer<Void>)
    >('fisica_core_entitlement_destroy')(_handle);
    _handle = nullptr;
  }

  Pointer<Void> get _open {
    if (_handle == nullptr) throw FisicaStateError(6, 'Entitlement is closed');
    return _handle;
  }

  /// Returns a [FisicaEntitlementOutcome]. A rejected or older token leaves
  /// verified information untouched.
  int importToken(String token) {
    final bytes = utf8.encode(token);
    final input = calloc<Uint8>(bytes.isEmpty ? 1 : bytes.length);
    final outcome = calloc<Uint32>();
    try {
      input.asTypedList(bytes.length).setAll(0, bytes);
      core._check(
        core._library.lookupFunction<
          Int32 Function(Pointer<Void>, Pointer<Uint8>, Uint32, Uint64, Pointer<Uint32>),
          int Function(Pointer<Void>, Pointer<Uint8>, int, int, Pointer<Uint32>)
        >('fisica_core_entitlement_import')(_open, input, bytes.length, _clock(), outcome),
      );
      final result = outcome.value;
      if (result == FisicaEntitlementOutcome.accepted) {
        // A newer confirmation stored earlier wins over an older file.
        final file = _stateFile();
        if (file != null && file.existsSync()) {
          try {
            restoreState(file.readAsBytesSync());
            core._library.lookupFunction<
              Int32 Function(Pointer<Void>, Pointer<Uint8>, Uint32, Uint64, Pointer<Uint32>),
              int Function(Pointer<Void>, Pointer<Uint8>, int, int, Pointer<Uint32>)
            >('fisica_core_entitlement_import')(_open, input, bytes.length, _clock(), outcome);
          } on Object catch (error) {
            lastSyncError = 'storage: $error';
          }
        }
        _save();
      }
      _notify();
      if (result == FisicaEntitlementOutcome.accepted) _autoSync();
      return result;
    } finally {
      calloc.free(input);
      calloc.free(outcome);
    }
  }

  File? _stateFile() {
    final directory = _storageDirectory;
    if (directory == null) return null;
    final company = _text(FisicaEntitlementText.company);
    if (company.isEmpty) return null;
    // Named by company and contract, the only attribution unit; never an
    // installation or device id.
    final name = '$company-${_text(FisicaEntitlementText.contract)}'.replaceAll(
      RegExp(r'[^A-Za-z0-9_-]'),
      '_',
    );
    return File('$directory${Platform.pathSeparator}entitlement-$name.state');
  }

  void _save() {
    try {
      final file = _stateFile();
      if (file == null) return;
      file.parent.createSync(recursive: true);
      final temporary = File('${file.path}.tmp')..writeAsBytesSync(exportState());
      temporary.renameSync(file.path);
    } on FileSystemException catch (error) {
      lastSyncError = 'storage: ${error.message}';
    }
  }

  /// Offline setup: a file downloaded on another PC works on a device that
  /// was never online.
  int importFile(String path) => importToken(File(path).readAsStringSync());

  Uint8List _read(String symbol, int capacity) {
    final output = calloc<Uint8>(capacity);
    final length = calloc<Uint32>();
    try {
      core._check(
        core._library.lookupFunction<_EntitlementBytesNative, _EntitlementBytesDart>(
          symbol,
        )(_open, output, capacity, length),
      );
      return Uint8List.fromList(output.asTypedList(length.value));
    } finally {
      calloc.free(output);
      calloc.free(length);
    }
  }

  String _text(int field) {
    final output = calloc<Uint8>(4096);
    final length = calloc<Uint32>();
    try {
      core._check(
        core._library.lookupFunction<
          Int32 Function(Pointer<Void>, Uint32, Pointer<Uint8>, Uint32, Pointer<Uint32>),
          int Function(Pointer<Void>, int, Pointer<Uint8>, int, Pointer<Uint32>)
        >('fisica_core_entitlement_text')(_open, field, output, 4096, length),
      );
      return utf8.decode(output.asTypedList(length.value));
    } finally {
      calloc.free(output);
      calloc.free(length);
    }
  }

  EntitlementStatus status() {
    final values = calloc<Uint64>(12);
    try {
      core._check(
        core._library.lookupFunction<
          Int32 Function(Pointer<Void>, Uint64, Pointer<Uint64>, Uint32),
          int Function(Pointer<Void>, int, Pointer<Uint64>, int)
        >('fisica_core_entitlement_status')(_open, _clock(), values, 12),
      );
      return EntitlementStatus._(
        values.asTypedList(12).toList(),
        _text(FisicaEntitlementText.company),
        _text(FisicaEntitlementText.contract),
        _text(FisicaEntitlementText.grants).split(',').where((g) => g.isNotEmpty).toList(),
      );
    } finally {
      calloc.free(values);
    }
  }

  void _notify() {
    final current = status();
    final fresh = current.warnings & ~_warned;
    _warned = current.warnings;
    if (fresh != 0) onWarning?.call(fresh, current);
  }

  /// Re-evaluate warnings for the current time and refresh in the background
  /// when online. Called from feature paths; it records nothing about the use.
  void checkStatus() {
    if (_handle == nullptr) return;
    _notify();
    _autoSync();
  }

  Future<List<int>> _post(String path, String token, List<int> body) async {
    final client = HttpClient()..connectionTimeout = const Duration(seconds: 5);
    try {
      final request = await client.postUrl(Uri.parse('$_endpoint$path'));
      request.headers.set('Authorization', 'Bearer $token');
      request.headers.contentType = ContentType.json;
      request.contentLength = body.length;
      request.add(body);
      final response = await request.close().timeout(const Duration(seconds: 5));
      final data = await response.fold<List<int>>([], (all, chunk) => all..addAll(chunk));
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw HttpException('HTTP ${response.statusCode}');
      }
      return data;
    } finally {
      client.close(force: true);
    }
  }

  Future<String?> _exchangeOnce() async {
    if (!online || _endpoint == null || _handle == nullptr) return null;
    try {
      final token = _text(FisicaEntitlementText.token);
      // Nothing verified: nothing to refresh.
      if (token.isEmpty) return null;
      final response =
          jsonDecode(utf8.decode(await _post('/sdk/v1/entitlement/refresh', token, utf8.encode('{}'))))
              as Map<String, dynamic>;
      if (_handle == nullptr) return null;
      final refreshed = ascii.encode(response['token'] as String);
      final input = calloc<Uint8>(refreshed.length);
      final outcome = calloc<Uint32>();
      try {
        input.asTypedList(refreshed.length).setAll(0, refreshed);
        core._library.lookupFunction<
          Int32 Function(Pointer<Void>, Pointer<Uint8>, Uint32, Uint64, Pointer<Uint32>),
          int Function(Pointer<Void>, Pointer<Uint8>, int, int, Pointer<Uint32>)
        >('fisica_core_entitlement_import')(_handle, input, refreshed.length, _clock(), outcome);
      } finally {
        calloc.free(input);
        calloc.free(outcome);
      }
      lastSyncError = null;
    } on Object catch (error) {
      // A communication failure is not an expiry or a revocation.
      lastSyncError = 'network: $error';
    }
    if (_handle != nullptr) {
      _save();
      _notify();
    }
    return lastSyncError;
  }

  /// Online confirmation. Completes with null or the problem and never throws.
  /// A failure changes no entitlement state.
  Future<String?> sync() => _exchange = _exchange.then((_) => _exchangeOnce());

  // Background sync, at most hourly; never awaited by the feature that triggered it.
  void _autoSync() {
    if (!online || _endpoint == null || DateTime.now().isBefore(_nextSync)) return;
    _nextSync = DateTime.now().add(const Duration(hours: 1));
    unawaited(sync());
  }

  /// Opaque state for the host to store. Restoring verifies every stored
  /// token again.
  Uint8List exportState() => _read('fisica_core_entitlement_export', 262144);

  void restoreState(Uint8List state) {
    final input = calloc<Uint8>(state.isEmpty ? 1 : state.length);
    try {
      input.asTypedList(state.length).setAll(0, state);
      core._check(
        core._library.lookupFunction<
          Int32 Function(Pointer<Void>, Pointer<Uint8>, Uint32),
          int Function(Pointer<Void>, Pointer<Uint8>, int)
        >('fisica_core_entitlement_restore')(_open, input, state.length),
      );
      _notify();
    } finally {
      calloc.free(input);
    }
  }
}
