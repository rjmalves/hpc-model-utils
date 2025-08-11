import re
from time import sleep

from app.utils.constants import SLURM_SUBMISSION_REGEX_PATTERN
from app.utils.terminal import run_in_terminal


def submit_job(queue: str, core_count: int, job_path: str, cpus_per_task: int = 1, max_tasks_per_node: int | None = None) -> str | None:
    status_code, output = run_in_terminal(
        [
            "sbatch",
            f"--partition={queue}",
            "--exclusive",
            "--job-name=`basename $PWD`",
            '--output="stdout.modelops"',
            '--error="stderr.modelops"',
            f"--cpus-per-task={cpus_per_task}",
            f"--ntasks-per-node={max_tasks_per_node}" if max_tasks_per_node is not None else "",
            "--ntasks",
            str(core_count),
            job_path,
            str(core_count),
            "2>&1",
        ],
        log_output=True,
    )
    if status_code == 0:
        match = re.match(SLURM_SUBMISSION_REGEX_PATTERN, output[0])
        if match:
            groups = match.groups()
            if len(groups) == 0:
                raise RuntimeError("Error matching job submission output")
        return groups[0]
    return None


def follow_submitted_job(job_id: str, timeout: float):
    sleep(5)
    status_code, _ = run_in_terminal(
        [
            f"while squeue | grep {job_id} > /dev/null ;do",
            "if [ -e stdout.modelops ];",
            "then tail -n 25 stdout.modelops;",
            f"else squeue -a -j {job_id};  fi; sleep 5; done 2>&1",
        ],
        timeout=timeout,
        last_lines_diff=50,
        log_output=True,
    )
    if status_code != 0:
        raise RuntimeError(f"Error following submitted job: {status_code}")
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
