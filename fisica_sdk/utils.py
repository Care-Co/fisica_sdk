import logging
logger = logging.getLogger(__name__)
import sys

_levelToName = {
    logging.CRITICAL: 'CRITICAL',
    logging.ERROR: 'ERROR',
    logging.WARNING: 'WARNING',
    logging.INFO: 'INFO',
    logging.DEBUG: 'DEBUG',
    logging.NOTSET: 'NOTSET',
}

class ColorFormatter(logging.Formatter):
    COLOR_MAP = {
        'DEBUG': "\033[37m",     # Light gray
        'INFO': "\033[36m",      # Cyan
        'WARNING': "\033[33m",   # Yellow
        'ERROR': "\033[31m",     # Red
        'CRITICAL': "\033[1;31m" # Bold red
    }
    RESET = "\033[0m"

    def format(self, record):
        message = super().format(record)
        color = self.COLOR_MAP.get(record.levelname, "")
        return f"{color}{message}{self.RESET}"

# Configure logging with color support
handler = logging.StreamHandler(sys.stdout)
formatter = ColorFormatter("[%(asctime)s] [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
handler.setFormatter(formatter)

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.handlers = [handler]

class LogWrapper:
    def __init__(self, logger):
        self.logger = logger

    def debug(self, *args):
        self.logger.debug(" ".join(str(arg) for arg in args))

    def info(self, *args):
        self.logger.info(" ".join(str(arg) for arg in args))

    def warning(self, *args):
        self.logger.warning(" ".join(str(arg) for arg in args))

    def error(self, *args):
        self.logger.error(" ".join(str(arg) for arg in args))

    def critical(self, *args):
        self.logger.critical(" ".join(str(arg) for arg in args))

    @property
    def level(self):
        return self.logger.level

    @level.setter
    def level(self, value):
        self.logger.setLevel(value)

log = LogWrapper(logging.getLogger(__name__))
