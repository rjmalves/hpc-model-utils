"""Models layer: typed wrappers around the inewave and idecomp libraries.

``PLUGINS`` is the explicit, in-repo plugin registry (R27) -- no
module scanning, no entry points: ``NewavePlugin()`` (ticket-046) and
``DecompPlugin()`` (ticket-050).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from hpc_model_utils.core.errors import UsageError
from hpc_model_utils.core.plugin import ModelPlugin
from hpc_model_utils.models.decomp import DecompPlugin
from hpc_model_utils.models.newave import NewavePlugin

PLUGINS: Mapping[str, ModelPlugin] = MappingProxyType(
    {"decomp": DecompPlugin(), "newave": NewavePlugin()}
)


def get_plugin(name: str) -> ModelPlugin:
    plugin = PLUGINS.get(name.lower())
    if plugin is None:
        raise UsageError(f"unknown model {name!r}; valid: {sorted(PLUGINS)}")
    return plugin
