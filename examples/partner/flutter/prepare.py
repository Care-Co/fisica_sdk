"""Create public Flutter host scaffolding and set Bluetooth/serial permissions.

Does not compile or copy private core sources. Run before flutter pub get.
"""
import argparse
import json
from pathlib import Path
import plistlib
import re
import subprocess
import xml.etree.ElementTree as ET

# Xcode 27 rejects macOS deployment targets below 12.0, also in plugin pods that declare lower ones.
POD_DEPLOYMENT_TARGET = """    target.build_configurations.each do |config|
      config.build_settings['MACOSX_DEPLOYMENT_TARGET'] = '12.0' if config.build_settings['MACOSX_DEPLOYMENT_TARGET'].to_f < 12.0
    end
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--flutter', default='flutter', help='Path to the Flutter executable')
    parser.add_argument('--sdk', type=Path, help='Directory containing flutter/ and flutter_transport/ packages')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    saved = {name: (root / name).read_bytes() for name in ('pubspec.yaml', 'lib/main.dart')}
    try:
        subprocess.run([args.flutter, 'create', '--no-pub', '--empty', '--platforms=windows,macos,android,ios',
                        '--project-name=fisica_partner_example', '--org=example.fisica', '.'], cwd=root, check=True)
    finally:
        for name, data in saved.items(): (root / name).write_bytes(data)
    if args.sdk:
        transport = args.sdk.resolve() / 'flutter_transport'
        if not (transport / 'pubspec.yaml').is_file(): parser.error('--sdk must contain flutter_transport/pubspec.yaml')
        pubspec = (root / 'pubspec.yaml').read_text()
        pubspec = pubspec.replace('path: ../../../wrappers/flutter_transport', 'path: ' + repr(transport.as_posix()))
        (root / 'pubspec.yaml').write_text(pubspec)

    (root / 'analysis_options.yaml').write_text('analyzer:\n  exclude:\n    - build/**\n')
    android = '{http://schemas.android.com/apk/res/android}'
    ET.register_namespace('android', 'http://schemas.android.com/apk/res/android')
    manifest = root / 'android/app/src/main/AndroidManifest.xml'
    tree = ET.parse(manifest)
    permissions = {
        'BLUETOOTH': {'maxSdkVersion': '30'}, 'BLUETOOTH_ADMIN': {'maxSdkVersion': '30'},
        'ACCESS_FINE_LOCATION': {'maxSdkVersion': '30'},
        'BLUETOOTH_SCAN': {'usesPermissionFlags': 'neverForLocation'}, 'BLUETOOTH_CONNECT': {},
    }
    existing = {item.get(android + 'name') for item in tree.getroot().findall('uses-permission')}
    for name, attributes in permissions.items():
        full = 'android.permission.' + name
        if full not in existing:
            ET.SubElement(tree.getroot(), 'uses-permission', {android + 'name': full,
                **{android + key: value for key, value in attributes.items()}})
    tree.write(manifest, encoding='unicode')
    gradle = root / 'android/app/build.gradle.kts'
    if gradle.exists():
        gradle.write_text(re.sub(r'minSdk\s*=\s*flutter.minSdkVersion', 'minSdk = 23', gradle.read_text()))

    for platform in ('ios', 'macos'):
        plist = root / platform / 'Runner/Info.plist'
        data = plistlib.loads(plist.read_bytes())
        data.update(NSBluetoothAlwaysUsageDescription='Connect to your Fisica Scale for measurement.',
                    NSBluetoothPeripheralUsageDescription='Connect to your Fisica Scale for measurement.')
        plist.write_bytes(plistlib.dumps(data))
    for name in ('DebugProfile.entitlements', 'Release.entitlements'):
        path = root / 'macos/Runner' / name
        data = plistlib.loads(path.read_bytes())
        data.update({'com.apple.security.device.bluetooth': True, 'com.apple.security.device.serial': True})
        path.write_bytes(plistlib.dumps(data))
    macos_podfile = root / 'macos/Podfile'
    if not macos_podfile.exists():
        # flutter create leaves the Podfile to the first build; start from the same Flutter template now.
        version = subprocess.run([args.flutter, '--version', '--machine'], cwd=root, check=True, capture_output=True, text=True)
        template = Path(json.loads(version.stdout)['flutterRoot']) / 'packages/flutter_tools/templates/cocoapods/Podfile-macos'
        macos_podfile.write_text(template.read_text())
    for platform, key, minimum in (('ios', 'IPHONEOS_DEPLOYMENT_TARGET', 13.0), ('macos', 'MACOSX_DEPLOYMENT_TARGET', 12.0)):
        project = root / platform / 'Runner.xcodeproj/project.pbxproj'
        def raise_target(match):
            version = max(float(match.group(1)), minimum)
            return key + ' = ' + str(version)
        project.write_text(re.sub(key + r' = (\d+\.\d+)', raise_target, project.read_text()))
        podfile = root / platform / 'Podfile'
        if podfile.exists():
            podfile.write_text(re.sub(r"(#\s*)?platform :(ios|osx), '[\d.]+'",
                lambda match: "platform :" + match.group(2) + ", '" + str(minimum) + "'", podfile.read_text()))
    podfile = macos_podfile.read_text()
    if 'MACOSX_DEPLOYMENT_TARGET' not in podfile:
        anchor = '    flutter_additional_macos_build_settings(target)\n'
        if anchor not in podfile: parser.error('macos/Podfile post_install must raise pod MACOSX_DEPLOYMENT_TARGET to 12.0')
        macos_podfile.write_text(podfile.replace(anchor, anchor + POD_DEPLOYMENT_TARGET))
    print('Host scaffold ready. Run flutter pub get, then flutter run -d <device>.')


if __name__ == '__main__': main()
