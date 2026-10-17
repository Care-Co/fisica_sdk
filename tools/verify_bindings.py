"""Run each language binding's contract tests on this host against a freshly built core.

Hardware-free: shared vectors plus Mock transports. A passing run is not a device,
Android/iOS runtime or real Windows verification.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from native_build import ROOT, build_native, prepare_release

FIXTURES = ROOT / 'tests' / 'fixtures'


def run(command, **options):
    print('+', ' '.join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], check=True, **options)


def host_core(output):
    return build_native(output / 'core')


def license_vectors():
    """Shared vectors plus keys activated for this host's PC (command-line test programs bind to the PC)."""
    from entitlement_dev import host_pc_vectors
    values = {key: value for key, value in json.loads((FIXTURES / 'entitlement.json').read_text()).items() if key != 'note'}
    values.update(host_pc_vectors(values['now']))
    return values


@contextmanager
def entitlement_server(output):
    """Reference mock developer portal for the online part of each binding's contract.

    It holds the shared vectors' unactivated key (key_test_1, a 14-day trial). A binding
    imports `unbound` with this endpoint, activates it for this PC and renews it. Then,
    without a key and with its own storage directory, it pairs: the server approves every
    code at once with key_pair_1, and a new instance must find that stored key. Yields the
    values a binding needs; afterwards checks what the server received: activation and
    pairing requests carrying exactly this PC's target, pairing polls carrying only the
    device code, and empty refreshes, nothing else, so no binding sends usage or activity data.
    """
    from entitlement_dev import MockEntitlementServer
    vectors = license_vectors()
    now = vectors['now'] + 86400
    server = MockEntitlementServer(clock=lambda: now)
    server.add_contract('acme', 'ctr-1', track='trial', full_until=vectors['now'] + 14 * 86400)
    server.add_key('key_test_1', 'acme', 'ctr-1', days=90)
    server.add_key('key_pair_1', 'acme', 'ctr-1', days=90)
    server.on_pairing = lambda code: server.approve(code, 'key_pair_1')  # an owner approves at once
    storage = output / 'storage'
    shutil.rmtree(storage, ignore_errors=True)
    target = {'bind_type': vectors['target_type'], 'bind': vectors['target']}
    try:
        yield {'endpoint': server.start(), 'storage': storage.as_posix(), 'server_now': now}
        assert any(path.endswith('/activate') for path, _ in server.requests), 'the binding never activated the key'
        assert any(path == '/sdk/v1/pairing/token' for path, _ in server.requests), 'the binding never paired'
        unexpected = [(path, body) for path, body in server.requests
                      if not ((path == '/sdk/v1/entitlement/refresh' and body == {}) or
                              (path in ('/sdk/v1/entitlement/activate', '/sdk/v1/pairing') and body == target) or
                              (path == '/sdk/v1/pairing/token' and set(body) == {'device_code'}))]
        assert not unexpected, f'binding sent more than its target, device codes and empty refreshes: {unexpected}'
        print('mock server: only activation and pairing for this PC, pairing polls and empty refreshes received')
    finally:
        server.stop()


def vectors_json(output):
    """License vectors (shared plus this PC's) as JSON, for bindings that read FISICA_LICENSE_VECTORS."""
    path = output / 'license-vectors.json'
    path.write_text(json.dumps(license_vectors(), indent=2) + '\n')
    return path


def flat_fixtures(output, extra=None):
    """Key=value copy of the shared vectors for hosts without a JSON library."""
    packets = json.loads((FIXTURES / 'legacy-processing.json').read_text())
    values = license_vectors()
    values['packet'] = next(item['packet_hex'] for item in packets if item.get('id') == 'synthetic-recorded-layout')
    values.update(extra or {})
    path = output / 'vectors.properties'
    path.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
    return path


def native(output):
    """Public C ABI contracts under UBSan (and ASan where its runtime works on the host)."""
    generated = output / 'generated'
    prepare_release(generated)
    vectors = [f'{key}={value}' for key, value in license_vectors().items()]
    sources = sorted((ROOT / 'native/src').glob('*.cpp'))
    frameworks = ['-framework', 'CoreFoundation'] if sys.platform == 'darwin' else []
    for sanitizer in os.environ.get('FISICA_SANITIZERS', 'undefined').split():
        for name, arguments in (('native_contract', []), ('native_entitlement', vectors)):
            binary = output / f'{name}-{sanitizer.replace(",", "-")}'
            run([os.environ.get('CXX', 'c++'), '-std=c++17', '-O1', '-g', '-UNDEBUG', '-ffp-contract=off', f'-fsanitize={sanitizer}',
                 '-fno-sanitize-recover=all', '-DFISICA_CORE_STATIC', '-I', ROOT / 'native/include', '-I', generated,
                 *sources, ROOT / 'tests' / f'{name}.cpp', *frameworks, '-o', binary])
            run([binary, *arguments], timeout=300)


