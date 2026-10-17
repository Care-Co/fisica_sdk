part of '../fisica_core.dart';

const _basicMode =
    'Basic mode keeps connection, measurement, weight and foot size; '
    'raw data and full-rate frames need full mode.';
const _reasonMessages = <int, String>{
  FisicaModeReason.noKey:
      'No SDK key. Create one in the developer portal and import it. $_basicMode',
  FisicaModeReason.legacyToken:
      'This is a contract confirmation, not an SDK key. Create an SDK key in the developer portal. $_basicMode',
  FisicaModeReason.notActivated:
      'The SDK key is not activated yet: connect to the internet, or enter the activation code in the developer portal and import the file it gives. $_basicMode',
  FisicaModeReason.noTarget:
      "The SDK cannot read this app's ID or this PC's identity, so a key cannot be activated here. $_basicMode",
  FisicaModeReason.otherTarget:
      'This SDK key is activated for another app or PC. $_basicMode',
  FisicaModeReason.revoked:
      'The SDK key was released or its contract was revoked. $_basicMode',
  FisicaModeReason.clockRollback:
      "This device's clock is earlier than a time the SDK has already seen; correct the clock. $_basicMode",
  FisicaModeReason.keyExpired:
      'The SDK key expired: connect to renew it, or import a renewal file from the developer portal. $_basicMode',
  FisicaModeReason.notStarted:
      'The contract of this SDK key has not started yet. $_basicMode',
  FisicaModeReason.fullEnded:
      'The trial or contract of this SDK key has ended. $_basicMode',
};
const _warningMessages = <int, String>{
  FisicaEntitlementWarning.renewDue:
      'The SDK key is due for renewal: connect to the internet or import a renewal file.',
  FisicaEntitlementWarning.grace:
      'The contract has ended; full mode continues during the grace period.',
  FisicaEntitlementWarning.fullEnding:
      'Full mode ends within 7 days (trial or contract end).',
  FisicaEntitlementWarning.importRejected:
      'The last SDK key or file was rejected (format, unknown signing key, signature or another app/PC).',
  FisicaEntitlementWarning.testKey:
      'The SDK key is signed with a TEST key; this is not a production license.',
};
const _activatePath = '/sdk/v1/entitlement/activate';
const _refreshPath = '/sdk/v1/entitlement/refresh';
const _pairingPath = '/sdk/v1/pairing';
const _pairingTokenPath = '/sdk/v1/pairing/token';

/// The license as the core decides it now: full or basic mode, why, the open
/// features and the SDK key's dates. Codes follow the generated classes
/// ([FisicaMode], [FisicaModeReason], [FisicaFeature], [FisicaContract],
/// [FisicaContractKind], [FisicaTrack], [FisicaBindType],
/// [FisicaEntitlementOutcome], [FisicaKeyEnvironment]). Times are Unix
/// seconds, 0 when not set.
class EntitlementStatus {
  /// [FisicaMode.full] or [FisicaMode.basic].
  final int mode;

  /// Why the license is in basic mode ([FisicaModeReason]);
  /// [FisicaModeReason.none] in full mode.
  final int reason;

  /// Bit set of [FisicaFeature] open now; 0 in basic mode.
  final int features;

  /// State of the key's contract by its signed dates ([FisicaContract]).
  final int contractState;
  final int contractKind, track;

  /// Target type the key is activated for, and this app's or PC's own
  /// ([FisicaBindType.none] when the core cannot read it).
  final int bindType, targetType;
  final int verifiedAt, refreshBy, keyUntil, fullUntil;
  final int contractStart, contractEnd, revokedAt;

  /// Outcome of the last import ([FisicaEntitlementOutcome]).
  final int lastImport;
  final int keyEnvironment;

  /// Bit set of [FisicaEntitlementWarning].
  final int warnings;

  /// Latest wall-clock time seen; a clock set back further than an hour is
  /// basic mode.
  final int latestTime;

