"""Unit test conftest.py - stubs missing dependencies before app imports."""

import sys
from unittest.mock import MagicMock

_STUB_MODULES = [
    "pandas",
    "pytz",
    "inewave",
    "inewave.newave",
    "idecomp",
    "idecomp.decomp",
    "idessem",
    "boto3",
    "botocore",
    "cfinterface",
    "cfinterface.components",
    "cfinterface.components.defaultblock",
]

for _mod_name in _STUB_MODULES:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

sys.modules["inewave"].newave = sys.modules["inewave.newave"]
sys.modules["cfinterface"].components = sys.modules["cfinterface.components"]
sys.modules["cfinterface.components"].defaultblock = sys.modules[
    "cfinterface.components.defaultblock"
]
