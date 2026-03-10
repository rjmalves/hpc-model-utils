"""Root conftest.py — shared pytest configuration.

Adds the system Python site-packages directory to sys.path so that packages
installed system-wide (e.g. click) are available in the isolated venv used
for unit testing, without requiring network access to reinstall them.
"""

import sys

_SYSTEM_SITE_PACKAGES = "/usr/lib/python3/dist-packages"

if _SYSTEM_SITE_PACKAGES not in sys.path:
    sys.path.insert(0, _SYSTEM_SITE_PACKAGES)
