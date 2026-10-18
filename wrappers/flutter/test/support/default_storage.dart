// Run by license_test.dart in a process whose HOME, USERPROFILE, LOCALAPPDATA
// and XDG_STATE_HOME point at a temporary folder: without storageDirectory the
// key goes to the default directory, and a new instance without a key loads it.
// Prints that directory.
import 'dart:convert';
import 'dart:io';
import 'package:fisica_core/fisica_core.dart';

void check(bool condition, String message) {
  if (!condition) throw StateError(message);
}

List<String> stateFiles(String directory) {
  final folder = Directory(directory);
  if (!folder.existsSync()) return [];
  return [
    for (final file in folder.listSync().whereType<File>())
      if (file.uri.pathSegments.last.startsWith('entitlement-') &&
          file.uri.pathSegments.last.endsWith('.state'))
        file.uri.pathSegments.last,
  ]..sort();
}

void main() {
  final core = FisicaCore.open(
    Platform.environment['FISICA_CORE_TEST_LIBRARY']!,
  );
  final vectors =
      jsonDecode(
            File(
              Platform.environment['FISICA_LICENSE_VECTORS']!,
            ).readAsStringSync(),
          )
          as Map<String, dynamic>;
  final now = vectors['now'] as int;
  final full = vectors['full'] as String;
  final directory = FisicaEntitlement.defaultStorageDirectory;
  if (directory == null) throw StateError('No default storage directory here');

  final first = FisicaEntitlement(core, clock: () => now);
  check(first.status().reason == FisicaModeReason.noKey, 'nothing stored yet');
  check(
    first.importToken(full) == FisicaEntitlementOutcome.accepted &&
        first.lastSyncError == null,
    'import: ${first.lastSyncError}',
  );
  first.close();
  final stored = stateFiles(directory);
  check(stored.length == 1, 'expected one state file in $directory: $stored');

  final later = FisicaEntitlement(core, clock: () => now);
  check(
    later.status().isFull && later.status().key == 'key_test_1',
    'the stored key was not found again',
  );
  later.close();

  // null keeps the key in memory only: nothing is read or written.
  final memory = FisicaEntitlement(core, clock: () => now, storageDirectory: null);
  check(memory.status().reason == FisicaModeReason.noKey, 'memory only read the stored key');
  check(memory.importToken(full) == FisicaEntitlementOutcome.accepted, 'memory import');
  memory.close();
  check(
    stateFiles(directory).join(',') == stored.join(','),
    'a memory-only license wrote to $directory',
  );
  stdout.writeln(directory);
}