  /// Company, contract and SDK key (seat) of the verified key; null without one.
  final String? company, contract, key;

  /// App ID or PC hash the key is activated for, and this app's or PC's own.
  final String? bind, target;
  final List<String> grants;

  /// FAC1 code for activating the loaded key in the developer portal from
  /// another device; only while the key is not activated.
  final String? activationCode;

  /// Last storage or network problem, or null. Never a license state.
  final String? lastSyncError;

  EntitlementStatus._(
    List<int> v, {
    required this.company,
    required this.contract,
    required this.key,
    required this.bind,
    required this.target,
    required this.grants,
    required this.activationCode,
    required this.lastSyncError,
  }) : mode = v[0],
       reason = v[1],
       features = v[2],
       contractState = v[3],
       contractKind = v[4],
       track = v[5],
       bindType = v[6],
       targetType = v[7],
       verifiedAt = v[8],
       refreshBy = v[9],
       keyUntil = v[10],
       fullUntil = v[11],
       contractStart = v[12],
       contractEnd = v[13],
       revokedAt = v[14],
       lastImport = v[15],
       keyEnvironment = v[16],
       warnings = v[17],
       latestTime = v[18];

  bool get isFull => mode == FisicaMode.full;

  /// True while full mode opens [feature] (a [FisicaFeature] bit).
  bool hasFeature(int feature) => features & feature == feature;

  /// What basic mode means here and what to do; null in full mode.
  String? get message => _reasonMessages[reason];

  /// Notices for the raised [warnings] bits.
  List<String> get warningMessages => [
    for (final entry in _warningMessages.entries)
      if (warnings & entry.key != 0) entry.value,
  ];
}

/// Result of one exchange with the server: whether a key was activated or
/// renewed, or the problem. A problem never changes the license.
typedef EntitlementSync = ({bool activated, bool refreshed, String? error});

/// What to show while pairing: an owner or admin enters [userCode] at
/// [verificationUri] ([verificationUriComplete] already holds the code). The
/// code is valid for [expiresIn] seconds.
class PairingCode {
  final String userCode, verificationUri, verificationUriComplete;
  final int expiresIn;
  const PairingCode({
    required this.userCode,
    required this.verificationUri,
    required this.verificationUriComplete,
    required this.expiresIn,
  });
}

/// How pairing ended. [approved]: the key is loaded and stored.
enum PairingResult { approved, denied, expired, cancelled }

typedef _EntitlementBytesNative =
    Int32 Function(Pointer<Void>, Pointer<Uint8>, Uint32, Pointer<Uint32>);
typedef _EntitlementBytesDart =
    int Function(Pointer<Void>, Pointer<Uint8>, int, Pointer<Uint32>);

/// One SDK instance's license, owned by one isolate: its SDK key, the key's
/// activation for this app or PC, and renewal. Always close explicitly.
///
/// The native core verifies the key, reads this app's ID or this PC's hash
/// itself and decides between full and basic mode. Without a key activated
/// here the SDK runs in basic mode: connection, measurement, settings, weight
/// and foot size work; frames come at most once a second without raw data,
/// and [FisicaCore.decodePacket] throws [FisicaLicenseError].
///
/// Online (with an endpoint) an unactivated key is activated here
/// automatically and a key is renewed before it ends. Network failures never
/// change the license. The SDK does not track its own use: the stored state is
/// the signed key and the latest time seen.
class FisicaEntitlement {
  final FisicaCore core;
  final int Function() _clock;
  final String? _endpoint;
  final String? _storageDirectory;

  /// False keeps a configured endpoint unused.
  final bool online;

  /// Network timeout of each server request.
  final Duration timeout;
  Pointer<Void> _handle = nullptr;
  int _warned = 0;
  String? _noticed;
  final _changes = StreamController<EntitlementStatus>.broadcast();
  // One exchange with the server at a time.
  Future<void> _exchange = Future.value();
  final _monotonic = Stopwatch()..start();
  int _nextSyncMs = 0;

