"""ADR-024/R44/R91/R10: the cancel step.

``cancel()`` scancels the ids recorded in ``RunState`` (R44), merged
with an optional ``--job-id`` that ADR-024 trusts only once its
``squeue %Z`` WorkDir exactly equals ``ws.root`` -- absolute and
resolved, never a relative path or a subdirectory (2026-10-02
amendment, standing approval). A recorded id is never re-checked for
ownership: it is already trusted, and is never duplicated when it
also matches ``--job-id``.

``store.load_optional()`` is the only state read; this module never
creates ``.hpcmu`` or writes state, and never calls a ``Reporter``
method (R91: the caller's cancellation branch owns the terminal
status hook).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from hpc_model_utils.core.errors import SchedulerError, UsageError
from hpc_model_utils.core.settings import EngineSettings
from hpc_model_utils.core.state import StateStore
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.infra.slurm import Slurm

logger = logging.getLogger(__name__)

_JOB_ID_RE = re.compile(r"[0-9]+")


@dataclass(frozen=True)
class CancelResult:
    cancelled: tuple[str, ...]
    refused: tuple[str, ...]
    gone: bool


def cancel(
    ws: Workspace,
    slurm: Slurm,
    store: StateStore,
    *,
    job_id: str | None,
    settings: EngineSettings,
) -> CancelResult:
    state = store.load_optional()
    if state is None:
        recorded: tuple[str, ...] = ()
    else:
        recorded = tuple(dict.fromkeys(job.job_id for job in state.jobs))
    candidates = list(recorded)
    refused: list[str] = []

    if job_id and job_id not in recorded:
        if _JOB_ID_RE.fullmatch(job_id) is None:
            raise UsageError(f"not a Slurm job id: {job_id!r}")
        wd = slurm.workdir(job_id)
        owned = wd is not None and wd.is_absolute() and wd.resolve() == ws.root
        if owned:
            candidates.append(job_id)
        else:
            refused.append(job_id)
            logger.warning(
                "refusing to cancel job %s: not owned by %s",
                job_id,
                ws.root,
            )

    if not candidates:
        logger.info("nothing to cancel")
        return CancelResult((), tuple(refused), True)

    slurm.cancel(tuple(candidates))
    gone = slurm.wait_gone(tuple(candidates), timeout=settings.cancel_timeout)
    if not gone:
        raise SchedulerError(
            f"jobs still queued after {settings.cancel_timeout}s: "
            f"{', '.join(candidates)}"
        )
    return CancelResult(tuple(candidates), tuple(refused), gone)
