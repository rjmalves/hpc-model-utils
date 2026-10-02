"""Models layer: typed wrappers around the inewave and idecomp libraries.

``PLUGINS`` is the explicit, in-repo plugin registry (R27) -- no
module scanning, no entry points. It starts empty by design;
ticket-046 and ticket-050 add ``NewavePlugin()`` and ``DecompPlugin()``.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from hpc_model_utils.core.errors import UsageError
from hpc_model_utils.core.plugin import ModelPlugin

PLUGINS: Mapping[str, ModelPlugin] = MappingProxyType({})


def get_plugin(name: str) -> ModelPlugin:
    plugin = PLUGINS.get(name.lower())
    if plugin is None:
        raise UsageError(f"unknown model {name!r}; valid: {sorted(PLUGINS)}")
    return plugin