  /// Called with newly raised [FisicaEntitlementWarning] bits so the app can
  /// show or log them.
  void Function(int warnings, EntitlementStatus status)? onWarning;

  /// Last storage or network problem, or null. Never a license state.
  String? lastSyncError;

  /// [endpoint]: server base URL for activation, renewal and [pair]; without
  /// it no network is used. [storageDirectory]: directory for the stored
  /// state (for example the app's support directory), one file per seat;
  /// null keeps it in memory only, so an online app activates its key again
  /// after each launch. The key stored there for this app or PC (paired or
  /// imported earlier) is loaded at once. [clock]: wall-clock Unix seconds.
  FisicaEntitlement(
    this.core, {
    int Function()? clock,
    String? endpoint,
    String? storageDirectory,
    this.online = true,
    this.timeout = const Duration(seconds: 5),
  }) : _clock = clock ?? (() => DateTime.now().millisecondsSinceEpoch ~/ 1000),
       _endpoint = endpoint?.replaceAll(RegExp(r'/+$'), ''),
       _storageDirectory = storageDirectory {
    _handle = _create();
    if (_loadStored()) {
      _notify();
      _autoSync();
    } else {
      _notify();
    }
  }

  /// The developer portal, for [pair].
  static const defaultEndpoint = 'https://developer.fisicahealthcare.com';

  Pointer<Void> _create() {
    final output = calloc<Pointer<Void>>();
    try {
      core._check(
        core._library.lookupFunction<
          Int32 Function(Pointer<Pointer<Void>>),
          int Function(Pointer<Pointer<Void>>)
        >('fisica_core_entitlement_create')(output),
      );
      return output.value;
    } finally {
      calloc.free(output);
    }
  }

  void _destroy(Pointer<Void> context) => core._library.lookupFunction<
    Void Function(Pointer<Void>),
    void Function(Pointer<Void>)
  >('fisica_core_entitlement_destroy')(context);

  /// The key stored for this app or PC, if any. The core names a state file
  /// from the company, the contract and the target, so a file it names again
  /// after restoring it belongs here. With several, a full-mode key wins,
  /// then the most recently verified one.
  bool _loadStored() {
    final directory = _storageDirectory;
    if (directory == null) return false;
    final List<File> files;
    try {
      files = Directory(directory).listSync().whereType<File>().where((file) {
        final name = file.uri.pathSegments.last;
        return name.startsWith('entitlement-') && name.endsWith('.state');
      }).toList()..sort((a, b) => a.path.compareTo(b.path));
    } on FileSystemException {
      return false;
    }
    ({bool full, int verified, Uint8List state})? best;
    for (final file in files) {
      final probe = _create();
      try {
        final state = file.readAsBytesSync();
        _restore(state, probe);
        if (_text(FisicaEntitlementText.stateName, probe) != file.uri.pathSegments.last) continue;
        final values = _values(probe);
        final full = values[0] == FisicaMode.full;
        final current = best;
        if (current == null || (full && !current.full) ||
            (full == current.full && values[8] > current.verified)) {
          best = (full: full, verified: values[8], state: state);
        }
      } on Object catch (_) {
        // Unreadable or foreign state: not this app's or PC's key.
      } finally {
        _destroy(probe);
      }
    }
    final chosen = best;
    if (chosen == null) return false;
    try {
      _restore(chosen.state);
      return true;
    } on Object catch (_) {
      return false;
    }
  }

  /// Emits the status whenever the license changes: mode, reason, features,
  /// key, renewal, last import or warnings. [FisicaClient] applies it to its
  /// session.
  Stream<EntitlementStatus> get changes => _changes.stream;

  void close() {
    if (_handle == nullptr) return;
    _destroy(_handle);
    _handle = nullptr;
    unawaited(_changes.close());
  }

  Pointer<Void> get _open {
    if (_handle == nullptr) throw FisicaStateError(6, 'Entitlement is closed');
    return _handle;
  }

