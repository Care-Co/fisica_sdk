"""Regenerate public Flutter version constants from the wrapper release manifest."""
import json
from native_build import ROOT, release_metadata

release = release_metadata(validate_flutter=False)
manifest = release['wrappers']['flutter']
version_file = ROOT / 'wrappers' / 'flutter' / 'lib' / 'src' / 'version.dart'
version_file.write_text(
    '// Generated from sdk-release.json and wrappers/flutter/wrapper.json.\n'
    '// Regenerate with: python tools/sync_flutter_metadata.py\n'
    + "const sdkVersion = '" + release['sdk_version'] + "';\n"
    + "const wrapperManifestJson = r'''" + json.dumps(manifest,separators=(',',':')) + "''';\n"
)
