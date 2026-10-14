import 'dart:collection';
import 'dart:convert';
import 'dart:async';
import 'dart:ffi';
import 'dart:io';
import 'dart:typed_data';
import 'package:ffi/ffi.dart';
import 'src/compatibility.dart';
import 'src/version.dart' as release;
export 'src/compatibility.dart';
part 'src/codes.dart';
part 'src/session.dart';
part 'src/entitlement.dart';
part 'src/client.dart';

typedef _StringNative = Pointer<Utf8> Function();
typedef _StringDart = Pointer<Utf8> Function();
typedef _AbiNative = Uint32 Function();
typedef _AbiDart = int Function();
typedef _ErrorNative = Pointer<Utf8> Function(Int32);
typedef _ErrorDart = Pointer<Utf8> Function(int);
typedef _ResponseNative =
    Int32 Function(Pointer<Uint8>, Uint32, Pointer<Int32>, Uint32);
typedef _ResponseDart = int Function(Pointer<Uint8>, int, Pointer<Int32>, int);
typedef _PayloadNative =
    Int32 Function(
      Pointer<Uint8>,
      Uint32,
      Pointer<Uint8>,
      Uint32,
      Pointer<Uint32>,
    );
typedef _PayloadDart =
    int Function(Pointer<Uint8>, int, Pointer<Uint8>, int, Pointer<Uint32>);

class FisicaArgumentError extends ArgumentError {
  final int code;
  FisicaArgumentError(this.code, String message) : super(message);
}

class FisicaStateError extends StateError {
  final int code;
  FisicaStateError(this.code, String message) : super(message);
}

typedef _DecodeNative =
    Int32 Function(
      Pointer<Uint8>,
      Uint32,
      Pointer<Double>,
      Uint32,
      Pointer<Double>,
      Uint32,
      Pointer<Double>,
      Uint32,
    );
typedef _DecodeDart =
    int Function(
      Pointer<Uint8>,
      int,
      Pointer<Double>,
      int,
      Pointer<Double>,
      int,
      Pointer<Double>,
      int,
    );
typedef _CommandNative =
    Int32 Function(
      Pointer<Utf8>,
      Double,
      Pointer<Uint8>,
      Uint32,
      Pointer<Uint8>,
      Uint32,
      Pointer<Uint32>,
    );
typedef _CommandDart =
    int Function(
      Pointer<Utf8>,
      double,
      Pointer<Uint8>,
      int,
      Pointer<Uint8>,
      int,
      Pointer<Uint32>,
    );
typedef _PacketNative =
    Int32 Function(
      Pointer<Uint8>,
      Uint32,
      Pointer<Uint32>,
      Pointer<Uint32>,
      Pointer<Uint8>,
      Uint32,
      Pointer<Uint32>,
    );
typedef _PacketDart =
    int Function(
      Pointer<Uint8>,
      int,
      Pointer<Uint32>,
      Pointer<Uint32>,
      Pointer<Uint8>,
      int,
      Pointer<Uint32>,
    );
typedef _AnalyzeNative =
    Int32 Function(
      Pointer<Double>,
      Pointer<Double>,
      Uint32,
      Uint32,
      Uint32,
      Pointer<Double>,
      Uint32,
    );
typedef _AnalyzeDart =
    int Function(
      Pointer<Double>,
      Pointer<Double>,
      int,
      int,
      int,
      Pointer<Double>,
      int,
    );

class DecodedMeasurement {
  final List<List<double>> rawGrid;
  final List<List<double>> sensorMatrix;
  final double weight;
  final double battery;
  final Uint8List rawData;
  final String coreVersion;
  final int schemaVersion;
  // The existing SDK battery value is a placeholder, not a hardware reading.
  bool get batteryValid => false;
  DecodedMeasurement(
    this.rawGrid,
    this.sensorMatrix,
    this.weight,
    this.battery,
    this.rawData,
    this.coreVersion,
    this.schemaVersion,
  );
}

class NativePacket {
  final int consumed;
  final int kind; // 0=no packet, 1=measurement, 2=command response
  final Uint8List bytes;
  NativePacket(this.consumed, this.kind, this.bytes);
}

/// FFI wrapper usable from Flutter native apps. OS BLE/USB transport belongs to
/// the host adapter. An explicitly packaged, trusted library must be supplied.
class FisicaCore {
  final DynamicLibrary _library;
  late final String coreVersion;
  late final int abiVersion;
  late final Map<String, dynamic> policy;
  late final Map<String, dynamic> wrapper;
  final String sdkVersion = release.sdkVersion;

