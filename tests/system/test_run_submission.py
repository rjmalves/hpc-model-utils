"""System tests for the ``run`` submission step (ticket-037, R39, R11,
R96, R101, ADR-020), against the fake SLURM harness plus a few
stub-``sbatch`` bin directories for scenarios the full fake harness
cannot drive deterministically (a submission that fails on its
second call). Also covers the two unit-shaped pieces the ticket
places in this same file by scope: ``blocked_signals`` and
``JobLedger``, plus the structural proof that
``platform.modelops.Reporter`` satisfies ``StatusReporter``.
"""

from __future__ import annotations

import re
import shlex
import signal
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from hpc_model_utils.core.launch import Resources, Toolchain
from hpc_model_utils.core.lifecycle.run import (
    JobLedger,
    StatusReporter,
    SubmitRequest,
    blocked_signals,
    submit,
)
from hpc_model_utils.core.state import ExecutionSource, StateStore
from hpc_model_utils.core.workspace import Phase, Workspace
from hpc_model_utils.infra.errors import SchedulerCommandError
from hpc_model_utils.infra.slurm import Slurm
from hpc_model_utils.platform.modelops import Reporter
from tests.support.cli_shim import write_cli_shim
from tests.support.fake_plugin import FakePlugin, install_fake_model
from tests.support.fake_slurm import FakeSlurm

_PLUGIN = FakePlugin()
_WAIT_TIMEOUT = 1.0
_WAIT_INTERVAL = 0.01


@pytest.fixture(autouse=True)
def _chdir_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A submission without an explicit #SBATCH --output writes
    # slurm-<id>.out against the process cwd; pin it to tmp_path so it
    # never leaks into the repo.
    monkeypatch.chdir(tmp_path)