  int _now() {
    final now = _clock();
    if (now < 0 || now > 1 << 53) {
      throw FisicaArgumentError(1, 'Expected Unix seconds');
    }
    return now;
  }

  int _import(String token, int now) {
    final bytes = utf8.encode(token);
    final input = calloc<Uint8>(bytes.isEmpty ? 1 : bytes.length);
    final outcome = calloc<Uint32>();
    try {
      input.asTypedList(bytes.length).setAll(0, bytes);
      core._check(
        core._library.lookupFunction<
          Int32 Function(Pointer<Void>, Pointer<Uint8>, Uint32, Uint64, Pointer<Uint32>),
          int Function(Pointer<Void>, Pointer<Uint8>, int, int, Pointer<Uint32>)
        >('fisica_core_entitlement_import')(_open, input, bytes.length, now, outcome),
      );
      return outcome.value;
    } finally {
      calloc.free(input);
      calloc.free(outcome);
    }
  }

  /// Import an SDK key or key file content from the developer portal (an
  /// activation or renewal file works on a device that is never online).
  /// Returns a [FisicaEntitlementOutcome]; a rejected or older key, or one
  /// activated for another app or PC, leaves the current key untouched.
  ///
  /// A key activated or renewed earlier and stored for the same company and
  /// contract wins over an older original key file. Online, a new key is
  /// activated here in the background (`await sync()` to wait for it).
  int importToken(String token) {
    final now = _now();
    final outcome = _import(token, now);
    if (outcome == FisicaEntitlementOutcome.accepted) {
      _nextSyncMs = 0; // A new key may need activation now.
      try {
        final file = _stateFile();
        if (file != null && file.existsSync()) {
          // The key activated or renewed earlier wins over the original key file.
          _restore(file.readAsBytesSync());
          _import(token, now);
        }
      } on Object catch (error) {
        lastSyncError = 'storage: $error';
      }
      _save();
    }
    _notify();
    if (outcome == FisicaEntitlementOutcome.accepted) _autoSync();
    return outcome;
  }

  int importFile(String path) =>
      importToken(File(path).readAsStringSync(encoding: latin1));

  File? _stateFile() {
    final directory = _storageDirectory;
    if (directory == null) return null;
    // One file per seat on this device, named by the core from the company,
    // the contract and this app's or PC's target, so every wrapper on a PC
    // finds the same file. Never a usage record.
    final name = _text(FisicaEntitlementText.stateName);
    if (name.isEmpty) return null;
    return File('$directory${Platform.pathSeparator}$name');
  }

  void _save() {
    try {
      final file = _stateFile();
      if (file == null) return;
      file.parent.createSync(recursive: true);
      final temporary = File('${file.path}.tmp')..writeAsBytesSync(exportState());
      temporary.renameSync(file.path);
    } on Object catch (error) {
      lastSyncError = 'storage: $error';
    }
  }

  void _delete(File? file) {
    try {
      if (file != null && file.existsSync()) file.deleteSync();
    } on Object catch (error) {
      lastSyncError = 'storage: $error';
    }
  }

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

  String _text(int field, [Pointer<Void>? context]) {
    final output = calloc<Uint8>(FisicaLimit.tokenBytes);
    final length = calloc<Uint32>();
    try {
      core._check(
        core._library.lookupFunction<
          Int32 Function(Pointer<Void>, Uint32, Pointer<Uint8>, Uint32, Pointer<Uint32>),
          int Function(Pointer<Void>, int, Pointer<Uint8>, int, Pointer<Uint32>)
        >('fisica_core_entitlement_text')(context ?? _open, field, output, FisicaLimit.tokenBytes, length),
      );
      return utf8.decode(output.asTypedList(length.value));
    } finally {
      calloc.free(output);
      calloc.free(length);
    }
  }

  /// Text the core only has in some states (FISICA_INVALID_STATE otherwise).
  String? _optionalText(int field) {
    try {
      return _text(field);
    } on FisicaStateError catch (error) {
      if (error.code == FisicaStatus.invalidState) return null;
      rethrow;
    }
  }

