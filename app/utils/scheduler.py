import os
import re
from collections import deque
from dataclasses import dataclass
from logging import Logger
from pathlib import Path
from time import sleep, time

from app.utils.constants import SLURM_SUBMISSION_REGEX_PATTERN
from app.utils.terminal import run_in_terminal


@dataclass
class JobCompletionInfo:
    job_id: str
    state: str          # COMPLETED, FAILED, TIMEOUT, CANCELLED, OUT_OF_MEMORY, UNKNOWN
    exit_code: str      # e.g., "0:0" (exit_code:signal)
    elapsed: str        # e.g., "00:05:23"
    max_rss: str        # e.g., "4096K" or "" if unavailable
    raw_output: str     # Full sacct output line for debugging


@dataclass
class JobOutputFiles:
    stdout_content: str | None  # Content of stdout.modelops, or None if file missing
    stderr_content: str | None  # Content of stderr.modelops, or None if file missing
    stdout_exists: bool
    stderr_exists: bool


def get_job_completion_info(job_id: str) -> JobCompletionInfo | None:
    """Query sacct for job completion information.

    Returns None if sacct is unavailable or the query fails.
    This function is a best-effort diagnostic — failure does not
    indicate a problem with the job itself.
    """
    try:
        status_code, output_lines = run_in_terminal(
            [
                "sacct",
                f"-j {job_id}",
                "--format=JobID,State,ExitCode,Elapsed,MaxRSS",
                "--noheader",
                "--parsable2",
            ],
            timeout=10,
        )
        if status_code is None or status_code != 0:
            return None

        base_line: str | None = None
        for line in output_lines:
            stripped = line.strip()
            if not stripped:
                continue
            parts = stripped.split("|")
            if parts[0] == job_id:
                base_line = stripped
                break

        if base_line is None:
            return None

        parts = base_line.split("|")
        if len(parts) < 5:
            return JobCompletionInfo(
                job_id=job_id,
                state="UNKNOWN",
                exit_code="",
                elapsed="",
                max_rss="",
                raw_output=base_line,
            )

        return JobCompletionInfo(
            job_id=parts[0],
            state=parts[1],
            exit_code=parts[2],
            elapsed=parts[3],
            max_rss=parts[4],
            raw_output=base_line,
        )
    except Exception:
        return None


def read_job_output_files(
    stdout_file: str = "stdout.modelops",
    stderr_file: str = "stderr.modelops",
) -> JobOutputFiles:
    """Read SLURM job output files after completion.

    Reads both stdout.modelops and stderr.modelops using Python's built-in
    open() rather than subprocess to avoid line-dedup suppression. Content is
    capped at the last 10000 lines to prevent memory exhaustion from large files.

    Never raises — missing or unreadable files result in content=None.
    """
    _MAX_LINES = 10000

    def _read_file(path: str) -> tuple[str | None, bool]:
        p = Path(path)
        if not p.exists():
            return None, False
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                lines = deque(fh, maxlen=_MAX_LINES)
            return "".join(lines), True
        except Exception:
            return None, True

    stdout_content, stdout_exists = _read_file(stdout_file)
    stderr_content, stderr_exists = _read_file(stderr_file)

    return JobOutputFiles(
        stdout_content=stdout_content,
        stderr_content=stderr_content,
        stdout_exists=stdout_exists,
        stderr_exists=stderr_exists,
    )


