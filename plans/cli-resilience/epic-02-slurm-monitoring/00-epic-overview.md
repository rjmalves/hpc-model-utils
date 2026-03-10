# Epic 02: SLURM Monitoring Overhaul

## Goal

Fix the SLURM job monitoring strategy so that output from fast-finishing jobs (< 5 seconds) is always captured, and the monitoring loop is more resilient to timing races. Currently, `follow_submitted_job()` sleeps 5 seconds before starting to monitor, which means sub-5-second jobs leave no trace in the logs.

## Scope

- Remove the initial `sleep(5)` from `follow_submitted_job()`
- Start monitoring `stdout.modelops` immediately after `sbatch` returns
- Add `sacct` as a post-completion information source to capture final job state and exit code
- Handle the race condition where the job finishes before `squeue` sees it
- Ensure `stderr.modelops` is also captured after job completion
- Add a `submit_and_follow_job()` function that combines submission and monitoring atomically

## Out of Scope

- Changing the SLURM job scripts in `assets/jobs/`
- Modifying `run_in_terminal()` internals (Epic 02 works within its existing interface)
- Error categorization of SLURM failures (Epic 03)
- Changing DESSEM/GEVAZP execution (they use `run_in_terminal` directly, not SLURM `sbatch`)

## Tickets

| Ticket     | Title                                                         | Points |
| ---------- | ------------------------------------------------------------- | ------ |
| ticket-005 | Rewrite follow_submitted_job without initial sleep            | 3      |
| ticket-006 | Add sacct-based post-completion status capture                | 2      |
| ticket-007 | Capture stderr.modelops and final stdout after job completion | 2      |

## Dependencies

- No dependencies on Epic 01 (Input Validation)
- Epic 03 will later wrap SLURM failures in `SlurmError`, but the monitoring improvements are independently valuable

## Success Criteria

- A SLURM job that finishes in < 2 seconds has its `stdout.modelops` content captured in logs
- After job completion, `sacct` exit code and state are logged
- `stderr.modelops` content is always captured and logged when it exists
- No regression for long-running jobs (hours-long NEWAVE/DECOMP runs)
