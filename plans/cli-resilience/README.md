# CLI Resilience and Error Handling

Improve error handling and resilience in the `hpc-model-utils` CLI application by adding upfront input validation, fixing SLURM job monitoring for fast-finishing jobs, and introducing structured error categorization with proper exit codes.

## Tech Stack

- Python >= 3.10
- Click 8.x (CLI framework)
- SLURM (HPC job scheduler)
- boto3 (AWS S3)

## Epics

| Epic | Name              | Tickets | Detail Level | Phase     |
| ---- | ----------------- | ------- | ------------ | --------- |
| 01   | Input Validation  | 4       | Detailed     | Executing |
| 02   | SLURM Monitoring  | 3       | Detailed     | Executing |
| 03   | Structured Errors | 3       | Outline      | Outline   |
| 04   | Test Coverage     | 3       | Outline      | Outline   |
| 05   | Observability     | 2       | Outline      | Outline   |

## Progress Tracking

| Ticket     | Title                                                         | Epic    | Status    | Detail Level | Readiness | Quality | Badge      |
| ---------- | ------------------------------------------------------------- | ------- | --------- | ------------ | --------- | ------- | ---------- |
| ticket-001 | Create validation primitives module                           | epic-01 | completed | Detailed     | 0.97      | 0.83    | ACCEPTABLE |
| ticket-002 | Implement per-command validator functions                     | epic-01 | completed | Detailed     | 0.97      | 0.80    | ACCEPTABLE |
| ticket-003 | Integrate validators into CLI commands                        | epic-01 | completed | Detailed     | 0.97      | 0.83    | ACCEPTABLE |
| ticket-004 | Add Click parameter types for semantic validation             | epic-01 | completed | Detailed     | 0.97      | 0.78    | ACCEPTABLE |
| ticket-005 | Rewrite follow_submitted_job without initial sleep            | epic-02 | pending   | Detailed     | 0.96      | --      | --         |
| ticket-006 | Add sacct-based post-completion status capture                | epic-02 | pending   | Detailed     | 0.99      | --      | --         |
| ticket-007 | Capture stderr.modelops and final stdout after job completion | epic-02 | pending   | Detailed     | 0.97      | --      | --         |
| ticket-008 | Define error hierarchy and exit code mapping                  | epic-03 | pending   | Outline      | --        | --      | --         |
| ticket-009 | Create centralized CLI error handler decorator                | epic-03 | pending   | Outline      | --        | --      | --         |
| ticket-010 | Replace try/except Exception in all CLI commands              | epic-03 | pending   | Outline      | --        | --      | --         |
| ticket-011 | Add unit tests for validation primitives and validators       | epic-04 | pending   | Outline      | --        | --      | --         |
| ticket-012 | Add unit tests for SLURM monitoring functions                 | epic-04 | pending   | Outline      | --        | --      | --         |
| ticket-013 | Add CLI integration tests for error handling and exit codes   | epic-04 | pending   | Outline      | --        | --      | --         |
| ticket-014 | Add structured error annotations to ModelOps signaling        | epic-05 | pending   | Outline      | --        | --      | --         |
| ticket-015 | Add command timing and diagnostic output                      | epic-05 | pending   | Outline      | --        | --      | --         |
