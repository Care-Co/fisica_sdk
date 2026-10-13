import 'package:pub_semver/pub_semver.dart';

typedef DeprecationHandler = void Function(String message);

/// Invoke from a wrapper API that has a deprecation entry in its manifest.
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

void _checkRange(
  String label,
  String version,
  Map<String, dynamic> support,
  DeprecationHandler warn,
) {
  final current = Version.parse(version);
  final minimum = Version.parse(support['minimum'] as String);
  final maximum = Version.parse(support['maximum_exclusive'] as String);
  if (current < minimum ||
      current >= maximum ||
      current.preRelease.isNotEmpty) {
    throw StateError(
      '$label $version is unsupported; required >= $minimum, < $maximum (stable releases)',
    );
  }
  final deprecated = support['deprecated_before'] as String?;
  if (deprecated != null && current < Version.parse(deprecated)) {
    warn(
      '$label $version is deprecated; upgrade to >= $deprecated before support is removed',
    );
  }
}

/// Each wrapper passes its own manifest; no Python-specific policy is assumed.
void verifyCompatibility({
  required Map<String, dynamic> policy,
  required Map<String, dynamic> wrapper,
  required String sdkVersion,
  required String coreVersion,
  required int abiVersion,
  required DeprecationHandler onDeprecation,
}) {
  if (policy['policy_schema_version'] != 1) {
    throw StateError('Unsupported native compatibility-policy schema');
  }
  if (!(wrapper['result_schema_versions'] as List<dynamic>).contains(
    policy['result_schema_version'],
  )) {
    throw StateError('Unsupported native result schema');
  }
  final major = abiVersion >> 16;
  final minor = abiVersion & 65535;
  final requiredAbi = wrapper['abi'] as Map<String, dynamic>;
  if (major != requiredAbi['major'] ||
      minor < (requiredAbi['minimum_minor'] as int)) {
    throw StateError(
      'ABI $major.$minor is incompatible with ${wrapper['name']} ${wrapper['version']}',
    );
  }
  if (major != policy['abi_major'] ||
      minor != policy['abi_minor'] ||
      coreVersion != policy['core_version']) {
    throw StateError('Native version metadata is inconsistent');
  }
  _checkRange(
    'Native core',
    coreVersion,
    wrapper['core'] as Map<String, dynamic>,
    onDeprecation,
  );
  _checkRange(
    'SDK',
    sdkVersion,
    policy['sdk_support'] as Map<String, dynamic>,
    onDeprecation,
  );
  final registered =
      (policy['wrappers'] as Map<String, dynamic>)[wrapper['name']];
  if (registered == null) {
    throw StateError(
      'Wrapper ${wrapper['name']} is not registered in this core release',
    );
  }
  _checkRange(
    '${wrapper['name']} wrapper',
    wrapper['version'] as String,
    registered['support'] as Map<String, dynamic>,
    onDeprecation,
  );
}