def _wait_until(
    predicate: Callable[[], bool], timeout: float = _WAIT_TIMEOUT
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(_WAIT_INTERVAL)
    raise AssertionError("condition not met before timeout")


class _ListChannel:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write_line(self, line: str) -> None:
        self.lines.append(line)


def _toolchain(tmp_path: Path, bin_dir: Path) -> Toolchain:
    return Toolchain(
        cli_bin=write_cli_shim(tmp_path),
        mpich_bin=tmp_path / "mpich" / "bin",
        slurm_bin=bin_dir,
    )


def _make_workspace(tmp_path: Path) -> Workspace:
    root = tmp_path / "study-fake"
    root.mkdir()
    return Workspace.at(root)


def _request(
    tools: Toolchain,
    *,
    skip_model: bool = False,
    synthesis_bin: Path | None = None,
) -> SubmitRequest:
    return SubmitRequest(
        resources=Resources(queue="batch", cores=2),
        tools=tools,
        skip_model=skip_model,
        synthesis_bin=synthesis_bin,
    )


# ---------------------------------------------------------------------------
# blocked_signals
# ---------------------------------------------------------------------------


def test_blocked_signals_restores_previous_mask_after_block() -> None:
    before = signal.pthread_sigmask(signal.SIG_BLOCK, set())

    with blocked_signals():
        during = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        assert signal.SIGTERM in during
        assert signal.SIGHUP in during

    after = signal.pthread_sigmask(signal.SIG_BLOCK, set())
    assert after == before


def test_blocked_signals_defers_pending_sigterm_until_block_exits() -> None:
    received: list[int] = []

    def handler(signum: int, frame: object | None) -> None:
        received.append(signum)

    previous_handler = signal.signal(signal.SIGTERM, handler)
    try:
        with blocked_signals():
            # Thread-directed: a process-directed kill could land on
            # any stray unblocked thread left alive by an earlier test,
            # and CPython would then run the handler here anyway.
            signal.pthread_kill(threading.get_ident(), signal.SIGTERM)
            assert received == []

        _wait_until(lambda: received != [])
        assert received == [signal.SIGTERM]
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


# ---------------------------------------------------------------------------
# JobLedger
# ---------------------------------------------------------------------------


def test_job_ledger_add_then_ids_returns_tuple_in_submission_order() -> None:
    ledger = JobLedger()

    ledger.add("1000")
    ledger.add("1001")

    assert ledger.ids == ("1000", "1001")
    assert isinstance(ledger.ids, tuple)


# ---------------------------------------------------------------------------
# StatusReporter / platform.modelops.Reporter conformance
# ---------------------------------------------------------------------------


def _accepts_status_reporter(reporter: StatusReporter) -> None:
    assert reporter is not None


def test_reporter_satisfies_status_reporter_protocol_structurally() -> None:
    reporter = Reporter(_ListChannel(), enabled=True)

    _accepts_status_reporter(reporter)


# ---------------------------------------------------------------------------
# submit(): normal two-job submission (AC2/AC3)
# ---------------------------------------------------------------------------


def test_submit_normal_announces_model_then_finalize_with_job_id_regex(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    ws = _make_workspace(tmp_path)
    install_fake_model(ws)
    tools = _toolchain(tmp_path, fake_slurm.bin_dir)
    slurm = Slurm(fake_slurm.bin_dir)
    store = StateStore(ws)
    channel = _ListChannel()
    reporter = Reporter(channel, enabled=True)
    ledger = JobLedger()

    result = submit(
        ws, _PLUGIN, slurm, reporter, store, _request(tools), ledger
    )

    assert result.model_id is not None
    assert channel.lines == [
        f"Submitted batch job {result.model_id}",
        f"Submitted batch job {result.finalize_id}",
    ]
    out = "\n".join(channel.lines)
    assert (
        re.findall(r"Submitted batch job (\d+)", out)[-1] == result.finalize_id
    )


def test_submit_normal_records_dependency_and_two_job_records(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    ws = _make_workspace(tmp_path)
    install_fake_model(ws)
    tools = _toolchain(tmp_path, fake_slurm.bin_dir)
    slurm = Slurm(fake_slurm.bin_dir)
    store = StateStore(ws)
    reporter = Reporter(_ListChannel(), enabled=True)
    ledger = JobLedger()

    result = submit(
        ws, _PLUGIN, slurm, reporter, store, _request(tools), ledger
    )

    assert result.model_id is not None
    finalize_entry = fake_slurm.submitted()[-1]
    assert finalize_entry["dependency"] == int(result.model_id)
    assert (
        finalize_entry["directives"]["dependency"]
        == f"afterany:{result.model_id}"
    )

    state = store.load()
    assert state is not None
    assert [job.phase for job in state.jobs] == [Phase.MODEL, Phase.FINALIZE]
    assert state.jobs[0].job_id == result.model_id
    assert state.jobs[0].log == f".hpcmu/logs/model-{result.model_id}.out"
    assert state.jobs[1].job_id == result.finalize_id
    assert state.jobs[1].log == f".hpcmu/logs/finalize-{result.finalize_id}.out"


# ---------------------------------------------------------------------------
# submit(): --skip
# ---------------------------------------------------------------------------


def test_submit_skip_model_submits_only_finalize_with_no_dependency(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    ws = _make_workspace(tmp_path)
    tools = _toolchain(tmp_path, fake_slurm.bin_dir)
    slurm = Slurm(fake_slurm.bin_dir)
    store = StateStore(ws)
    channel = _ListChannel()
    reporter = Reporter(channel, enabled=True)
    ledger = JobLedger()

    result = submit(
        ws,
        _PLUGIN,
        slurm,
        reporter,
        store,
        _request(tools, skip_model=True),
        ledger,
    )

    assert result.model_id is None
    assert channel.lines == [f"Submitted batch job {result.finalize_id}"]
    submitted = fake_slurm.submitted()
    assert len(submitted) == 1
    assert submitted[0]["dependency"] is None
    script_text = ws.job_script(Phase.FINALIZE).read_text(encoding="utf-8")
    assert "--model-job-id none" in script_text


# ---------------------------------------------------------------------------
# submit(): OFFLINE execution source (AC4)
# ---------------------------------------------------------------------------


def test_submit_offline_execution_source_submits_only_finalize(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    ws = _make_workspace(tmp_path)
    tools = _toolchain(tmp_path, fake_slurm.bin_dir)
    slurm = Slurm(fake_slurm.bin_dir)
    store = StateStore(ws)
    state = store.load_or_create(_PLUGIN.name)
    store.save(replace(state, execution_source=ExecutionSource.OFFLINE))
    channel = _ListChannel()
    reporter = Reporter(channel, enabled=True)
    ledger = JobLedger()

    result = submit(
        ws, _PLUGIN, slurm, reporter, store, _request(tools), ledger
    )

    assert result.model_id is None
    submitted = fake_slurm.submitted()
    assert len(submitted) == 1
    assert submitted[0]["dependency"] is None
    assert channel.lines == [f"Submitted batch job {result.finalize_id}"]
    script_text = ws.job_script(Phase.FINALIZE).read_text(encoding="utf-8")
    assert "--model-job-id none" in script_text


# ---------------------------------------------------------------------------
# submit(): the finalize sbatch call fails (AC5)
# ---------------------------------------------------------------------------


def _write_flaky_sbatch(bin_dir: Path, marker: Path) -> None:
    script = bin_dir / "sbatch"
    script.write_text(
        "#!/bin/bash\n"
        f"marker={shlex.quote(str(marker))}\n"
        'if [ -e "$marker" ]; then\n'
        '  echo "sbatch: error: submission failed" >&2\n'
        "  exit 1\n"
        "fi\n"
        'touch "$marker"\n'
        'echo "1000"\n'
    )
    script.chmod(0o755)


def test_submit_finalize_submission_failure_keeps_model_id_ledger_only(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_flaky_sbatch(bin_dir, tmp_path / "sbatch-called")
    ws = _make_workspace(tmp_path)
    tools = _toolchain(tmp_path, bin_dir)
    slurm = Slurm(bin_dir)
    store = StateStore(ws)
    reporter = Reporter(_ListChannel(), enabled=True)
    ledger = JobLedger()

    with pytest.raises(SchedulerCommandError, match="sbatch failed"):
        submit(ws, _PLUGIN, slurm, reporter, store, _request(tools), ledger)

    assert ledger.ids == ("1000",)
    state = store.load()
    assert state is not None
    assert state.jobs == ()


# ---------------------------------------------------------------------------
# submit(): --synthesis-bin propagation
# ---------------------------------------------------------------------------


def test_submit_synthesis_bin_propagates_into_finalize_script(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    ws = _make_workspace(tmp_path)
    install_fake_model(ws)
    tools = _toolchain(tmp_path, fake_slurm.bin_dir)
    synthesis_bin = tmp_path / "custom-sintetizador"
    synthesis_bin.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    synthesis_bin.chmod(0o755)
    slurm = Slurm(fake_slurm.bin_dir)
    store = StateStore(ws)
    reporter = Reporter(_ListChannel(), enabled=True)
    ledger = JobLedger()

    submit(
        ws,
        _PLUGIN,
        slurm,
        reporter,
        store,
        _request(tools, synthesis_bin=synthesis_bin),
        ledger,
    )

    script_text = ws.job_script(Phase.FINALIZE).read_text(encoding="utf-8")
    assert f"--synthesis-bin {shlex.quote(str(synthesis_bin))}" in script_text
