"""System tests for the hidden ``finalize`` command (ticket-036, R72,
ADR-006, ADR-021), against the fake SLURM harness and the generated
CLI shim (``tests/support/cli_shim.py``). The finalize job always runs
the shim under the fake runner's own environment, which exports
``HPCMU_PLATFORM=off`` via the rendered prelude and sets
``SLURM_JOB_ID`` (``tests/support/fake_slurm/shim.py``), so these
tests exercise the real job-side path end to end.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from hpc_model_utils.cli.finalize import _validate_model_job_id
from hpc_model_utils.cli.root import AppContext, cli
from hpc_model_utils.core.diagnosis import RunStatus
from hpc_model_utils.core.launch import (
    FinalizeInvocation,
    Resources,
    Toolchain,
    write_finalize_script,
    write_model_script,
)
from hpc_model_utils.core.lifecycle.finalize import legacy_synthesis_bin
from hpc_model_utils.core.state import StateStore, load_finalize
from hpc_model_utils.core.workspace import Phase, Workspace
from hpc_model_utils.infra.slurm import Slurm
from hpc_model_utils.platform.modelops import Reporter
from tests.support.cli_shim import write_cli_shim
from tests.support.fake_plugin import FakePlugin, install_fake_model
from tests.support.fake_slurm import FakeSlurm
from tests.support.hooks import parse_hooks

_PLUGIN = FakePlugin()


@pytest.fixture(autouse=True)
def _chdir_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A submission without an explicit #SBATCH --output writes
    # slurm-<id>.out against the process cwd; pin it to tmp_path so it
    # never leaks into the repo.
    monkeypatch.chdir(tmp_path)


def _toolchain(tmp_path: Path, fake_slurm: FakeSlurm) -> Toolchain:
    return Toolchain(
        cli_bin=write_cli_shim(tmp_path),
        mpich_bin=tmp_path / "mpich" / "bin",
        slurm_bin=fake_slurm.bin_dir,
    )


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


def _submit_model(
    ws: Workspace,
    tools: Toolchain,
    res: Resources,
    slurm: Slurm,
    *,
    token: str = "SUCCESS",
) -> str:
    install_fake_model(ws, token=token)
    spec = _PLUGIN.launch(ws, res)
    script = write_model_script(ws, spec, res, tools)
    return slurm.submit(script)


def _submit_finalize(
    ws: Workspace,
    tools: Toolchain,
    res: Resources,
    slurm: Slurm,
    *,
    model_job_id: str | None,
    after: str | None,
) -> str:
    inv = FinalizeInvocation(_PLUGIN.name, model_job_id, res.cores, None)
    script = write_finalize_script(ws, res, tools, inv)
    return slurm.submit(script, after=after)


# ---------------------------------------------------------------------------
# AC2/AC3: the finalize job over the fake SLURM.
# ---------------------------------------------------------------------------


def test_finalize_job_success_writes_finalize_json_and_job_completed(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    ws = _make_workspace(tmp_path)
    tools = _toolchain(tmp_path, fake_slurm)
    res = Resources(queue="batch", cores=2)
    slurm = Slurm(fake_slurm.bin_dir)
    state = StateStore(ws).load_or_create(_PLUGIN.name)
    _write_legacy_sintetizador(ws)

    model_job_id = _submit_model(ws, tools, res, slurm)
    finalize_job_id = _submit_finalize(
        ws, tools, res, slurm, model_job_id=model_job_id, after=model_job_id
    )
    fake_slurm.wait_idle()

    record = load_finalize(ws, state.run_id)
    assert record is not None
    assert record.diagnosis.status is RunStatus.SUCCESS
    assert fake_slurm.job(int(finalize_job_id))["state"] == "COMPLETED"


def test_finalize_job_log_has_start_line_and_no_hook_lines(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    ws = _make_workspace(tmp_path)
    tools = _toolchain(tmp_path, fake_slurm)
    res = Resources(queue="batch", cores=2)
    slurm = Slurm(fake_slurm.bin_dir)
    StateStore(ws).load_or_create(_PLUGIN.name)
    _write_legacy_sintetizador(ws)

    model_job_id = _submit_model(ws, tools, res, slurm)
    finalize_job_id = _submit_finalize(
        ws, tools, res, slurm, model_job_id=model_job_id, after=model_job_id
    )
    fake_slurm.wait_idle()

    log_text = ws.log_path(Phase.FINALIZE, finalize_job_id).read_text(
        encoding="utf-8"
    )
    assert any(line.startswith("HPCMU_START") for line in log_text.splitlines())
    assert "${CurrentExecution" not in log_text


def test_finalize_job_timeout_forced_model_still_writes_timeout_record(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    ws = _make_workspace(tmp_path)
    tools = _toolchain(tmp_path, fake_slurm)
    res = Resources(queue="batch", cores=2)
    slurm = Slurm(fake_slurm.bin_dir)
    state = StateStore(ws).load_or_create(_PLUGIN.name)
    install_fake_model(ws, sleep=30.0)
    fake_slurm.force("model", state="TIMEOUT")
    spec = _PLUGIN.launch(ws, res)
    model_script = write_model_script(ws, spec, res, tools)
    model_job_id = slurm.submit(model_script)

    finalize_job_id = _submit_finalize(
        ws, tools, res, slurm, model_job_id=model_job_id, after=model_job_id
    )
    fake_slurm.wait_idle()

    record = load_finalize(ws, state.run_id)
    assert record is not None
    assert record.diagnosis.status is RunStatus.TIMEOUT
    assert fake_slurm.job(int(finalize_job_id))["state"] == "COMPLETED"


def test_finalize_job_missing_state_writes_fatal_line_and_job_failed(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    ws = _make_workspace(tmp_path)
    tools = _toolchain(tmp_path, fake_slurm)
    res = Resources(queue="batch", cores=1)
    slurm = Slurm(fake_slurm.bin_dir)

    finalize_job_id = _submit_finalize(
        ws, tools, res, slurm, model_job_id=None, after=None
    )
    fake_slurm.wait_idle()

    log_text = ws.log_path(Phase.FINALIZE, finalize_job_id).read_text(
        encoding="utf-8"
    )
    assert (
        "hpc-model-utils finalize: StateFormatError: "
        "finalize requires .hpcmu/state.json" in log_text
    )
    assert fake_slurm.job(int(finalize_job_id))["state"] == "FAILED"


# ---------------------------------------------------------------------------
# AC4/amendment 2: --model-job-id validation through the shim subprocess.
# ---------------------------------------------------------------------------


def _run_shim(
    shim: Path,
    args: list[str],
    cwd: Path,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        [str(shim), *args],
        cwd=cwd,
        env=full_env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_finalize_subprocess_invalid_model_job_id_exits_2_one_stderr_line(
    tmp_path: Path,
) -> None:
    shim = write_cli_shim(tmp_path)

    result = _run_shim(
        shim,
        ["finalize", "fake", "--model-job-id", "abc", "--cores", "2"],
        tmp_path,
    )

    assert result.returncode == 2
    stderr_lines = result.stderr.splitlines()
    assert len(stderr_lines) == 1
    assert "UsageError" in stderr_lines[0]


@pytest.mark.parametrize("job_id", ["6208\n", "abc", "None"])
def test_finalize_subprocess_amendment_job_id_values_exit_2(
    tmp_path: Path, job_id: str
) -> None:
    shim = write_cli_shim(tmp_path)

    result = _run_shim(
        shim,
        ["finalize", "fake", "--model-job-id", job_id, "--cores", "2"],
        tmp_path,
    )

    assert result.returncode == 2
    stderr_lines = result.stderr.splitlines()
    assert len(stderr_lines) == 1
    assert "UsageError" in stderr_lines[0]


# ---------------------------------------------------------------------------
# AC5: the hidden command is absent from --help, but reachable directly.
# ---------------------------------------------------------------------------


def test_help_through_shim_does_not_list_finalize(tmp_path: Path) -> None:
    shim = write_cli_shim(tmp_path)

    result = _run_shim(shim, ["--help"], tmp_path)

    assert result.returncode == 0
    assert "finalize" not in result.stdout


def test_finalize_help_through_shim_still_works(tmp_path: Path) -> None:
    shim = write_cli_shim(tmp_path)

    result = _run_shim(shim, ["finalize", "--help"], tmp_path)

    assert result.returncode == 0
    assert "--model-job-id" in result.stdout


# ---------------------------------------------------------------------------
# Amendment 1: EngineSettings.from_env() runs inside the chokepoint.
# ---------------------------------------------------------------------------


def test_finalize_subprocess_invalid_poll_interval_exits_2_one_stderr_line(
    tmp_path: Path,
) -> None:
    shim = write_cli_shim(tmp_path)

    result = _run_shim(
        shim,
        ["finalize", "fake", "--model-job-id", "none", "--cores", "1"],
        tmp_path,
        env={"HPCMU_POLL_INTERVAL": "nan"},
    )

    assert result.returncode == 2
    stderr_lines = result.stderr.splitlines()
    assert len(stderr_lines) == 1
    assert "UsageError" in stderr_lines[0]
    assert "HPCMU_POLL_INTERVAL" in stderr_lines[0]


# ---------------------------------------------------------------------------
# Amendment 3: finalize never emits a terminal-status hook, platform on.
# ---------------------------------------------------------------------------


def test_finalize_subprocess_platform_on_emits_no_terminal_status_hook(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    shim = write_cli_shim(tmp_path)

    result = _run_shim(
        shim,
        ["finalize", "fake", "--model-job-id", "none", "--cores", "1"],
        ws,
        env={"HPCMU_PLATFORM": "on"},
    )

    assert result.returncode == 99
    stderr_lines = result.stderr.splitlines()
    assert len(stderr_lines) == 1
    hooks = parse_hooks(result.stdout)
    assert all(
        h.method
        not in (
            "SetSuccess",
            "SetModelError",
            "SetDataError",
            "SetRuntimeError",
            "SetAnnotation",
        )
        for h in hooks
    )


# ---------------------------------------------------------------------------
# Unit tests: --model-job-id validation and a missing --cores (CliRunner).
# ---------------------------------------------------------------------------


def _click_probe() -> tuple[click.Context, click.Parameter]:
    ctx = click.Context(click.Command("probe"))
    param = click.Option(["--model-job-id"])
    return ctx, param


def test_validate_model_job_id_literal_none_returns_none() -> None:
    ctx, param = _click_probe()

    assert _validate_model_job_id(ctx, param, "none") is None


def test_validate_model_job_id_digits_returns_same_string() -> None:
    ctx, param = _click_probe()

    assert _validate_model_job_id(ctx, param, "6208") == "6208"


@pytest.mark.parametrize("value", ["abc", "None", "6208\n"])
def test_validate_model_job_id_invalid_value_raises_usage_error(
    value: str,
) -> None:
    ctx, param = _click_probe()

    with pytest.raises(click.UsageError, match="model-job-id"):
        _validate_model_job_id(ctx, param, value)


class _ListChannel:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write_line(self, line: str) -> None:
        self.lines.append(line)


def _app_context() -> AppContext:
    return AppContext(
        reporter=Reporter(_ListChannel(), enabled=True), channels=None
    )


def test_cli_runner_missing_cores_option_exits_2() -> None:
    result = CliRunner().invoke(
        cli,
        ["finalize", "fake", "--model-job-id", "6208"],
        obj=_app_context(),
    )

    assert result.exit_code == 2
