"""Legacy command facade. Device protocol implementation is in the native core."""
from .port_scanner import find_all_com_ports
from .native import get_core
from .versions import warn_deprecated_api


def scan(transport=None):
    return find_all_com_ports(transport=transport)


class _Commands(type):
    def __getattr__(cls, name):
        if name.startswith('_'):
            raise AttributeError(name)
        try:
            command = get_core().command(name)
        except ValueError as error:
            raise AttributeError(name) from error
        return command.decode('ascii') if name.endswith('_V1') else list(command)


class ATCommands(metaclass=_Commands):
    @classmethod
    def build_command(cls, header, cmd_type, data_high=0, data_low=0):
        warn_deprecated_api('ATCommands.build_command')
        return list(get_core().command('_legacy_fixed', payload=bytes([header,cmd_type,data_high,data_low])))

    @classmethod
    def build_variable_command(cls, header, cmd_type, data_bytes):
        warn_deprecated_api('ATCommands.build_variable_command')
        return list(get_core().command('_legacy_variable', payload=bytes([header,cmd_type])+bytes(data_bytes)))

    @classmethod
    def set_min_weight(cls, weight_kg):
        return list(get_core().command('set_min_weight', value=weight_kg))

    @classmethod
    def set_measurement_count(cls, count):
        return list(get_core().command('set_measurement_count', value=count))

    @classmethod
    def set_measurement_interval(cls, interval_ms):
        return list(get_core().command('set_measurement_interval', value=interval_ms))

    @classmethod
    def set_wifi_credentials(cls, ssid, password):
        return list(get_core().command('set_wifi_credentials', payload=ssid.encode('utf-8')+b','+password.encode('utf-8')))

    @classmethod
    def start_single_measurement(cls, user_type=1, user_id=1):
        return list(get_core().command('start_single_measurement', payload=bytes([user_type,user_id])))

    @classmethod
    def stop_single_measurement(cls):
        return list(get_core().command('stop_single_measurement'))

    @classmethod
    def create_weight_scale(cls, weight_value):
        return list(get_core().command('create_weight_scale', value=weight_value))
