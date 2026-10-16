"""Public Python wrapper. Import performs no network or device operations."""
from .native import NativeCore, NativeSession, NativeEntitlement
from .versions import CoreCompatibilityError, FisicaDeprecationWarning, local_release
from .errors import (FisicaError, FisicaErrorCode, FisicaTransportError, FisicaFirmwareWarning,
                     FisicaCommandError, FisicaEntitlementWarning, FisicaLicenseError)
from .entitlement import Entitlement
from .transport import ByteTransport, MockTransport

__version__ = local_release()['wrappers']['python']['version']
__all__ = ['FisicaSDK', 'NativeCore', 'NativeSession', 'NativeEntitlement', 'Entitlement',
           'FisicaEntitlementWarning', 'FisicaLicenseError', 'FisicaCommandError', 'Visualizer', 'VisualOptions',
           'CoreCompatibilityError', 'FisicaDeprecationWarning', 'FisicaError',
           'FisicaErrorCode', 'FisicaTransportError', 'FisicaFirmwareWarning',
           'ByteTransport', 'MockTransport', '__version__']


def __getattr__(name):
    if name == 'FisicaSDK':
        from .fisica_sdk import FisicaSDK
        return FisicaSDK
    if name in ('Visualizer', 'VisualOptions'):
        from . import visualizer
        return getattr(visualizer, name)
    raise AttributeError(name)
