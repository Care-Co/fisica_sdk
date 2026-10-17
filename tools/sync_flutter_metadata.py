"""Regenerate public Flutter version constants from the wrapper release manifest.

    python tools/sync_flutter_metadata.py           rewrite lib/src/version.dart when it differs
    python tools/sync_flutter_metadata.py --check   write nothing; exit 1 when it is stale
"""
import argparse
import json
import sys
from native_build import ROOT, release_metadata

VERSION_FILE = ROOT / 'wrappers' / 'flutter' / 'lib' / 'src' / 'version.dart'


def expected():
    release = release_metadata(validate_flutter=False)
    manifest = release['wrappers']['flutter']
    return ('// Generated from sdk-release.json and wrappers/flutter/wrapper.json.\n'
            '// Regenerate with: python tools/sync_flutter_metadata.py\n'
            + "const sdkVersion = '" + release['sdk_version'] + "';\n"
            + "const wrapperManifestJson = r'''" + json.dumps(manifest, separators=(',', ':')) + "''';\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--check', action='store_true', help='write nothing; exit 1 when version.dart differs')
    args = parser.parse_args(argv)
    text = expected()
    stale = not VERSION_FILE.is_file() or VERSION_FILE.read_text() != text
    if stale:
        print(('stale: ' if args.check else 'updated: ') + VERSION_FILE.relative_to(ROOT).as_posix())
        if not args.check:
            VERSION_FILE.write_text(text)
    return 1 if args.check and stale else 0


if __name__ == '__main__':
    sys.exit(main())
