"""System tests for the Slurm cancellation adapter (ticket-027,
ADR-024, ADR-046, R44, R45, R136), against the fake SLURM harness and
stub bin directories.

The fake-SLURM scenarios (AC2, AC3) use real wall-clock time but keep
their budgets small to stay fast. The stub-based scenarios that must
eventually answer ``False`` use an injected fake ``now``/no-op
``sleep`` so they never really wait; the one exception is the
"hung squeue" scenario, which must use real time because it is
proving that a hung subprocess cannot overrun ``wait_gone``'s overall
``timeout``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hpc_model_utils.infra.errors import SchedulerCommandError
from hpc_model_utils.infra.slurm import Slurm
from tests.support.fake_slurm import FakeSlurm
from tests.support.fake_slurm.cli import (
    sbatch,
    squeue,
    wait_for_state,
    write_executable_stub,
    write_script,
)


def _cmd_records(fake_slurm: FakeSlurm, cmd: str) -> list[dict[str, Any]]:
    records = [
        json.loads(line)
        for line in (fake_slurm.state_dir / "invocations.jsonl")
        .read_text()
        .splitlines()
        if line.strip()
    ]
    return [record for record in records if record["cmd"] == cmd]


def _fake_clock(*, step: float = 0.3) -> Callable[[], float]:
    state = {"t": 0.0}

    def _now() -> float:
        state["t"] += step
        return state["t"]

    return _now


@pytest.fixture(autouse=True)
def _chdir_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A job without an explicit #SBATCH --output writes slurm-<id>.out
    # against the process cwd; pin it to tmp_path so it never leaks
    # into the repo.
    monkeypatch.chdir(tmp_path)


# --- cancel: id validation ---


def test_cancel_empty_ids_returns_without_subprocess(
    fake_slurm: FakeSlurm,
) -> None:
    slurm = Slurm(fake_slurm.bin_dir)

    slurm.cancel([])

    log = (fake_slurm.state_dir / "invocations.jsonl").read_text()
    assert log == ""


def test_cancel_malformed_id_raises_value_error_no_invocation(
    fake_slurm: FakeSlurm,
) -> None:
    slurm = Slurm(fake_slurm.bin_dir)

    with pytest.raises(ValueError, match="not a Slurm job id"):
        slurm.cancel(["123;rm"])

    assert _cmd_records(fake_slurm, "scancel") == []


def test_cancel_trailing_newline_id_raises_value_error(
    fake_slurm: FakeSlurm,
) -> None:
    slurm = Slurm(fake_slurm.bin_dir)

    with pytest.raises(ValueError, match="not a Slurm job id"):
        slurm.cancel(["123\n"])

    assert _cmd_records(fake_slurm, "scancel") == []


def test_cancel_one_invalid_id_among_valid_blocks_all_subprocesses(
    fake_slurm: FakeSlurm,
) -> None:
    slurm = Slurm(fake_slurm.bin_dir)

    with pytest.raises(ValueError, match="not a Slurm job id"):
        slurm.cancel(["100", "123;rm", "200"])

    assert _cmd_records(fake_slurm, "scancel") == []


# --- cancel: rc/output handling (AC6, Requirement 1, amendment 2) ---


def test_cancel_unknown_id_benign_error_returns_normally(
    fake_slurm: FakeSlurm,
) -> None:
    slurm = Slurm(fake_slurm.bin_dir)

    slurm.cancel(["999999"])

    records = _cmd_records(fake_slurm, "scancel")
    assert len(records) == 1


def test_cancel_nonzero_rc_empty_output_raises_scheduler_command_error(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(bin_dir, "scancel", "exit 3")
    slurm = Slurm(bin_dir)

    with pytest.raises(SchedulerCommandError, match="scancel failed"):
        slurm.cancel(["123"])


def test_cancel_mixed_benign_and_other_error_lines_raises(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(
        bin_dir,
        "scancel",
        'echo "Invalid job id specified" >&2\n'
        'echo "scancel: error: socket timed out" >&2\n'
        "exit 1",
    )
    slurm = Slurm(bin_dir)

    with pytest.raises(SchedulerCommandError, match="scancel failed"):
        slurm.cancel(["123", "124"])


def test_cancel_timeout_raises_scheduler_command_error(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(bin_dir, "scancel", "sleep 5")
    slurm = Slurm(bin_dir, timeout=0.3)

    with pytest.raises(SchedulerCommandError, match="scancel timed out"):
        slurm.cancel(["123"])


def test_cancel_missing_executable_raises_scheduler_command_error(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    slurm = Slurm(bin_dir)

    with pytest.raises(SchedulerCommandError, match="scancel not found"):
        slurm.cancel(["123"])


# --- cancel + wait_gone: fake SLURM scenarios (AC2, AC3) ---


def test_cancel_then_wait_gone_held_and_running_jobs_returns_true(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    held_script = write_script(tmp_path, "held.sh", "sleep 30")
    running_script = write_script(tmp_path, "running.sh", "sleep 30")
    slurm = Slurm(fake_slurm.bin_dir)
    held_id = slurm.submit(held_script)
    fake_slurm.hold(int(held_id))
    running_id = slurm.submit(running_script)
    wait_for_state(fake_slurm, int(running_id), "RUNNING")

    slurm.cancel([held_id, running_id])
    gone = slurm.wait_gone([held_id, running_id], timeout=10)

    assert gone is True
    records = _cmd_records(fake_slurm, "scancel")
    assert len(records) == 1
    assert held_id in records[0]["argv"]
    assert running_id in records[0]["argv"]


def test_wait_gone_held_pending_job_never_cancelled_times_out_false(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    script = write_script(tmp_path, "held.sh", "sleep 30")
    slurm = Slurm(fake_slurm.bin_dir)
    job_id = slurm.submit(script)
    fake_slurm.hold(int(job_id))

    start = time.perf_counter()
    gone = slurm.wait_gone([job_id], timeout=1.0, poll=0.2)
    elapsed = time.perf_counter() - start

    assert gone is False
    assert elapsed < 2.0
    result = squeue("-h", "-j", job_id, "-o", "%i")
    assert result.returncode == 0
    assert result.stdout.strip() == job_id


# --- workdir (AC4) ---


def test_workdir_running_job_with_chdir_returns_path(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    script = write_script(tmp_path, "job.sh", f"#SBATCH --chdir={ws}\nsleep 30")
    slurm = Slurm(fake_slurm.bin_dir)
    job_id = slurm.submit(script)
    wait_for_state(fake_slurm, int(job_id), "RUNNING")

    workdir = slurm.workdir(job_id)

    assert workdir == ws


def test_workdir_unknown_job_id_returns_none(fake_slurm: FakeSlurm) -> None:
    slurm = Slurm(fake_slurm.bin_dir)

    assert slurm.workdir("777777") is None


def test_workdir_with_space_in_chdir_preserved_exactly(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    ws = tmp_path / "work space"
    ws.mkdir()
    script = write_script(tmp_path, "job.sh", "sleep 30")
    result = sbatch("--parsable", f"--chdir={ws}", str(script), cwd=tmp_path)
    assert result.returncode == 0
    job_id = result.stdout.strip()
    slurm = Slurm(fake_slurm.bin_dir)

    assert slurm.workdir(job_id) == ws


def test_workdir_malformed_job_id_raises_value_error(
    fake_slurm: FakeSlurm,
) -> None:
    slurm = Slurm(fake_slurm.bin_dir)

    with pytest.raises(ValueError, match="not a Slurm job id"):
        slurm.workdir("123;rm")

    assert _cmd_records(fake_slurm, "squeue") == []


def test_workdir_trailing_newline_raises_value_error(
    fake_slurm: FakeSlurm,
) -> None:
    slurm = Slurm(fake_slurm.bin_dir)

    with pytest.raises(ValueError, match="not a Slurm job id"):
        slurm.workdir("123\n")


def test_workdir_timeout_raises_scheduler_command_error(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(bin_dir, "squeue", "sleep 5")
    slurm = Slurm(bin_dir, timeout=0.3)

    with pytest.raises(SchedulerCommandError, match="squeue timed out"):
        slurm.workdir("123")


def test_workdir_nonzero_rc_without_invalid_message_raises(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(
        bin_dir,
        "squeue",
        'echo "slurm_load_jobs error: something else" >&2\nexit 1',
    )
    slurm = Slurm(bin_dir)

    with pytest.raises(SchedulerCommandError, match="squeue failed"):
        slurm.workdir("123")


def test_workdir_rc_zero_empty_output_returns_none(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(bin_dir, "squeue", "exit 0")
    slurm = Slurm(bin_dir)

    assert slurm.workdir("123") is None


def test_workdir_multiple_nonempty_lines_raises_scheduler_command_error(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(bin_dir, "squeue", "echo /tmp/a\necho /tmp/b")
    slurm = Slurm(bin_dir)

    with pytest.raises(SchedulerCommandError, match="returned 2 lines"):
        slurm.workdir("123")


def test_workdir_missing_executable_raises_scheduler_command_error(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    slurm = Slurm(bin_dir)

    with pytest.raises(SchedulerCommandError, match="squeue not found"):
        slurm.workdir("123")


# --- wait_gone: id validation ---


def test_wait_gone_empty_ids_returns_true_without_subprocess(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    slurm = Slurm(bin_dir)

    assert slurm.wait_gone([], timeout=1.0) is True


def test_wait_gone_malformed_id_raises_value_error_no_subprocess(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    slurm = Slurm(bin_dir)

    with pytest.raises(ValueError, match="not a Slurm job id"):
        slurm.wait_gone(["abc"], timeout=1.0)


def test_wait_gone_trailing_newline_id_raises_value_error(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    slurm = Slurm(bin_dir)

    with pytest.raises(ValueError, match="not a Slurm job id"):
        slurm.wait_gone(["123\n"], timeout=1.0)


# --- wait_gone: AC5 stub scenarios ---


def test_wait_gone_stub_both_terminal_returns_true_on_first_poll(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(
        bin_dir, "squeue", "echo '123|CANCELLED'\necho '124|COMPLETED'"
    )
    slurm = Slurm(bin_dir)

    assert slurm.wait_gone(["123", "124"], timeout=1.0, poll=0.2) is True


def test_wait_gone_stub_one_still_completing_returns_false(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(bin_dir, "squeue", "echo '123|COMPLETING'")
    slurm = Slurm(bin_dir)

    assert slurm.wait_gone(["123"], timeout=1.0, poll=0.2) is False


# --- wait_gone: amendment fail-closed rules ---


def test_wait_gone_controller_unreachable_returns_false(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(
        bin_dir,
        "squeue",
        'echo "slurm_load_jobs error: Unable to contact slurm '
        'controller" >&2\nexit 1',
    )
    slurm = Slurm(bin_dir)

    gone = slurm.wait_gone(
        ["123"],
        timeout=1.0,
        poll=0.0,
        sleep=lambda _: None,
        now=_fake_clock(),
    )

    assert gone is False


def test_wait_gone_hung_squeue_does_not_overrun_timeout(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(bin_dir, "squeue", "sleep 5")
    slurm = Slurm(bin_dir)

    start = time.perf_counter()
    gone = slurm.wait_gone(["123"], timeout=0.5, poll=0.1)
    elapsed = time.perf_counter() - start

    assert gone is False
    assert elapsed < 3.0


def test_wait_gone_invalid_job_id_with_nonterminal_row_not_gone(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(
        bin_dir,
        "squeue",
        "echo '100|RUNNING'\n"
        'echo "slurm_load_jobs error: Invalid job id specified" >&2\n'
        "exit 1",
    )
    slurm = Slurm(bin_dir)

    gone = slurm.wait_gone(
        ["100", "200"],
        timeout=1.0,
        poll=0.0,
        sleep=lambda _: None,
        now=_fake_clock(),
    )

    assert gone is False


def test_wait_gone_rc_nonzero_missing_id_without_invalid_line_stays_false(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(bin_dir, "squeue", "echo '100|COMPLETED'\nexit 1")
    slurm = Slurm(bin_dir)

    gone = slurm.wait_gone(
        ["100", "200"],
        timeout=1.0,
        poll=0.0,
        sleep=lambda _: None,
        now=_fake_clock(),
    )

    assert gone is False


def test_wait_gone_rc_nonzero_all_ids_present_in_rows_returns_true(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(
        bin_dir,
        "squeue",
        "echo '100|COMPLETED'\necho '200|CANCELLED'\nexit 1",
    )
    slurm = Slurm(bin_dir)

    gone = slurm.wait_gone(["100", "200"], timeout=1.0, poll=0.2)

    assert gone is True


def test_wait_gone_ignores_rows_for_unrequested_ids(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(
        bin_dir, "squeue", "echo '999|RUNNING'\necho '100|COMPLETED'"
    )
    slurm = Slurm(bin_dir)

    assert slurm.wait_gone(["100"], timeout=1.0, poll=0.2) is True
