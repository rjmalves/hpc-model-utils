"""AC1-AC3 (ticket-056, R69/R70/R71): the console script switch from
v1's ``main:main`` to the v2 CLI entry point, checked statically in
``pyproject.toml``, in the installed ``console_scripts`` metadata, and
on the installed script itself.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from importlib import metadata
from pathlib import Path

_ENTRY_POINT = "hpc_model_utils.cli:main"
_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"

_VISIBLE_COMMANDS = frozenset(
    {
        "check_and_fetch_executables",
        "check_and_fetch_inputs",
        "extract_sanitize_inputs",
        "preprocess",
        "run",
        "result_upload",
        "cancel_run",
        "ingest_offline_run",
        "generate_execution_status",
        "postprocess",
    }
)

_HIDDEN_COMMANDS = (
    "finalize",
    "output_compression_and_cleanup",
    "download_executed_run",
    "fetch_extract_raw_outputs",
)


def _script() -> Path:
    # ticket-053 pitfall: never resolve this path. A symlinked venv
    # script keeps sys.executable at .venv/bin/python, which is what
    # locates the sibling script by its unresolved parent directory.
    candidate = Path(sys.executable).parent / "hpc-model-utils"
    if not candidate.is_file():
        raise AssertionError(
            f"{candidate} does not exist; run `uv sync --dev` to "
            "regenerate the editable install's console script"
        )
    return candidate


def _run(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_script()), *args],
        cwd=cwd,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=30,
    )


def _listed_commands(help_output: str) -> set[str]:
    section = help_output.split("Commands:\n", 1)[1]
    return {line.split()[0] for line in section.splitlines() if line.strip()}


# ---------------------------------------------------------------------------
# AC1: the static switch in pyproject.toml.
# ---------------------------------------------------------------------------


def test_pyproject_scripts_points_at_v2_cli_entry_point() -> None:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))

    assert data["project"]["scripts"] == {"hpc-model-utils": _ENTRY_POINT}


# ---------------------------------------------------------------------------
# AC2: the installed console_scripts entry-point metadata. A stale
# editable install (metadata still on main:main) must fail here, never
# skip, with a message telling the developer to run uv sync.
# ---------------------------------------------------------------------------


def test_installed_entry_point_metadata_matches_v2_cli() -> None:
    eps = list(
        metadata.entry_points(group="console_scripts", name="hpc-model-utils")
    )

    assert len(eps) == 1, (
        "expected exactly one hpc-model-utils console_scripts entry; "
        "run `uv sync --dev` to refresh the editable install's metadata"
    )
    assert eps[0].value == _ENTRY_POINT, (
        f"installed entry point is {eps[0].value!r}, not "
        f"{_ENTRY_POINT!r}; run `uv sync --dev` to refresh the "
        "editable install's metadata"
    )


# ---------------------------------------------------------------------------
# AC3: the installed script surface (R69, R70, R71).
# ---------------------------------------------------------------------------


def test_installed_script_help_lists_exactly_the_v2_visible_commands(
    tmp_path: Path,
) -> None:
    result = _run(["--help"], tmp_path)

    assert result.returncode == 0
    assert _listed_commands(result.stdout) == _VISIBLE_COMMANDS
    for name in _HIDDEN_COMMANDS:
        assert name not in result.stdout


def test_installed_script_version_reports_installed_distribution(
    tmp_path: Path,
) -> None:
    result = _run(["--version"], tmp_path)

    assert result.returncode == 0
    assert result.stdout.strip() == (
        f"hpc-model-utils, version {metadata.version('hpc-model-utils')}"
    )


def test_installed_script_kebab_case_alias_help_exits_zero(
    tmp_path: Path,
) -> None:
    result = _run(["check-and-fetch-inputs", "--help"], tmp_path)

    assert result.returncode == 0
