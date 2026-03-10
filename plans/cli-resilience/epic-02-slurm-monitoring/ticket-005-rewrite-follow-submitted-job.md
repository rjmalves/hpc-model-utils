# ticket-005 Rewrite follow_submitted_job Without Initial Sleep

## Context

### Background

The current `follow_submitted_job()` in `app/utils/scheduler.py` starts with a hardcoded `sleep(5)` before beginning to monitor the SLURM job. This means jobs that complete in under 5 seconds are never monitored — the function sleeps through the entire execution, wakes up, checks `squeue` (job is gone), and exits without having read `stdout.modelops`. The operator sees no output from the model run.

This is the most critical fragility in the SLURM monitoring path. DECOMP post-processing jobs and some small GEVAZP-related SLURM submissions finish in seconds, and their output is lost.

### Relation to Epic

This is the first and most important ticket in Epic 02 (SLURM Monitoring). It fixes the root cause of the fast-job output loss. Tickets 006 and 007 build on this foundation.

### Current State

File `app/utils/scheduler.py`, lines 65-80:

```python
def follow_submitted_job(job_id: str, timeout: float):
    sleep(5)  # PROBLEM: blocks for 5s before any monitoring
    status_code, _ = run_in_terminal(
        [
            f"while squeue | grep {job_id} > /dev/null ;do",
            "if [ -e stdout.modelops ];",
            "then tail -n 100 stdout.modelops;",
            f"else squeue -a -j {job_id};  fi; sleep 5; done 2>&1",
        ],
        timeout=timeout,
        last_lines_diff=100,
        log_output=True,
    )
    if status_code != 0:
        raise RuntimeError(f"Error following submitted job: {status_code}")
    return None
```

Problems:

1. `sleep(5)` at line 66 means zero monitoring for the first 5 seconds
2. The `while squeue | grep` loop exits immediately if the job already finished
3. After the loop exits, `stdout.modelops` is never read — the function just returns
4. The `tail -n 100` inside the loop only shows the last 100 lines, losing earlier output on long runs (acceptable trade-off, but the loop must at least execute)

Callers:

- `app/adapter/repository/newave.py` line 369: `follow_submitted_job(job_id, self.NEWAVE_JOB_TIMEOUT)` (172800s = 48h)
- `app/adapter/repository/decomp.py` line 505: `follow_submitted_job(job_id, self.DECOMP_JOB_TIMEOUT)`

## Specification

### Requirements

Rewrite `follow_submitted_job()` with the following behavior:

1. **No initial sleep**: Start monitoring immediately after being called
2. **Immediate file check**: Before entering the monitoring loop, check if `stdout.modelops` already exists (the job may have already started writing)
3. **Polling loop**: Check if the job is still in `squeue`. If yes, read `stdout.modelops` (if it exists) and sleep for a shorter interval (2 seconds instead of 5). If no, exit the loop.
4. **Post-completion read**: After the loop exits (job no longer in `squeue`), **always** read the final `stdout.modelops` content. This is the critical fix — even if the loop body never executed, the final read captures the output.
5. **Maintain the existing interface**: Same function signature `follow_submitted_job(job_id: str, timeout: float)`, same return type, same `RuntimeError` on failure.
6. **Add a logger parameter**: Add an optional `logger` parameter to enable structured logging of monitoring events. If not provided, use `print()` as currently done via `run_in_terminal(log_output=True)`.

### Shell Command Strategy

Replace the single compound shell command with separate, targeted commands:

- **Job status check**: `squeue -h -j {job_id}` (returns empty if job not found, non-zero exit if job ID is invalid)
- **Output tailing**: `tail -n 100 stdout.modelops` (read via `run_in_terminal`)
- **Final output read**: `cat stdout.modelops` (full read after completion)

This decomposition avoids the fragile compound `while/if/fi/done` shell one-liner and makes each step independently testable.

### Outputs/Behavior

- Returns `None` on success (same as current)
- Raises `RuntimeError` if the monitoring encounters an error
- Logs output from `stdout.modelops` as it becomes available
- After job completion, logs the full final content of `stdout.modelops`

### Error Handling

- If `squeue` command fails (non-zero exit): raise `RuntimeError`
- If `stdout.modelops` does not exist after job completion: log a warning but do not raise (the job may not have produced output)
- If timeout is exceeded: terminate monitoring and raise `RuntimeError` (same as current behavior via `run_in_terminal` timeout)

## Acceptance Criteria

