"""R42/R100/R101/R114/R137/ADR-010/ADR-053: the finalize lifecycle step."""

from __future__ import annotations

import dataclasses
import errno
import logging
import os
import shlex
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from hpc_model_utils.core.diagnosis import (
    Diagnosis,
    EvidenceItem,
    JobReport,
    RunStatus,
    Verdict,
)
from hpc_model_utils.core.errors import StateFormatError
from hpc_model_utils.core.lifecycle import finalize as finalize_module
from hpc_model_utils.core.lifecycle.finalize import (
    finalize,
    legacy_synthesis_bin,
    physical_cores,
    synthesis_status,
)
from hpc_model_utils.core.outputs import OutputPlan, RealizedOutputs
from hpc_model_utils.core.settings import EngineSettings
from hpc_model_utils.core.state import (
    FinalizeRecord,
    StateStore,
    StepOutcome,
    StepRecord,
    load_finalize,
    write_finalize,
)
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.infra.errors import ShellCommandError
from hpc_model_utils.infra.shell import ShellResult
from hpc_model_utils.infra.slurm import JobOutcome
from tests.support.fake_plugin import FakePlugin

_SETTINGS = EngineSettings()
_FINALIZE_LOGGER = "hpc_model_utils.core.lifecycle.finalize"
_SYNTHESIS_OUTPUT = (
    "2026-10-04 20:21:31,000 INFO: a",
    "2026-10-04 20:21:32,000 ERROR: b",
    "Traceback (most recent call last):",
    '  File "x", line 1',
    "2026-10-04 20:21:33,000 WARNING: c",
)


def _noop(line: str) -> None:
    return None


def _job_outcome(state: str = "COMPLETED") -> JobOutcome:
    return JobOutcome(
        job_id="111",
        state=state,
        exit_code=0,
        signal=None,
        elapsed="00:00:05",
        time_limit="01:00:00",
        oom=False,
        source="sacct",
        raw="",
    )


def _minimal_diagnosis() -> Diagnosis:
    return Diagnosis(status=RunStatus.SUCCESS, rule_id="core.fake", reason="ok")


@dataclass
class StubSlurm:
    value: JobOutcome
    calls: list[str] = field(default_factory=list)

    def outcome(
        self, job_id: str, *, attempts: int = 6, backoff: float = 10.0
    ) -> JobOutcome:
        self.calls.append(job_id)
        return self.value


class _UnusedSlurm:
    def outcome(
        self, job_id: str, *, attempts: int = 6, backoff: float = 10.0
    ) -> JobOutcome:
        raise AssertionError(
            "slurm.outcome must not be called when model_job_id is None"
        )


class _RaisingPostprocessPlugin(FakePlugin):
    def postprocess(self, ws: Workspace) -> None:
        raise RuntimeError("boom")


class _RaisingOutputsPlugin(FakePlugin):
    def outputs(self, ws: Workspace) -> OutputPlan:
        raise ValueError("boom-outputs")


class _RaisingSynthesisArgsPlugin(FakePlugin):
    def synthesis_args(
        self, ws: Workspace, cpus: int
    ) -> tuple[str, ...] | None:
        raise RuntimeError("args-boom")


class _NoSynthesisArgsPlugin(FakePlugin):
    def synthesis_args(
        self, ws: Workspace, cpus: int
    ) -> tuple[str, ...] | None:
        return None


class _SynthesisArgsCapturingPlugin(FakePlugin):
    captured_ws: Workspace | None = None
    captured_cpus: int | None = None

    def synthesis_args(
        self, ws: Workspace, cpus: int
    ) -> tuple[str, ...] | None:
        self.captured_ws = ws
        self.captured_cpus = cpus
        return super().synthesis_args(ws, cpus)


class _CapturingPlugin(FakePlugin):
    captured_process_exit: int | None = None

    def diagnose(self, ws: Workspace, job: JobReport) -> Verdict:
        self.captured_process_exit = job.process_exit
        return super().diagnose(ws, job)


