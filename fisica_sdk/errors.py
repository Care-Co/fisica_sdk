"""Stable error codes; messages are descriptive, never a branching contract."""
from enum import IntEnum


class FisicaErrorCode(IntEnum):
    OK = 0
    INVALID_ARGUMENT = 1
    BUFFER_TOO_SMALL = 2
    NO_RESULT = 3
    UNKNOWN_COMMAND = 4
    INTERNAL_ERROR = 5
    INVALID_STATE = 6
    BUSY = 7
    COMMAND_TIMEOUT = 8
    DEVICE_REJECTED = 9
    CANCELLED = 10
    TRANSPORT_LOST = 11
    INCOMPATIBLE_VERSION = 12
    FEATURE_NOT_LICENSED = 13
    TRANSPORT_ERROR = 1001


class FisicaError(Exception):
    def __init__(self, code, message):
        self.code = int(code)
        super().__init__(message)


class FisicaArgumentError(FisicaError, ValueError):
    pass


class FisicaBufferError(FisicaError, BufferError):
    pass


class FisicaRuntimeError(FisicaError, RuntimeError):
    pass


class FisicaLicenseError(FisicaRuntimeError):
    """The feature needs full mode: an SDK key activated for this app or PC."""


class FisicaTransportError(FisicaRuntimeError):
    def __init__(self, message):
        super().__init__(FisicaErrorCode.TRANSPORT_ERROR, message)


class FisicaFirmwareWarning(UserWarning):
    code = 'FIRMWARE_UNVERIFIED'


class FisicaEntitlementWarning(UserWarning):
    """License notice: basic mode, an ending trial or contract, or a key to renew. Never a technical error."""


class FisicaCommandError(FisicaRuntimeError):
    """Command result: stable SDK code plus the Scale's distinct 16-bit code."""
    def __init__(self, event):
        self.device_code = event['device_error']
        self.operation = event['operation']
        self.response = event['payload']
        super().__init__(event['status'],
                         f"Command operation {self.operation} failed: SDK code {event['status']}, device code {self.device_code}")
