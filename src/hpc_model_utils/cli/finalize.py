"""R72/ADR-006/ADR-021: the hidden ``finalize`` command.

Only the rendered finalize job (ticket-029) calls this. It runs
job-side with ``HPCMU_PLATFORM=off`` (R31), so the Reporter stays
silent, and it never emits a terminal-status hook (``emits_terminal_
status=False`` below) even when the platform is on: its exit code
and ``finalize.json`` are the only outputs the login-side ``run``
(ticket-038) consumes.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import click

from hpc_model_utils.cli.params import ModelArg
from hpc_model_utils.cli.root import AppContext, HpcmuCommand, cli
from hpc_model_utils.core.lifecycle.finalize import finalize
from hpc_model_utils.core.plugin import ModelPlugin
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.infra.slurm import Slurm

_MODEL_JOB_ID_PATTERN = re.compile(r"[0-9]+")


def _validate_model_job_id(
    ctx: click.Context, param: click.Parameter, value: str
) -> str | None:
    if value == "none":
        return None
    if _MODEL_JOB_ID_PATTERN.fullmatch(value) is None:
        raise click.UsageError(
            f"--model-job-id must be digits or 'none': {value!r}"
        )
    return value


@cli.command(
    "finalize", cls=HpcmuCommand, hidden=True, emits_terminal_status=False
)
@click.argument("plugin", type=ModelArg())
@click.option("--model-job-id", required=True, callback=_validate_model_job_id)
@click.option("--cores", required=True, type=click.IntRange(min=1))
@click.option("--synthesis-bin", type=click.Path(path_type=Path))
@click.option("--slurm-path", type=click.Path(path_type=Path))
@click.pass_obj
def finalize_command(
    app_ctx: AppContext,
    plugin: ModelPlugin,
    model_job_id: str | None,
    cores: int,
    synthesis_bin: Path | None,
    slurm_path: Path | None,
) -> None:
    finalize(
        Workspace.at(Path.cwd()),
        plugin,
        Slurm(slurm_path),
        model_job_id=model_job_id,
        cores=cores,
        synthesis_bin=synthesis_bin,
        settings=app_ctx.settings,
        emit=logging.getLogger("hpc_model_utils.finalize").info,
    )
