"""System tests for the Slurm submission adapter (ticket-025, ADR-020,
R39, R11, R44), against the fake SLURM harness and stub bin
directories. Includes the parser tables for ``parse_time_limit`` and
``parse_slurm_time``, and the 2026-10-01 AC4 amendment: ``job_state``
returns a ``COMPLETED`` state until ``fake_slurm.purge(id)``, not
``None`` right after the job ends (cluster fact F2).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from hpc_model_utils.infra.errors import SchedulerCommandError
from hpc_model_utils.infra.slurm import (
    NON_TERMINAL_STATES,
    Slurm,
    parse_slurm_time,
    parse_time_limit,
)
from tests.support.fake_slurm import FakeSlurm
from tests.support.fake_slurm.cli import (
    scancel,
    wait_for_state,
    write_executable_stub,
    write_script,
)


@pytest.fixture(autouse=True)
def _chdir_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Slurm.submit() has no cwd parameter (per the ticket signature), so
    # the fake sbatch runner's default `slurm-<id>.out` resolves against
    # the process cwd. Pin it to tmp_path so a job without an explicit
    # #SBATCH --output never leaks a slurm-*.out file into the repo.
    monkeypatch.chdir(tmp_path)


# --- submit ---


def test_submit_returns_digit_string_job_id(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    script = write_script(tmp_path, "job.sh", "exit 0")
    slurm = Slurm(fake_slurm.bin_dir)

    job_id = slurm.submit(script)

    assert job_id.isdigit()
    fake_slurm.wait_idle()
    assert fake_slurm.job(int(job_id))["state"] == "COMPLETED"


def test_submit_with_after_sets_dependency_and_omits_job_name(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    script1 = write_script(tmp_path, "model.sh", "exit 0")
    script2 = write_script(tmp_path, "finalize.sh", "exit 0")
    slurm = Slurm(fake_slurm.bin_dir)

    job_id1 = slurm.submit(script1)
    job_id2 = slurm.submit(script2, after=job_id1)

    assert job_id1.isdigit()
    assert job_id2.isdigit()
    submitted = fake_slurm.submitted()
    second = submitted[1]
    assert second["dependency"] == int(job_id1)
    assert second["directives"]["dependency"] == f"afterany:{job_id1}"
    assert "--kill-on-invalid-dep=yes" in second["argv"]
    assert not any(arg.startswith("--job-name") for arg in second["argv"])


def test_submit_stub_parsable_with_cluster_suffix_returns_bare_id(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(bin_dir, "sbatch", 'echo "4242;cluster-a"')
    slurm = Slurm(bin_dir)

    job_id = slurm.submit(tmp_path / "ignored.sh")

    assert job_id == "4242"


def test_submit_stub_nonzero_exit_raises_scheduler_command_error(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(
        bin_dir,
        "sbatch",
        'echo "sbatch: error: Batch job submission failed" >&2\nexit 1',
    )
    slurm = Slurm(bin_dir)

    with pytest.raises(
        SchedulerCommandError, match="Batch job submission failed"
    ):
        slurm.submit(tmp_path / "ignored.sh")


def test_submit_stub_warning_line_before_id_returns_bare_id(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(
        bin_dir,
        "sbatch",
        'echo "sbatch: warning: something" >&2\necho "4243"',
    )
    slurm = Slurm(bin_dir)

    job_id = slurm.submit(tmp_path / "ignored.sh")

    assert job_id == "4243"


def test_submit_stub_two_id_lines_raises_scheduler_command_error(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(bin_dir, "sbatch", 'echo "4243"\necho "4244"')
    slurm = Slurm(bin_dir)

    with pytest.raises(SchedulerCommandError, match="sbatch failed"):
        slurm.submit(tmp_path / "ignored.sh")


def test_submit_after_malformed_raises_value_error(tmp_path: Path) -> None:
    slurm = Slurm(tmp_path / "bin")

    with pytest.raises(ValueError, match="not a Slurm job id"):
        slurm.submit(tmp_path / "ignored.sh", after="1,singleton")


def test_submit_after_trailing_newline_raises_value_error(
    tmp_path: Path,
) -> None:
    slurm = Slurm(tmp_path / "bin")

    with pytest.raises(ValueError, match="not a Slurm job id"):
        slurm.submit(tmp_path / "ignored.sh", after="123\n")


def test_submit_missing_executable_in_bin_dir_raises_scheduler_command_error(
    tmp_path: Path,
) -> None:
    empty_bin = tmp_path / "emptybin"
    empty_bin.mkdir()
    slurm = Slurm(empty_bin)

    with pytest.raises(SchedulerCommandError, match="sbatch not found"):
        slurm.submit(tmp_path / "ignored.sh")


def test_submit_missing_executable_on_path_raises_scheduler_command_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    monkeypatch.setenv("PATH", str(empty_dir))
    slurm = Slurm()

    with pytest.raises(SchedulerCommandError, match="sbatch not found"):
        slurm.submit(tmp_path / "ignored.sh")


def test_submit_exec_permission_error_wrapped_as_scheduler_command_error(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "sbatch"
    stub.write_text("#!/bin/bash\necho hi\n")
    stub.chmod(0o644)
    slurm = Slurm(bin_dir)

    with pytest.raises(SchedulerCommandError):
        slurm.submit(tmp_path / "ignored.sh")


# --- job_state ---


def test_job_state_unknown_job_id_returns_none(fake_slurm: FakeSlurm) -> None:
    slurm = Slurm(fake_slurm.bin_dir)

    assert slurm.job_state("999999") is None


def test_job_state_amended_lifecycle_running_completed_then_purged(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    script = write_script(tmp_path, "job.sh", "sleep 2\nexit 0")
    slurm = Slurm(fake_slurm.bin_dir)
    job_id = slurm.submit(script)

    wait_for_state(fake_slurm, int(job_id), "RUNNING")
    running = slurm.job_state(job_id)
    assert running is not None
    assert running.state == "RUNNING"
    assert running.start_time is not None

    fake_slurm.wait_idle()
    completed = slurm.job_state(job_id)
    assert completed is not None
    assert completed.state == "COMPLETED"
    assert "COMPLETED" not in NON_TERMINAL_STATES

    fake_slurm.purge(int(job_id))
    assert slurm.job_state(job_id) is None


def test_job_state_reason_with_space_is_preserved_intact(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    script = write_script(tmp_path, "job.sh", "sleep 30")
    slurm = Slurm(fake_slurm.bin_dir)
    job_id = slurm.submit(script)
    wait_for_state(fake_slurm, int(job_id), "RUNNING")

    cancel_result = scancel(job_id)
    assert cancel_result.returncode == 0
    wait_for_state(fake_slurm, int(job_id), "CANCELLED")

    state = slurm.job_state(job_id)

    assert state is not None
    assert state.reason == f"cancelled by {os.getuid()}"


def test_job_state_squeue_wrong_job_id_raises_scheduler_command_error(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(
        bin_dir, "squeue", "echo '999|RUNNING|None|UNLIMITED|N/A'"
    )
    slurm = Slurm(bin_dir)

    with pytest.raises(SchedulerCommandError, match="malformed output"):
        slurm.job_state("123")


def test_job_state_squeue_multiple_lines_raises_scheduler_command_error(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    line = "123|RUNNING|None|UNLIMITED|2026-01-01T00:00:00"
    write_executable_stub(bin_dir, "squeue", f"echo '{line}'\necho '{line}'")
    slurm = Slurm(bin_dir)

    with pytest.raises(SchedulerCommandError, match="returned 2 lines"):
        slurm.job_state("123")


def test_job_state_reason_with_pipe_character_survives_split(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(
        bin_dir, "squeue", "echo '123|PENDING|weird|reason|30:00|N/A'"
    )
    slurm = Slurm(bin_dir)

    state = slurm.job_state("123")

    assert state is not None
    assert state.reason == "weird|reason"
    assert state.time_limit == timedelta(minutes=30)
    assert state.start_time is None


def test_job_state_squeue_too_few_fields_raises_scheduler_command_error(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(bin_dir, "squeue", "echo '123|RUNNING'")
    slurm = Slurm(bin_dir)

    with pytest.raises(SchedulerCommandError, match="malformed output"):
        slurm.job_state("123")


def test_job_state_squeue_timeout_raises_scheduler_command_error(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(bin_dir, "squeue", "sleep 5")
    slurm = Slurm(bin_dir, timeout=0.5)

    with pytest.raises(SchedulerCommandError, match="squeue timed out"):
        slurm.job_state("123")


def test_job_state_squeue_nonzero_rc_empty_output_raises_scheduler_command_error(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(bin_dir, "squeue", "exit 2")
    slurm = Slurm(bin_dir)

    with pytest.raises(SchedulerCommandError, match="squeue failed"):
        slurm.job_state("123")


def test_job_state_squeue_malformed_time_limit_raises_scheduler_command_error(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(
        bin_dir, "squeue", "echo '123|RUNNING|None|garbage|N/A'"
    )
    slurm = Slurm(bin_dir)

    with pytest.raises(SchedulerCommandError, match="malformed output"):
        slurm.job_state("123")


def test_job_state_job_id_malformed_raises_value_error(
    tmp_path: Path,
) -> None:
    slurm = Slurm(tmp_path / "bin")

    with pytest.raises(ValueError, match="not a Slurm job id"):
        slurm.job_state("1?afterok:2")


def test_job_state_job_id_trailing_newline_raises_value_error(
    tmp_path: Path,
) -> None:
    slurm = Slurm(tmp_path / "bin")

    with pytest.raises(ValueError, match="not a Slurm job id"):
        slurm.job_state("123\n")


# --- parse_time_limit ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1-00:00:00", timedelta(days=1)),
        ("24:00:00", timedelta(hours=24)),
        ("30:00", timedelta(minutes=30)),
        ("5", timedelta(minutes=5)),
        ("UNLIMITED", None),
        ("INVALID", None),
        ("NOT_SET", None),
        ("", None),
        (
            "2-10:30:15",
            timedelta(days=2, hours=10, minutes=30, seconds=15),
        ),
    ],
)
def test_parse_time_limit_table_returns_expected_timedelta(
    text: str, expected: timedelta | None
) -> None:
    assert parse_time_limit(text) == expected


@pytest.mark.parametrize(
    "text", ["abc", "1-2-3", "12:60:00x", "100:", ":30", "1-"]
)
def test_parse_time_limit_malformed_value_raises_value_error(
    text: str,
) -> None:
    with pytest.raises(ValueError, match="malformed Slurm time limit"):
        parse_time_limit(text)


def test_parse_time_limit_trailing_newline_raises_value_error() -> None:
    with pytest.raises(ValueError, match="malformed Slurm time limit"):
        parse_time_limit("24:00:00\n")


# --- parse_slurm_time ---


@pytest.mark.parametrize("text", ["N/A", "Unknown", "None", ""])
def test_parse_slurm_time_unset_values_return_none(text: str) -> None:
    assert parse_slurm_time(text) is None


def test_parse_slurm_time_valid_timestamp_returns_tz_aware_datetime() -> None:
    result = parse_slurm_time("2026-01-01T12:30:00")

    assert result is not None
    assert result.tzinfo is not None
    assert result == datetime(2026, 1, 1, 12, 30, 0).astimezone()


def test_parse_slurm_time_malformed_value_raises_value_error() -> None:
    with pytest.raises(ValueError, match="does not match format"):
        parse_slurm_time("garbage-not-a-date")