def submit_job(
    queue: str,
    core_count: int,
    job_path: str,
    cpus_per_task: int = 1,
    max_tasks_per_node: int | None = None,
    max_job_time_hours: int | None = None,
    skip_model: bool = False,
) -> str | None:
    formatted_job_time_str = ""
    if max_job_time_hours is not None:
        days = max_job_time_hours // 24
        hours = max_job_time_hours % 24
        formatted_job_time_str = f"{days}-{hours:02}:00:00"

    status_code, output = run_in_terminal(
        [
            "sbatch",
            f"--partition={queue}",
            "--exclusive",
            "--job-name=`basename $PWD`",
            '--output="stdout.modelops"',
            '--error="stderr.modelops"',
            f"--cpus-per-task={cpus_per_task}",
            f"--ntasks-per-node={max_tasks_per_node}"
            if max_tasks_per_node is not None
            else "",
            f"--time={formatted_job_time_str}"
            if max_job_time_hours is not None
            else "",
            "--ntasks",
            str(core_count),
            job_path,
            str(core_count),
            "true" if skip_model else "false",
            "2>&1",
        ],
        log_output=True,
    )
    if status_code == 0:
        match = re.match(SLURM_SUBMISSION_REGEX_PATTERN, output[0])
        if match and (groups := match.groups()):
            return groups[0]
    return None


def follow_submitted_job(
    job_id: str,
    timeout: float,
    logger: Logger | None = None,
) -> None:
    """Monitor a submitted SLURM job until completion.

    Immediately starts monitoring (no initial delay). Polls squeue every
    2 seconds and tails stdout.modelops while the job is running. After
    the job exits squeue, reads the final stdout.modelops content.
    """
    start_time = time()
    poll_interval = 2.0
    stdout_file = "stdout.modelops"

    def _log(msg: str) -> None:
        if logger is not None:
            logger.info(msg)
        else:
            print(msg, flush=True)

    def _job_in_queue() -> bool:
        code, output = run_in_terminal(
            [f"squeue -h -j {job_id}"],
            timeout=10,
        )
        if code is None or code != 0:
            raise RuntimeError(
                f"squeue failed for job {job_id} (exit code: {code})"
            )
        return any(line.strip() for line in output)

    def _tail_stdout() -> None:
        if os.path.exists(stdout_file):
            run_in_terminal(
                [f"tail -n 100 {stdout_file}"],
                timeout=10,
                log_output=True,
                last_lines_diff=100,
            )

    while _job_in_queue():
        _tail_stdout()
        elapsed = time() - start_time
        if elapsed > timeout:
            raise RuntimeError(
                f"Timeout ({timeout}s) waiting for job {job_id}"
            )
        sleep(poll_interval)

    _log(f"Job {job_id} no longer in squeue. Reading final output...")
    output_files = read_job_output_files()
    if output_files.stdout_content:
        _log(f"=== Final stdout.modelops ({len(output_files.stdout_content)} chars) ===")
        # Log last 200 lines to avoid flooding
        for line in output_files.stdout_content.splitlines()[-200:]:
            _log(line)
    if output_files.stderr_content:
        _log(f"WARNING: stderr.modelops contains output ({len(output_files.stderr_content)} chars):")
        for line in output_files.stderr_content.splitlines()[-50:]:
            _log(f"  STDERR: {line}")
    if not output_files.stdout_exists:
        _log("Warning: stdout.modelops not found after job completion")

    completion_info = get_job_completion_info(job_id)
    if completion_info:
        _log(
            f"Job {job_id} final state: {completion_info.state}, "
            f"exit code: {completion_info.exit_code}, "
            f"elapsed: {completion_info.elapsed}"
        )
        if completion_info.state not in ("COMPLETED",):
            _log(f"WARNING: Job {job_id} ended with state {completion_info.state}")

    return None


def cancel_submitted_job(job_id: str):
    status_code, _ = run_in_terminal(
        ["scancel", f"{job_id}"],
        log_output=True,
    )
    return status_code == 0


def wait_cancelled_job(job_id: str, timeout: float):
    status_code, _ = run_in_terminal(
        [
            f"while squeue | grep {job_id} > /dev/null ;do",
            "squeue -a -j {job_id}; sleep 5; done 2>&1",
        ],
        timeout=timeout,
        last_lines_diff=50,
        log_output=True,
    )
    if status_code != 0:
        raise RuntimeError(f"Error cancelling submitted job: {status_code}")
    return None