class _TwentyEvidencePlugin(FakePlugin):
    def diagnose(self, ws: Workspace, job: JobReport) -> Verdict:
        first_line = (
            (ws.root / "fake.out").read_text(encoding="utf-8").splitlines()[0]
        )
        evidence = tuple(
            EvidenceItem("plugin", f"item{i}", f"detail{i}") for i in range(20)
        )
        return Verdict(
            RunStatus.parse(first_line),
            "core.fake",
            f"fake.out token {first_line!r}",
            evidence=evidence,
        )


def _prepare_workspace(tmp_path: Path, *, token: str = "SUCCESS") -> Workspace:
    ws = Workspace.at(tmp_path)
    StateStore(ws).load_or_create("fake")
    (ws.root / "deck.txt").write_text("", encoding="utf-8")
    (ws.root / "fake.out").write_text(f"{token}\n", encoding="utf-8")
    (ws.root / "model.fake").write_text("", encoding="utf-8")
    return ws


def _install_legacy_sintetizador(
    ws: Workspace,
    plugin_name: str,
    *,
    exit_code: int = 0,
    output: Sequence[str] = (),
) -> Path:
    bin_path = (
        ws.root
        / f"sintetizador-{plugin_name}"
        / "venv"
        / "bin"
        / f"sintetizador-{plugin_name}"
    )
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    printed = "".join(
        f"printf '%s\\n' {shlex.quote(text)}\n" for text in output
    )
    script = (
        "#!/bin/bash\nset -u\nmkdir -p sintese\n: > sintese/x.parquet\n"
        f"{printed}exit {exit_code}\n"
    )
    bin_path.write_text(script, encoding="utf-8")
    bin_path.chmod(0o755)
    return bin_path


def _stub_shell_run(
    *,
    returncode: int = 0,
    timed_out: bool = False,
    output: tuple[str, ...] = (),
) -> Callable[..., ShellResult]:
    def _run(
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
        on_line: Callable[[str], None] | None = None,
        keep_output: bool = True,
    ) -> ShellResult:
        return ShellResult(
            argv=tuple(argv),
            returncode=returncode,
            timed_out=timed_out,
            output=output,
        )

    return _run


def _raising_shell_run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    on_line: Callable[[str], None] | None = None,
    keep_output: bool = True,
) -> ShellResult:
    raise ShellCommandError("lscpu: command not found")


class _FlakyLogFile:
    def __init__(
        self, *, fail_write_after: int | None = None, fail_close: bool = False
    ) -> None:
        self.written: list[str] = []
        self._fail_write_after = fail_write_after
        self._fail_close = fail_close

    def write(self, text: str) -> int:
        if (
            self._fail_write_after is not None
            and len(self.written) >= self._fail_write_after
        ):
            raise OSError(errno.ENOSPC, "No space left on device")
        self.written.append(text)
        return len(text)

    def close(self) -> None:
        if self._fail_close:
            raise OSError(errno.EIO, "Input/output error")


def _swap_synthesis_log_file(
    monkeypatch: pytest.MonkeyPatch, ws: Workspace, flaky: _FlakyLogFile
) -> None:
    real_open = Path.open

    def _open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self == ws.synthesis_log_path:
            return flaky
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _open)


def _run_finalize(ws: Workspace, emit: Callable[[str], None]) -> FinalizeRecord:
    return finalize(
        ws,
        FakePlugin(),
        StubSlurm(_job_outcome()),
        model_job_id="111",
        cores=2,
        synthesis_bin=None,
        settings=_SETTINGS,
        emit=emit,
    )


def _finalize_messages(
    caplog: pytest.LogCaptureFixture, level: int
) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == _FINALIZE_LOGGER and record.levelno == level
    ]


# -- finalize() --------------------------------------------------------


