"""Shared test license: an SDK key activated for this host's PC, signed with the TEST key.

Raw and processed grids, full-rate frames and full report exports need full mode, so tests of
those features use this key. It is signed at the real current time, so calls that read the
wall clock themselves see full mode.
"""
import time
import pytest
from fisica_sdk import NativeEntitlement
from tools.entitlement_dev import issue_token

DAY = 86400


def host_target():
    """This test process's target: a Python script is bound to the PC."""
    with NativeEntitlement() as context:
        return context.status()['target']


def full_key(now=None, **fields):
    now = int(time.time()) if now is None else now
    seat = dict(key='key_test_1', bind_type='pc', bind=host_target(), key_until=now + 90 * DAY,
                full_until=now + 14 * DAY, track='trial')
    seat.update(fields)
    return issue_token('acme', 'ctr-1', now, **seat)


@pytest.fixture(scope='session')
def full_license():
    context = NativeEntitlement()
    assert context.import_token(full_key()) == 1
    yield context
    context.close()
