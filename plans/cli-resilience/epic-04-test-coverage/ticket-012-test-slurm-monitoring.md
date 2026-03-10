# ticket-012 Add Unit Tests for SLURM Monitoring Functions

## Context

### Background

Epic 02 rewrote `follow_submitted_job()` and introduced `get_job_completion_info()`, `read_job_output_files()`, `JobCompletionInfo`, and `JobOutputFiles` in `app/utils/scheduler.py`. These functions already have 33 tests in `tests/unit/test_follow_submitted_job.py` covering the core scenarios (fast job, normal job, timeout, squeue failure, sacct parsing, output file reading, stderr logging). However, three functions in the same module remain untested: `submit_job()`, `cancel_submitted_job()`, and `wait_cancelled_job()`. This ticket adds tests for those functions.

### Relation to Epic

Epic 04 adds comprehensive test coverage. Ticket-012 is scoped to SLURM scheduler functions in `app/utils/scheduler.py`. The existing `test_follow_submitted_job.py` file already provides thorough coverage for `follow_submitted_job`, `get_job_completion_info`, and `read_job_output_files`. This ticket focuses exclusively on the three untested functions: `submit_job()`, `cancel_submitted_job()`, and `wait_cancelled_job()`.

### Current State

- `tests/unit/test_follow_submitted_job.py` has 33 passing tests across 8 test classes covering `follow_submitted_job`, `get_job_completion_info`, and `read_job_output_files`.
- `submit_job()` (lines 124-167 of `app/utils/scheduler.py`) calls `run_in_terminal` with sbatch arguments, parses the output with `SLURM_SUBMISSION_REGEX_PATTERN`, and returns the job ID string or `None`.
- `cancel_submitted_job()` (lines 247-252) calls `scancel` via `run_in_terminal` and returns `True` if exit code is 0, `False` otherwise.
- `wait_cancelled_job()` (lines 255-267) polls `squeue | grep` via `run_in_terminal` and raises `RuntimeError` on non-zero exit.
- None of these three functions have any test coverage.

## Specification

### Requirements

1. Add test classes for `submit_job()`, `cancel_submitted_job()`, and `wait_cancelled_job()` to the existing `tests/unit/test_follow_submitted_job.py` file (keeping all SLURM scheduler tests in one file).
2. `submit_job()` tests must cover: successful submission (returns job ID), failed submission (returns `None`), missing regex match (returns `None`), and the `max_tasks_per_node` / `max_job_time_hours` optional parameter branches.
3. `cancel_submitted_job()` tests must cover: successful cancel (returns `True`), failed cancel (returns `False`).
4. `wait_cancelled_job()` tests must cover: successful wait (returns `None`), non-zero exit (raises `RuntimeError`).

### Inputs/Props

- `submit_job(queue, core_count, job_path, cpus_per_task=1, max_tasks_per_node=None, max_job_time_hours=None, skip_model=False)` — all subprocess calls go through `run_in_terminal`, which must be patched.
- `cancel_submitted_job(job_id)` — calls `run_in_terminal(["scancel", job_id], log_output=True)`.
- `wait_cancelled_job(job_id, timeout)` — calls `run_in_terminal` with a compound shell command.

### Outputs/Behavior

- `submit_job()` returns `str | None` (the job ID or `None`).
- `cancel_submitted_job()` returns `bool`.
- `wait_cancelled_job()` returns `None` on success, raises `RuntimeError` on failure.

### Error Handling

- `wait_cancelled_job()` raises `RuntimeError` with message containing the status code when `run_in_terminal` returns non-zero.
- `submit_job()` never raises; returns `None` on any failure path.

## Acceptance Criteria

- [ ] Given `tests/unit/test_follow_submitted_job.py` exists with 33 tests, when this ticket is implemented, then the file contains at least 41 tests total (33 existing + 8 new minimum) and `pytest tests/unit/test_follow_submitted_job.py` exits with code 0
- [ ] Given `submit_job()` parses sbatch output using `SLURM_SUBMISSION_REGEX_PATTERN`, when `run_in_terminal` returns `(0, ["Submitted batch job 67890"])`, then `submit_job("normal", 64, "run.sh")` returns `"67890"`
- [ ] Given `submit_job()` receives `max_job_time_hours=48`, when the function builds the sbatch command, then the command list contains a `--time=2-00:00:00` argument (48 hours = 2 days, 0 hours)
- [ ] Given `cancel_submitted_job()` wraps `scancel`, when `run_in_terminal` returns exit code 1, then `cancel_submitted_job("12345")` returns `False`
- [ ] Given `wait_cancelled_job()` raises on non-zero exit, when `run_in_terminal` returns `(1, [...])`, then `RuntimeError` is raised with the status code in the message

## Implementation Guide

### Suggested Approach

1. Add three new test classes at the bottom of `tests/unit/test_follow_submitted_job.py`: `TestSubmitJob`, `TestCancelSubmittedJob`, `TestWaitCancelledJob`.
2. Import `submit_job`, `cancel_submitted_job`, `wait_cancelled_job` from `app.utils.scheduler` (add to the existing import block at line 6-10).
3. Import `SLURM_SUBMISSION_REGEX_PATTERN` from `app.utils.constants` to verify the regex is used correctly in tests.
4. For `submit_job()` tests, patch `app.utils.scheduler.run_in_terminal` to return controlled responses. The sbatch output format is `"Submitted batch job NNNNN"` (matched by `SLURM_SUBMISSION_REGEX_PATTERN`).
5. For `submit_job()` optional parameter tests, capture the command list passed to `run_in_terminal` using `mock.call_args` and assert the presence/absence of `--ntasks-per-node` and `--time` flags.
6. For `cancel_submitted_job()`, patch `run_in_terminal` and verify the return value is `True` or `False` based on exit code.
7. For `wait_cancelled_job()`, patch `run_in_terminal` and test both the success path (`status_code == 0`, returns `None`) and the failure path (`status_code != 0`, raises `RuntimeError`).

### Key Files to Modify

- `tests/unit/test_follow_submitted_job.py` (append ~120 lines)

### Patterns to Follow

- Follow the existing patching pattern in `TestFastJob` and `TestNormalJob`: use `patch("app.utils.scheduler.run_in_terminal", ...)` as the mock target.
- Use module-level constants for test data (like `_SQUEUE_RUNNING`, `_SACCT_COMPLETED` at lines 15-21 of the existing file).
- One test class per function, matching the structure of `TestGetJobCompletionInfo` and `TestReadJobOutputFiles`.

### Pitfalls to Avoid

- `submit_job()` uses the walrus operator `if match and (groups := match.groups())` (line 165). The test must provide output that matches `SLURM_SUBMISSION_REGEX_PATTERN` exactly. Read the constant from `app/utils/constants.py` to understand the expected format.
- `submit_job()` includes empty strings in the command list when optional params are `None` (e.g., the `--ntasks-per-node` ternary on line 148-150 evaluates to `""`). Tests should not assert exact command list length; instead check for presence of specific flags.
- Do not modify any of the existing 33 tests. Append new classes only.

## Testing Requirements

### Unit Tests

This ticket IS the unit tests. The deliverable is new test classes appended to the existing test file.

### Integration Tests

Not applicable. CLI integration tests are ticket-013.

### E2E Tests

Not applicable.

## Dependencies

- **Blocked By**: ticket-007-capture-stderr-and-final-stdout.md (scheduler functions must exist; completed)
- **Blocks**: None

## Effort Estimate

**Points**: 2
**Confidence**: High
