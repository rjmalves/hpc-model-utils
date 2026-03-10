# ticket-010 Replace try/except Exception in All CLI Commands

## Context

### Background

With the error hierarchy (ticket-008) and the `@handle_cli_errors` decorator (ticket-009) in place, this ticket applies them to all 12 CLI commands in `app/cli.py`. Each command currently has its own `try/except Exception` block that calls `ModelOpsCommands.set_model_error()` and `logger.exception()`. After this ticket, every command uses the decorator instead, and the `try/except Exception` blocks are removed entirely.

Additionally, this ticket completes the CLI validation wiring that was left incomplete in Epic 01 — calling per-command validators from `app/validation.py` and using Click types from `app/click_types.py` in argument declarations. The learnings from Epics 01 and 02 explicitly recommend completing this wiring before (or during) the error handler refactor to avoid a second pass over `app/cli.py`.

### Relation to Epic

This is the final ticket in Epic 03 and the one that delivers the user-visible behavior change: CLI commands now exit with distinct codes, produce categorized error messages, and send appropriate ModelOps signals per error type.

### Current State

- `app/cli.py` has 12 commands (listed below), each with the identical `try/except Exception: set_model_error(); logger.exception(str(e))` pattern.
- `app/cli.py` does NOT call any validators from `app/validation.py` and does NOT use `ModelNameType`/`S3PathType`/`PositiveIntType` in argument declarations (Epic 01 wiring was not completed — see learnings).
- `app/error_handler.py` exists (after ticket-009) with the `@handle_cli_errors(command_name)` decorator.
- `app/errors.py` exists (after ticket-008) with all error classes and exit code constants.
- `app/validation.py` has 12 per-command validator functions (e.g., `validate_check_and_fetch_inputs`, `validate_run`, etc.).
- `app/click_types.py` has `ModelNameType`, `S3PathType`, `PositiveIntType`.
- `fetch_extract_raw_outputs` is the only command that does NOT use `ModelFactory` — it directly calls `path_to_bucket_and_key()` and S3 utility functions.

## Specification

### Requirements

1. Apply `@handle_cli_errors("command_name")` decorator to all 12 command functions. The decorator goes BELOW the `@click.command` and `@click.argument`/`@click.option` decorators (so Click processes the function first, then the error handler wraps the Click-resolved function body).
2. Remove the `try/except Exception` block from every command. The command body becomes the "happy path" only — no exception handling inside the function.
3. Add per-command validator calls at the top of each command body, AFTER `Log.configure_logger()` and BEFORE any business logic. Use the validator functions from `app/validation.py`. Validation calls go inside the decorator's scope (so `click.BadParameter` from validators propagates through Click, not through the decorator).
4. Update Click argument/option type declarations to use `ModelNameType()`, `S3PathType()`, and `PositiveIntType()` where applicable. Exception: `parent_path` in `check_and_fetch_inputs` stays as `type=str` (Epic 01 decision — empty string default is incompatible with `S3PathType`).
5. Wrap `ModelFactory().factory()` `ValueError` as `ValidationError` at the call site OR let the decorator's `Exception` handler catch it as an unexpected error. Decision: wrap it as `ValidationError` since an unknown model name is a validation failure, and this provides a better error message and exit code 2 instead of 99.

### The 12 Commands to Modify

| Command                          | Validator Function                        | Click Types                                                    |
| -------------------------------- | ----------------------------------------- | -------------------------------------------------------------- |
| `check_and_fetch_inputs`         | `validate_check_and_fetch_inputs`         | `model_name: ModelNameType()`, `path: S3PathType()`            |
| `check_and_fetch_executables`    | `validate_check_and_fetch_executables`    | `model_name: ModelNameType()`, `path: S3PathType()`            |
| `extract_sanitize_inputs`        | `validate_extract_sanitize_inputs`        | `model_name: ModelNameType()`                                  |
| `preprocess`                     | `validate_preprocess`                     | `model_name: ModelNameType()`                                  |
| `run`                            | `validate_run`                            | `model_name: ModelNameType()`, `core_count: PositiveIntType()` |
| `generate_execution_status`      | `validate_generate_execution_status`      | `model_name: ModelNameType()`                                  |
| `postprocess`                    | `validate_postprocess`                    | `model_name: ModelNameType()`                                  |
| `output_compression_and_cleanup` | `validate_output_compression_and_cleanup` | `model_name: ModelNameType()`, `num_cpus: PositiveIntType()`   |
| `result_upload`                  | `validate_result_upload`                  | `model_name: ModelNameType()`, `path: S3PathType()`            |
| `cancel_run`                     | `validate_cancel_run`                     | `model_name: ModelNameType()`                                  |
| `download_executed_run`          | `validate_download_executed_run`          | `model_name: ModelNameType()`, `artifacts_path: S3PathType()`  |
| `fetch_extract_raw_outputs`      | `validate_fetch_extract_raw_outputs`      | `outputs_path: S3PathType()`                                   |

### Inputs/Props

No new inputs. The commands keep their existing Click arguments and options.

