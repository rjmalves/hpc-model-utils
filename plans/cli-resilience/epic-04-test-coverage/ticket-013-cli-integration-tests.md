# ticket-013 Add CLI Integration Tests for Error Handling and Exit Codes

> **[OUTLINE]** This ticket requires refinement before execution.
> It will be refined with learnings from earlier epics.

## Objective

Create integration tests using Click's `CliRunner` that invoke CLI commands with various invalid and valid inputs and verify correct exit codes, error messages, and ModelOps signal behavior. Tests cover the full path from CLI invocation through validation, error handling, and exit code propagation.

## Anticipated Scope

- **Files likely to be modified**: `tests/test_cli.py` (new or extend existing)
- **Key decisions needed**:
  - How to mock `ModelFactory` and model methods for CLI-level tests
  - Whether to test ModelOps command output by capturing stdout or by mocking `_send_command`
  - Which error scenarios to prioritize (validation failures are simplest; SLURM/S3 failures require more mocking)
- **Open questions**:
  - What exit codes were finalized in Epic 03?
  - Does the error handler decorator (ticket-009) interact with Click's own error handling in ways that affect `CliRunner` behavior?
  - How does `CliRunner` handle `sys.exit()` calls?

## Dependencies

- **Blocked By**: ticket-010-replace-try-except-in-cli.md (Epic 03 must be complete)
- **Blocks**: None

## Effort Estimate

**Points**: 3
**Confidence**: Low (will be re-estimated during refinement)
