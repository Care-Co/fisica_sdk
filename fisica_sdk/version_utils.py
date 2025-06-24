# fisica_sdk/version_utils.py
import warnings
import logging
from importlib.metadata import version as get_version, PackageNotFoundError
import requests
from packaging import version

logger = logging.getLogger(__name__)

def check_for_update():
    try:
        installed = get_version("fisica-sdk")
    except PackageNotFoundError:
        return

    try:
        resp = requests.get("https://pypi.org/pypi/fisica-sdk/json", timeout=2)
        resp.raise_for_status()
        latest = resp.json().get("info", {}).get("version")
        if latest and version.parse(installed) < version.parse(latest):
            msg = (
                f"fisica_sdk {installed} is out of date; latest is {latest}.\n"
                "Please upgrade via:\n"
                "    pip install --upgrade fisica-sdk"
            )
            warnings.warn(msg, UserWarning, stacklevel=3)
            logger.warning(msg)
    except Exception as e:
        logger.debug(f"version check failed: {e}")