def kotlin(output):
    gradle = Path(os.environ.get('FISICA_GRADLE_LIB', '/opt/homebrew/opt/gradle/libexec/lib'))
    java_home = Path(os.environ.get('JAVA_HOME') or subprocess.check_output(['/usr/libexec/java_home'], text=True).strip())
    stdlib = next(gradle.glob('kotlin-stdlib-[0-9]*.jar'))
    generated = output / 'generated'
    prepare_release(generated)
    system = {'Darwin': 'darwin', 'Linux': 'linux'}.get(platform.system(), 'win32')
    library = output / ('libfisica_jni.dylib' if system == 'darwin' else 'libfisica_jni.so')
    run([os.environ.get('CXX', 'c++'), '-std=c++17', '-O2', '-shared', '-fPIC', '-ffp-contract=off', '-DFISICA_CORE_STATIC',
         '-I', ROOT / 'native/include', '-I', generated, '-I', java_home / 'include', '-I', java_home / 'include' / system,
         *sorted((ROOT / 'native/src').glob('*.cpp')), ROOT / 'wrappers/kotlin/bridge/fisica_jni.cpp',
         *(['-framework', 'CoreFoundation'] if system == 'darwin' else []), '-o', library])
    sources = sorted((ROOT / 'wrappers/kotlin/src/main/kotlin').rglob('*.kt'))
    compiler = ['java', '-cp', str(gradle / '*'), 'org.jetbrains.kotlin.cli.jvm.K2JVMCompiler', '-no-stdlib', '-no-reflect', '-jvm-target', '1.8']
    jar = output / 'fisica-kotlin-test.jar'
    # BleTransport needs the Android SDK; everything else must also run on a plain JVM.
    run([*compiler, '-classpath', stdlib, '-d', jar, *[s for s in sources if s.name != 'BleTransport.kt'], ROOT / 'tests/kotlin_client.kt'])
    android = Path(os.environ.get('ANDROID_SDK_ROOT', Path.home() / 'Library/Android/sdk')) / 'platforms/android-35/android.jar'
    if android.is_file():
        run([*compiler, '-classpath', f'{stdlib}{os.pathsep}{android}', '-d', output / 'fisica-kotlin-android.jar', *sources])
    else:
        print('Android SDK not found: BleTransport.kt was not compiled')
    classpath = os.pathsep.join(str(p) for p in [jar, stdlib, *gradle.glob('groovy-[0-9]*.jar'), *gradle.glob('groovy-json-*.jar')])
    run(['java', '-cp', classpath, 'groovy.ui.GroovyMain', ROOT / 'tests/kotlin_contract.groovy', library, FIXTURES, vectors_json(output)])
    with entitlement_server(output) as online:
        run(['java', '-cp', classpath, 'Kotlin_clientKt', library, flat_fixtures(output, online)])


def swift(output):
    with entitlement_server(output) as online:
        run(['swift', 'test'], cwd=ROOT / 'wrappers/swift', env={
            **os.environ, 'FISICA_FIXTURES': str(FIXTURES), 'FISICA_LICENSE_VECTORS': str(vectors_json(output)),
            'FISICA_ENTITLEMENT_ENDPOINT': online['endpoint'],
            'FISICA_ENTITLEMENT_STORAGE': online['storage'], 'FISICA_ENTITLEMENT_NOW': str(online['server_now'])})


def dotnet(output):
    core = host_core(output)
    executable = os.environ.get('DOTNET', shutil.which('dotnet') or str(ROOT / '.build/dotnet/dotnet'))
    # Keep the SDK's first-run setup (telemetry, developer certificate) out of the user's profile.
    environment = {**os.environ, 'DOTNET_CLI_HOME': str(output / 'home'), 'DOTNET_NOLOGO': '1', 'DOTNET_CLI_TELEMETRY_OPTOUT': '1',
                   'DOTNET_GENERATE_ASPNET_CERTIFICATE': 'false', 'NUGET_PACKAGES': str(output / 'nuget')}
    # The WinRT BLE transport only exists for the Windows target; compile it on every host.
    run([executable, 'build', ROOT / 'wrappers/dotnet/Fisica.Scale.csproj', '-f', 'net8.0-windows10.0.19041.0', '-v', 'quiet', '-nologo'], env=environment)
    with entitlement_server(output) as online:
        run([executable, 'run', '--project', ROOT / 'tests/dotnet/Contract.csproj', '--', core, FIXTURES, flat_fixtures(output, online)], env=environment)


def dart(output):
    core = host_core(output)
    executable = os.environ.get('DART', shutil.which('dart') or 'dart')
    with entitlement_server(output) as online:
        environment = {**os.environ, 'FISICA_CORE_TEST_LIBRARY': str(core), 'FISICA_LICENSE_VECTORS': str(vectors_json(output)),
                       'FISICA_ENTITLEMENT_ENDPOINT': online['endpoint'],
                       'FISICA_ENTITLEMENT_STORAGE': online['storage'], 'FISICA_ENTITLEMENT_NOW': str(online['server_now'])}
        for command in (['pub', 'get'], ['analyze'], ['test']):
            run([executable, *command], cwd=ROOT / 'wrappers/flutter', env=environment)


def main():
    targets = {'native': native, 'kotlin': kotlin, 'swift': swift, 'dotnet': dotnet, 'dart': dart}
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('languages', nargs='+', choices=sorted(targets))
    parser.add_argument('--output', type=Path, default=ROOT / '.build' / 'binding-verification')
    args = parser.parse_args()
    for name in args.languages:
        directory = args.output / name
        directory.mkdir(parents=True, exist_ok=True)
        targets[name](directory)
        print(f'{name}: binding contract passed', flush=True)


if __name__ == '__main__':
    main()