- [ ] Given `follow_submitted_job(job_id, timeout)` is called, when the function starts executing, then there is no `sleep()` call before the first `squeue` check
- [ ] Given a SLURM job that finishes in 1 second and writes to `stdout.modelops`, when `follow_submitted_job` is called for that job, then the content of `stdout.modelops` is read and logged via the post-completion read step
- [ ] Given a SLURM job that runs for 30 seconds, when `follow_submitted_job` monitors it, then `stdout.modelops` is tailed at least 10 times during the 30-second run (approximately every 2 seconds)
- [ ] Given a SLURM job completes and `stdout.modelops` does not exist, when the post-completion read step runs, then a warning is logged and no exception is raised
- [ ] Given the function signature, when `follow_submitted_job(job_id, timeout)` is called without a logger, then it behaves identically to when called with `logger=None` (backward compatible)

## Implementation Guide

### Suggested Approach

Replace the implementation of `follow_submitted_job` in `app/utils/scheduler.py`:

```python
import os
from logging import Logger
from time import sleep, time

from app.utils.terminal import run_in_terminal


def follow_submitted_job(
    job_id: str,
    timeout: float,
    logger: Logger | None = None,
):
    """Monitor a submitted SLURM job until completion.

    Immediately starts monitoring (no initial delay).
    After job exits squeue, reads final stdout.modelops.
    """
    start_time = time()
    poll_interval = 2.0
    stdout_file = "stdout.modelops"

    def _log(msg: str):
        if logger:
            logger.info(msg)
        else:
            print(msg, flush=True)

    def _job_in_queue() -> bool:
        code, output = run_in_terminal(
            [f"squeue -h -j {job_id}"],
            timeout=10,
        )
        # squeue -h returns empty output if job not found
        if code is None or code != 0:
            return False
        return any(line.strip() for line in output)

    def _tail_stdout():
        if os.path.exists(stdout_file):
            code, output = run_in_terminal(
                [f"tail -n 100 {stdout_file}"],
                timeout=10,
                log_output=True,
                last_lines_diff=100,
            )

    # Monitoring loop — no initial sleep
    while _job_in_queue():
        _tail_stdout()
        elapsed = time() - start_time
        if elapsed > timeout:
            raise RuntimeError(
                f"Timeout ({timeout}s) waiting for job {job_id}"
            )
        sleep(poll_interval)

    # Post-completion: always read final output
    _log(f"Job {job_id} no longer in squeue. Reading final output...")
    if os.path.exists(stdout_file):
        code, output = run_in_terminal(
            [f"cat {stdout_file}"],
            timeout=30,
            log_output=True,
        )
    else:
        _log(f"Warning: {stdout_file} not found after job completion")

    return None
```

Note: The above is a reference implementation. The implementer should adapt it to match the exact coding conventions found in the codebase (import ordering, logging style, etc.).

### Key Files to Modify

- `app/utils/scheduler.py` (rewrite `follow_submitted_job` function, lines 65-80)

### Patterns to Follow

- Use `run_in_terminal()` for all shell commands (existing pattern throughout the codebase)
- Use `os.path.exists()` for file existence checks (used in `app/utils/fs.py`)
- Keep the function signature compatible: callers pass `(job_id, timeout)` — the new `logger` parameter is optional

### Pitfalls to Avoid

- Do NOT use `inotifywait` or filesystem watchers — the target systems may not have `inotify-tools` installed, and `run_in_terminal` already handles subprocess I/O efficiently
- Do NOT change the callers in `newave.py` and `decomp.py` in this ticket — they pass `(job_id, timeout)` which remains valid. The `logger` parameter will be wired in a later ticket or as a quick follow-up
- Do NOT reduce `last_lines_diff` below 100 — the current value is tuned to avoid flooding logs with repeated lines during long NEWAVE runs
- Do NOT change `submit_job()`, `cancel_submitted_job()`, or `wait_cancelled_job()` in this ticket — they are out of scope

## Testing Requirements

### Unit Tests

- Mock `run_in_terminal` and `os.path.exists` to simulate:
  - Fast job (first `_job_in_queue()` returns False, `stdout.modelops` exists)
  - Normal job (3 iterations of loop, then job exits)
  - Missing stdout (job exits, `stdout.modelops` does not exist)
  - Timeout (loop runs until timeout)

### Integration Tests

Not applicable (requires SLURM cluster).

## Dependencies

- **Blocked By**: None (independent of Epic 01)
- **Blocks**: ticket-006-add-sacct-post-completion.md, ticket-007-capture-stderr-and-final-stdout.md

## Effort Estimate

**Points**: 3
**Confidence**: High
