# Master Plan: CLI Resilience and Error Handling

## Executive Summary

The `hpc-model-utils` CLI application suffers from three categories of fragility: deferred input validation that causes late failures in HPC workflows, a brittle SLURM job monitoring strategy that misses output from fast-finishing jobs, and silent error swallowing that makes diagnosing failures impossible. This plan introduces an upfront validation layer, a robust SLURM monitoring approach, structured error categorization with proper exit codes, comprehensive test coverage, and improved observability for the ModelOps integration.

## Goals & Non-Goals

### Goals

- **Fail fast**: Validate all command inputs before any work (S3 calls, file I/O, SLURM submission) begins
- **Never miss SLURM output**: Redesign job monitoring so that even sub-5-second jobs produce capturable stdout
- **Categorized errors**: Replace broad `except Exception` with a typed error hierarchy that maps to distinct exit codes and ModelOps signals
- **Backward compatibility**: Preserve all existing CLI command names, arguments, and the `${...}` ModelOps command protocol
- **Testability**: Make the new validation and monitoring logic independently testable without SLURM or S3

### Non-Goals

- Changing the CLI framework (Click stays)
- Adding new CLI commands (existing 13 commands only)
- Modifying ModelOps protocol semantics (only adding structured error info within the existing protocol)
- Refactoring domain model logic (newave/decomp/dessem business rules stay as-is)
- Migrating to async I/O

## Architecture Overview

### Current State

```
CLI Command (click) --> try/except Exception --> model.method() --> [late validation, deep failures]
                        catch: ModelOpsCommands.set_model_error() + logger.exception()
                        no re-raise, exit code always 0
```

SLURM monitoring: `sleep 5` -> poll `squeue` -> `tail stdout.modelops` -> `sleep 5` loop. Jobs finishing in <5s are invisible.

### Target State

```
CLI Command (click) --> InputValidator.validate(ctx) --> model.method()
                        |                                   |
                        | ValidationError(details)          | CLIError subclasses
                        v                                   v
                   structured error handler:
                   - categorize error (validation/slurm/s3/model/unknown)
                   - log structured info
                   - signal ModelOps (set_model_error / set_data_error)
                   - sys.exit(specific code)
```

SLURM monitoring: `sbatch` captures job_id -> immediately start watching `stdout.modelops` via inotify-style polling (no initial sleep) -> use `sacct` as post-completion fallback -> always capture final state even for instant jobs.

### Key Design Decisions

1. **Validation as a separate layer**: A `validate_inputs()` function per command that runs before `ModelFactory().factory()`. This keeps validation fast-fail and testable without model instances.
2. **Error hierarchy via dataclasses**: A `CLIError` base with subclasses `ValidationError`, `SlurmError`, `S3Error`, `ModelExecutionError`. Each carries structured context (command name, field name, message, suggestion).
3. **Exit code mapping**: Validation errors -> exit 2, SLURM errors -> exit 3, S3 errors -> exit 4, model errors -> exit 1, unknown -> exit 99.
4. **SLURM monitoring redesign**: Remove initial `sleep 5`. Start file watching immediately after `sbatch`. Use `sacct -j {job_id} --format=State,ExitCode` after job disappears from `squeue` to capture final status regardless of timing.
5. **Backward compatibility**: All changes are additive. No command signatures change. New optional `--verbose` flag for structured error output.

## Technical Approach

### Tech Stack

- Python >= 3.10 (dataclasses, match statements, `|` union types)
- Click 8.x (existing)
- No new dependencies required

### Component/Module Breakdown

| Module                              | Purpose                                                                                        |
| ----------------------------------- | ---------------------------------------------------------------------------------------------- |
| `app/errors.py` (new)               | Error hierarchy: `CLIError`, `ValidationError`, `SlurmError`, `S3Error`, `ModelExecutionError` |
| `app/validation.py` (new)           | Per-command input validators, reusable validation primitives                                   |
| `app/utils/scheduler.py` (modified) | Rewritten `follow_submitted_job` with immediate watching and `sacct` fallback                  |
| `app/cli.py` (modified)             | Validation calls before model dispatch, structured error handler wrapping each command         |
| `app/utils/commands.py` (modified)  | Optional structured error reporting via `SetAnnotation`                                        |

### Data Flow

```
User invokes CLI command
  -> Click parses args (type-level)
  -> validate_inputs(command_name, args) raises ValidationError or passes
  -> ModelFactory().factory(model_name, logger) raises ValueError if unknown model
  -> model.method(args) executes business logic
  -> On success: normal exit (code 0)
  -> On any CLIError subclass: structured log + ModelOps signal + sys.exit(code)
  -> On unexpected Exception: wrap in CLIError(unknown) + same handling
```

### Testing Strategy

- **Unit tests**: Validators (pure functions), error hierarchy (construction, exit code mapping), SLURM output parsing
- **Integration tests**: CLI invocation with invalid inputs (Click test runner), mock SLURM monitoring scenarios
- **No E2E**: Actual SLURM/S3 integration is tested in existing integration test suite

## Phases & Milestones

| Phase | Epic                       | Description                                                         | Tickets            |
| ----- | -------------------------- | ------------------------------------------------------------------- | ------------------ |
| 1     | Epic 01: Input Validation  | Validation primitives, per-command validators, CLI integration      | 4 detailed tickets |
| 2     | Epic 02: SLURM Monitoring  | Rewrite follow_submitted_job, add sacct fallback, capture fast jobs | 3 detailed tickets |
| 3     | Epic 03: Structured Errors | Error hierarchy, CLI error handler, exit codes, ModelOps signaling  | 3 outline tickets  |
| 4     | Epic 04: Test Coverage     | Unit + integration tests for validation, monitoring, error handling | 3 outline tickets  |
| 5     | Epic 05: Observability     | Structured logging, error annotations to ModelOps, diagnostics      | 2 outline tickets  |

## Risk Analysis

| Risk                                                       | Likelihood | Impact | Mitigation                                                                                                    |
| ---------------------------------------------------------- | ---------- | ------ | ------------------------------------------------------------------------------------------------------------- |
| Validation too strict (rejects valid inputs in production) | Medium     | High   | Conservative validators with clear error messages; test with real deck paths                                  |
| SLURM `sacct` not available on all clusters                | Low        | Medium | Graceful fallback to current behavior if `sacct` fails                                                        |
| ModelOps expects exit code 0 always                        | Medium     | High   | Verify ModelOps behavior with non-zero exit codes before deploying; make exit codes opt-in via flag if needed |
| Breaking `${...}` protocol by printing extra output        | Low        | High   | Error output goes to stderr, not stdout; ModelOps commands stay on stdout                                     |

## Success Metrics

- Every CLI command validates all inputs before any side-effect (S3 call, file write, SLURM submission)
- SLURM jobs finishing in <5s have their stdout captured in logs
- Every error produces a structured log entry with: error category, command name, affected field/resource, and actionable message
- Exit codes distinguish error categories (0, 1, 2, 3, 4, 99)
- Zero backward-incompatible changes to command signatures or ModelOps protocol