def test_finalize_success_with_synthesis_writes_success_record_and_outputs(
    tmp_path: Path,
) -> None:
    ws = _prepare_workspace(tmp_path)
    _install_legacy_sintetizador(ws, "fake")
    state = StateStore(ws).load()
    assert state is not None
    slurm = StubSlurm(_job_outcome())

    record = finalize(
        ws,
        FakePlugin(),
        slurm,
        model_job_id="111",
        cores=4,
        synthesis_bin=None,
        settings=_SETTINGS,
        emit=_noop,
    )

    assert record.diagnosis.status is RunStatus.SUCCESS
    assert record.synthesis is not None
    assert record.synthesis.ok is True
    assert record.outputs is not None
    assert record.outputs.archives == (".hpcmu/outputs/fake.zip",)
    loaded = load_finalize(ws, state.run_id)
    assert loaded == record


def test_finalize_no_synthesis_bin_and_no_legacy_sets_synthesis_missing(
    tmp_path: Path,
) -> None:
    ws = _prepare_workspace(tmp_path)
    slurm = StubSlurm(_job_outcome())

    record = finalize(
        ws,
        FakePlugin(),
        slurm,
        model_job_id="111",
        cores=2,
        synthesis_bin=None,
        settings=_SETTINGS,
        emit=_noop,
    )

    assert record.diagnosis.status is RunStatus.RUNTIME_ERROR
    assert record.diagnosis.rule_id == "core.synthesis_missing"
    legacy = legacy_synthesis_bin(ws, FakePlugin())
    assert str(legacy) in record.diagnosis.reason
    assert record.diagnosis.reason.endswith("; pass --synthesis-bin")
    assert "core.fake" in record.diagnosis.matched


def test_finalize_synthesis_nonzero_exit_keeps_success_with_evidence_first(
    tmp_path: Path,
) -> None:
    ws = _prepare_workspace(tmp_path)
    _install_legacy_sintetizador(ws, "fake", exit_code=3)
    plugin = _TwentyEvidencePlugin()
    slurm = StubSlurm(_job_outcome())

    record = finalize(
        ws,
        plugin,
        slurm,
        model_job_id="111",
        cores=2,
        synthesis_bin=None,
        settings=_SETTINGS,
        emit=_noop,
    )

    assert record.diagnosis.status is RunStatus.SUCCESS
    assert record.diagnosis.rule_id == "core.fake"
    assert record.synthesis is not None
    assert record.synthesis.ok is False
    assert record.diagnosis.reason.startswith(
        "synthesis failed: synthesis tool exited 3; "
    )
    assert record.diagnosis.evidence[0] == EvidenceItem(
        "plugin", "synthesis", "synthesis tool exited 3"
    )
    assert len(record.diagnosis.evidence) == 20
    assert synthesis_status(record) == "failed"


def test_finalize_timeout_outcome_skips_postprocess_and_synthesis(
    tmp_path: Path,
) -> None:
    ws = _prepare_workspace(tmp_path)
    slurm = StubSlurm(_job_outcome(state="TIMEOUT"))

    record = finalize(
        ws,
        FakePlugin(),
        slurm,
        model_job_id="111",
        cores=2,
        synthesis_bin=None,
        settings=_SETTINGS,
        emit=_noop,
    )

    assert record.diagnosis.status is RunStatus.TIMEOUT
    assert record.postprocess is None
    assert record.synthesis is None
    assert record.outputs is not None


def test_finalize_leaves_state_json_byte_identical(tmp_path: Path) -> None:
    ws = _prepare_workspace(tmp_path)
    _install_legacy_sintetizador(ws, "fake")
    before = ws.state_path.read_bytes()
    slurm = StubSlurm(_job_outcome())

    finalize(
        ws,
        FakePlugin(),
        slurm,
        model_job_id="111",
        cores=2,
        synthesis_bin=None,
        settings=_SETTINGS,
        emit=_noop,
    )

    assert ws.state_path.read_bytes() == before


