"""Public, offline release/compatibility policy shared by wrapper maintainers."""
import json
from pathlib import Path
import warnings
from packaging.version import Version


class CoreCompatibilityError(RuntimeError):
    """The wrapper cannot safely call this core or SDK release."""


class FisicaDeprecationWarning(UserWarning):
    """Visible by default, unlike Python's normally hidden DeprecationWarning."""


def local_release():
    package = Path(__file__).resolve().parent
    installed = package / '_release.json'
    if installed.is_file():
        return json.loads(installed.read_text())
    root = package.parent
    release = json.loads((root / 'sdk-release.json').read_text())
    release['wrappers'] = {
        path.parent.name: json.loads(path.read_text())
        for path in (root / 'wrappers').glob('*/wrapper.json')
    }
    return release


def _check_range(label, version, support):
    current = Version(version)
    minimum = Version(support['minimum'])
    maximum = Version(support['maximum_exclusive'])
    if current < minimum or current >= maximum or current.is_prerelease:
        raise CoreCompatibilityError(
            f'{label} {version} is unsupported; required >= {minimum}, < {maximum} (stable releases)'
        )
    deprecated = support.get('deprecated_before')
    if deprecated and current < Version(deprecated):
        warnings.warn(
            f'{label} {version} is deprecated; upgrade to >= {deprecated} before support is removed',
            FisicaDeprecationWarning, stacklevel=4,
        )


def check_compatibility(policy, wrapper, sdk_version, core_version, abi_version):
    if policy.get('policy_schema_version') != 1:
        raise CoreCompatibilityError('Unsupported native compatibility-policy schema')
    if policy.get('result_schema_version') not in wrapper['result_schema_versions']:
        raise CoreCompatibilityError('Unsupported native result schema')
    required = wrapper['abi']
    major, minor = abi_version >> 16, abi_version & 65535
    if major != required['major'] or minor < required['minimum_minor']:
        raise CoreCompatibilityError(
            f"ABI {major}.{minor} is incompatible with {wrapper['name']} {wrapper['version']}; "
            f"required {required['major']}.{required['minimum_minor']} or newer minor"
        )
    if (major, minor) != (policy['abi_major'], policy['abi_minor']) or core_version != policy['core_version']:
        raise CoreCompatibilityError('Native version metadata is inconsistent')
    _check_range('Native core', core_version, wrapper['core'])
    _check_range('SDK', sdk_version, policy['sdk_support'])
    supported = policy['wrappers'].get(wrapper['name'])
    if supported is None:
        raise CoreCompatibilityError(f"Wrapper {wrapper['name']!r} is not registered in this core release")
    _check_range(f"{wrapper['name']} wrapper", wrapper['version'], supported['support'])


def warn_deprecated_api(name):
    wrapper = local_release()['wrappers']['python']
    rule = wrapper['deprecated_apis'].get(name)
    if not rule or Version(wrapper['version']) < Version(rule['since']):
        return
    message = (
        f"{name} is deprecated since Python wrapper {rule['since']}; "
        f"removed in {rule['removed_in']}. Use {rule['replacement']}"
    )
    if Version(wrapper['version']) >= Version(rule['removed_in']):
        raise CoreCompatibilityError(message)
    warnings.warn(message, FisicaDeprecationWarning, stacklevel=3)
