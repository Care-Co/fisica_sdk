from copy import deepcopy
import pytest
from fisica_sdk import NativeCore, FisicaDeprecationWarning, CoreCompatibilityError
from fisica_sdk.versions import check_compatibility


@pytest.fixture
def release():
    core = NativeCore()
    return deepcopy(core.policy), deepcopy(core.wrapper)


# Current values come from release metadata so a version bump never edits these tests.
CORE = NativeCore().core_version
ABI = NativeCore().abi_version
SDK = NativeCore().sdk_version


def test_current_version_supported(release):
    policy,wrapper = release
    check_compatibility(policy,wrapper,SDK,CORE,ABI)


def test_sdk_and_wrapper_deprecation_are_visible(release):
    policy,wrapper = release
    # A deprecation window like the ones minor releases open (3.0.0 itself has none).
    policy['sdk_support']['minimum'] = policy['wrappers']['python']['support']['minimum'] = '1.0.0'
    wrapper['version'] = '1.5.0'
    with pytest.warns(FisicaDeprecationWarning) as warnings:
        check_compatibility(policy,wrapper,'1.5.0',CORE,ABI)
    assert len(warnings) == 2
    assert 'SDK 1.5.0' in str(warnings[0].message)
    assert 'python wrapper 1.5.0' in str(warnings[1].message)


@pytest.mark.parametrize('version',['0.9.9','2.4.0','4.0.0','3.0.0rc1'])
def test_unsupported_sdk_stops_before_numeric_calls(release,version):
    policy,wrapper = release
    with pytest.raises(CoreCompatibilityError,match='SDK'):
        check_compatibility(policy,wrapper,version,CORE,ABI)


@pytest.mark.parametrize('abi',[0,65540,196608])
def test_incompatible_abi_is_rejected(release,abi):
    policy,wrapper = release
    with pytest.raises(CoreCompatibilityError,match='ABI'):
        check_compatibility(policy,wrapper,SDK,CORE,abi)


def test_future_wrapper_can_have_its_own_version_policy(release):
    policy,wrapper = release
    wrapper.update(name='custom',version='4.1.0')
    policy['wrappers']['custom'] = {'support': {'minimum':'4.0.0','deprecated_before':'4.1.0','maximum_exclusive':'5.0.0'}}
    check_compatibility(policy,wrapper,SDK,CORE,ABI)


def test_unknown_result_schema_is_rejected(release):
    policy,wrapper = release
    policy['result_schema_version'] = 2
    with pytest.raises(CoreCompatibilityError,match='result schema'):
        check_compatibility(policy,wrapper,SDK,CORE,ABI)


@pytest.fixture
def deprecated_api(monkeypatch):
    """A deprecation entry as wrapper.json would carry it (the list is empty in 3.0.0)."""
    import fisica_sdk.versions as versions
    manifest = versions.local_release()
    manifest['wrappers']['python']['deprecated_apis'] = {
        'Old.call': {'since': '3.0.0', 'removed_in': '4.0.0', 'replacement': 'New.call()'}}
    monkeypatch.setattr(versions, 'local_release', lambda: manifest)
    return versions, manifest


def test_deprecated_api_warns_with_replacement(deprecated_api):
    versions, _ = deprecated_api
    with pytest.warns(FisicaDeprecationWarning, match='removed in 4.0.0. Use New.call'):
        versions.warn_deprecated_api('Old.call')


def test_removed_api_fails(deprecated_api):
    versions, manifest = deprecated_api
    manifest['wrappers']['python']['version'] = '4.0.0'
    with pytest.raises(CoreCompatibilityError, match='removed in 4.0.0'):
        versions.warn_deprecated_api('Old.call')