def test_finalize_steps_holds_only_its_own_step_not_state_steps(
    tmp_path: Path,
) -> None:
    ws = _prepare_workspace(tmp_path)
    _install_legacy_sintetizador(ws, "fake")
    existing = StateStore(ws).load()
    assert existing is not None
    prior_steps = (
        StepRecord(
            command="submit-model",
            host="login01",
            started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T00:00:05+00:00",
            duration_seconds=5.0,
            outcome="ok",
        ),
        StepRecord(
            command="follow",
            host="login01",
            started_at="2026-01-01T00:00:05+00:00",
            finished_at="2026-01-01T00:05:00+00:00",
            duration_seconds=295.0,
            outcome="ok",
        ),
    )
    StateStore(ws).save(dataclasses.replace(existing, steps=prior_steps))
    slurm = StubSlurm(_job_outcome())

    record = finalize(
        ws,
        FakePlugin(),
        slurm,
        model_job_id="111",
        cores=2,
        synthesis_bin=None,
        settings=_SETTINGS,
        emit=_noop,
    )

    assert len(record.steps) == 1
    assert record.steps[0].command == "finalize"
    for prior in prior_steps:
        assert prior not in record.steps


def test_finalize_step_record_has_nonnegative_duration_and_ordered_times(
    tmp_path: Path,
) -> None:
    ws = _prepare_workspace(tmp_path)
    _install_legacy_sintetizador(ws, "fake")
    slurm = StubSlurm(_job_outcome())

    record = finalize(
        ws,
        FakePlugin(),
        slurm,
        model_job_id="111",
        cores=2,
        synthesis_bin=None,
        settings=_SETTINGS,
        emit=_noop,
    )

    assert len(record.steps) == 1
    step = record.steps[0]
    assert step.host != ""
    assert step.outcome == "ok"
    assert step.duration_seconds >= 0
    started = datetime.fromisoformat(step.started_at)
    finished = datetime.fromisoformat(step.finished_at)
    assert started <= finished


def test_finalize_both_steps_failing_orders_synthesis_prefix_first(
    tmp_path: Path,
) -> None:
    ws = _prepare_workspace(tmp_path)
    _install_legacy_sintetizador(ws, "fake", exit_code=5)
    plugin = _RaisingPostprocessPlugin()
    slurm = StubSlurm(_job_outcome())

    record = finalize(
        ws,
        plugin,
        slurm,
        model_job_id="111",
        cores=2,
        synthesis_bin=None,
        settings=_SETTINGS,
        emit=_noop,
    )

    assert record.diagnosis.status is RunStatus.SUCCESS
    assert record.postprocess is not None
    assert record.postprocess.ok is False
    assert record.synthesis is not None
    assert record.synthesis.ok is False
    assert record.diagnosis.reason.startswith(
        "synthesis failed: synthesis tool exited 5; postprocess failed: boom; "
    )
    assert record.diagnosis.evidence[0] == EvidenceItem(
        "plugin", "synthesis", "synthesis tool exited 5"
    )
    assert record.diagnosis.evidence[1] == EvidenceItem(
        "plugin", "postprocess", "boom"
    )


def test_finalize_postprocess_exception_keeps_success_with_prefix(
    tmp_path: Path,
) -> None:
    ws = _prepare_workspace(tmp_path)
    _install_legacy_sintetizador(ws, "fake")
    plugin = _RaisingPostprocessPlugin()
    slurm = StubSlurm(_job_outcome())

    record = finalize(
        ws,
        plugin,
        slurm,
        model_job_id="111",
        cores=2,
        synthesis_bin=None,
        settings=_SETTINGS,
        emit=_noop,
    )

    assert record.diagnosis.status is RunStatus.SUCCESS
    assert record.postprocess is not None
    assert record.postprocess.ok is False
    assert record.diagnosis.reason.startswith("postprocess failed: boom; ")
    assert record.synthesis is not None
    assert record.synthesis.ok is True


def test_finalize_outputs_plugin_raises_sets_outputs_failed(
    tmp_path: Path,
) -> None:
    ws = _prepare_workspace(tmp_path)
    _install_legacy_sintetizador(ws, "fake")
    plugin = _RaisingOutputsPlugin()
    slurm = StubSlurm(_job_outcome())

    record = finalize(
        ws,
        plugin,
        slurm,
        model_job_id="111",
        cores=2,
        synthesis_bin=None,
        settings=_SETTINGS,
        emit=_noop,
    )

    assert record.diagnosis.status is RunStatus.RUNTIME_ERROR
    assert record.diagnosis.rule_id == "core.outputs_failed"
    assert "core.fake" in record.diagnosis.matched
    assert record.outputs is None


