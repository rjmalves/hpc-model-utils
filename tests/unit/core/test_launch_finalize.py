"""ticket-029: render the finalize job script on one exclusive node."""

from __future__ import annotations

import dataclasses
import inspect
import os
import subprocess
from pathlib import Path

import pytest

from hpc_model_utils.core.errors import UsageError
from hpc_model_utils.core.launch import (
    FinalizeInvocation,
    Resources,
    Toolchain,
    render_finalize_script,
    write_finalize_script,
)
from hpc_model_utils.core.workspace import Workspace
from tests.support.launch_helpers import run_script, toolchain

_REGEN_ENV = "HPCMU_REGEN_GOLDENS"
_GOLDEN_PATH = (
    Path(__file__).resolve().parents[2]
    / "goldens"
    / "v2"
    / "sbatch"
    / "finalize.sbatch"
)


def _write_cli_stub(cli_bin: Path, body: str) -> None:
    cli_bin.parent.mkdir(parents=True, exist_ok=True)
    cli_bin.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    cli_bin.chmod(0o755)


# -- FinalizeInvocation.__post_init__ ---------------------------------------


def test_finalize_invocation_invalid_model_raises_usage_error() -> None:
    with pytest.raises(UsageError, match="model"):
        FinalizeInvocation("NEWAVE", None, 4, None)


@pytest.mark.parametrize("model", ["newave\n", "newave\r\n", "\nnewave"])
def test_finalize_invocation_model_trailing_newline_raises_usage_error(
    model: str,
) -> None:
    with pytest.raises(UsageError, match="model"):
        FinalizeInvocation(model, None, 4, None)


def test_finalize_invocation_invalid_model_job_id_raises_usage_error() -> None:
    with pytest.raises(UsageError, match="model_job_id"):
        FinalizeInvocation("newave", "abc", 4, None)


@pytest.mark.parametrize("job_id", ["6208\n", "6208\r\n", "\n6208"])
def test_finalize_invocation_model_job_id_trailing_newline_raises_usage_error(
    job_id: str,
) -> None:
    with pytest.raises(UsageError, match="model_job_id"):
        FinalizeInvocation("newave", job_id, 4, None)


def test_finalize_invocation_none_model_job_id_constructs() -> None:
    inv = FinalizeInvocation("newave", None, 4, None)
    assert inv.model_job_id is None


def test_finalize_invocation_relative_synthesis_bin_raises_usage_error() -> (
    None
):
    with pytest.raises(UsageError, match="synthesis_bin"):
        FinalizeInvocation("newave", "6208", 4, Path("relative/bin"))


def test_finalize_invocation_cores_less_than_one_raises_usage_error() -> None:
    with pytest.raises(UsageError, match="cores"):
        FinalizeInvocation("newave", "6208", 0, None)


def test_finalize_invocation_valid_values_construct() -> None:
    inv = FinalizeInvocation("newave", "6208", 64, Path("/opt/bin/x"))
    assert inv.model == "newave"
    assert inv.model_job_id == "6208"
    assert inv.cores == 64
    assert inv.synthesis_bin == Path("/opt/bin/x")


# -- Requirement 4: no execution/study/deck parameter -----------------------


def test_finalize_invocation_fields_exclude_execution_and_study_name() -> None:
    names = {f.name for f in dataclasses.fields(FinalizeInvocation)}
    assert names == {"model", "model_job_id", "cores", "synthesis_bin"}


def test_render_finalize_script_signature_has_no_execution_or_study_param() -> (
    None
):
    params = set(inspect.signature(render_finalize_script).parameters)
    assert params == {"ws", "res", "tools", "inv"}


# -- render_finalize_script: #SBATCH shape (AC3) -----------------------------


def test_render_finalize_script_sbatch_flags_shape(tmp_path: Path) -> None:
    ws = Workspace.at(tmp_path)
    tools = toolchain(tmp_path, cli_bin_name="hpcmu")
    inv = FinalizeInvocation("newave", "6208", 64, None)
    res = Resources(queue="batch", cores=64)
    rendered = render_finalize_script(ws, res, tools, inv)
    assert "#SBATCH --nodes=1" in rendered
    assert "#SBATCH --ntasks=1" in rendered
    assert "#SBATCH --exclusive" in rendered
    assert "#SBATCH --mem=0" in rendered
    assert "--ntasks-per-node" not in rendered
    assert "--cpus-per-task" not in rendered


# -- render_finalize_script: the guard block (module constant) --------------


def test_render_finalize_script_contains_guard_lines(tmp_path: Path) -> None:
    ws = Workspace.at(tmp_path)
    tools = toolchain(tmp_path, cli_bin_name="hpcmu")
    inv = FinalizeInvocation("newave", "6208", 4, None)
    res = Resources(queue="batch", cores=4)
    rendered = render_finalize_script(ws, res, tools, inv)
    assert (
        "ulimit -u unlimited 2>/dev/null || ulimit -u 65535 2>/dev/null || true"
    ) in rendered
    assert (
        "export POLARS_MAX_THREADS=1 RAYON_NUM_THREADS=1 "
        "OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 "
        "NUMEXPR_NUM_THREADS=1"
    ) in rendered


# -- render_finalize_script: root-character rejection (ticket-028 amendment) -


def test_render_finalize_script_root_with_percent_raises_usage_error(
    tmp_path: Path,
) -> None:
    root = tmp_path / "a%b"
    root.mkdir()
    ws = Workspace.at(root)
    tools = toolchain(tmp_path, cli_bin_name="hpcmu")
    inv = FinalizeInvocation("newave", "6208", 4, None)
    res = Resources(queue="batch", cores=4)
    with pytest.raises(UsageError, match="%"):
        render_finalize_script(ws, res, tools, inv)


