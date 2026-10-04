"""System tests for ticket-043 (D2, R45/ADR-024/ADR-005/R121/R91):
scancel ``run``'s own jobs on SIGTERM, SIGHUP or a broken stdout pipe.

The subprocess scenarios (AC2-AC5) drive the real chain end to end,
through the ticket-036 CLI shim's test-only ``engine-run-test``
command (registered only under ``HPCMU_TEST_ENGINE_COMMAND=1``) and
the fake SLURM harness. The in-process tests below them exercise
``cancel_on_termination``'s handler install/restore and cancellation
logic directly, driving the ``Terminated`` branch by raising it in
the ``with`` body rather than by sending a real signal -- and never
the ``BrokenPipeError`` branch in-process, since that branch really
does ``os.dup2(..., 1)``, which must not run against this test
process's own stdout.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from hpc_model_utils.core.diagnosis import utc_now_iso
from hpc_model_utils.core.lifecycle.finalize import legacy_synthesis_bin
from hpc_model_utils.core.lifecycle.run import JobLedger
from hpc_model_utils.core.lifecycle.signals import (
    Terminated,
    cancel_on_termination,
)
from hpc_model_utils.core.state import JobRecord, StateStore
from hpc_model_utils.core.workspace import Phase, Workspace
from hpc_model_utils.infra.errors import SchedulerCommandError
from hpc_model_utils.infra.slurm import Slurm
from tests.support.cli_shim import write_cli_shim
from tests.support.fake_plugin import FakePlugin, install_fake_model
from tests.support.fake_slurm import FakeSlurm
from tests.support.hooks import parse_hooks

_PLUGIN = FakePlugin()
_STATUS_AND_ANNOTATION_HOOKS = frozenset(
    {
        "SetSuccess",
        "SetModelError",
        "SetDataError",
        "SetRuntimeError",
        "SetAnnotation",
    }
)


@pytest.fixture(autouse=True)
def _chdir_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


# ---------------------------------------------------------------------------
# Shared subprocess helpers.
# ---------------------------------------------------------------------------


def _make_workspace(tmp_path: Path) -> Workspace:
    root = tmp_path / "study-fake"
    root.mkdir()
    return Workspace.at(root)


def _write_legacy_sintetizador(ws: Workspace) -> None:
    stub = legacy_synthesis_bin(ws, _PLUGIN)
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(
        "#!/bin/bash\nmkdir -p sintese\n: > sintese/x.parquet\nexit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)


def _install_chatty_model(
    ws: Workspace, *, lines: int = 200, interval: float = 0.05
) -> None:
    """A model that keeps appending fresh log lines, paced in real
    time by a genuine bash loop, instead of ``install_fake_model``'s
    instant, one-shot ``extra_lines`` burst -- AC4 needs the engine
    still actively relaying output at the moment the parent closes
    its end of the stdout pipe."""
    ws.assets.mkdir(parents=True, exist_ok=True)
    fake_out = shlex.quote(str(ws.root / "fake.out"))
    fake_file = shlex.quote(str(ws.root / "model.fake"))
    script = ws.assets / "fake-model"
    script.write_text(
        "#!/bin/bash\n"
        "set -u\n"
        "i=0\n"
        f'while [ "$i" -lt {lines} ]; do\n'
        "  printf 'chatty-%d\\n' \"$i\"\n"
        f"  sleep {interval}\n"
        "  i=$((i+1))\n"
        "done\n"
        f"printf '%s\\n' SUCCESS > {fake_out}\n"
        f": > {fake_file}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def _make_delayed_sbatch_bin(
    tmp_path: Path, real_bin_dir: Path, marker: Path
) -> Path:
    """AC5: a bin dir whose ``sbatch`` touches ``marker`` then sleeps,
    so a test can wait for proof that submission is already inside
    ``blocked_signals()`` before sending a signal, then forwards to
    the real fake-SLURM ``sbatch``. Every other command is the real
    fake-SLURM shim, symlinked unchanged."""
    bin_dir = tmp_path / "slow-bin"
    bin_dir.mkdir()
    for name in ("squeue", "scancel", "sacct", "scontrol", "mpiexec", "srun"):
        (bin_dir / name).symlink_to(real_bin_dir / name)
    sbatch = bin_dir / "sbatch"
    sbatch.write_text(
        "#!/bin/bash\n"
        f"touch {shlex.quote(str(marker))}\n"
        "sleep 2\n"
        f'exec {shlex.quote(str(real_bin_dir / "sbatch"))} "$@"\n',
        encoding="utf-8",
    )
    sbatch.chmod(0o755)
    return bin_dir


def _wait_until(
    predicate: Callable[[], bool], *, timeout: float = 10.0
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met before timeout")


def _spawn_engine(
    shim: Path,
    ws: Workspace,
    slurm_bin: Path,
    *,
    env_overrides: Mapping[str, str],
) -> subprocess.Popen[str]:
    env = {
        **os.environ,
        "HPCMU_TEST_ENGINE_COMMAND": "1",
        **env_overrides,
    }
    return subprocess.Popen(
        [
            str(shim),
            "engine-run-test",
            str(ws.root),
            "--slurm-bin",
            str(slurm_bin),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def _ensure_dead(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=5.0)


# ---------------------------------------------------------------------------
# AC2: SIGTERM -> 143, both jobs CANCELLED, empty stderr, no status hooks.
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
def test_engine_run_sigterm_exits_143_cancels_both_jobs(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    ws = _make_workspace(tmp_path)
    install_fake_model(ws, sleep=60.0)
    _write_legacy_sintetizador(ws)
    shim = write_cli_shim(tmp_path)

    proc = _spawn_engine(
        shim,
        ws,
        fake_slurm.bin_dir,
        env_overrides={
            "HPCMU_POLL_INTERVAL": "0.02",
            "HPCMU_SETTLE_WINDOW": "0.05",
            "HPCMU_CANCEL_TIMEOUT": "5",
        },
    )
    try:
        _wait_until(lambda: len(fake_slurm.submitted()) >= 2)
        time.sleep(0.2)
        proc.send_signal(signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=15.0)
    finally:
        _ensure_dead(proc)

    assert proc.returncode == 143
    assert stderr == ""
    jobs = fake_slurm.submitted()
    assert len(jobs) == 2
    for job in jobs:
        assert fake_slurm.job(job["id"])["state"] == "CANCELLED"
    hooks = parse_hooks(stdout)
    assert all(h.method not in _STATUS_AND_ANNOTATION_HOOKS for h in hooks)


# ---------------------------------------------------------------------------
# AC3: SIGHUP -> 129, both jobs CANCELLED.
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
def test_engine_run_sighup_exits_129_cancels_both_jobs(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    ws = _make_workspace(tmp_path)
    install_fake_model(ws, sleep=60.0)
    _write_legacy_sintetizador(ws)
    shim = write_cli_shim(tmp_path)

    proc = _spawn_engine(
        shim,
        ws,
        fake_slurm.bin_dir,
        env_overrides={
            "HPCMU_POLL_INTERVAL": "0.02",
            "HPCMU_SETTLE_WINDOW": "0.05",
            "HPCMU_CANCEL_TIMEOUT": "5",
        },
    )
    try:
        _wait_until(lambda: len(fake_slurm.submitted()) >= 2)
        time.sleep(0.2)
        proc.send_signal(signal.SIGHUP)
        stdout, stderr = proc.communicate(timeout=15.0)
    finally:
        _ensure_dead(proc)

    assert proc.returncode == 129
    assert stderr == ""
    jobs = fake_slurm.submitted()
    assert len(jobs) == 2
    for job in jobs:
        assert fake_slurm.job(job["id"])["state"] == "CANCELLED"
    hooks = parse_hooks(stdout)
    assert all(h.method not in _STATUS_AND_ANNOTATION_HOOKS for h in hooks)


# ---------------------------------------------------------------------------
# AC4: the parent closes its stdout pipe mid-follow -> 141, both jobs
# CANCELLED, empty stderr (the exit-time flush stays silent too).
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
def test_engine_run_closed_stdout_pipe_exits_141_cancels_both_jobs(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    ws = _make_workspace(tmp_path)
    _install_chatty_model(ws)
    _write_legacy_sintetizador(ws)
    shim = write_cli_shim(tmp_path)

    proc = _spawn_engine(
        shim,
        ws,
        fake_slurm.bin_dir,
        env_overrides={
            "HPCMU_POLL_INTERVAL": "0.02",
            "HPCMU_SETTLE_WINDOW": "0.05",
            "HPCMU_CANCEL_TIMEOUT": "5",
        },
    )
    try:
        _wait_until(lambda: len(fake_slurm.submitted()) >= 2)
        time.sleep(0.3)
        assert proc.stdout is not None
        proc.stdout.close()
        proc.wait(timeout=15.0)
        stderr = proc.stderr.read() if proc.stderr is not None else ""
    finally:
        _ensure_dead(proc)

    assert proc.returncode == 141
    assert stderr == ""
    jobs = fake_slurm.submitted()
    assert len(jobs) == 2
    for job in jobs:
        assert fake_slurm.job(job["id"])["state"] == "CANCELLED"


# ---------------------------------------------------------------------------
# AC5: a signal delivered while submission is inside blocked_signals()
# still cancels the id that sbatch already returned -- no leaked job.
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
def test_engine_run_sigterm_during_blocked_submission_cancels_recorded_job(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    ws = _make_workspace(tmp_path)
    install_fake_model(ws, sleep=1.0)
    _write_legacy_sintetizador(ws)
    shim = write_cli_shim(tmp_path)
    marker = tmp_path / "sbatch-entered"
    slurm_bin = _make_delayed_sbatch_bin(tmp_path, fake_slurm.bin_dir, marker)

    proc = _spawn_engine(
        shim,
        ws,
        slurm_bin,
        env_overrides={"HPCMU_CANCEL_TIMEOUT": "5"},
    )
    try:
        _wait_until(lambda: marker.exists())
        proc.send_signal(signal.SIGTERM)
        _, stderr = proc.communicate(timeout=15.0)
    finally:
        _ensure_dead(proc)

    assert proc.returncode == 143
    assert stderr == ""
    jobs = fake_slurm.submitted()
    assert len(jobs) == 1
    assert fake_slurm.job(jobs[0]["id"])["state"] == "CANCELLED"


# ---------------------------------------------------------------------------
# In-process: handler install/restore, driven by raising Terminated in the
# `with` body rather than sending a real signal. Never BrokenPipeError here
# -- that branch really dup2()s onto fd 1.
# ---------------------------------------------------------------------------


class _RecordingSlurm(Slurm):
    def __init__(self) -> None:
        super().__init__(Path("/nonexistent-bin"))
        self.cancel_calls: list[list[str]] = []
        self.wait_gone_calls: list[list[str]] = []
        self.sigterm_during_cancel: object = None
        self._cancel_error: SchedulerCommandError | None = None

    def raise_on_cancel(self, exc: SchedulerCommandError) -> None:
        self._cancel_error = exc

    def cancel(self, job_ids: Sequence[str]) -> None:
        self.sigterm_during_cancel = signal.getsignal(signal.SIGTERM)
        self.cancel_calls.append(list(job_ids))
        if self._cancel_error is not None:
            raise self._cancel_error

    def wait_gone(
        self,
        job_ids: Sequence[str],
        *,
        timeout: float,
        poll: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> bool:
        self.wait_gone_calls.append(list(job_ids))
        return True


class _RaisingSlurm(Slurm):
    """Fails the test if `cancel`/`wait_gone` is ever called."""

    def __init__(self) -> None:
        super().__init__(Path("/nonexistent-bin"))

    def cancel(self, job_ids: Sequence[str]) -> None:
        raise AssertionError("cancel must not be called with no ids")

    def wait_gone(
        self,
        job_ids: Sequence[str],
        *,
        timeout: float,
        poll: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> bool:
        raise AssertionError("wait_gone must not be called with no ids")


def _store(tmp_path: Path) -> StateStore:
    return StateStore(Workspace.at(tmp_path))


def test_cancel_on_termination_on_enter_installs_sigterm_and_sighup_handlers(
    tmp_path: Path,
) -> None:
    before_term = signal.getsignal(signal.SIGTERM)
    before_hup = signal.getsignal(signal.SIGHUP)

    with cancel_on_termination(
        _RaisingSlurm(), JobLedger(), _store(tmp_path), timeout=1.0
    ):
        assert signal.getsignal(signal.SIGTERM) is not before_term
        assert signal.getsignal(signal.SIGHUP) is not before_hup

    assert signal.getsignal(signal.SIGTERM) == before_term
    assert signal.getsignal(signal.SIGHUP) == before_hup


def test_cancel_on_termination_normal_exit_restores_previous_handlers(
    tmp_path: Path,
) -> None:
    before_term = signal.getsignal(signal.SIGTERM)
    before_hup = signal.getsignal(signal.SIGHUP)

    with cancel_on_termination(
        _RaisingSlurm(), JobLedger(), _store(tmp_path), timeout=1.0
    ):
        pass

    assert signal.getsignal(signal.SIGTERM) == before_term
    assert signal.getsignal(signal.SIGHUP) == before_hup


def test_cancel_on_termination_no_ids_skips_cancel_and_raises_system_exit(
    tmp_path: Path,
) -> None:
    before_term = signal.getsignal(signal.SIGTERM)

    with pytest.raises(SystemExit) as exc_info:
        with cancel_on_termination(
            _RaisingSlurm(), JobLedger(), _store(tmp_path), timeout=1.0
        ):
            raise Terminated(signal.SIGTERM)

    assert exc_info.value.code == 143
    assert signal.getsignal(signal.SIGTERM) == before_term


def test_cancel_on_termination_terminated_sets_sig_ignore_before_cancelling(
    tmp_path: Path,
) -> None:
    slurm = _RecordingSlurm()
    ledger = JobLedger()
    ledger.add("1000")

    with pytest.raises(SystemExit):
        with cancel_on_termination(
            slurm, ledger, _store(tmp_path), timeout=1.0
        ):
            raise Terminated(signal.SIGTERM)

    assert slurm.sigterm_during_cancel is signal.SIG_IGN


def test_cancel_on_termination_terminated_raises_exit_code_for_signum(
    tmp_path: Path,
) -> None:
    for signum, expected in (
        (signal.SIGTERM, 143),
        (signal.SIGHUP, 129),
    ):
        with pytest.raises(SystemExit) as exc_info:
            with cancel_on_termination(
                _RaisingSlurm(), JobLedger(), _store(tmp_path), timeout=1.0
            ):
                raise Terminated(signum)
        assert exc_info.value.code == expected


def test_cancel_on_termination_cancels_union_of_ledger_and_recorded_ids(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    state = store.load_or_create(_PLUGIN.name)
    store.save(
        replace(
            state,
            jobs=(
                JobRecord(Phase.MODEL, "2000", utc_now_iso(), "model.out"),
                JobRecord(
                    Phase.FINALIZE, "9001", utc_now_iso(), "finalize.out"
                ),
            ),
        )
    )
    ledger = JobLedger()
    ledger.add("1000")
    ledger.add("2000")
    slurm = _RecordingSlurm()

    with pytest.raises(SystemExit):
        with cancel_on_termination(slurm, ledger, store, timeout=1.0):
            raise Terminated(signal.SIGTERM)

    assert slurm.cancel_calls == [["1000", "2000", "9001"]]
    assert slurm.wait_gone_calls == [["1000", "2000", "9001"]]


def test_cancel_on_termination_swallows_scheduler_command_error_during_cancel(
    tmp_path: Path,
) -> None:
    slurm = _RecordingSlurm()
    slurm.raise_on_cancel(SchedulerCommandError("scancel unreachable"))
    ledger = JobLedger()
    ledger.add("1000")

    with pytest.raises(SystemExit) as exc_info:
        with cancel_on_termination(
            slurm, ledger, _store(tmp_path), timeout=1.0
        ):
            raise Terminated(signal.SIGTERM)

    assert exc_info.value.code == 143
    assert slurm.cancel_calls == [["1000"]]
    assert slurm.wait_gone_calls == []
