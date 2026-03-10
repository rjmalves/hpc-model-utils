# ticket-006 Add sacct-Based Post-Completion Status Capture

## Context

### Background

After ticket-005 rewrites `follow_submitted_job()` to remove the initial sleep and add post-completion output reading, this ticket adds `sacct` (SLURM accounting) as a secondary information source. When a job disappears from `squeue`, `sacct` can tell us the job's final state (COMPLETED, FAILED, TIMEOUT, CANCELLED, OUT_OF_MEMORY) and its exit code. This information is currently unavailable — we only know the job left the queue.

`sacct` is particularly valuable for fast-finishing jobs and for jobs that fail silently (e.g., out of memory kills where `stdout.modelops` is never created).

### Relation to Epic

This is the second ticket in Epic 02. It builds on ticket-005's rewritten monitoring loop to add post-completion diagnostics.

### Current State

After ticket-005, `follow_submitted_job()` exits its monitoring loop when the job leaves `squeue`, then reads `stdout.modelops`. But there is no mechanism to determine HOW the job ended (success vs failure vs OOM vs timeout).

The `sacct` command is available on all SLURM clusters and provides accounting data:

```bash
sacct -j <job_id> --format=JobID,State,ExitCode,Elapsed,MaxRSS --noheader --parsable2
```

Output example: `12345|COMPLETED|0:0|00:05:23|4096K`

## Specification

### Requirements

Add a new function `get_job_completion_info()` to `app/utils/scheduler.py` that calls `sacct` after a job finishes and returns structured information about the job's final state.

Also modify `follow_submitted_job()` (rewritten in ticket-005) to call `get_job_completion_info()` after the monitoring loop exits and log the results.

### New Function

```python
@dataclass
class JobCompletionInfo:
    job_id: str
    state: str          # COMPLETED, FAILED, TIMEOUT, CANCELLED, OUT_OF_MEMORY, UNKNOWN
    exit_code: str      # e.g., "0:0" (exit_code:signal)
    elapsed: str        # e.g., "00:05:23"
    max_rss: str        # e.g., "4096K" or "" if unavailable
    raw_output: str     # Full sacct output line for debugging

def get_job_completion_info(job_id: str) -> JobCompletionInfo | None:
    """Query sacct for job completion information.

    Returns None if sacct is unavailable or the query fails.
    This function is a best-effort diagnostic — failure does not
    indicate a problem with the job itself.
    """
```

### Integration with follow_submitted_job

After the post-completion output read (added in ticket-005), call `get_job_completion_info(job_id)` and log the result:

```python
# After reading stdout.modelops...
completion_info = get_job_completion_info(job_id)
if completion_info:
    _log(f"Job {job_id} final state: {completion_info.state}, "
         f"exit code: {completion_info.exit_code}, "
         f"elapsed: {completion_info.elapsed}")
    if completion_info.state not in ("COMPLETED",):
        _log(f"WARNING: Job {job_id} ended with state {completion_info.state}")
```

### Outputs/Behavior

- `get_job_completion_info()` returns a `JobCompletionInfo` dataclass on success, or `None` if `sacct` is unavailable or fails
- The function never raises exceptions — it is purely diagnostic
- `follow_submitted_job()` logs the completion info but does not change its return value or error behavior

### Error Handling

- If `sacct` is not installed or not in PATH: return `None` (graceful degradation)
- If `sacct` returns non-zero exit code: return `None`
- If `sacct` output cannot be parsed: return `JobCompletionInfo` with `state="UNKNOWN"` and `raw_output` set to the unparseable output

## Acceptance Criteria

- [ ] Given `app/utils/scheduler.py` after ticket-005, when ticket-006 is implemented, then a `JobCompletionInfo` dataclass is defined at module level and a `get_job_completion_info(job_id)` function exists
- [ ] Given `sacct -j 12345 --format=JobID,State,ExitCode,Elapsed,MaxRSS --noheader --parsable2` returns `"12345|COMPLETED|0:0|00:05:23|4096K"`, when `get_job_completion_info("12345")` is called, then it returns `JobCompletionInfo(job_id="12345", state="COMPLETED", exit_code="0:0", elapsed="00:05:23", max_rss="4096K", raw_output="12345|COMPLETED|0:0|00:05:23|4096K")`
- [ ] Given `sacct` is not in PATH, when `get_job_completion_info("12345")` is called, then it returns `None` without raising an exception
- [ ] Given `follow_submitted_job()` monitoring loop exits for job "12345", when post-completion runs, then `get_job_completion_info("12345")` is called and its result is logged
- [ ] Given `sacct` returns state `"FAILED"` for a job, when `follow_submitted_job` logs the result, then the log includes a WARNING about the non-COMPLETED state

## Implementation Guide

### Suggested Approach

1. Add `from dataclasses import dataclass` to `app/utils/scheduler.py` imports
2. Define `JobCompletionInfo` dataclass before the function definitions
3. Implement `get_job_completion_info()`:
   - Build command: `sacct -j {job_id} --format=JobID,State,ExitCode,Elapsed,MaxRSS --noheader --parsable2`
   - Call `run_in_terminal([command], timeout=10)`
   - Parse the pipe-separated output (split by `|`)
   - Handle `.batch` suffix lines from `sacct` (filter to the line matching the base job_id)
   - Wrap in try/except to return `None` on any failure
4. In `follow_submitted_job()`, after the final `stdout.modelops` read, add the `get_job_completion_info()` call and logging

### Key Files to Modify

- `app/utils/scheduler.py` (add `JobCompletionInfo` dataclass, add `get_job_completion_info()` function, modify `follow_submitted_job()`)

### Patterns to Follow

- Use `run_in_terminal()` for the `sacct` call (consistent with all other shell commands in the codebase)
- Use `@dataclass` (Python 3.10+, used in the project)
- Keep `get_job_completion_info()` as a standalone function (not nested inside `follow_submitted_job`) so it can be tested independently

### Pitfalls to Avoid

- Do NOT make `get_job_completion_info()` raise exceptions — it is a best-effort diagnostic tool. All errors must be caught and result in `None` return
- Do NOT block on `sacct` for more than 10 seconds — SLURM accounting databases can be slow; use a short timeout
- `sacct` output may contain multiple lines per job (e.g., `12345` and `12345.batch`). Parse only the line matching the base job ID (no `.` suffix)
- Do NOT change the return type or error behavior of `follow_submitted_job()` — the `sacct` info is logged but does not affect control flow

## Testing Requirements

### Unit Tests

- Test `get_job_completion_info()` with mocked `run_in_terminal` returning valid `sacct` output
- Test with `sacct` returning non-zero exit code (returns `None`)
- Test with unparseable output (returns `JobCompletionInfo` with `state="UNKNOWN"`)
- Test parsing of multi-line `sacct` output (base job + `.batch` line)

### Integration Tests

Not applicable (requires SLURM cluster with accounting enabled).

## Dependencies

- **Blocked By**: ticket-005-rewrite-follow-submitted-job.md
- **Blocks**: ticket-007-capture-stderr-and-final-stdout.md

## Effort Estimate

**Points**: 2
**Confidence**: High