def test_finalize_synthesis_args_raises_records_synthesis_failure(
    tmp_path: Path,
) -> None:
    ws = _prepare_workspace(tmp_path)
    plugin = _RaisingSynthesisArgsPlugin()
    slurm = StubSlurm(_job_outcome())

    record = finalize(
        ws,
        plugin,
        slurm,
        model_job_id="111",
        cores=2,
        synthesis_bin=None,
        settings=_SETTINGS,
        emit=_noop,
    )

    assert record.diagnosis.status is RunStatus.SUCCESS
    assert record.synthesis is not None
    assert record.synthesis.ok is False
    assert record.synthesis.detail == "args-boom"
    assert record.diagnosis.reason.startswith("synthesis failed: args-boom; ")


def test_finalize_passes_its_workspace_to_synthesis_args(
    tmp_path: Path,
) -> None:
    ws = _prepare_workspace(tmp_path)
    _install_legacy_sintetizador(ws, "fake")
    plugin = _SynthesisArgsCapturingPlugin()

    record = finalize(
        ws,
        plugin,
        StubSlurm(_job_outcome()),
        model_job_id="111",
        cores=2,
        synthesis_bin=None,
        settings=_SETTINGS,
        emit=_noop,
    )

    assert record.diagnosis.status is RunStatus.SUCCESS
    assert plugin.captured_ws is ws
    assert plugin.captured_cpus == min(2, physical_cores())


def test_finalize_explicit_synthesis_bin_missing_ignores_existing_legacy(
    tmp_path: Path,
) -> None:
    ws = _prepare_workspace(tmp_path)
    _install_legacy_sintetizador(ws, "fake")
    missing_bin = tmp_path / "does-not-exist" / "sintetizador-fake"
    slurm = StubSlurm(_job_outcome())

    record = finalize(
        ws,
        FakePlugin(),
        slurm,
        model_job_id="111",
        cores=2,
        synthesis_bin=missing_bin,
        settings=_SETTINGS,
        emit=_noop,
    )

    assert record.diagnosis.rule_id == "core.synthesis_missing"
    assert str(missing_bin) in record.diagnosis.reason
    legacy = legacy_synthesis_bin(ws, FakePlugin())
    assert str(legacy) not in record.diagnosis.reason
    assert "pass --synthesis-bin" not in record.diagnosis.reason


def test_finalize_synthesis_bin_not_executable_records_failure_not_fatal(
    tmp_path: Path,
) -> None:
    ws = _prepare_workspace(tmp_path)
    bad_bin = ws.root / "sintetizador-fake-bad"
    bad_bin.write_text("not a script\n", encoding="utf-8")
    slurm = StubSlurm(_job_outcome())

    record = finalize(
        ws,
        FakePlugin(),
        slurm,
        model_job_id="111",
        cores=2,
        synthesis_bin=bad_bin,
        settings=_SETTINGS,
        emit=_noop,
    )

    assert record.diagnosis.status is RunStatus.SUCCESS
    assert record.synthesis is not None
    assert record.synthesis.ok is False
    assert record.diagnosis.reason.startswith("synthesis failed: ")


