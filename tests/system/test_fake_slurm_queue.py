"""System smoke tests for the fake SLURM queue harness (ticket-022,
ADR-025, R81), including the 2026-10-01 operator amendment: `squeue`
lists a job in every state until it is purged.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from tests.support.fake_slurm import FakeSlurm
from tests.support.fake_slurm.cli import (
    sacct,
    sbatch,
    scancel,
    squeue,
    wait_for_state,
    write_script,
)


def _wait_for_pgid_dead(pgid: int, timeout: float, message: str) -> None:
    deadline = time.time() + timeout
    while True:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        if time.time() > deadline:
            raise AssertionError(message)
        time.sleep(0.05)


def test_sbatch_parsable_flag_prints_bare_job_id(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    script = write_script(tmp_path, "noop.sh", "exit 0")

    result = sbatch("--parsable", str(script), cwd=tmp_path)

    assert result.returncode == 0
    job_id = int(result.stdout.strip())
    fake_slurm.wait_idle()
    assert fake_slurm.job(job_id)["state"] == "COMPLETED"


def test_sbatch_without_parsable_prints_submitted_message(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    script = write_script(tmp_path, "noop.sh", "exit 0")

    result = sbatch(str(script), cwd=tmp_path)

    assert result.returncode == 0
    assert result.stdout.strip().startswith("Submitted batch job ")
    job_id = int(result.stdout.strip().rsplit(" ", 1)[1])
    fake_slurm.wait_idle()
    assert fake_slurm.job(job_id)["state"] == "COMPLETED"


def test_sbatch_percent_j_output_contains_job_id(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    log_dir = tmp_path / "logs"
    script = tmp_path / "echo_id.sh"
    script.write_text(
        "#!/bin/bash\n"
        f"#SBATCH --output={log_dir}/model-%j.out\n"
        "echo $SLURM_JOB_ID\n"
    )
    script.chmod(0o755)

    result = sbatch("--parsable", str(script), cwd=tmp_path)

    assert result.returncode == 0
    job_id = int(result.stdout.strip())
    fake_slurm.wait_idle()
    output = (log_dir / f"model-{job_id}.out").read_text()
    assert str(job_id) in output
    assert fake_slurm.job(job_id)["state"] == "COMPLETED"


def test_sbatch_output_cli_overrides_sbatch_directive(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    script_output = tmp_path / "from-directive.out"
    cli_output = tmp_path / "from-cli.out"
    script = tmp_path / "job.sh"
    script.write_text(
        f"#!/bin/bash\n#SBATCH --output={script_output}\necho hello\n"
    )
    script.chmod(0o755)

    result = sbatch(
        "--parsable", f"--output={cli_output}", str(script), cwd=tmp_path
    )

    assert result.returncode == 0
    job_id = int(result.stdout.strip())
    fake_slurm.wait_idle()
    assert cli_output.read_text().strip() == "hello"
    assert not script_output.exists()
    assert fake_slurm.job(job_id)["directives"]["output"] == str(cli_output)


def test_sbatch_dependency_afterany_runs_after_failed_job(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    script_a = write_script(tmp_path, "a.sh", "sleep 1\nexit 3")
    script_b = write_script(tmp_path, "b.sh", "exit 0")

    result_a = sbatch("--parsable", str(script_a), cwd=tmp_path)
    job_a = int(result_a.stdout.strip())
    result_b = sbatch(
        "--parsable",
        f"--dependency=afterany:{job_a}",
        str(script_b),
        cwd=tmp_path,
    )
    job_b = int(result_b.stdout.strip())

    fake_slurm.wait_idle()

    assert fake_slurm.job(job_a)["state"] == "FAILED"
    assert fake_slurm.job(job_a)["exit_code"] == "3:0"
    assert fake_slurm.job(job_b)["state"] == "COMPLETED"
    assert (
        fake_slurm.job(job_b)["start_time"] >= fake_slurm.job(job_a)["end_time"]
    )


def test_sbatch_dependency_unknown_with_kill_on_invalid_dep_cancels(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    script = write_script(tmp_path, "job.sh", "exit 0")

    result = sbatch(
        "--parsable",
        "--dependency=afterany:999999",
        "--kill-on-invalid-dep=yes",
        str(script),
        cwd=tmp_path,
    )

    assert result.returncode == 0
    job_id = int(result.stdout.strip())
    fake_slurm.wait_idle()
    job = fake_slurm.job(job_id)
    assert job["state"] == "CANCELLED"
    assert job["reason"] == "DependencyNeverSatisfied"


def test_squeue_format_codes_report_job_fields(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    script = write_script(tmp_path, "job.sh", "exit 0")
    result = sbatch("--parsable", str(script), cwd=tmp_path)
    job_id = int(result.stdout.strip())
    fake_slurm.wait_idle()

    squeue_result = squeue("-h", "-j", str(job_id), "-o", "%i|%T|%t|%r|%l|%Z")

    assert squeue_result.returncode == 0
    fields = squeue_result.stdout.strip().split("|")
    assert fields[0] == str(job_id)
    assert fields[1] == "COMPLETED"
    assert fields[2] == "CD"
    assert fields[3] == "None"
    assert fields[4] == "UNLIMITED"
    assert fields[5] == str(tmp_path)


def test_scancel_held_and_running_jobs_both_become_cancelled(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    held_script = write_script(tmp_path, "held.sh", "exit 0")
    running_script = write_script(tmp_path, "running.sh", "sleep 60")

    held_result = sbatch("--parsable", str(held_script), cwd=tmp_path)
    held_id = int(held_result.stdout.strip())
    fake_slurm.hold(held_id)

    running_result = sbatch("--parsable", str(running_script), cwd=tmp_path)
    running_id = int(running_result.stdout.strip())
    wait_for_state(fake_slurm, running_id, "RUNNING")
    pgid = fake_slurm.job(running_id)["pgid"]

    cancel_result = scancel(str(held_id), str(running_id))
    assert cancel_result.returncode == 0

    deadline = time.time() + 5.0
    while True:
        held_state = fake_slurm.job(held_id)["state"]
        running_state = fake_slurm.job(running_id)["state"]
        if held_state == "CANCELLED" and running_state == "CANCELLED":
            break
        if time.time() > deadline:
            raise AssertionError("jobs did not reach CANCELLED within 5s")
        time.sleep(0.05)

    _wait_for_pgid_dead(pgid, 2.0, "running job's process group still alive")

    squeue_result = squeue("-h", "-j", f"{held_id},{running_id}", "-o", "%T")
    assert squeue_result.returncode == 0
    assert squeue_result.stdout.strip().splitlines() == [
        "CANCELLED",
        "CANCELLED",
    ]


def test_scancel_running_job_ignoring_sigterm_escalates_to_sigkill(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    script = write_script(
        tmp_path, "trap.sh", "trap '' TERM\nwhile true; do sleep 1; done\n"
    )

    result = sbatch("--parsable", str(script), cwd=tmp_path)
    job_id = int(result.stdout.strip())
    wait_for_state(fake_slurm, job_id, "RUNNING")
    pgid = fake_slurm.job(job_id)["pgid"]

    cancel_result = scancel(str(job_id))
    assert cancel_result.returncode == 0

    _wait_for_pgid_dead(
        pgid, 5.0, "SIGTERM-ignoring job was not escalated to SIGKILL"
    )

    assert fake_slurm.job(job_id)["state"] == "CANCELLED"


def test_squeue_unknown_single_job_id_exits_1(fake_slurm: FakeSlurm) -> None:
    result = squeue("-h", "-j", "999999", "-o", "%i")

    assert result.returncode == 1
    assert "Invalid job id" in result.stderr


def test_scancel_configuring_job_cancels_before_script_runs(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    marker = tmp_path / "ran.txt"
    script = write_script(tmp_path, "job.sh", f"touch {marker}")

    result = sbatch("--parsable", str(script), cwd=tmp_path)
    job_id = int(result.stdout.strip())
    fake_slurm.hold(job_id)
    fake_slurm.configuring(job_id, 1.0)
    fake_slurm.release(job_id)

    wait_for_state(fake_slurm, job_id, "CONFIGURING")

    cancel_result = scancel(str(job_id))
    assert cancel_result.returncode == 0

    wait_for_state(fake_slurm, job_id, "CANCELLED")
    # Outlive the configuring sleep so _run's post-sleep check has run.
    time.sleep(1.5)

    assert fake_slurm.job(job_id)["state"] == "CANCELLED"
    assert not marker.exists()

    sacct_result = sacct(
        "-j", str(job_id), "--noheader", "--parsable2", "--format=JobID,State"
    )
    assert sacct_result.stdout.strip() == f"{job_id}|CANCELLED"