  List<int> _values([Pointer<Void>? context]) {
    final values = calloc<Uint64>(FisicaLimit.statusFields);
    try {
      core._check(
        core._library.lookupFunction<
          Int32 Function(Pointer<Void>, Uint64, Pointer<Uint64>, Uint32),
          int Function(Pointer<Void>, int, Pointer<Uint64>, int)
        >('fisica_core_entitlement_status')(context ?? _open, _now(), values, FisicaLimit.statusFields),
      );
      return values.asTypedList(FisicaLimit.statusFields).toList();
    } finally {
      calloc.free(values);
    }
  }

  EntitlementStatus status() {
    final values = _values();
    String? text(int field) {
      final value = _text(field);
      return value.isEmpty ? null : value;
    }

    return EntitlementStatus._(
      values,
      company: text(FisicaEntitlementText.company),
      contract: text(FisicaEntitlementText.contract),
      key: text(FisicaEntitlementText.key),
      bind: text(FisicaEntitlementText.bind),
      target: text(FisicaEntitlementText.target),
      grants: _text(
        FisicaEntitlementText.grants,
      ).split(',').where((grant) => grant.isNotEmpty).toList(),
      activationCode: values[1] == FisicaModeReason.notActivated
          ? activationCode()
          : null,
      lastSyncError: lastSyncError,
    );
  }

  /// True while full mode hands out every frame ([FisicaFeature.fullRate]).
  bool get fullRate => hasFeature(FisicaFeature.fullRate);

  /// True while full mode opens [feature] (a [FisicaFeature] bit).
  bool hasFeature(int feature) => _values()[2] & feature == feature;

  /// FAC1 code for activating the loaded key in the developer portal, for a
  /// device that is never online; null without an unactivated key.
  String? activationCode() =>
      _optionalText(FisicaEntitlementText.activationCode);

  /// Release the key activated here: the core forgets it, the stored state is
  /// deleted, and the FRC1 code to enter in the developer portal is returned.
  /// Online, the seat is also freed when the server next sees the key as
  /// released; offline, the code tells the portal this device let it go.
  /// Throws [FisicaStateError] without a key activated for this app or PC.
  String release() {
    final file = _stateFile();
    final code = ascii.decode(_read('fisica_core_entitlement_release', 1024));
    _delete(file);
    _notify();
    return code;
  }

  void _notify() {
    final current = status();
    final fresh = current.warnings & ~_warned;
    _warned = current.warnings;
    if (fresh != 0) {
      try {
        onWarning?.call(fresh, current);
      } on Object catch (_) {
        // A notice handler never breaks the feature that raised it.
      }
    }
    final noticed = [
      current.mode,
      current.reason,
      current.features,
      current.key,
      current.verifiedAt,
      current.warnings,
      current.lastImport,
    ].join(' ');
    if (noticed != _noticed) {
      _noticed = noticed;
      if (!_changes.isClosed) _changes.add(current);
    }
  }

  /// Re-evaluate the license for the current time and activate or renew in
  /// the background when due. Called from feature paths so a long-running
  /// app notices an ending key or contract. It records nothing about the use
  /// and never throws.
  void checkStatus() {
    if (_handle == nullptr) return;
    try {
      _notify();
      _autoSync();
    } on Object catch (error) {
      lastSyncError = 'check: $error';
    }
  }

  /// What the server should do now: activate, renew, or nothing.
  ({String path, String token, String body})? _due(bool force) {
    final values = _values();
    final token = _text(FisicaEntitlementText.token);
    final reason = values[1];
    if (token.isEmpty) return null;
    const blocked = [
      FisicaModeReason.noKey,
      FisicaModeReason.legacyToken,
      FisicaModeReason.otherTarget,
      FisicaModeReason.noTarget,
      FisicaModeReason.revoked,
    ];
    if (!blocked.contains(reason)) {
      if (reason == FisicaModeReason.notActivated) {
        // The core's activation request names this app or PC; sent verbatim.
        final request = _optionalText(FisicaEntitlementText.activationRequest);
        if (request != null) {
          return (path: _activatePath, token: token, body: request);
        }
      } else if (values[17] & FisicaEntitlementWarning.renewDue != 0 ||
          reason == FisicaModeReason.keyExpired ||
          reason == FisicaModeReason.fullEnded ||
          reason == FisicaModeReason.notStarted) {
        return (path: _refreshPath, token: token, body: '{}');
      }
    }
    return force ? (path: _refreshPath, token: token, body: '{}') : null;
  }

