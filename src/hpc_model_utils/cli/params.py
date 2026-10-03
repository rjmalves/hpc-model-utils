"""ADR-004: the case-insensitive ``MODEL`` argument type, resolved at
parse time through ``models.get_plugin`` so a registry swapped by a
test (``tests.support.fake_plugin.fake_registered``) is honoured.
"""

from __future__ import annotations

import click

from hpc_model_utils.core.errors import UsageError
from hpc_model_utils.core.plugin import ModelPlugin
from hpc_model_utils.models import PLUGINS, get_plugin


class ModelArg(click.ParamType[ModelPlugin]):
    name = "model"

    def get_metavar(
        self, param: click.Parameter, ctx: click.Context
    ) -> str | None:
        return "|".join(sorted(PLUGINS))

    def convert(
        self,
        value: str,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> ModelPlugin:
        try:
            return get_plugin(value)
        except UsageError as exc:
            self.fail(str(exc), param, ctx)
