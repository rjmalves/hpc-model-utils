"""R42/R100/R101/R114/R137/ADR-010/ADR-014/ADR-021/ADR-053: the
``finalize`` job-side step.

``finalize`` diagnoses the model job, runs postprocess and the
sintetizador on ``SUCCESS`` only, always realizes outputs, and writes
``.hpcmu/finalize.json`` once via ``write_atomic`` -- never
``state.json``, which the login side owns (ticket-038 ingests this
record).

A sintetizador that exits non-zero, or a postprocess step that
raises, keeps ``SUCCESS`` but records the failure loudly (R137 /
ADR-053): the diagnosis reason is prefixed, the first evidence item
names the failing step, and ``synthesis_status`` reports ``"failed"``.
Only a *missing* sintetizador binary changes the status, to
``RUNTIME_ERROR`` / ``core.synthesis_missing`` (R101) -- and only when
no ``--synthesis-bin`` was given does the legacy workspace path
apply; an explicit, missing ``--synthesis-bin`` never falls back to
it.
"""

from __future__ import annotations

import dataclasses
import os
import re
import socket
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from hpc_model_utils.core.diagnosis import (
    Diagnosis,
    EvidenceItem,
    JobReport,
    RunStatus,
    evaluate,
    utc_now_iso,
)
from hpc_model_utils.core.errors import StateFormatError
from hpc_model_utils.core.outputs import OutputPlan, RealizedOutputs, realize
from hpc_model_utils.core.plugin import ModelPlugin
from hpc_model_utils.core.settings import EngineSettings
from hpc_model_utils.core.state import (
    FinalizeRecord,
    StateStore,
    StepOutcome,
    StepRecord,
    write_finalize,
)
from hpc_model_utils.core.workspace import Phase, Workspace
from hpc_model_utils.infra.errors import ShellCommandError, UnsafeArchiveError
from hpc_model_utils.infra.shell import run as shell_run
from hpc_model_utils.infra.slurm import JobOutcome

_LSCPU_TIMEOUT = 10.0
_LSCPU_PAIR_RE = re.compile(r"(\d+),(\d+)")
_PROCESS_EXIT_RE = re.compile(r"-?[0-9]+")


class SlurmLike(Protocol):
    """Structural seam for ``finalize``'s ``slurm`` argument, matching
    ticket-034's ``SlurmLike`` in ``core.follow``. A real
    ``infra.slurm.Slurm`` satisfies this without inheriting from it;
    tests pass a scripted double instead of a live scheduler."""

    def outcome(
        self,
        job_id: str,
        *,
        attempts: int = 6,
        backoff: float = 10.0,
    ) -> JobOutcome: ...


def physical_cores() -> int:
    """R100/amendment 4: count unique ``(Core, Socket)`` pairs via
    ``lscpu -p=Core,Socket``.

    Falls back to ``os.cpu_count() or 1`` whenever ``lscpu`` is
    missing, exits non-zero, times out (10s), or yields no pairs.
    This never raises.
    """
    fallback = os.cpu_count() or 1
    try:
        result = shell_run(["lscpu", "-p=Core,Socket"], timeout=_LSCPU_TIMEOUT)
    except ShellCommandError:
        # lscpu is missing or not executable on this host; degrade to
        # the logical core count rather than failing finalize.
        return fallback
    if result.timed_out or result.returncode != 0:
        return fallback
    pairs: set[tuple[str, str]] = set()
    for line in result.output:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _LSCPU_PAIR_RE.fullmatch(stripped)
        if match is not None:
            pairs.add((match.group(1), match.group(2)))
    return len(pairs) if pairs else fallback


def legacy_synthesis_bin(ws: Workspace, plugin: ModelPlugin) -> Path:
    return (
        ws.root
        / f"sintetizador-{plugin.name}"
        / "venv"
        / "bin"
        / f"sintetizador-{plugin.name}"
    )


def synthesis_status(rec: FinalizeRecord) -> str | None:
    """Requirement 5: ``None`` when neither step ran, ``"failed"``
    when either has ``ok=False``, ``"ok"`` otherwise."""
    if rec.postprocess is None and rec.synthesis is None:
        return None
    failed = (rec.postprocess is not None and not rec.postprocess.ok) or (
        rec.synthesis is not None and not rec.synthesis.ok
    )
    return "failed" if failed else "ok"