  Future<({int status, String text})> _send(
    String path,
    String? token,
    String body,
  ) async {
    final client = HttpClient()..connectionTimeout = timeout;
    try {
      final request = await client
          .postUrl(Uri.parse('$_endpoint$path'))
          .timeout(timeout);
      if (token != null) {
        request.headers.set(HttpHeaders.authorizationHeader, 'Bearer $token');
      }
      request.headers.contentType = ContentType.json;
      final bytes = utf8.encode(body);
      request.contentLength = bytes.length;
      request.add(bytes);
      final response = await request.close().timeout(timeout);
      final text = await utf8.decoder
          .bind(response)
          .join()
          .timeout(timeout);
      return (status: response.statusCode, text: text);
    } finally {
      client.close(force: true);
    }
  }

  Future<Map<String, dynamic>> _post(
    String path,
    String? token,
    String body,
  ) async {
    final (:status, :text) = await _send(path, token, body);
    if (status < 200 || status >= 300) {
      // A refused activation (key in use elsewhere, released, trial used) is reported.
      throw HttpException(
        '$status ${text.length > 200 ? text.substring(0, 200) : text}',
      );
    }
    return jsonDecode(text) as Map<String, dynamic>;
  }

  /// Get a key without one: the developer portal approves a code shown here
  /// (device authorization).
  ///
  /// Only this app's ID or this PC's hash is sent. [onCode] receives the code
  /// to show; an owner or admin of the organization enters it at
  /// [PairingCode.verificationUri] and approves it with a new or unused key.
  /// The key, already activated for this app or PC, then arrives, is loaded
  /// and stored, and later instances load it by themselves. Completes when
  /// the code is approved, denied or expires, or [cancel] completes. Throws
  /// [FisicaStateError] without an endpoint or a readable app ID or PC, and
  /// [HttpException] or [SocketException] when pairing cannot start.
  Future<PairingResult> pair(
    void Function(PairingCode code) onCode, {
    Future<void>? cancel,
  }) async {
    if (_endpoint == null) {
      throw FisicaStateError(6, 'Pairing needs the developer portal endpoint');
    }
    final request = _optionalText(FisicaEntitlementText.activationRequest);
    if (request == null) {
      throw FisicaStateError(6, _reasonMessages[FisicaModeReason.noTarget]!);
    }
    final device = await _post(_pairingPath, null, request);
    final code = PairingCode(
      userCode: device['user_code'] as String,
      verificationUri: device['verification_uri'] as String,
      verificationUriComplete: device['verification_uri_complete'] as String,
      expiresIn: (device['expires_in'] as num).toInt(),
    );
    onCode(code);
    final body = jsonEncode({'device_code': device['device_code']});
    var interval = ((device['interval'] as num?) ?? 5).toDouble();
    var cancelled = false;
    unawaited(cancel?.then((_) => cancelled = true));
    // A key approved just before the code expires can still be received for a short while.
    final deadline = DateTime.now().add(Duration(seconds: code.expiresIn + 30));
    while (true) {
      var wait = Duration(milliseconds: (interval * 1000).round());
      final left = deadline.difference(DateTime.now());
      if (left < wait) wait = left.isNegative ? Duration.zero : left;
      final waiting = Future<void>.delayed(wait);
      await (cancel == null ? waiting : Future.any([waiting, cancel]));
      if (cancelled) return PairingResult.cancelled;
      try {
        final (:status, :text) = await _send(_pairingTokenPath, null, body);
        final answer = jsonDecode(text);
        if (status == 200 && answer is Map && answer['token'] is String) {
          final outcome = importToken(answer['token'] as String);
          if (outcome != FisicaEntitlementOutcome.accepted) {
            throw FisicaStateError(6, 'The paired key was not accepted here ($outcome)');
          }
          return PairingResult.approved;
        }
        switch (answer is Map ? answer['error'] : null) {
          case 'access_denied':
            return PairingResult.denied;
          case 'expired_token':
            return PairingResult.expired;
          case 'slow_down':
            interval += 5;
        }
      } on FisicaStateError {
        rethrow;
      } on Object catch (problem) {
        lastSyncError = 'network: $problem'; // keep polling until the code expires
      }
      if (!DateTime.now().isBefore(deadline)) return PairingResult.expired;
    }
  }