# -- write_finalize_script -----------------------------------------------


def test_write_finalize_script_creates_executable_file(tmp_path: Path) -> None:
    ws = Workspace.at(tmp_path)
    tools = toolchain(tmp_path, cli_bin_name="hpcmu")
    inv = FinalizeInvocation("newave", "6208", 4, None)
    res = Resources(queue="batch", cores=4)
    path = write_finalize_script(ws, res, tools, inv)
    assert path == ws.jobs_dir / "finalize.sbatch"
    assert path.is_file()
    assert path.stat().st_mode & 0o777 == 0o755


# -- AC2: the regenerable golden ----------------------------------------


def test_render_finalize_script_matches_golden(tmp_path: Path) -> None:
    root = tmp_path / "study-newave"
    root.mkdir()
    ws = Workspace.at(root)
    tools = Toolchain(
        mpich_bin=Path("/opt/mpich/bin"),
        slurm_bin=Path("/opt/slurm/bin"),
        cli_bin=Path("/opt/hpcmu/bin"),
    )
    inv = FinalizeInvocation(
        "newave",
        "6208",
        64,
        Path(
            "/shared-zfs/tools/sintetizador-newave/<sha>/.venv/bin/"
            "sintetizador-newave"
        ),
    )
    res = Resources(queue="batch", cores=64, time_limit_hours=24)
    rendered = render_finalize_script(ws, res, tools, inv)
    normalized = rendered.replace(str(ws.root), "<WS>")

    if os.environ.get(_REGEN_ENV) == "1":
        _GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        _GOLDEN_PATH.write_text(normalized, encoding="utf-8")
        pytest.fail(
            f"{_REGEN_ENV}=1: regenerated {_GOLDEN_PATH}; "
            "re-run without it to verify"
        )

    expected = _GOLDEN_PATH.read_text(encoding="utf-8")
    assert normalized == expected
    assert "#SBATCH --exclusive" in rendered
    assert "#SBATCH --mem=0" in rendered

    script_path = tmp_path / "golden-check.sbatch"
    script_path.write_text(rendered, encoding="utf-8")
    subprocess.run(["bash", "-n", str(script_path)], check=True)


# -- AC4: none job id and no synthesis-bin flag reach the stub argv ---------


def test_write_finalize_script_stub_receives_finalize_argv_without_synthesis_bin(  # noqa: E501
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    tools = toolchain(tmp_path, cli_bin_name="hpcmu")
    _write_cli_stub(tools.cli_bin, 'printf "%s\\n" "$*"')
    inv = FinalizeInvocation("newave", None, 4, None)
    res = Resources(queue="batch", cores=4)
    script = write_finalize_script(ws, res, tools, inv)

    result = run_script(script, tmp_path, {})
    assert result.returncode == 0, result.stderr
    last_line = result.stdout.splitlines()[-1]
    assert last_line == "finalize newave --model-job-id none --cores 4"
    assert "--synthesis-bin" not in last_line


# -- AC5: the environment guards seen by the stub ----------------------------


def test_write_finalize_script_stub_sees_omp_and_platform_guards(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    tools = toolchain(tmp_path, cli_bin_name="hpcmu")
    _write_cli_stub(tools.cli_bin, 'echo "$OMP_NUM_THREADS $HPCMU_PLATFORM"')
    inv = FinalizeInvocation("newave", None, 4, None)
    res = Resources(queue="batch", cores=4)
    script = write_finalize_script(ws, res, tools, inv)
    rendered = script.read_text(encoding="utf-8")
    assert "rm -f" not in rendered
    assert "LD_LIBRARY_PATH" not in rendered

    result = run_script(script, tmp_path, {})
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == "1 off"


# -- the optional --synthesis-bin flag --------------------------------------


def test_write_finalize_script_synthesis_bin_appends_flag(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    tools = toolchain(tmp_path, cli_bin_name="hpcmu")
    _write_cli_stub(tools.cli_bin, 'printf "%s\\n" "$*"')
    synthesis_bin = tmp_path / "venv" / "bin" / "sintetizador-newave"
    inv = FinalizeInvocation("newave", "6208", 32, synthesis_bin)
    res = Resources(queue="batch", cores=32)
    script = write_finalize_script(ws, res, tools, inv)

    result = run_script(script, tmp_path, {})
    assert result.returncode == 0, result.stderr
    last_line = result.stdout.splitlines()[-1]
    assert last_line == (
        "finalize newave --model-job-id 6208 --cores 32 "
        f"--synthesis-bin {synthesis_bin}"
    )


# -- bash -n over every rendered variant -------------------------------------


@pytest.mark.parametrize(
    "inv",
    [
        FinalizeInvocation("newave", "6208", 64, Path("/opt/bin/x")),
        FinalizeInvocation("newave", None, 4, None),
    ],
)
def test_render_finalize_script_bash_n_on_every_variant(
    tmp_path: Path, inv: FinalizeInvocation
) -> None:
    ws = Workspace.at(tmp_path)
    tools = toolchain(tmp_path, cli_bin_name="hpcmu")
    res = Resources(queue="batch", cores=4, time_limit_hours=10)
    rendered = render_finalize_script(ws, res, tools, inv)
    script_path = tmp_path / f"variant-{inv.model_job_id}.sbatch"
    script_path.write_text(rendered, encoding="utf-8")
    subprocess.run(["bash", "-n", str(script_path)], check=True)