def _read_process_exit(ws: Workspace) -> int | None:
    """Read and strictly parse ``ws.model_exit_path``.

    Decision (ticket left this open): a malformed file -- anything
    that does not fullmatch ``-?[0-9]+`` once a single trailing
    newline is stripped -- is treated the same as a missing file:
    ``None``, with no evidence note. ``process_exit`` is context
    handed through to ``plugin.diagnose``, not an input ``evaluate``
    itself reads, so there is no natural evidence slot to attach a
    note to here, and inventing one would need to survive the later
    R137 evidence inserts, for no decided benefit. This never raises.
    """
    try:
        raw = ws.model_exit_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    text = raw[:-1] if raw.endswith("\n") else raw
    if _PROCESS_EXIT_RE.fullmatch(text) is None:
        return None
    return int(text)


def diagnose_workspace(
    ws: Workspace,
    plugin: ModelPlugin,
    report: JobReport,
    *,
    job_id: str | None,
) -> Diagnosis:
    """ticket-054: the one diagnosis entry point shared by ``finalize``
    and the C2 toolbox's ``generate_execution_status``, so the two can
    never diverge."""
    return evaluate(
        report,
        log_patterns=plugin.log_patterns,
        primary_evidence=plugin.primary_evidence(ws),
        rules=lambda: plugin.diagnose(ws, report),
        job_id=job_id,
    )


def _record_step_failure(diag: Diagnosis, step: str, detail: str) -> Diagnosis:
    """R137: prefix ``reason`` and prepend the evidence item for a
    plugin/synthesis step that failed, without changing the status or
    ``rule_id``."""
    return dataclasses.replace(
        diag,
        reason=f"{step} failed: {detail}; {diag.reason}",
        evidence=(EvidenceItem("plugin", step, detail), *diag.evidence),
    )


def _override_diagnosis(
    diag: Diagnosis, *, rule_id: str, reason: str
) -> Diagnosis:
    """A fatal-for-this-step override (synthesis-missing,
    outputs-failed): keeps the original ``rule_id`` in ``matched``."""
    return dataclasses.replace(
        diag,
        status=RunStatus.RUNTIME_ERROR,
        rule_id=rule_id,
        reason=reason,
        matched=(*diag.matched, diag.rule_id),
    )


def _run_postprocess(
    plugin: ModelPlugin, ws: Workspace, diag: Diagnosis
) -> tuple[Diagnosis, StepOutcome]:
    start = time.monotonic()
    try:
        plugin.postprocess(ws)
    except Exception as exc:
        # plugin-supplied call, fault-isolated under R137; never
        # BaseException, so KeyboardInterrupt/SystemExit still
        # propagate.
        detail = str(exc)
        duration = time.monotonic() - start
        return (
            _record_step_failure(diag, "postprocess", detail),
            StepOutcome("postprocess", False, detail, duration),
        )
    duration = time.monotonic() - start
    return diag, StepOutcome("postprocess", True, "", duration)


def _resolve_synthesis_args(
    plugin: ModelPlugin, cpus: int
) -> tuple[tuple[str, ...] | None, str | None]:
    try:
        return plugin.synthesis_args(cpus), None
    except Exception as exc:
        # plugin-supplied call, fault-isolated under R137 (recorded as
        # the synthesis step failing); never BaseException.
        return None, str(exc)


def _resolve_synthesis_bin(
    ws: Workspace, plugin: ModelPlugin, synthesis_bin: Path | None
) -> tuple[Path | None, tuple[Path, ...]]:
    """R101/amendment 3: an explicit ``synthesis_bin`` is used exactly,
    with no legacy fallback. Only its absence lets the legacy
    workspace path apply."""
    if synthesis_bin is not None:
        tried = (synthesis_bin,)
        return (synthesis_bin if synthesis_bin.exists() else None), tried
    legacy = legacy_synthesis_bin(ws, plugin)
    tried = (legacy,)
    return (legacy if legacy.exists() else None), tried


def _run_synthesis(
    ws: Workspace,
    plugin: ModelPlugin,
    synthesis_bin: Path | None,
    args: tuple[str, ...],
    emit: Callable[[str], None],
    diag: Diagnosis,
) -> tuple[Diagnosis, StepOutcome | None]:
    resolved, tried = _resolve_synthesis_bin(ws, plugin, synthesis_bin)
    if resolved is None:
        tried_text = ", ".join(str(path) for path in tried)
        return (
            _override_diagnosis(
                diag,
                rule_id="core.synthesis_missing",
                reason=(f"sintetizador binary not found; tried: {tried_text}"),
            ),
            None,
        )
    start = time.monotonic()
    try:
        result = shell_run(
            [str(resolved), *args], cwd=ws.root, on_line=emit, timeout=None
        )
    except ShellCommandError as exc:
        # an exec failure (e.g. a non-executable binary) counts as the
        # synthesis step failing under R137, not a fatal path
        # (amendment 3), and is not the fatal path.
        detail = str(exc)
        duration = time.monotonic() - start
        return (
            _record_step_failure(diag, "synthesis", detail),
            StepOutcome("synthesis", False, detail, duration),
        )
    duration = time.monotonic() - start
    if result.returncode != 0:
        detail = f"sintetizador exited {result.returncode}"
        return (
            _record_step_failure(diag, "synthesis", detail),
            StepOutcome("synthesis", False, detail, duration),
        )
    return diag, StepOutcome("synthesis", True, "", duration)


