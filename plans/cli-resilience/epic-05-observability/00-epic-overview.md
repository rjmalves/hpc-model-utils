# Epic 05: Observability Improvements

## Goal

Improve the logging and error reporting so that when a CLI command fails, the operator and ModelOps have enough structured information to diagnose the issue without re-running the command. Currently, errors are logged as unstructured exception messages with no categorization, and ModelOps receives only a binary "model error" signal.

## Scope

- Structured log entries with error category, command name, timing, and affected resource
- Error annotations sent to ModelOps via `SetAnnotation` with structured error summaries
- Optional `--verbose` flag for detailed diagnostic output
- Timing information for each CLI command phase (validation, execution, upload)

## Out of Scope

- External monitoring/alerting systems
- Log aggregation infrastructure
- Changing the log file format (stays as rotating file handler)

## Tickets

| Ticket     | Title                                                  | Points |
| ---------- | ------------------------------------------------------ | ------ |
| ticket-014 | Add structured error annotations to ModelOps signaling | 2      |
| ticket-015 | Add command timing and diagnostic output               | 2      |

## Dependencies

- Depends on Epic 03 (structured error types to report)
- Benefits from Epic 01 (validation errors to annotate) and Epic 02 (SLURM timing info)

## Success Criteria

- Every error sent to ModelOps includes a human-readable annotation with error category and affected resource
- Command timing is logged for each phase (validation, execution, upload)
