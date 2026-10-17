import logging
import os, datetime

# The library is quiet by default: records go to the application's logging configuration.
log = logging.getLogger("fisica_sdk")
log.addHandler(logging.NullHandler())
_console = None


def enable_console_logging(level=logging.DEBUG):
    """Print this library's records at level and above on stderr (FisicaSDK(debug=...)); other loggers are untouched."""
    global _console
    if _console is None:
        _console = logging.StreamHandler()
        _console.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
        log.addHandler(_console)
    _console.setLevel(level)
    if log.level == logging.NOTSET or log.level > level:
        log.setLevel(level)


def resolve_output_path(output: str) -> str:
    if not output:
        filename = f"session_report_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}.json"
        log.debug(f"filename set to {filename}")
        return os.path.join(os.getcwd(), filename)

    output = os.path.abspath(output)

    if os.path.isdir(output):
        filename = f"session_report_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}.json"
        return os.path.join(output, filename)

    if not os.path.exists(output):
        if not os.path.splitext(output)[1]:
            output += ".json"
        return output

    return output
