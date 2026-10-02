"""System tests for the cancel lifecycle step (ticket-041, ADR-024,
R44, R91, R10), against the fake SLURM harness and the 2026-10-02
ownership-check-hardening amendment.

The hybrid-bin-dir scenario (AC5) swaps in a no-op ``scancel`` stub
while keeping the fake's own ``squeue`` (via symlink), so ``cancel()``
drives a real, never-clearing held job without editing the fake
itself.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from hpc_model_utils.core.errors import SchedulerError, UsageError
from hpc_model_utils.core.lifecycle.cancel import CancelResult, cancel
from hpc_model_utils.core.settings import EngineSettings
from hpc_model_utils.core.state import (
    JobRecord,
    RunState,
    StateStore,
    current_tool,
)
from hpc_model_utils.core.workspace import Phase, Workspace
from hpc_model_utils.infra.slurm import Slurm
from tests.support.fake_slurm import FakeSlurm
from tests.support.fake_slurm.cli import (
    wait_for_state,
    write_executable_stub,
    write_script,
)

_PLUGIN_NAME = "fake-plugin"


@pytest.fixture(autouse=True)
def _chdir_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


def _make_workspace(tmp_path: Path, name: str = "ws") -> Workspace:
    root = tmp_path / name
    root.mkdir()
    return Workspace.at(root)


def _save_recorded(
    ws: Workspace, store: StateStore, job_ids: Sequence[str]
) -> None:
    ws.ensure_layout()
    phases = (Phase.MODEL, Phase.FINALIZE)
    jobs = tuple(
        JobRecord(
            phases[index % 2],
            job_id,
            "2026-01-01T00:00:00Z",
            f"logs/{job_id}.out",
        )
        for index, job_id in enumerate(job_ids)
    )
    state = replace(RunState.new(_PLUGIN_NAME, current_tool()), jobs=jobs)
    store.save(state)


def _hybrid_bin_dir(tmp_path: Path, fake_slurm: FakeSlurm) -> Path:
    bin_dir = tmp_path / "hybrid-bin"
    bin_dir.mkdir()
    for exe in ("sbatch", "squeue", "sacct", "scontrol"):
        (bin_dir / exe).symlink_to(fake_slurm.bin_dir / exe)
    write_executable_stub(bin_dir, "scancel", "exit 0")
    return bin_dir


def _cmd_records(fake_slurm: FakeSlurm, cmd: str) -> list[dict[str, Any]]:
    records = [
        json.loads(line)
        for line in (fake_slurm.state_dir / "invocations.jsonl")
        .read_text()
        .splitlines()
        if line.strip()
    ]
    return [record for record in records if record["cmd"] == cmd]


# --- AC2: recorded pair plus a matching --job-id ---


def test_cancel_recorded_pair_with_matching_job_id_cancels_both(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    ws = _make_workspace(tmp_path)
    store = StateStore(ws)
    slurm = Slurm(fake_slurm.bin_dir)
    model_script = write_script(tmp_path, "model.sh", "sleep 30")
    finalize_script = write_script(tmp_path, "finalize.sh", "sleep 30")
    model_id = slurm.submit(model_script)
    finalize_id = slurm.submit(finalize_script)
    wait_for_state(fake_slurm, int(model_id), "RUNNING")
    wait_for_state(fake_slurm, int(finalize_id), "RUNNING")
    _save_recorded(ws, store, [model_id, finalize_id])
    settings = EngineSettings(cancel_timeout=10.0)

    result = cancel(ws, slurm, store, job_id=finalize_id, settings=settings)

    assert result == CancelResult((model_id, finalize_id), (), True)
    assert fake_slurm.job(int(model_id))["state"] == "CANCELLED"
    assert fake_slurm.job(int(finalize_id))["state"] == "CANCELLED"
    records = _cmd_records(fake_slurm, "scancel")
    assert len(records) == 1
    assert model_id in records[0]["argv"]
    assert finalize_id in records[0]["argv"]


# --- AC3: a foreign WorkDir is refused and never scancelled ---


def test_cancel_job_id_foreign_workdir_refused_no_scancel(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    ws = _make_workspace(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    store = StateStore(ws)
    slurm = Slurm(fake_slurm.bin_dir)
    script = write_script(
        tmp_path, "foreign.sh", f"#SBATCH --chdir={other}\nsleep 30"
    )
    job_id = slurm.submit(script)
    wait_for_state(fake_slurm, int(job_id), "RUNNING")
    settings = EngineSettings(cancel_timeout=5.0)

    result = cancel(ws, slurm, store, job_id=job_id, settings=settings)

    assert result == CancelResult((), (job_id,), True)
    assert fake_slurm.job(int(job_id))["state"] == "RUNNING"
    assert _cmd_records(fake_slurm, "scancel") == []


# --- AC4: no state file, an owned --job-id is cancelled, .hpcmu stays
# absent ---


def test_cancel_no_state_owned_job_id_cancelled_hpcmu_absent(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    ws = _make_workspace(tmp_path)
    store = StateStore(ws)
    slurm = Slurm(fake_slurm.bin_dir)
    script = write_script(
        tmp_path, "owned.sh", f"#SBATCH --chdir={ws.root}\nsleep 30"
    )
    job_id = slurm.submit(script)
    wait_for_state(fake_slurm, int(job_id), "RUNNING")
    settings = EngineSettings(cancel_timeout=5.0)

    result = cancel(ws, slurm, store, job_id=job_id, settings=settings)

    assert result == CancelResult((job_id,), (), True)
    assert fake_slurm.job(int(job_id))["state"] == "CANCELLED"
    assert not (ws.root / ".hpcmu").exists()


# --- AC5: a held job plus a no-op scancel stub times out ---


def test_cancel_wait_timeout_with_noop_scancel_raises_scheduler_error(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    ws = _make_workspace(tmp_path)
    store = StateStore(ws)
    slurm = Slurm(fake_slurm.bin_dir)
    script = write_script(tmp_path, "held.sh", "sleep 30")
    job_id = slurm.submit(script)
    fake_slurm.hold(int(job_id))
    _save_recorded(ws, store, [job_id])
    hybrid_slurm = Slurm(_hybrid_bin_dir(tmp_path, fake_slurm))
    settings = EngineSettings(cancel_timeout=0.5)

    with pytest.raises(SchedulerError, match="still queued"):
        cancel(ws, hybrid_slurm, store, job_id=None, settings=settings)


# --- Amendment 1: the WorkDir must be absolute and exactly equal to
# ws.root ---


def test_cancel_job_id_relative_workdir_refused(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    store = StateStore(ws)
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    write_executable_stub(bin_dir, "squeue", "echo 'relative/path'")
    slurm = Slurm(bin_dir)
    settings = EngineSettings(cancel_timeout=5.0)

    result = cancel(ws, slurm, store, job_id="4242", settings=settings)

    assert result == CancelResult((), ("4242",), True)


def test_cancel_job_id_subdirectory_workdir_refused(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    store = StateStore(ws)
    sub = ws.root / "sub"
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    write_executable_stub(bin_dir, "squeue", f"echo '{sub}'")
    slurm = Slurm(bin_dir)
    settings = EngineSettings(cancel_timeout=5.0)

    result = cancel(ws, slurm, store, job_id="4242", settings=settings)

    assert result == CancelResult((), ("4242",), True)


# --- Amendment 2: id validation and the empty-string case ---


def test_cancel_job_id_trailing_newline_raises_usage_error(
    tmp_path: Path,
) -> None:
    ws = _make_workspace(tmp_path)
    store = StateStore(ws)
    bin_dir = tmp_path / "empty-bin"
    bin_dir.mkdir()
    slurm = Slurm(bin_dir)
    settings = EngineSettings(cancel_timeout=5.0)

    with pytest.raises(UsageError, match="not a Slurm job id"):
        cancel(ws, slurm, store, job_id="123\n", settings=settings)


def test_cancel_job_id_empty_string_treated_as_no_job_id(
    tmp_path: Path,
) -> None:
    ws = _make_workspace(tmp_path)
    store = StateStore(ws)
    bin_dir = tmp_path / "empty-bin"
    bin_dir.mkdir()
    slurm = Slurm(bin_dir)
    settings = EngineSettings(cancel_timeout=5.0)

    result = cancel(ws, slurm, store, job_id="", settings=settings)

    assert result == CancelResult((), (), True)


# --- Amendment 4: an already-finished recorded job is fine ---


def test_cancel_recorded_finished_job_returns_normally(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    ws = _make_workspace(tmp_path)
    store = StateStore(ws)
    slurm = Slurm(fake_slurm.bin_dir)
    script = write_script(tmp_path, "quick.sh", "true")
    job_id = slurm.submit(script)
    wait_for_state(fake_slurm, int(job_id), "COMPLETED")
    _save_recorded(ws, store, [job_id])
    settings = EngineSettings(cancel_timeout=5.0)

    result = cancel(ws, slurm, store, job_id=None, settings=settings)

    assert result == CancelResult((job_id,), (), True)
    assert len(_cmd_records(fake_slurm, "scancel")) == 1


# --- A --job-id already recorded is trusted and never duplicated ---


def test_cancel_job_id_already_recorded_not_duplicated_in_argv(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    ws = _make_workspace(tmp_path)
    store = StateStore(ws)
    slurm = Slurm(fake_slurm.bin_dir)
    script = write_script(tmp_path, "job.sh", "sleep 30")
    job_id = slurm.submit(script)
    wait_for_state(fake_slurm, int(job_id), "RUNNING")
    _save_recorded(ws, store, [job_id])
    settings = EngineSettings(cancel_timeout=5.0)

    result = cancel(ws, slurm, store, job_id=job_id, settings=settings)

    assert result == CancelResult((job_id,), (), True)
    records = _cmd_records(fake_slurm, "scancel")
    assert len(records) == 1
    assert records[0]["argv"].count(job_id) == 1