### Outputs/Behavior

- On validation failure (Click types or per-command validators): `click.BadParameter` propagates to Click, which prints the error and exits with code 2. No ModelOps signal is sent (this is correct — validation failures happen before any model state is created).
- On model execution failure: `ModelExecutionError` is raised (or `Exception` is caught by decorator), `set_model_error()` is called, exit code 1 or 99.
- On S3 failure in `fetch_extract_raw_outputs` or model S3 methods: `Exception` is caught by decorator as unexpected (exit code 99). Wrapping boto3 errors as `S3Error` inside model methods is out of scope for this ticket.

### Error Handling

Error handling is delegated entirely to the `@handle_cli_errors` decorator. The command body contains no `try/except` blocks.

## Acceptance Criteria

- [ ] Given `app/cli.py` is modified, when `grep -c "except Exception" app/cli.py` is run, then the count is `0` (no bare `except Exception` blocks remain)
- [ ] Given `app/cli.py` is modified, when `grep -c "handle_cli_errors" app/cli.py` is run, then the count is `12` (every command has the decorator)
- [ ] Given `app/cli.py` is modified, when `grep -c "ModelNameType" app/cli.py` is run, then the count is at least `11` (all commands with `model_name` use the Click type, except none — all 11 commands with `model_name` use it)
- [ ] Given `app/cli.py` is modified, when `grep -c "validate_" app/cli.py` is run, then the count is at least `12` (every command calls its per-command validator)
- [ ] Given the `run` command is invoked with an invalid model name, when Click's `ModelNameType` rejects it, then Click prints an error message containing "MODEL_NAME" and exits with code 2 without calling `set_model_error()`

## Implementation Guide

### Suggested Approach

1. Add imports at the top of `app/cli.py`:
   - `from app.error_handler import handle_cli_errors`
   - `from app.click_types import ModelNameType, S3PathType, PositiveIntType`
   - `from app.validation import validate_check_and_fetch_inputs, validate_check_and_fetch_executables, ...` (all 12 validator functions)
   - `from app.errors import ValidationError`
2. For each command, apply the transformation in this order:
   a. Add `@handle_cli_errors("command_name")` decorator below all Click decorators.
   b. Change `type=str` to `type=ModelNameType()` for `model_name` arguments. Change `type=str` to `type=S3PathType()` for S3 path arguments. Change `type=int` to `type=PositiveIntType()` for `core_count` and `num_cpus`.
   c. Add the validator call after `logger = Log.configure_logger()`.
   d. Remove the `try:` line, the `except Exception as e:` line, and the two lines inside the except block (`ModelOpsCommands.set_model_error()` and `logger.exception(str(e))`). Dedent the remaining body.
   e. Wrap the `ModelFactory().factory()` call: `try: model_type = ModelFactory().factory(model_name, logger) except ValueError as exc: raise ValidationError(str(exc), command_name="command_name") from exc`.
3. For `fetch_extract_raw_outputs` (no ModelFactory): just add the decorator, Click type, validator call, and remove the try/except. No ModelFactory wrapping needed.
4. Remove the `from app.utils.commands import ModelOpsCommands` import from `app/cli.py` if it is no longer used directly (all ModelOps calls are now in the decorator). Keep it if any command still calls `set_success()` or other ModelOps methods explicitly.

### Key Files to Modify

- `app/cli.py` (modify all 12 command functions, ~300 lines changed)

### Patterns to Follow

- Decorator ordering: `@click.command` -> `@click.argument`/`@click.option` -> `@handle_cli_errors` (outermost to innermost). This means Click processes arguments first, then the error handler wraps the resolved function.
- Validation call placement: immediately after `Log.configure_logger()`, before any business logic. This matches the constraint from Epic 01 learnings.
- `ModelFactory` ValueError wrapping: use `raise ValidationError(...) from exc` to preserve the exception chain.

### Pitfalls to Avoid

- Do NOT move `Log.configure_logger()` inside the decorator. Each command must configure logging before the decorator's try/except scope, so the logger is available for the handler.
- Do NOT add `try/except` for S3 operations inside the command body. Wrapping boto3 errors as `S3Error` inside individual model methods is out of scope — the decorator's `Exception` fallback (exit code 99) handles these for now.
- Do NOT modify `app/validation.py` or `app/errors.py` — those are owned by ticket-001/002 and ticket-008 respectively.
- Do NOT change the `parent_path` argument type to `S3PathType()` in `check_and_fetch_inputs` — it must remain `type=str` because the empty string default is a valid "no parent" value.

## Testing Requirements

### Unit Tests

No new unit test file. The decorator is tested in ticket-009's tests. The validators are tested in `tests/unit/test_validation_per_command.py` (Epic 01).

### Integration Tests

None in this ticket. CLI integration tests are ticket-013's scope (Epic 04).

### E2E Tests

None.

## Dependencies

- **Blocked By**: ticket-009-create-cli-error-handler.md
- **Blocks**: ticket-013-cli-integration-tests.md (Epic 04)

## Effort Estimate

**Points**: 3
**Confidence**: High
