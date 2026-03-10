# ticket-015 Add Command Timing and Diagnostic Output

> **[OUTLINE]** This ticket requires refinement before execution.
> It will be refined with learnings from earlier epics.

## Objective

Add timing instrumentation to each CLI command so that the duration of each phase (validation, execution, upload) is logged and optionally reported to ModelOps. The existing `app/utils/timing.py` provides a `time_and_log` context manager that is already used in some model methods — this ticket extends its use to the CLI command level and adds an optional `--verbose` flag for detailed diagnostic output.

## Anticipated Scope

- **Files likely to be modified**: `app/cli.py` (add timing and --verbose flag), `app/utils/timing.py` (may extend for structured output)
- **Key decisions needed**:
  - Whether `--verbose` should be a global Click option or per-command
  - Whether timing data should be sent to ModelOps via `SetMetadata` or `SetAnnotation`
  - Whether to add timing to the error handler decorator (ticket-009) or independently per command
- **Open questions**:
  - What is the current `time_and_log` interface from `app/utils/timing.py`? Does it need extension?
  - Should timing data be written to a file (e.g., `timing.modelops`) for post-hoc analysis?
  - Does the `--verbose` flag conflict with any existing Click options?

## Dependencies

- **Blocked By**: ticket-010-replace-try-except-in-cli.md (error handler decorator provides the natural place for timing)
- **Blocks**: None

## Effort Estimate

**Points**: 2
**Confidence**: Low (will be re-estimated during refinement)
