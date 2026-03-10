# Epic 03: Structured Error Handling

## Goal

Replace the broad `except Exception` pattern in all CLI commands with a typed error hierarchy that produces categorized errors, distinct exit codes, and structured information for debugging. Currently, every command catches all exceptions identically: log + `set_model_error()` + silent continuation with exit code 0.

## Scope

- Define an error hierarchy: `CLIError` base, `ValidationError`, `SlurmError`, `S3Error`, `ModelExecutionError`
- Create a centralized error handler that maps error types to exit codes and ModelOps signals
- Replace the `try/except Exception` pattern in all 13 commands with the structured handler
- Ensure errors are raised (not swallowed) with proper `sys.exit()` codes

## Out of Scope

- Modifying model business logic error handling (internal to newave/decomp/dessem)
- Adding retry logic for transient failures
- Changing the ModelOps `${...}` protocol format

## Tickets

| Ticket     | Title                                            | Points |
| ---------- | ------------------------------------------------ | ------ |
| ticket-008 | Define error hierarchy and exit code mapping     | 2      |
| ticket-009 | Create centralized CLI error handler decorator   | 3      |
| ticket-010 | Replace try/except Exception in all CLI commands | 3      |

## Dependencies

- Benefits from Epic 01 (validation errors to categorize) and Epic 02 (SLURM errors to categorize)
- Can be implemented independently by catching existing exception types and wrapping them

## Success Criteria

- No CLI command catches bare `Exception` without categorization
- Exit codes distinguish: success (0), model error (1), validation error (2), SLURM error (3), S3 error (4), unknown (99)
- Every error log entry includes: error category, command name, and actionable message
