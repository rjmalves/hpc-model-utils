"""ADR-005/ADR-011/R12/R31/R122/R123: the C2 toolbox steps for a bare
deck directory (ranqueamento-prospectivo-utils's in-job calls,
ticket-054).

Neither step reads, creates or writes ``.hpcmu/``: ``RunState`` is
built fresh in memory from ``RunState.new``, never through
``StateStore``, and ``write_projections`` never reads a projection
back (ADR-011) -- a pre-existing ``metadata.modelops`` is overwritten,
never merged.
"""

from __future__ import annotations

import dataclasses
import logging

from hpc_model_utils.core.diagnosis import Diagnosis, JobReport
from hpc_model_utils.core.lifecycle.finalize import diagnose_workspace
from hpc_model_utils.core.lifecycle.run import StatusReporter
from hpc_model_utils.core.plugin import ModelPlugin, PostprocessError
from hpc_model_utils.core.state import (
    RunState,
    current_tool,
    metadata_items,
    write_projections,
)
from hpc_model_utils.core.workspace import Workspace

logger = logging.getLogger(__name__)


def generate_execution_status(
    ws: Workspace,
    plugin: ModelPlugin,
    reporter: StatusReporter,
    *,
    job_id: str,
) -> Diagnosis:
    """R31: re-emits v1's ``job_id`` then ``status`` SetMetadata hooks,
    in that order. ``job_id`` is accepted verbatim, never validated or
    queried against Slurm."""
    diag = diagnose_workspace(
        ws,
        plugin,
        JobReport(outcome=None, process_exit=None, log_paths=()),
        job_id=job_id or None,
    )
    state = dataclasses.replace(
        RunState.new(plugin.name, current_tool()),
        reported_job_id=job_id,
        diagnosis=diag,
    )
    write_projections(
        ws, state, always_write_parent_path=plugin.always_write_parent_path
    )
    for key, value in metadata_items(
        state, always_write_parent_path=plugin.always_write_parent_path
    ):
        reporter.metadata(key, value)
    logger.info(
        "Generated execution status: %s [%s]", diag.status, diag.rule_id
    )
    return diag


def postprocess(ws: Workspace, plugin: ModelPlugin) -> None:
    try:
        plugin.postprocess(ws)
    except PostprocessError as exc:
        logger.warning("postprocess failed: %s", exc)
