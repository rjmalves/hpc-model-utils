# ticket-012 Add Unit Tests for SLURM Monitoring Functions

> **[OUTLINE]** This ticket requires refinement before execution.
> It will be refined with learnings from earlier epics.

## Objective

Create unit tests for the rewritten SLURM monitoring functions: `follow_submitted_job()`, `get_job_completion_info()`, and `read_job_output_files()`. Tests must cover: fast-finishing jobs (<2s), normal multi-iteration monitoring, timeout behavior, sacct parsing (success/failure/unparseable), and output file reading (present/absent/large/encoding issues).

## Anticipated Scope

- **Files likely to be modified**: `tests/test_scheduler.py` (new)
- **Key decisions needed**:
  - Mocking strategy for `run_in_terminal` — whether to use `unittest.mock.patch` or a custom fixture
  - How to simulate file existence/content for `read_job_output_files` tests — `tmp_path` fixture vs mocking `os.path.exists`
- **Open questions**:
  - What is the exact interface of the rewritten `follow_submitted_job` after Epic 02? (Function signature, return type, logger parameter)
  - What sacct output formats were encountered during Epic 02 implementation?

## Dependencies

- **Blocked By**: ticket-007-capture-stderr-and-final-stdout.md (Epic 02 must be complete)
- **Blocks**: None

## Effort Estimate

**Points**: 3
**Confidence**: Low (will be re-estimated during refinement)