  FisicaCore(this._library, {DeprecationHandler? onDeprecation}) {
    coreVersion = _library
        .lookupFunction<_StringNative, _StringDart>('fisica_core_version')()
        .toDartString();
    abiVersion = _library.lookupFunction<_AbiNative, _AbiDart>(
      'fisica_core_abi_version',
    )();
    policy =
        jsonDecode(
              _library
                  .lookupFunction<_StringNative, _StringDart>(
                    'fisica_core_policy_json',
                  )()
                  .toDartString(),
            )
            as Map<String, dynamic>;
    wrapper = jsonDecode(release.wrapperManifestJson) as Map<String, dynamic>;
    verifyCompatibility(
      policy: policy,
      wrapper: wrapper,
      sdkVersion: sdkVersion,
      coreVersion: coreVersion,
      abiVersion: abiVersion,
      onDeprecation:
          onDeprecation ?? (message) => stderr.writeln('Fisica: $message'),
    );
  }

  factory FisicaCore.open(
    String libraryPath, {
    DeprecationHandler? onDeprecation,
  }) => FisicaCore(
    DynamicLibrary.open(libraryPath),
    onDeprecation: onDeprecation,
  );

  factory FisicaCore.platform({DeprecationHandler? onDeprecation}) =>
      FisicaCore(
        (Platform.isIOS || Platform.isMacOS)
            ? DynamicLibrary.process()
            : DynamicLibrary.open(
                Platform.isWindows
                    ? 'fisica_core.dll'
                    : Platform.isMacOS
                    ? 'libfisica_core.dylib'
                    : 'libfisica_core.so',
              ),
        onDeprecation: onDeprecation,
      );

  Map<String, dynamic> get versionInfo => {
    'sdk_version': sdkVersion,
    'wrapper_name': wrapper['name'],
    'wrapper_version': wrapper['version'],
    'core_version': coreVersion,
    'abi_major': abiVersion >> 16,
    'abi_minor': abiVersion & 65535,
    'result_schema_version': policy['result_schema_version'],
    'license': policy['license'],
    'entitlement': policy['entitlement'],
  };

  void _check(int status) {
    if (status == 0) return;
    final message = _library
        .lookupFunction<_ErrorNative, _ErrorDart>('fisica_core_error_message')(
          status,
        )
        .toDartString();
    if (status == 1 || status == 4) throw FisicaArgumentError(status, message);
    throw FisicaStateError(status, message);
  }

  DecodedMeasurement decodePacket(Uint8List packet) {
    if (packet.length != 645)
      throw FisicaArgumentError(1, 'Expected a 645-byte packet');
    final input = calloc<Uint8>(packet.length);
    final raw = calloc<Double>(638);
    final display = calloc<Double>(841);
    final metrics = calloc<Double>(2);
    try {
      input.asTypedList(packet.length).setAll(0, packet);
      final decode = _library.lookupFunction<_DecodeNative, _DecodeDart>(
        'fisica_core_decode_packet',
      );
      _check(decode(input, packet.length, raw, 638, display, 841, metrics, 2));
      return DecodedMeasurement(
        List.generate(29, (r) => List.generate(22, (c) => raw[r * 22 + c])),
        List.generate(29, (r) => List.generate(29, (c) => display[r * 29 + c])),
        metrics[0],
        metrics[1],
        Uint8List.fromList(packet),
        coreVersion,
        policy['result_schema_version'] as int,
      );
    } finally {
      calloc.free(input);
      calloc.free(raw);
      calloc.free(display);
      calloc.free(metrics);
    }
  }

  Uint8List command(String name, {double value = 0, Uint8List? payload}) {
    if (name.contains('\u0000'))
      throw FisicaArgumentError(1, 'Command name contains NUL');
    final data = payload ?? Uint8List(0);
    if (data.length > 4096)
      throw FisicaArgumentError(1, 'Payload is too large');
    final nativeName = name.toNativeUtf8(allocator: calloc);
    final input = calloc<Uint8>(data.isEmpty ? 1 : data.length);
    final output = calloc<Uint8>(4102);
    final length = calloc<Uint32>();
    try {
      input.asTypedList(data.length).setAll(0, data);
      final build = _library.lookupFunction<_CommandNative, _CommandDart>(
        'fisica_core_command',
      );
      _check(
        build(nativeName, value, input, data.length, output, 4102, length),
      );
      return Uint8List.fromList(output.asTypedList(length.value));
    } finally {
      calloc.free(nativeName);
      calloc.free(input);
      calloc.free(output);
      calloc.free(length);
    }
  }

