import 'package:pub_semver/pub_semver.dart';

typedef DeprecationHandler = void Function(String message);

/// Invoke from a wrapper API that has a deprecation entry in its manifest.
/// Whether this core supports the SDK and wrapper release at all is the
/// core's decision (`fisica_core_check_wrapper`, checked by `FisicaCore`).
void warnDeprecatedApi(
  Map<String, dynamic> wrapper,
  String name,
  DeprecationHandler warn,
) {
  final rule = (wrapper['deprecated_apis'] as Map<String, dynamic>)[name];
  if (rule == null) return;
  final version = Version.parse(wrapper['version'] as String);
  if (version < Version.parse(rule['since'] as String)) return;
  final message =
      '$name is deprecated since ${rule['since']}; '
      'removed in ${rule['removed_in']}. Use ${rule['replacement']}';
  if (version >= Version.parse(rule['removed_in'] as String))
    throw StateError(message);
  warn(message);
}
