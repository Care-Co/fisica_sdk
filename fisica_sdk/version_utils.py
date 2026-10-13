# fisica_sdk/version_utils.py
import warnings
import logging
from importlib.metadata import version as get_version, PackageNotFoundError
from packaging import version

logger = logging.getLogger(__name__)

def check_for_update():
    """Optional online update check; never invoked by import or initialization."""
    try:
        installed = get_version("fisica")
    except PackageNotFoundError:
        return

    try:
        import requests
        resp = requests.get("https://pypi.org/pypi/fisica/json", timeout=2)
        resp.raise_for_status()
        latest = resp.json().get("info", {}).get("version")
        if latest and version.parse(installed) < version.parse(latest):
            msg = (
                f"fisica {installed} is out of date; latest is {latest}.\n"
                "Please upgrade via:\n"
                "    pip install --upgrade fisica"
            )
            warnings.warn(msg, UserWarning, stacklevel=3)
            logger.warning(msg)
    except Exception as e:
        logger.debug(f"version check failed: {e}")
