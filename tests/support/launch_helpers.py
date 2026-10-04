"""Shared ``Toolchain``/script-execution helpers for the launch-related
unit tests (tests/unit/core/test_launch_model.py and
test_launch_finalize.py), which built identical/near-identical copies
of these independently."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

from hpc_model_utils.core.launch import Toolchain


def toolchain(tmp_path: Path, *, cli_bin_name: str | None = None) -> Toolchain:
    cli_bin = tmp_path / "toolchain" / "cli" / "bin"
    if cli_bin_name is not None:
        cli_bin = cli_bin / cli_bin_name
    return Toolchain(
        mpich_bin=tmp_path / "toolchain" / "mpich" / "bin",
        slurm_bin=tmp_path / "toolchain" / "slurm" / "bin",
        cli_bin=cli_bin,
    )


def run_script(
    script_path: Path, tmp_path: Path, env_overrides: Mapping[str, str]
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **env_overrides}
    return subprocess.run(
        ["bash", str(script_path)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
