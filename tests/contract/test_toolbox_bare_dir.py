"""R12/R31/R122/R123/ADR-005/ADR-011 contract tests for the C2 toolbox
commands over a bare NEWAVE deck directory (ticket-054), run as real
subprocesses against ``python -m hpc_model_utils.cli``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from hpc_model_utils.models.newave.deck import caso as read_caso
from hpc_model_utils.models.newave.deck import dger as read_dger
from tests.support.decks import newave_workspace
from tests.support.fake_slurm.cli import write_executable_stub
from tests.support.hooks import parse_hooks
from tests.support.newave_outputs import pmo_bytes, write_dger

GOLDEN_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "goldens"
    / "v1_1_2"
    / "projections.json"
)


def _golden(name: str) -> dict[str, str]:
    data = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    sequence = data["sequences"][name]
    assert isinstance(sequence, dict)
    return sequence


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["HPCMU_PLATFORM"] = "on"
    env.pop("SLURM_JOB_ID", None)
    return subprocess.run(
        [sys.executable, "-m", "hpc_model_utils.cli", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


_TERMINAL_HOOKS = (
    "SetSuccess",
    "SetModelError",
    "SetDataError",
    "SetRuntimeError",
)


# ---------------------------------------------------------------------------
# AC1: golden bytes in a bare directory (R12, R123).
# ---------------------------------------------------------------------------


def test_generate_execution_status_bare_dir_job_id_matches_golden_bytes(
    tmp_path: Path,
) -> None:
    deck = newave_workspace(tmp_path)
    (deck.ws.root / "pmo.dat").write_bytes(pmo_bytes("complete"))
    golden = _golden("newave_toolbox_bare")

    result = _run(
        deck.ws.root,
        "generate_execution_status",
        "newave",
        "--job-id",
        "777",
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert deck.ws.legacy_metadata_path.read_bytes() == golden[
        "metadata.modelops"
    ].encode("ascii")
    assert deck.ws.legacy_status_path.read_bytes() == golden[
        "status.modelops"
    ].encode("ascii")
    assert not deck.ws.hpcmu_dir.exists()
    hooks = parse_hooks(result.stdout)
    metadata_hooks = [h for h in hooks if h.method == "SetMetadata"]
    assert metadata_hooks[0].args == ("job_id", "777")
    assert metadata_hooks[1].args == ("status", "SUCCESS")
    assert all(h.method not in _TERMINAL_HOOKS for h in hooks)


def test_generate_execution_status_bare_dir_empty_job_id_matches_golden_bytes(
    tmp_path: Path,
) -> None:
    deck = newave_workspace(tmp_path)
    (deck.ws.root / "pmo.dat").write_bytes(pmo_bytes("complete"))
    golden = _golden("newave_toolbox_no_job_id")

    result = _run(
        deck.ws.root, "generate_execution_status", "newave", "--job-id", ""
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert deck.ws.legacy_metadata_path.read_bytes() == golden[
        "metadata.modelops"
    ].encode("ascii")
    assert deck.ws.legacy_status_path.read_bytes() == golden[
        "status.modelops"
    ].encode("ascii")
    assert not deck.ws.hpcmu_dir.exists()


def test_generate_execution_status_bare_dir_existing_metadata_is_overwritten(
    tmp_path: Path,
) -> None:
    """ADR-011/README `newave_toolbox_existing_metadata`: v2 never reads
    a projection back, so a pre-existing ``metadata.modelops`` is
    overwritten, never merged. The golden's own ``metadata.modelops``
    field freezes v1's *merge* result (for the README row) and is
    therefore not the v2 byte comparison here (only ``status.modelops``
    is unaffected by the merge/overwrite difference).
    """
    deck = newave_workspace(tmp_path)
    (deck.ws.root / "pmo.dat").write_bytes(pmo_bytes("complete"))
    deck.ws.legacy_metadata_path.write_text(
        '{"model_name": "NEWAVE", "study_name": "old"}', encoding="ascii"
    )
    golden = _golden("newave_toolbox_existing_metadata")

    result = _run(
        deck.ws.root,
        "generate_execution_status",
        "newave",
        "--job-id",
        "778",
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert (
        deck.ws.legacy_metadata_path.read_bytes()
        == b'{"job_id": "778", "status": "SUCCESS"}'
    )
    assert deck.ws.legacy_status_path.read_bytes() == golden[
        "status.modelops"
    ].encode("ascii")


# ---------------------------------------------------------------------------
# AC2: a model failure is not a command failure.
# ---------------------------------------------------------------------------


def test_generate_execution_status_model_failure_exits_0_with_runtime_error(
    tmp_path: Path,
) -> None:
    deck = newave_workspace(tmp_path)
    write_dger(deck.ws, tipo_execucao=1, tipo_simulacao_final=1)
    (deck.ws.root / "pmo.dat").write_bytes(pmo_bytes("no_simulated_cost"))

    result = _run(
        deck.ws.root, "generate_execution_status", "newave", "--job-id", "9"
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert deck.ws.legacy_status_path.read_bytes() == b"RUNTIME_ERROR"
    assert (
        deck.ws.legacy_metadata_path.read_bytes()
        == b'{"job_id": "9", "status": "RUNTIME_ERROR"}'
    )
    hooks = parse_hooks(result.stdout)
    assert all(h.method not in _TERMINAL_HOOKS for h in hooks)


# ---------------------------------------------------------------------------
# AC3: preprocess in a bare directory (ticket-053's command).
# ---------------------------------------------------------------------------


def test_preprocess_bare_dir_sets_process_manager_and_title_leaves_stale_files(
    tmp_path: Path,
) -> None:
    deck = newave_workspace(tmp_path)
    stale = {
        "pmo.dat": b"STALE pmo",
        "engnat.dat": b"STALE engnat",
        "vazoes.csv": b"STALE vazoes",
    }
    for name, content in stale.items():
        (deck.ws.root / name).write_bytes(content)

    result = _run(
        deck.ws.root, "preprocess", "newave", "--execution-name", "superior"
    )

    assert result.returncode == 0
    assert result.stderr == ""
    for name, content in stale.items():
        assert (deck.ws.root / name).read_bytes() == content
    assert not deck.ws.hpcmu_dir.exists()

    assert read_caso(deck.ws).gerenciador_processos == f"{deck.ws.assets}/"
    assert read_dger(deck.ws).nome_caso == "superior"


# ---------------------------------------------------------------------------
# AC4: postprocess exits 0 (v1 parity).
# ---------------------------------------------------------------------------


def test_postprocess_bare_dir_nwlistcf_failure_exits_0_logs_warning(
    tmp_path: Path,
) -> None:
    deck = newave_workspace(tmp_path)
    write_executable_stub(deck.ws.assets, "nwlistcf", "exit 3")
    before = (deck.ws.root / "arquivos.dat").read_bytes()

    result = _run(deck.ws.root, "postprocess", "newave")

    assert result.returncode == 0
    assert result.stderr == ""
    assert "postprocess failed:" in result.stdout
    assert "nwlistcf option 1 exited 3" in result.stdout
    hooks = parse_hooks(result.stdout)
    assert all(h.method not in _TERMINAL_HOOKS for h in hooks)
    assert (deck.ws.root / "arquivos.dat").read_bytes() == before


# ---------------------------------------------------------------------------
# AC5: D7 fatal hooks (R122).
# ---------------------------------------------------------------------------


def _assert_one_fatal_hook_and_stderr_line(
    result: subprocess.CompletedProcess[str], *, terminal_hook: str
) -> None:
    hooks = parse_hooks(result.stdout)
    terminal = [h for h in hooks if h.method in _TERMINAL_HOOKS]
    assert len(terminal) == 1
    assert terminal[0].method == terminal_hook
    assert terminal[0].method != "SetModelError"
    assert any(h.method == "SetAnnotation" for h in hooks)
    stderr_lines = result.stderr.splitlines()
    assert len(stderr_lines) == 1


def test_generate_execution_status_unwritable_metadata_exits_99_set_runtime_error(
    tmp_path: Path,
) -> None:
    (tmp_path / "metadata.modelops").mkdir()

    result = _run(
        tmp_path, "generate_execution_status", "newave", "--job-id", "1"
    )

    assert result.returncode == 99
    _assert_one_fatal_hook_and_stderr_line(
        result, terminal_hook="SetRuntimeError"
    )


def test_preprocess_missing_caso_dat_exits_5_set_data_error(
    tmp_path: Path,
) -> None:
    result = _run(tmp_path, "preprocess", "newave")

    assert result.returncode == 5
    _assert_one_fatal_hook_and_stderr_line(result, terminal_hook="SetDataError")


def test_postprocess_unknown_model_exits_2_set_runtime_error(
    tmp_path: Path,
) -> None:
    result = _run(tmp_path, "postprocess", "NOSUCH")

    assert result.returncode == 2
    _assert_one_fatal_hook_and_stderr_line(
        result, terminal_hook="SetRuntimeError"
    )