  NativePacket nextPacket(Uint8List buffer) {
    final input = calloc<Uint8>(buffer.isEmpty ? 1 : buffer.length);
    final consumed = calloc<Uint32>();
    final kind = calloc<Uint32>();
    final length = calloc<Uint32>();
    final output = calloc<Uint8>(4096);
    try {
      input.asTypedList(buffer.length).setAll(0, buffer);
      final next = _library.lookupFunction<_PacketNative, _PacketDart>(
        'fisica_core_next_packet',
      );
      final status = next(
        input,
        buffer.length,
        consumed,
        kind,
        output,
        4096,
        length,
      );
      if (status != 3) _check(status);
      return NativePacket(
        consumed.value,
        kind.value,
        Uint8List.fromList(output.asTypedList(length.value)),
      );
    } finally {
      calloc.free(input);
      calloc.free(consumed);
      calloc.free(kind);
      calloc.free(length);
      calloc.free(output);
    }
  }

  Map<String, dynamic> decodeResponse(Uint8List packet) {
    final input = calloc<Uint8>(packet.isEmpty ? 1 : packet.length);
    final response = calloc<Int32>(6);
    final payload = calloc<Uint8>(4096);
    final length = calloc<Uint32>();
    try {
      input.asTypedList(packet.length).setAll(0, packet);
      final decode = _library.lookupFunction<_ResponseNative, _ResponseDart>(
        'fisica_core_decode_response',
      );
      final decodePayload = _library
          .lookupFunction<_PayloadNative, _PayloadDart>(
            'fisica_core_response_payload',
          );
      _check(decode(input, packet.length, response, 6));
      _check(decodePayload(input, packet.length, payload, 4096, length));
      return {
        'header': response[0],
        'type': response[1],
        'ret': response[2],
        'data_high': response[3],
        'data_low': response[4],
        'success': response[5] != 0,
        'payload': Uint8List.fromList(payload.asTypedList(length.value)),
      };
    } finally {
      calloc.free(input);
      calloc.free(response);
      calloc.free(payload);
      calloc.free(length);
    }
  }

  List<double>? analyzeMatrices(
    List<List<List<double>>> matrices,
    List<double> weights,
  ) {
    if (matrices.isEmpty ||
        matrices.length != weights.length ||
        matrices.first.isEmpty ||
        matrices.first.first.isEmpty) {
      throw FisicaArgumentError(
        1,
        'Expected matching nonempty frame matrices and weights',
      );
    }
    final rows = matrices.first.length;
    final cols = matrices.first.first.length;
    if (matrices.any(
      (grid) => grid.length != rows || grid.any((row) => row.length != cols),
    )) {
      throw FisicaArgumentError(1, 'Frame shapes must match');
    }
    final input = calloc<Double>(matrices.length * rows * cols);
    final nativeWeights = calloc<Double>(weights.length);
    final output = calloc<Double>(5);
    try {
      input
          .asTypedList(matrices.length * rows * cols)
          .setAll(0, matrices.expand((grid) => grid.expand((row) => row)));
      nativeWeights.asTypedList(weights.length).setAll(0, weights);
      final analyze = _library.lookupFunction<_AnalyzeNative, _AnalyzeDart>(
        'fisica_core_analyze',
      );
      final status = analyze(
        input,
        nativeWeights,
        matrices.length,
        rows,
        cols,
        output,
        5,
      );
      if (status == 3) return null;
      _check(status);
      return output.asTypedList(5).toList();
    } finally {
      calloc.free(input);
      calloc.free(nativeWeights);
      calloc.free(output);
    }
  }

  /// Weight and foot dimensions from measurement packets as received:
  /// `[weight_kg, left_width_cm, left_length_cm, right_width_cm, right_length_cm]`,
  /// or null without a stable measurement. The analysis runs in the shared core.
  List<double>? analyzePackets(List<Uint8List> packets) {
    if (packets.isEmpty || packets.any((packet) => packet.length != 645)) {
      throw FisicaArgumentError(1, 'Expected 645-byte measurement packets');
    }
    final input = calloc<Uint8>(packets.length * 645);
    final output = calloc<Double>(5);
    try {
      final bytes = input.asTypedList(packets.length * 645);
      for (var i = 0; i < packets.length; i++) {
        bytes.setAll(i * 645, packets[i]);
      }
      final status = _library
          .lookupFunction<
            Int32 Function(Pointer<Uint8>, Uint32, Pointer<Double>, Uint32),
            int Function(Pointer<Uint8>, int, Pointer<Double>, int)
          >('fisica_core_analyze_packets')(input, packets.length, output, 5);
      if (status == 3) return null;
      _check(status);
      return output.asTypedList(5).toList();
    } finally {
      calloc.free(input);
      calloc.free(output);
    }
  }
}