def test_finalize_synthesis_output_goes_to_log_and_only_warnings_are_emitted(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = _prepare_workspace(tmp_path)
    _install_legacy_sintetizador(ws, "fake", output=_SYNTHESIS_OUTPUT)
    emitted: list[str] = []

    with caplog.at_level(logging.INFO, logger=_FINALIZE_LOGGER):
        record = _run_finalize(ws, emitted.append)

    log_text = ws.synthesis_log_path.read_text(encoding="utf-8")
    assert log_text.splitlines() == list(_SYNTHESIS_OUTPUT)
    assert emitted == [_SYNTHESIS_OUTPUT[1], _SYNTHESIS_OUTPUT[4]]
    assert _finalize_messages(caplog, logging.INFO) == [
        "synthesis tool exited 0: 5 lines in saidas/logs/synthesis.out, "
        "2 at WARNING or above"
    ]
    assert record.synthesis is not None
    assert record.synthesis.ok is True


def test_finalize_synthesis_nonzero_exit_logs_output_and_summarizes_exit_code(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = _prepare_workspace(tmp_path)
    _install_legacy_sintetizador(
        ws, "fake", exit_code=3, output=_SYNTHESIS_OUTPUT[:2]
    )

    with caplog.at_level(logging.INFO, logger=_FINALIZE_LOGGER):
        record = _run_finalize(ws, _noop)

    assert ws.synthesis_log_path.read_text(
        encoding="utf-8"
    ).splitlines() == list(_SYNTHESIS_OUTPUT[:2])
    assert _finalize_messages(caplog, logging.INFO) == [
        "synthesis tool exited 3: 2 lines in saidas/logs/synthesis.out, "
        "1 at WARNING or above"
    ]
    assert record.diagnosis.status is RunStatus.SUCCESS
    assert record.synthesis is not None
    assert record.synthesis.ok is False


def test_finalize_synthesis_log_overwrites_previous_attempt(
    tmp_path: Path,
) -> None:
    ws = _prepare_workspace(tmp_path)
    _install_legacy_sintetizador(ws, "fake", output=_SYNTHESIS_OUTPUT[:1])
    ws.synthesis_log_path.write_text("stale\nstale\n", encoding="utf-8")

    _run_finalize(ws, _noop)

    assert ws.synthesis_log_path.read_text(encoding="utf-8").splitlines() == [
        _SYNTHESIS_OUTPUT[0]
    ]


@pytest.mark.parametrize(
    ("plugin", "job_state"),
    [
        pytest.param(FakePlugin(), "COMPLETED", id="binary-missing"),
        pytest.param(
            _RaisingSynthesisArgsPlugin(), "COMPLETED", id="args-failure"
        ),
        pytest.param(_NoSynthesisArgsPlugin(), "COMPLETED", id="args-none"),
        pytest.param(FakePlugin(), "TIMEOUT", id="model-not-successful"),
    ],
)
def test_finalize_without_running_synthesis_discards_stale_synthesis_log(
    tmp_path: Path, plugin: FakePlugin, job_state: str
) -> None:
    ws = _prepare_workspace(tmp_path)
    ws.synthesis_log_path.write_text("stale\n", encoding="utf-8")

    finalize(
        ws,
        plugin,
        StubSlurm(_job_outcome(state=job_state)),
        model_job_id="111",
        cores=2,
        synthesis_bin=None,
        settings=_SETTINGS,
        emit=_noop,
    )

    assert not ws.synthesis_log_path.exists()


def test_finalize_stale_synthesis_log_unremovable_warns_and_keeps_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = _prepare_workspace(tmp_path)
    ws.synthesis_log_path.write_text("stale\n", encoding="utf-8")

    real_unlink = Path.unlink

    def _unlink(self: Path, missing_ok: bool = False) -> None:
        if self == ws.synthesis_log_path:
            raise PermissionError(errno.EACCES, "Permission denied")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", _unlink)

    with caplog.at_level(logging.WARNING, logger=_FINALIZE_LOGGER):
        record = _run_finalize(ws, _noop)

    warnings = _finalize_messages(caplog, logging.WARNING)
    assert len(warnings) == 1
    assert warnings[0].startswith("stale synthesis log not removed")
    assert record.diagnosis.rule_id == "core.synthesis_missing"


def test_finalize_synthesis_log_unopenable_relays_every_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = _prepare_workspace(tmp_path)
    _install_legacy_sintetizador(ws, "fake", output=_SYNTHESIS_OUTPUT)
    ws.synthesis_log_path.mkdir()
    emitted: list[str] = []

    with caplog.at_level(logging.INFO, logger=_FINALIZE_LOGGER):
        record = _run_finalize(ws, emitted.append)

    assert emitted == list(_SYNTHESIS_OUTPUT)
    warnings = [
        message
        for message in _finalize_messages(caplog, logging.WARNING)
        if message.startswith("synthesis log unavailable")
    ]
    assert len(warnings) == 1
    assert _finalize_messages(caplog, logging.INFO) == []
    assert record.synthesis is not None
    assert record.synthesis.ok is True


def test_finalize_synthesis_log_write_failure_relays_the_remaining_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = _prepare_workspace(tmp_path)
    _install_legacy_sintetizador(ws, "fake", output=_SYNTHESIS_OUTPUT)
    flaky = _FlakyLogFile(fail_write_after=2)
    _swap_synthesis_log_file(monkeypatch, ws, flaky)
    emitted: list[str] = []

    with caplog.at_level(logging.INFO, logger=_FINALIZE_LOGGER):
        record = _run_finalize(ws, emitted.append)

    assert flaky.written == [f"{line}\n" for line in _SYNTHESIS_OUTPUT[:2]]
    assert emitted == list(_SYNTHESIS_OUTPUT[1:])
    warnings = _finalize_messages(caplog, logging.WARNING)
    assert len(warnings) == 1
    assert warnings[0].startswith("synthesis log write failed")
    assert _finalize_messages(caplog, logging.INFO) == [
        "synthesis tool exited 0: 2 lines in saidas/logs/synthesis.out, "
        "1 at WARNING or above"
    ]
    assert record.synthesis is not None
    assert record.synthesis.ok is True


def test_finalize_synthesis_log_close_failure_keeps_synthesis_ok(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = _prepare_workspace(tmp_path)
    _install_legacy_sintetizador(ws, "fake", output=_SYNTHESIS_OUTPUT)
    _swap_synthesis_log_file(monkeypatch, ws, _FlakyLogFile(fail_close=True))

    with caplog.at_level(logging.WARNING, logger=_FINALIZE_LOGGER):
        record = _run_finalize(ws, _noop)

    warnings = _finalize_messages(caplog, logging.WARNING)
    assert len(warnings) == 1
    assert warnings[0].startswith("synthesis log close failed")
    assert record.synthesis is not None
    assert record.synthesis.ok is True


def test_finalize_synthesis_exec_failure_logs_no_summary(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = _prepare_workspace(tmp_path)
    bad_bin = ws.root / "sintetizador-fake-bad"
    bad_bin.write_text("not a script\n", encoding="utf-8")

    with caplog.at_level(logging.INFO, logger=_FINALIZE_LOGGER):
        record = finalize(
            ws,
            FakePlugin(),
            StubSlurm(_job_outcome()),
            model_job_id="111",
            cores=2,
            synthesis_bin=bad_bin,
            settings=_SETTINGS,
            emit=_noop,
        )

    assert _finalize_messages(caplog, logging.INFO) == []
    assert record.synthesis is not None
    assert record.synthesis.ok is False


def test_finalize_model_job_id_none_never_calls_slurm_outcome(
    tmp_path: Path,
) -> None:
    ws = _prepare_workspace(tmp_path)
    _install_legacy_sintetizador(ws, "fake")

    record = finalize(
        ws,
        FakePlugin(),
        _UnusedSlurm(),
        model_job_id=None,
        cores=2,
        synthesis_bin=None,
        settings=_SETTINGS,
        emit=_noop,
    )

    assert record.diagnosis.job_id is None
    assert record.diagnosis.status is RunStatus.SUCCESS


def test_finalize_malformed_model_exit_file_treated_as_none(
    tmp_path: Path,
) -> None:
    ws = _prepare_workspace(tmp_path)
    _install_legacy_sintetizador(ws, "fake")
    ws.model_exit_path.write_text("not-an-int\n", encoding="utf-8")
    plugin = _CapturingPlugin()
    slurm = StubSlurm(_job_outcome())

    finalize(
        ws,
        plugin,
        slurm,
        model_job_id="111",
        cores=2,
        synthesis_bin=None,
        settings=_SETTINGS,
        emit=_noop,
    )

    assert plugin.captured_process_exit is None


# -- FinalizeRecord codec / load_finalize ------------------------------


def test_finalize_record_round_trip_via_write_and_load_finalize(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    ws.ensure_layout()
    diag = Diagnosis(
        status=RunStatus.SUCCESS,
        rule_id="core.fake",
        reason="ok",
        evidence=(EvidenceItem("plugin", "synthesis", "detail"),),
        matched=("core.fake",),
        job_id="111",
        at="2026-01-01T00:00:00+00:00",
    )
    record = FinalizeRecord(
        run_id="run-1",
        diagnosis=diag,
        postprocess=StepOutcome("postprocess", True, "", 1.5),
        synthesis=StepOutcome(
            "synthesis", False, "synthesis tool exited 3", 2.5
        ),
        outputs=RealizedOutputs(
            deck=".hpcmu/outputs/deck_processado.zip",
            archives=(".hpcmu/outputs/fake.zip",),
            raw=(("fake.out", "fake.out"),),
        ),
        steps=(
            StepRecord(
                command="submit-model",
                host="login01",
                started_at="2026-01-01T00:00:00+00:00",
                finished_at="2026-01-01T00:00:05+00:00",
                duration_seconds=5.0,
                outcome="ok",
            ),
        ),
    )

    write_finalize(ws, record)
    reloaded = load_finalize(ws, "run-1")

    assert reloaded == record


def test_load_finalize_missing_file_returns_none(tmp_path: Path) -> None:
    ws = Workspace.at(tmp_path)
    ws.ensure_layout()
    assert load_finalize(ws, "run-1") is None


def test_load_finalize_run_id_mismatch_raises_state_format_error(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    ws.ensure_layout()
    write_finalize(
        ws, FinalizeRecord(run_id="aaa", diagnosis=_minimal_diagnosis())
    )

    with pytest.raises(StateFormatError, match="run_id"):
        load_finalize(ws, "bbb")


def test_load_finalize_malformed_json_raises_state_format_error(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    ws.ensure_layout()
    ws.finalize_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(StateFormatError):
        load_finalize(ws, "run-1")


# -- synthesis_status() -------------------------------------------------


def test_synthesis_status_no_steps_ran_returns_none() -> None:
    record = FinalizeRecord(run_id="r", diagnosis=_minimal_diagnosis())
    assert synthesis_status(record) is None


def test_synthesis_status_both_steps_ok_returns_ok() -> None:
    record = FinalizeRecord(
        run_id="r",
        diagnosis=_minimal_diagnosis(),
        postprocess=StepOutcome("postprocess", True, "", 0.1),
        synthesis=StepOutcome("synthesis", True, "", 0.1),
    )
    assert synthesis_status(record) == "ok"


def test_synthesis_status_one_step_failed_returns_failed() -> None:
    record = FinalizeRecord(
        run_id="r",
        diagnosis=_minimal_diagnosis(),
        postprocess=StepOutcome("postprocess", True, "", 0.1),
        synthesis=StepOutcome("synthesis", False, "boom", 0.1),
    )
    assert synthesis_status(record) == "failed"


# -- physical_cores() ----------------------------------------------------


def test_physical_cores_counts_unique_core_socket_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        finalize_module,
        "shell_run",
        _stub_shell_run(output=("# comment", "0,0", "0,0", "1,0", "2,1")),
    )
    assert physical_cores() == 3


def test_physical_cores_falls_back_when_lscpu_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(finalize_module, "shell_run", _raising_shell_run)
    assert physical_cores() == (os.cpu_count() or 1)


def test_physical_cores_falls_back_when_lscpu_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        finalize_module, "shell_run", _stub_shell_run(returncode=1)
    )
    assert physical_cores() == (os.cpu_count() or 1)


def test_physical_cores_falls_back_when_lscpu_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        finalize_module, "shell_run", _stub_shell_run(timed_out=True)
    )
    assert physical_cores() == (os.cpu_count() or 1)


def test_physical_cores_falls_back_when_no_pairs_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        finalize_module,
        "shell_run",
        _stub_shell_run(output=("# only a comment",)),
    )
    assert physical_cores() == (os.cpu_count() or 1)
