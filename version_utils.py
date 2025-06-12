# carenco/version_utils.py

import warnings
import logging

# Python 3.8+ 표준 모듈
try:
    from importlib.metadata import version as get_version, PackageNotFoundError
except ImportError:
    # Python <3.8
    from importlib_metadata import version as get_version, PackageNotFoundError

import requests
from packaging import version

logger = logging.getLogger(__name__)

def check_for_update():
    try:
        installed = get_version("carenco")
    except PackageNotFoundError:
        # 패키지가 설치되지 않았거나 이름이 다를 때
        return

    try:
        resp = requests.get("https://pypi.org/pypi/carenco/json", timeout=2)
        resp.raise_for_status()
        latest = resp.json().get("info", {}).get("version")
        if latest and version.parse(installed) < version.parse(latest):
            msg = (
                f"carenco {installed} is out of date; latest is {latest}.\n"
                "Please upgrade via:\n"
                "    pip install --upgrade carenco"
            )
            warnings.warn(msg, UserWarning, stacklevel=3)
            logger.warning(msg)
    except Exception as e:
        # 네트워크 에러 등 무시
        logger.debug(f"version check failed: {e}")