def _resolve_output_plan(
    plugin: ModelPlugin, ws: Workspace, diag: Diagnosis
) -> tuple[OutputPlan | None, Diagnosis]:
    try:
        return plugin.outputs(ws), diag
    except Exception as exc:
        # plugin-supplied call, fault-isolated: a failure here is
        # reported the same way a realize() failure is
        # (core.outputs_failed), never BaseException.
        return None, _override_diagnosis(
            diag,
            rule_id="core.outputs_failed",
            reason=f"outputs failed: {exc}",
        )


def _realize_plan(
    plan: OutputPlan, ws: Workspace, cores: int, diag: Diagnosis
) -> tuple[RealizedOutputs | None, Diagnosis]:
    try:
        workers = min(cores, os.cpu_count() or 1)
        return realize(plan, ws, workers=workers), diag
    except (UnsafeArchiveError, ValueError, OSError) as exc:
        # core.outputs.realize's own documented failure modes; any
        # other exception is a bug and propagates to the fatal path.
        return None, _override_diagnosis(
            diag,
            rule_id="core.outputs_failed",
            reason=f"outputs failed: {exc}",
        )


def finalize(
    ws: Workspace,
    plugin: ModelPlugin,
    slurm: SlurmLike,
    *,
    model_job_id: str | None,
    cores: int,
    synthesis_bin: Path | None,
    settings: EngineSettings,
    emit: Callable[[str], None],
) -> FinalizeRecord:
    """Run the finalize job-side step and write ``finalize.json``.

    ``record.steps`` carries exactly this call's own ``StepRecord``,
    never ``state.steps`` -- ticket-038 ingests a finalize record as
    ``state.steps + rec.steps + (run_step,)``, so copying the prior
    history here would duplicate it on every ingest. Its ``outcome``
    is always ``"ok"``: it reports that the finalize step itself ran
    to completion and wrote its record, not the run's outcome, which
    lives in ``record.diagnosis.status``.
    """
    started_at = utc_now_iso()
    entry_monotonic = time.monotonic()

    state = StateStore(ws).load()
    if state is None:
        raise StateFormatError("finalize requires .hpcmu/state.json")

    log_paths: tuple[Path, ...]
    outcome: JobOutcome | None
    if model_job_id is not None:
        outcome = slurm.outcome(
            model_job_id,
            attempts=settings.outcome_attempts,
            backoff=settings.outcome_backoff,
        )
        log_paths = (ws.log_path(Phase.MODEL, model_job_id),)
    else:
        outcome = None
        log_paths = ()

    report = JobReport(
        outcome=outcome,
        process_exit=_read_process_exit(ws),
        log_paths=log_paths,
    )
    diag = diagnose_workspace(ws, plugin, report, job_id=model_job_id)

    postprocess_outcome: StepOutcome | None = None
    synthesis_outcome: StepOutcome | None = None
    if diag.status is RunStatus.SUCCESS:
        diag, postprocess_outcome = _run_postprocess(plugin, ws, diag)
        cpus = min(cores, physical_cores())
        args, args_error = _resolve_synthesis_args(plugin, cpus)
        if args_error is not None:
            synthesis_outcome = StepOutcome("synthesis", False, args_error, 0.0)
            diag = _record_step_failure(diag, "synthesis", args_error)
        elif args is not None:
            diag, synthesis_outcome = _run_synthesis(
                ws, plugin, synthesis_bin, args, emit, diag
            )

    plan, diag = _resolve_output_plan(plugin, ws, diag)
    realized: RealizedOutputs | None = None
    if plan is not None:
        realized, diag = _realize_plan(plan, ws, cores, diag)

    finished_at = utc_now_iso()
    duration = time.monotonic() - entry_monotonic
    record = FinalizeRecord(
        run_id=state.run_id,
        diagnosis=diag,
        postprocess=postprocess_outcome,
        synthesis=synthesis_outcome,
        outputs=realized,
        steps=(
            StepRecord(
                command="finalize",
                host=socket.gethostname(),
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=duration,
                outcome="ok",
            ),
        ),
    )
    write_finalize(ws, record)
    return record
