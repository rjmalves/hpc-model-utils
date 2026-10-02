"""System tests for the Slurm outcome adapter (ticket-026, ADR-022,
R43, R47, R110), against the fake SLURM harness and stub bin
directories.

Several fake-SLURM scenarios set a forced outcome with
``fake_slurm.force("model", ...)`` (the output-filename phase key)
rather than ``force(job_id, ...)``: the job id is not known until
``submit()`` returns, and the detached runner reads the force record
once, right as it starts the job, so setting it by job id only after
``submit()`` would race the runner. Keying by the ``model`` phase
(via ``#SBATCH --output=model.out``) lets the force record be written
before submission, deterministically.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hpc_model_utils.infra.errors import SchedulerCommandError
from hpc_model_utils.infra.slurm import (
    Slurm,
    _first_token,
    _parse_exit_code,
    _parse_sacct,
    _parse_scontrol,
)
from tests.support.fake_slurm import FakeSlurm
from tests.support.fake_slurm.cli import (
    scancel,
    wait_for_state,
    write_executable_stub,
    write_script,
)


def _count_invocations(fake_slurm: FakeSlurm, cmd: str) -> int:
    log = (fake_slurm.state_dir / "invocations.jsonl").read_text()
    return sum(
        1
        for line in log.splitlines()
        if line.strip() and json.loads(line)["cmd"] == cmd
    )


@pytest.fixture(autouse=True)
def _chdir_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A job without an explicit #SBATCH --output writes slurm-<id>.out
    # against the process cwd; pin it to tmp_path so it never leaks
    # into the repo.
    monkeypatch.chdir(tmp_path)


# --- _parse_sacct ---


def test_parse_sacct_job_and_step_rows_split_by_prefix() -> None:
    lines = [
        "12|RUNNING|0:0|1:00|UNLIMITED|2026-01-01T00:00:00|2026-01-01T01:00:00",
        "12.batch|COMPLETED|0:0|1:00||2026-01-01T00:00:00|2026-01-01T01:00:00",
        "123|RUNNING|0:0|2:00|UNLIMITED|2026-01-01T00:00:00|"
        "2026-01-01T02:00:00",
    ]

    job_row, step_rows = _parse_sacct(lines, "12")

    assert job_row is not None
    assert job_row.job_id == "12"
    assert job_row.state == "RUNNING"
    assert [row.job_id for row in step_rows] == ["12.batch"]


def test_parse_sacct_malformed_line_fewer_than_seven_fields_ignored() -> None:
    job_row, step_rows = _parse_sacct(["12|RUNNING|0:0"], "12")

    assert job_row is None
    assert step_rows == []


def test_parse_sacct_blank_and_warning_lines_ignored() -> None:
    lines = [
        "",
        "sacct: warning: something",
        "12|COMPLETED|0:0|1:00|UNLIMITED|2026-01-01T00:00:00|"
        "2026-01-01T01:00:00",
    ]

    job_row, _ = _parse_sacct(lines, "12")

    assert job_row is not None
    assert job_row.state == "COMPLETED"


# --- _parse_scontrol ---


def test_parse_scontrol_reason_with_spaces_inside_parens_reassembled() -> None:
    line = (
        "JobId=1000 JobState=PENDING Reason=(launch failed requeued "
        "held) ExitCode=0:0 TimeLimit=UNLIMITED StartTime=N/A "
        "EndTime=Unknown WorkDir=/tmp"
    )

    fields = _parse_scontrol(line)

    assert fields["JobId"] == "1000"
    assert fields["JobState"] == "PENDING"
    assert fields["Reason"] == "(launch failed requeued held)"
    assert fields["WorkDir"] == "/tmp"


def test_parse_scontrol_workdir_path_with_spaces_reassembled() -> None:
    line = "JobId=1000 JobState=COMPLETED WorkDir=/some path/with spaces"

    fields = _parse_scontrol(line)

    assert fields["WorkDir"] == "/some path/with spaces"


# --- _first_token ---


def test_first_token_cancelled_by_uid_normalizes_to_cancelled() -> None:
    assert _first_token("CANCELLED by 0") == "CANCELLED"


def test_first_token_single_word_returns_itself() -> None:
    assert _first_token("COMPLETED") == "COMPLETED"


def test_first_token_empty_string_returns_itself() -> None:
    assert _first_token("") == ""


# --- _parse_exit_code ---


def test_parse_exit_code_valid_rc_sig_parses_ints() -> None:
    assert _parse_exit_code("1:0") == (1, None)
    assert _parse_exit_code("0:9") == (0, 9)


def test_parse_exit_code_malformed_returns_none_none() -> None:
    assert _parse_exit_code("not-a-code") == (None, None)
    assert _parse_exit_code("") == (None, None)


# --- outcome: fake-SLURM scenarios ---


def test_outcome_forced_failed_with_step_oom_returns_sacct_source(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    fake_slurm.force("model", state="FAILED", exit_code="1:0", step_oom=True)
    script = write_script(
        tmp_path, "model.sh", "#SBATCH --output=model.out\nexit 0"
    )
    slurm = Slurm(fake_slurm.bin_dir)
    job_id = slurm.submit(script)
    fake_slurm.wait_idle()

    result = slurm.outcome(job_id, sleep=lambda s: None)

    assert result.state == "FAILED"
    assert result.oom is True
    assert result.exit_code == 1
    assert result.signal is None
    assert result.source == "sacct"


def test_outcome_forced_cancelled_normalizes_state_to_cancelled(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    fake_slurm.force("model", state="CANCELLED")
    script = write_script(
        tmp_path, "model.sh", "#SBATCH --output=model.out\nexit 0"
    )
    slurm = Slurm(fake_slurm.bin_dir)
    job_id = slurm.submit(script)
    fake_slurm.wait_idle()

    result = slurm.outcome(job_id, sleep=lambda s: None)

    assert result.state == "CANCELLED"
    assert result.source == "sacct"


def test_outcome_forced_timeout_returns_sacct_source(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    fake_slurm.force("model", state="TIMEOUT")
    script = write_script(
        tmp_path, "model.sh", "#SBATCH --output=model.out\nsleep 30"
    )
    slurm = Slurm(fake_slurm.bin_dir)
    job_id = slurm.submit(script)
    fake_slurm.wait_idle()

    result = slurm.outcome(job_id, sleep=lambda s: None)

    assert result.state == "TIMEOUT"
    assert result.source == "sacct"


def test_outcome_forced_node_fail_returns_sacct_source(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    fake_slurm.force("model", state="NODE_FAIL")
    script = write_script(
        tmp_path, "model.sh", "#SBATCH --output=model.out\nsleep 30"
    )
    slurm = Slurm(fake_slurm.bin_dir)
    job_id = slurm.submit(script)
    fake_slurm.wait_idle()

    result = slurm.outcome(job_id, sleep=lambda s: None)

    assert result.state == "NODE_FAIL"
    assert result.source == "sacct"


def test_outcome_scancel_by_uid_returns_cancelled_state(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    script = write_script(tmp_path, "job.sh", "sleep 30")
    slurm = Slurm(fake_slurm.bin_dir)
    job_id = slurm.submit(script)
    wait_for_state(fake_slurm, int(job_id), "RUNNING")

    cancel_result = scancel(job_id)
    assert cancel_result.returncode == 0
    wait_for_state(fake_slurm, int(job_id), "CANCELLED")

    result = slurm.outcome(job_id, sleep=lambda s: None)

    assert result.state == "CANCELLED"
    assert result.source == "sacct"


def test_outcome_disabled_accounting_falls_back_to_scontrol(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    script = write_script(tmp_path, "job.sh", "exit 0")
    slurm = Slurm(fake_slurm.bin_dir)
    job_id = slurm.submit(script)
    fake_slurm.wait_idle()
    fake_slurm.disable_accounting()

    result = slurm.outcome(job_id, sleep=lambda s: None)

    assert result.source == "scontrol"
    assert result.state == "COMPLETED"


def test_outcome_purged_after_disabled_accounting_returns_none_with_two_sleeps(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    script = write_script(tmp_path, "job.sh", "exit 0")
    slurm = Slurm(fake_slurm.bin_dir)
    job_id = slurm.submit(script)
    fake_slurm.wait_idle()
    fake_slurm.disable_accounting()
    fake_slurm.purge(int(job_id))
    sleeps: list[float] = []

    result = slurm.outcome(job_id, attempts=3, sleep=sleeps.append)

    assert result.source == "none"
    assert result.state == "UNKNOWN"
    assert sleeps == [10.0, 10.0]


def test_outcome_lag_sacct_recovers_to_sacct_source(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    script = write_script(tmp_path, "job.sh", "exit 0")
    slurm = Slurm(fake_slurm.bin_dir)
    job_id = slurm.submit(script)
    fake_slurm.wait_idle()
    fake_slurm.lag_sacct(2)

    result = slurm.outcome(job_id, attempts=4, sleep=lambda s: None)

    assert result.source == "sacct"
    assert result.state == "COMPLETED"


def test_outcome_lag_longer_than_budget_falls_back_to_scontrol_on_last_attempt(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    script = write_script(tmp_path, "job.sh", "exit 0")
    slurm = Slurm(fake_slurm.bin_dir)
    job_id = slurm.submit(script)
    fake_slurm.wait_idle()
    fake_slurm.lag_sacct(10)
    sleeps: list[float] = []

    result = slurm.outcome(job_id, attempts=3, sleep=sleeps.append)

    assert result.source == "scontrol"
    assert result.state == "COMPLETED"
    assert sleeps == [10.0, 10.0]
    assert _count_invocations(fake_slurm, "scontrol") == 1


def test_outcome_job_still_running_exhausts_attempts_returns_none(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    script = write_script(tmp_path, "job.sh", "sleep 30")
    slurm = Slurm(fake_slurm.bin_dir)
    job_id = slurm.submit(script)
    wait_for_state(fake_slurm, int(job_id), "RUNNING")
    sleeps: list[float] = []

    result = slurm.outcome(job_id, attempts=3, sleep=sleeps.append)

    assert result.source == "none"
    assert result.state == "UNKNOWN"
    assert sleeps == [10.0, 10.0]


# --- outcome: validation and stub-bin error handling ---


def test_outcome_job_id_malformed_raises_value_error(tmp_path: Path) -> None:
    slurm = Slurm(tmp_path / "bin")

    with pytest.raises(ValueError, match="not a Slurm job id"):
        slurm.outcome("123\n")


def test_outcome_attempts_less_than_one_raises_value_error(
    tmp_path: Path,
) -> None:
    slurm = Slurm(tmp_path / "bin")

    with pytest.raises(ValueError, match="attempts must be at least 1"):
        slurm.outcome("123", attempts=0)


def test_outcome_missing_sacct_executable_raises_scheduler_command_error(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(bin_dir, "scontrol", "echo noop")
    slurm = Slurm(bin_dir)

    with pytest.raises(SchedulerCommandError, match="sacct not found"):
        slurm.outcome("123")


def test_outcome_missing_scontrol_executable_raises_scheduler_command_error(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(bin_dir, "sacct", "echo noop")
    slurm = Slurm(bin_dir)

    with pytest.raises(SchedulerCommandError, match="scontrol not found"):
        slurm.outcome("123")


def test_outcome_sacct_exec_permission_error_wrapped_as_scheduler_command_error(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "sacct"
    stub.write_text("#!/bin/bash\necho hi\n")
    stub.chmod(0o644)
    write_executable_stub(bin_dir, "scontrol", "echo noop")
    slurm = Slurm(bin_dir)

    with pytest.raises(SchedulerCommandError):
        slurm.outcome("123")


def test_outcome_sacct_timeout_is_absorbed_and_falls_back_to_scontrol(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(bin_dir, "sacct", "sleep 5")
    write_executable_stub(
        bin_dir,
        "scontrol",
        "echo 'JobId=123 JobState=COMPLETED ExitCode=0:0 "
        "TimeLimit=UNLIMITED RunTime=00:01:00'",
    )
    slurm = Slurm(bin_dir, timeout=0.2)

    result = slurm.outcome("123", sleep=lambda s: None)

    assert result.source == "scontrol"
    assert result.state == "COMPLETED"
    assert result.elapsed == "00:01:00"


def test_outcome_scontrol_malformed_exit_code_returns_none_none(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(bin_dir, "sacct", "exit 1")
    write_executable_stub(
        bin_dir,
        "scontrol",
        "echo 'JobId=123 JobState=COMPLETED ExitCode=garbage "
        "TimeLimit=UNLIMITED'",
    )
    slurm = Slurm(bin_dir)

    result = slurm.outcome("123", sleep=lambda s: None)

    assert result.source == "scontrol"
    assert result.state == "COMPLETED"
    assert result.exit_code is None
    assert result.signal is None


def test_outcome_raw_truncated_to_500_characters(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    long_line = "x" * 600
    write_executable_stub(bin_dir, "sacct", f"echo '{long_line}'\nexit 1")
    write_executable_stub(bin_dir, "scontrol", f"echo '{long_line}'\nexit 1")
    slurm = Slurm(bin_dir)

    result = slurm.outcome("123", attempts=1, sleep=lambda s: None)

    assert result.source == "none"
    assert len(result.raw) <= 500


def test_outcome_sleeps_exactly_attempts_minus_one_times(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable_stub(bin_dir, "sacct", "exit 1")
    write_executable_stub(bin_dir, "scontrol", "exit 1")
    slurm = Slurm(bin_dir)
    sleeps: list[float] = []

    slurm.outcome("123", attempts=5, backoff=2.5, sleep=sleeps.append)

    assert sleeps == [2.5, 2.5, 2.5, 2.5]
