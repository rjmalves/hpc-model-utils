"""ADR-005/ADR-011/R12/R31/R122/R123: the C2 toolbox commands for a
bare deck directory -- thin click wrappers over the lifecycle steps
(ticket-054). No command body catches anything: a lifecycle failure
propagates to ``cli.root.main``'s single fatal-path chokepoint.
"""

from __future__ import annotations

from pathlib import Path

import click

from hpc_model_utils.cli.params import ModelArg
from hpc_model_utils.cli.root import AppContext, HpcmuCommand, cli
from hpc_model_utils.core.lifecycle import toolbox
from hpc_model_utils.core.plugin import ModelPlugin
from hpc_model_utils.core.workspace import Workspace


@cli.command("generate_execution_status", cls=HpcmuCommand)
@click.argument("model", type=ModelArg())
@click.option("--job-id", default="")
@click.pass_obj
def generate_execution_status_command(
    app_ctx: AppContext, model: ModelPlugin, job_id: str
) -> None:
    toolbox.generate_execution_status(
        Workspace.at(Path.cwd()), model, app_ctx.reporter, job_id=job_id
    )


@cli.command("postprocess", cls=HpcmuCommand)
@click.argument("model", type=ModelArg())
def postprocess_command(model: ModelPlugin) -> None:
    toolbox.postprocess(Workspace.at(Path.cwd()), model)