  Future<EntitlementSync> _exchangeOnce(bool force) async {
    var activated = false, refreshed = false, save = false;
    String? error;
    try {
      final due = _handle == nullptr ? null : _due(force);
      if (due == null) return (activated: false, refreshed: false, error: null);
      final response = await _post(due.path, due.token, due.body);
      if (_handle == nullptr) {
        return (activated: false, refreshed: false, error: null);
      }
      final token = response['token'];
      if (token is! String) throw const FormatException('No key in the response');
      save = _import(token, _now()) == FisicaEntitlementOutcome.accepted;
      if (due.path == _activatePath) {
        activated = save;
      } else {
        refreshed = save;
      }
      if (_values()[1] == FisicaModeReason.revoked) {
        // The server reports this key released: the device lets go of it.
        _delete(_stateFile());
        save = false;
      }
      lastSyncError = null;
      _nextSyncMs = _monotonic.elapsedMilliseconds + 3600 * 1000;
    } on Object catch (problem) {
      // A communication failure is not an expiry, a release or a revocation.
      error = lastSyncError = 'network: $problem';
    }
    try {
      if (_handle != nullptr) {
        if (save) _save();
        _notify();
      }
    } on Object catch (_) {
      // Closed while a background exchange was finishing.
    }
    return (activated: activated, refreshed: refreshed, error: error);
  }

  /// Activate or renew online when due; [force] renews now. Runs after any
  /// exchange already in progress, completes with what happened (null without
  /// an endpoint or when [online] is false) and never throws. A failure
  /// changes no license state.
  Future<EntitlementSync?> sync({bool force = false}) {
    if (!online || _endpoint == null) return Future.value(null);
    final result = _exchange.then((_) => _exchangeOnce(force));
    _exchange = result.then((_) {}, onError: (Object _) {});
    return result;
  }

  // Background exchange when due; retried after a minute until it succeeds,
  // then at most hourly. Never awaited by the feature that triggered it.
  void _autoSync() {
    if (!online || _endpoint == null) return;
    final now = _monotonic.elapsedMilliseconds;
    if (now < _nextSyncMs) return;
    _nextSyncMs = now + 60 * 1000;
    unawaited(sync());
  }

  /// Opaque state for the host to store: the verified key and the latest
  /// time seen. Restoring verifies the stored key again.
  Uint8List exportState() =>
      _read('fisica_core_entitlement_export', 262144);

  void _restore(Uint8List state, [Pointer<Void>? context]) {
    final input = calloc<Uint8>(state.isEmpty ? 1 : state.length);
    try {
      input.asTypedList(state.length).setAll(0, state);
      core._check(
        core._library.lookupFunction<
          Int32 Function(Pointer<Void>, Pointer<Uint8>, Uint32),
          int Function(Pointer<Void>, Pointer<Uint8>, int)
        >('fisica_core_entitlement_restore')(context ?? _open, input, state.length),
      );
    } finally {
      calloc.free(input);
    }
  }

  void restoreState(Uint8List state) {
    _restore(state);
    _notify();
  }
}
