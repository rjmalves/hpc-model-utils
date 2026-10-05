"""The epic-02 integration gate (ticket-042, ADR-025/ADR-020/ADR-042/
ADR-053, R81/R95/R96/R11/R15/R43/R137): fake-SLURM end-to-end ``run``
+ ``publish``/``cancel`` scenarios, against the real E2 lifecycle
(``core.lifecycle.{run,publish,cancel}``), the executing fake SLURM,
``FakePlugin`` and the ticket-036 CLI shim.

Scenarios (c) and (d) each need a model/Slurm shape
``tests/support/fake_plugin.py``/``fake_slurm`` cannot express through
their own public surface alone; both additions are documented at their
own definition, following this suite's sibling system tests' own
"scripted double"/local-duplication precedent rather than adding a new
shared fake.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from hpc_model_utils.core.diagnosis import RunStatus
from hpc_model_utils.core.launch import Resources, Toolchain
from hpc_model_utils.core.lifecycle.cancel import cancel
from hpc_model_utils.core.lifecycle.finalize import legacy_synthesis_bin
from hpc_model_utils.core.lifecycle.prepare import extract_sanitize_inputs
from hpc_model_utils.core.lifecycle.publish import publish
from hpc_model_utils.core.lifecycle.run import JobLedger, SubmitRequest, run
from hpc_model_utils.core.settings import EngineSettings
from hpc_model_utils.core.state import (
    ExecutionSource,
    RunState,
    StateStore,
)
from hpc_model_utils.core.workspace import Phase, Workspace
from hpc_model_utils.infra.s3 import S3Uri
from hpc_model_utils.infra.slurm import JobState, Slurm
from hpc_model_utils.platform.modelops import Reporter
from tests.support.cli_shim import write_cli_shim
from tests.support.decks import input_zip
from tests.support.fake_plugin import FakePlugin, install_fake_model
from tests.support.fake_slurm import FakeSlurm
from tests.support.hooks import Hook, parse_hooks
from tests.support.object_store import RecordingObjectStore

_PLUGIN = FakePlugin()
_ARTIFACTS_URI = "s3://bucket-a/artifacts/" + "a" * 64 + "/"
_STATUS_HOOK_METHODS = frozenset(
    {"SetSuccess", "SetModelError", "SetDataError", "SetRuntimeError"}
)
_RELEVANT_HOOK_METHODS = _STATUS_HOOK_METHODS | {"SetExecutionArtifactsPath"}


@pytest.fixture(autouse=True)
def _chdir_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A submission without an explicit #SBATCH --output writes
    # slurm-<id>.out against the process cwd; pin it to tmp_path so it
    # never leaks into the repo.
    monkeypatch.chdir(tmp_path)


# ---------------------------------------------------------------------------
# Small module-level helpers, copied and adapted from the sibling
# ticket-037/038/040/041 system tests (they are not shared modules, so
# their private helpers cannot be imported directly).
# ---------------------------------------------------------------------------


class _ListChannel:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write_line(self, line: str) -> None:
        self.lines.append(line)


def _make_workspace(tmp_path: Path) -> Workspace:
    root = tmp_path / "study-fake"
    root.mkdir()
    return Workspace.at(root)


def _toolchain(tmp_path: Path, bin_dir: Path) -> Toolchain:
    return Toolchain(
        cli_bin=write_cli_shim(tmp_path),
        mpich_bin=tmp_path / "mpich" / "bin",
        slurm_bin=bin_dir,
    )


def _request(tools: Toolchain, *, skip_model: bool = False) -> SubmitRequest:
    return SubmitRequest(
        resources=Resources(queue="batch", cores=2),
        tools=tools,
        skip_model=skip_model,
        synthesis_bin=None,
    )


def _write_legacy_sintetizador(ws: Workspace, *, exit_code: int = 0) -> Path:
    stub = legacy_synthesis_bin(ws, _PLUGIN)
    stub.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "#!/bin/bash\nmkdir -p sintese\n: > sintese/x.parquet\n"
        f"exit {exit_code}\n"
    )
    stub.write_text(body, encoding="utf-8")
    stub.chmod(0o755)
    return stub


def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 5.0,
    interval: float = 0.01,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("condition not met before timeout")


def _install_crashing_model(ws: Workspace) -> Path:
    """Scenario (c)'s E12 guard needs a model that exits before ever
    writing ``fake.out``. ``install_fake_model``'s own options cannot
    express that ordering: ``token``/``exit_code``/``extra_lines`` all
    run ahead of its unconditional write-then-exit tail, and every
    ``fake_slurm.force()`` state that kills the process early
    (TIMEOUT/NODE_FAIL/CANCELLED) is also one ``core.diagnosis`` L1
    already maps to its own status, which would pre-empt the very
    guard this scenario targets. This mirrors ``install_fake_model``'s
    own file-writing shape with the write step removed -- the same
    local duplication this suite's siblings already use for
    ``_write_legacy_sintetizador`` rather than a new shared fake.
    """
    ws.assets.mkdir(parents=True, exist_ok=True)
    script = ws.assets / _PLUGIN.executables.entrypoint
    script.write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
    script.chmod(0o755)
    return script


class _WipeStateOnFirstPollSlurm(Slurm):
    """Scenario (d): ticket-038's technique. The fake's own
    ``force()`` can only override a job's bookkeeping once the real
    finalize process has already exited, by which point a
    well-behaved finalize has already written its record -- it cannot
    make the process crash before writing it. This double instead
    deletes ``state.json`` on the first queue poll ``run`` makes after
    ``submit`` returns, so the real finalize CLI subprocess genuinely
    crashes with a ``StateFormatError`` before it ever gets to write
    ``finalize.json``.
    """

    def __init__(self, bin_dir: Path, ws: Workspace) -> None:
        super().__init__(bin_dir)
        self._ws = ws
        self._wiped = False

    def job_state(self, job_id: str) -> JobState | None:
        if not self._wiped:
            self._wiped = True
            self._ws.state_path.unlink(missing_ok=True)
        return super().job_state(job_id)


class _SequencedChannel:
    """AC5 (h): feeds the hook lines it writes into a ``sequence``
    list shared with ``_SequencedStore``, so the two otherwise
    independent recordings (hook lines, store ops) can be compared on
    one single-threaded, real call-order timeline."""

    def __init__(self, sequence: list[tuple[str, str]]) -> None:
        self.lines: list[str] = []
        self._sequence = sequence

    def write_line(self, line: str) -> None:
        self.lines.append(line)
        self._sequence.append(("hook", line))


class _SequencedStore(RecordingObjectStore):
    def __init__(self, sequence: list[tuple[str, str]]) -> None:
        super().__init__()
        self._sequence = sequence

    def upload(self, src: Path, uri: S3Uri) -> None:
        super().upload(src, uri)
        self._sequence.append(("upload", str(uri)))


# ---------------------------------------------------------------------------
# Requirement 1: the shared scenario helper.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    state: RunState
    protocol: list[str]
    emitted: list[str]
    ops: list[tuple[str, str]]
    submitted: list[dict[str, Any]]
    sequence: list[tuple[str, str]]
    store: RecordingObjectStore


def run_scenario(
    tmp_path: Path,
    fake_slurm: FakeSlurm,
    *,
    token: str = "SUCCESS",
    model_exit: int = 0,
    force: tuple[str, str] | None = None,
    offline: bool = False,
    stale: tuple[tuple[str, str], ...] = (),
    synthesis_exit: int = 0,
    crash_model: bool = False,
    skip_model: bool = False,
    sleep: float = 0.0,
) -> ScenarioResult:
    ws = _make_workspace(tmp_path)
    store = StateStore(ws)
    sequence: list[tuple[str, str]] = []
    channel = _SequencedChannel(sequence)
    reporter = Reporter(channel, enabled=True)

    if stale:
        input_zip(
            {"deck.txt": b"deck", **{n: c.encode() for n, c in stale}},
            ws.eco_deck_path,
        )
        extract_sanitize_inputs(ws, _PLUGIN, store, reporter)
        for name, _ in stale:
            assert not (ws.root / name).exists(), name

    if force is not None:
        phase, state = force
        fake_slurm.force(phase, state=state)

    if offline:
        initial = store.load_or_create(_PLUGIN.name)
        store.save(replace(initial, execution_source=ExecutionSource.OFFLINE))
    if offline or skip_model:
        (ws.root / "fake.out").write_text(f"{token}\n", encoding="utf-8")
        (ws.root / "model.fake").write_bytes(b"")
    elif crash_model:
        _install_crashing_model(ws)
    else:
        install_fake_model(ws, token=token, exit_code=model_exit, sleep=sleep)
    _write_legacy_sintetizador(ws, exit_code=synthesis_exit)

    tools = _toolchain(tmp_path, fake_slurm.bin_dir)
    slurm = Slurm(fake_slurm.bin_dir)
    ledger = JobLedger()
    emitted: list[str] = []
    settings = EngineSettings(
        poll_interval=0.05,
        settle_window=0.2,
        outcome_attempts=3,
        outcome_backoff=0.1,
        cancel_timeout=5.0,
    )

    new_state = run(
        ws,
        _PLUGIN,
        slurm,
        reporter,
        store,
        _request(tools, skip_model=skip_model),
        ledger,
        settings=settings,
        emit=emitted.append,
    )

    object_store = _SequencedStore(sequence)
    publish(ws, _PLUGIN, object_store, reporter, store, _ARTIFACTS_URI)

    return ScenarioResult(
        state=new_state,
        protocol=list(channel.lines),
        emitted=emitted,
        ops=list(object_store.ops),
        submitted=fake_slurm.submitted(),
        sequence=sequence,
        store=object_store,
    )


# ---------------------------------------------------------------------------
# (a) success with afterany: AC2.
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
def test_run_scenario_a_success_afterany_orders_hooks_and_dependency(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    result = run_scenario(tmp_path, fake_slurm)

    assert result.state.diagnosis is not None
    assert result.state.diagnosis.status is RunStatus.SUCCESS
    model_entry, finalize_entry = result.submitted
    assert finalize_entry["dependency"] == model_entry["id"]
    assert (
        finalize_entry["directives"]["dependency"]
        == f"afterany:{model_entry['id']}"
    )

    stdout = "\n".join(result.protocol)
    ids = re.findall(r"Submitted batch job (\d+)", stdout)
    assert ids[-1] == str(finalize_entry["id"])

    hooks: list[Hook] = parse_hooks(stdout)
    relevant = [h for h in hooks if h.method in _RELEVANT_HOOK_METHODS]
    assert [h.method for h in relevant[-2:]] == [
        "SetExecutionArtifactsPath",
        "SetSuccess",
    ]
    assert relevant[-2].args == (_ARTIFACTS_URI,)


# ---------------------------------------------------------------------------
# (b) TIMEOUT forced on the model job: AC3.
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
def test_run_scenario_b_model_timeout_publishes_set_runtime_error(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    result = run_scenario(
        tmp_path, fake_slurm, force=("model", "TIMEOUT"), sleep=2.0
    )

    assert result.state.diagnosis is not None
    assert result.state.diagnosis.status is RunStatus.TIMEOUT
    assert result.state.diagnosis.rule_id == "slurm.timeout"

    hooks: list[Hook] = parse_hooks("\n".join(result.protocol))
    status_hooks = [h for h in hooks if h.method in _STATUS_HOOK_METHODS]
    assert len(status_hooks) == 1
    assert status_hooks[0] == Hook("SetRuntimeError", ())


# ---------------------------------------------------------------------------
# (c) the E12 guard: a model that crashes before writing fake.out,
# with a stale, INFEASIBLE-tagged fake.out purged by the real
# extract_sanitize_inputs before run() ever starts (ticket-045).
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
def test_run_scenario_c_model_crash_before_output_diagnoses_missing(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    result = run_scenario(
        tmp_path,
        fake_slurm,
        crash_model=True,
        stale=(("fake.out", "INFEASIBLE\n"),),
    )

    assert result.state.diagnosis is not None
    assert result.state.diagnosis.status is RunStatus.RUNTIME_ERROR
    assert result.state.diagnosis.rule_id == "core.missing_output"


# ---------------------------------------------------------------------------
# (d) a finalize crash: AC3. Bespoke (ticket-038's Slurm double, no
# model job), mirroring test_run_follow.py's own crash test exactly.
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
def test_run_scenario_d_finalize_crash_diagnoses_finalize_crashed(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    ws = _make_workspace(tmp_path)
    tools = _toolchain(tmp_path, fake_slurm.bin_dir)
    slurm = _WipeStateOnFirstPollSlurm(fake_slurm.bin_dir, ws)
    store = StateStore(ws)
    reporter = Reporter(_ListChannel(), enabled=True)
    ledger = JobLedger()
    settings = EngineSettings(
        poll_interval=0.05, settle_window=0.2, outcome_attempts=1
    )

    new_state = run(
        ws,
        _PLUGIN,
        slurm,
        reporter,
        store,
        _request(tools, skip_model=True),
        ledger,
        settings=settings,
        emit=lambda _line: None,
    )

    assert new_state.diagnosis is not None
    assert new_state.diagnosis.status is RunStatus.RUNTIME_ERROR
    assert new_state.diagnosis.rule_id == "core.finalize_crashed"
    assert "ended FAILED" in new_state.diagnosis.reason


# ---------------------------------------------------------------------------
# (e) cancel mid-run: AC4. Bespoke (needs a background thread for
# ``run`` while the main thread cancels).
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
def test_run_scenario_e_cancel_mid_run_diagnoses_finalize_crashed(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    ws = _make_workspace(tmp_path)
    install_fake_model(ws, sleep=30.0)
    tools = _toolchain(tmp_path, fake_slurm.bin_dir)
    slurm = Slurm(fake_slurm.bin_dir)
    store = StateStore(ws)
    channel = _ListChannel()
    reporter = Reporter(channel, enabled=True)
    ledger = JobLedger()
    settings = EngineSettings(
        poll_interval=0.05,
        settle_window=0.2,
        cancel_timeout=5.0,
        outcome_attempts=2,
        outcome_backoff=0.1,
    )
    results: list[RunState] = []

    def _run_in_background() -> None:
        results.append(
            run(
                ws,
                _PLUGIN,
                slurm,
                reporter,
                store,
                _request(tools),
                ledger,
                settings=settings,
                emit=lambda _line: None,
            )
        )

    thread = threading.Thread(target=_run_in_background)
    thread.start()
    try:
        _wait_until(lambda: len(ledger.ids) >= 2)
        model_id, finalize_id = ledger.ids[0], ledger.ids[1]
        _wait_until(lambda: fake_slurm.job(int(model_id))["state"] == "RUNNING")

        def _state_has_both_jobs() -> bool:
            recorded = store.load_optional()
            return recorded is not None and len(recorded.jobs) >= 2

        # cancel() reads state.json, not the in-memory ledger -- wait
        # for the persisted state to catch up with both submissions
        # before relying on it to decide what is safe to cancel.
        _wait_until(_state_has_both_jobs)

        cancel_result = cancel(
            ws, slurm, store, job_id=finalize_id, settings=settings
        )
    finally:
        thread.join(timeout=30.0)
    assert not thread.is_alive()

    assert cancel_result.cancelled == (model_id, finalize_id)
    assert fake_slurm.job(int(model_id))["state"] == "CANCELLED"
    assert fake_slurm.job(int(finalize_id))["state"] == "CANCELLED"

    assert len(results) == 1
    new_state = results[0]
    assert new_state.diagnosis is not None
    assert new_state.diagnosis.status is RunStatus.RUNTIME_ERROR
    assert new_state.diagnosis.rule_id == "core.finalize_crashed"
    assert "ended CANCELLED" in new_state.diagnosis.reason
    assert reporter.terminal_emitted is False
    assert not any(
        h.method in _STATUS_HOOK_METHODS
        for h in parse_hooks("\n".join(channel.lines))
    )


# ---------------------------------------------------------------------------
# (f) OFFLINE finalize-only.
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
def test_run_scenario_f_offline_submits_finalize_only(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    result = run_scenario(tmp_path, fake_slurm, offline=True)

    assert len(result.submitted) == 1
    assert len(result.state.jobs) == 1
    assert result.state.jobs[0].phase is Phase.FINALIZE
    assert result.state.diagnosis is not None
    assert result.state.diagnosis.status is RunStatus.SUCCESS


# ---------------------------------------------------------------------------
# (g) the jobId regex check, over a single-job (--skip) submission.
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
def test_run_scenario_g_skip_model_job_id_regex_matches_finalize(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    result = run_scenario(tmp_path, fake_slurm, skip_model=True)

    assert len(result.submitted) == 1
    stdout = "\n".join(result.protocol)
    ids = re.findall(r"Submitted batch job (\d+)", stdout)
    assert ids == [str(result.submitted[0]["id"])]
    assert ids[-1] == result.state.reported_job_id


# ---------------------------------------------------------------------------
# (h) status appears only after the uploads: AC5.
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
def test_run_scenario_h_status_hook_follows_last_upload_op(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    result = run_scenario(tmp_path, fake_slurm)

    assert result.ops[-1][1].endswith("saidas/metadata.modelops")
    assert result.ops[-2][1].endswith("saidas/run.json")

    status_positions: list[int] = []
    upload_positions: list[int] = []
    for index, (kind, detail) in enumerate(result.sequence):
        if kind == "upload":
            upload_positions.append(index)
        elif kind == "hook":
            for hook in parse_hooks(detail):
                if hook.method in _STATUS_HOOK_METHODS:
                    status_positions.append(index)

    assert len(status_positions) == 1
    assert upload_positions
    assert status_positions[0] > upload_positions[-1]


# ---------------------------------------------------------------------------
# (i) a SUCCESS model whose sintetizador fails: AC6.
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
def test_run_scenario_i_synthesis_failure_keeps_success(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    result = run_scenario(tmp_path, fake_slurm, synthesis_exit=2)

    assert result.state.diagnosis is not None
    assert result.state.diagnosis.status is RunStatus.SUCCESS

    hooks: list[Hook] = parse_hooks("\n".join(result.protocol))
    status_hooks = [h for h in hooks if h.method in _STATUS_HOOK_METHODS]
    assert status_hooks == [Hook("SetSuccess", ())]

    annotations = [h for h in hooks if h.method == "SetAnnotation"]
    assert len(annotations) == 1
    assert (
        annotations[0]
        .args[0]
        .startswith("SUCCESS: synthesis failed: synthesis tool exited 2; ")
    )
    assert ("SetMetadata", ("synthesis_status", "failed")) in {
        (h.method, h.args) for h in hooks
    }

    run_json_key = S3Uri.parse(_ARTIFACTS_URI).join("saidas/run.json")
    run_json = json.loads(result.store.get_bytes(run_json_key))
    assert run_json["diagnosis"]["evidence"][0]["source"] == "synthesis"
