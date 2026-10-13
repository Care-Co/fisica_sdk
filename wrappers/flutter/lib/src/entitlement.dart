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

typedef _EntitlementNowNative = Int32 Function(Pointer<Void>, Uint64);
typedef _EntitlementNowDart = int Function(Pointer<Void>, int);
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
  Pointer<Void> _handle = nullptr;
  int _warned = 0;

  /// Called with newly raised warning bits so the app can show or log them.
  void Function(int warnings, EntitlementStatus status)? onWarning;

  FisicaEntitlement(this.core, {int Function()? clock})
    : _clock = clock ?? (() => DateTime.now().millisecondsSinceEpoch ~/ 1000) {
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
      _notify();
      return result;
    } finally {
      calloc.free(input);
      calloc.free(outcome);
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

  /// Record real feature use (connect, measure, analyze). Does nothing
  /// without a verified contract.
  void markUsed() {
    if (_handle == nullptr) return;
    core._library.lookupFunction<_EntitlementNowNative, _EntitlementNowDart>(
      'fisica_core_entitlement_mark_used',
    )(_handle, _clock());
    _notify();
  }

  /// Next pending usage report: bearer token and JSON body for the host to
  /// send, or null. [reportId] is 16 random bytes naming this report for
  /// retry de-duplication only.
  ({String token, Uint8List body})? pendingReport(Uint8List reportId) {
    final id = calloc<Uint8>(reportId.isEmpty ? 1 : reportId.length);
    final token = calloc<Uint8>(4096);
    final body = calloc<Uint8>(256);
    final tokenLength = calloc<Uint32>();
    final bodyLength = calloc<Uint32>();
    try {
      id.asTypedList(reportId.length).setAll(0, reportId);
      final status = core._library.lookupFunction<
        Int32 Function(Pointer<Void>, Pointer<Uint8>, Uint32, Pointer<Uint8>, Uint32, Pointer<Uint32>, Pointer<Uint8>, Uint32, Pointer<Uint32>),
        int Function(Pointer<Void>, Pointer<Uint8>, int, Pointer<Uint8>, int, Pointer<Uint32>, Pointer<Uint8>, int, Pointer<Uint32>)
      >('fisica_core_entitlement_report')(_open, id, reportId.length, token, 4096, tokenLength, body, 256, bodyLength);
      if (status == 3) return null;
      core._check(status);
      return (
        token: ascii.decode(token.asTypedList(tokenLength.value)),
        body: Uint8List.fromList(body.asTypedList(bodyLength.value)),
      );
    } finally {
      calloc.free(id);
      calloc.free(token);
      calloc.free(body);
      calloc.free(tokenLength);
      calloc.free(bodyLength);
    }
  }

  /// Call only after the server confirmed the report; the local record is
  /// then cleared.
  void reportDone() => core._check(
    core._library.lookupFunction<
      Int32 Function(Pointer<Void>),
      int Function(Pointer<Void>)
    >('fisica_core_entitlement_report_done')(_open),
  );

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
