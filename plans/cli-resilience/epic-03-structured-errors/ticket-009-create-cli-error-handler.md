# ticket-009 Create Centralized CLI Error Handler Decorator

## Context

### Background

Every CLI command in `app/cli.py` follows an identical error handling pattern: `try: ... except Exception as e: ModelOpsCommands.set_model_error(); logger.exception(str(e))`. This pattern has three problems: (1) all errors are treated as model errors regardless of category, (2) the process exits with code 0 even on failure, and (3) `click.BadParameter` from the validation layer (Epic 01) is caught and swallowed by `set_model_error()` instead of producing Click's standard exit-code-2 output. This ticket creates a `@handle_cli_errors` decorator that replaces the `try/except Exception` blocks with structured error handling using the error hierarchy from ticket-008.

### Relation to Epic

This is the second ticket in Epic 03 and the architectural centerpiece. It creates the decorator that ticket-010 will apply to all 12 CLI commands. The decorator maps `CLIError` subclasses to appropriate ModelOps signals and exit codes.

### Current State

- `app/errors.py` exists (after ticket-008) with `CLIError`, `ValidationError`, `SlurmError`, `S3Error`, `ModelExecutionError`, and exit code constants.
- `app/utils/commands.py` provides `ModelOpsCommands` with `set_model_error()`, `set_data_error()`, and `set_success()` static methods.
- `app/utils/log.py` provides `Log.configure_logger()` which returns a `logging.Logger`.
- Each CLI command currently calls `Log.configure_logger()` at the top of its body, then enters a `try/except Exception` block.
- `app/validation.py` raises `click.BadParameter` (not `CLIError` subclasses). The decorator must let `click.BadParameter` propagate to Click's machinery uninterrupted.

## Specification

### Requirements

1. Create a `handle_cli_errors` decorator function in `app/error_handler.py`.
2. The decorator wraps the command function body. It must NOT wrap `Log.configure_logger()` — the logger must be configured before the decorator's try/except runs so that logging is available inside the handler.
3. The decorator must accept a `command_name: str` parameter (the CLI command name for error context).
4. Error handling dispatch logic inside the decorator:
   - `click.BadParameter`, `click.UsageError`, `click.exceptions.Exit`: re-raise immediately. Do NOT catch these. They must propagate to Click's error formatting.
   - `ValidationError`: call `ModelOpsCommands.set_model_error()`, log the error via `logger.error(str(e))`, call `sys.exit(e.exit_code)`.
   - `S3Error`: call `ModelOpsCommands.set_data_error()`, log via `logger.error(str(e))`, call `sys.exit(e.exit_code)`.
   - `SlurmError`: call `ModelOpsCommands.set_model_error()`, log via `logger.error(str(e))`, if `e.completion_info` is not None log the completion info fields, call `sys.exit(e.exit_code)`.
   - `ModelExecutionError`: call `ModelOpsCommands.set_model_error()`, log via `logger.error(str(e))`, call `sys.exit(e.exit_code)`.
   - `CLIError` (base, if raised directly): call `ModelOpsCommands.set_model_error()`, log via `logger.error(str(e))`, call `sys.exit(e.exit_code)`.
   - `Exception` (unexpected): wrap in `CLIError(str(e), command_name=command_name, exit_code=EXIT_UNKNOWN_ERROR)`, call `ModelOpsCommands.set_model_error()`, log via `logger.exception(str(e))` (with traceback), call `sys.exit(EXIT_UNKNOWN_ERROR)`.
5. The decorator must inject `command_name` into any `CLIError` instance that has an empty `command_name` field, so that error messages always identify which command failed.
6. The decorator retrieves the logger by calling `logging.getLogger("hpc-model-utils")` (the same logger name used by `Log.configure_logger()`). It does NOT accept a logger parameter — the logger is always the same singleton.

### Inputs/Props

- `command_name: str` — passed to the decorator factory: `@handle_cli_errors("check_and_fetch_inputs")`.
- The decorated function's arguments are passed through unchanged via `*args, **kwargs`.

### Outputs/Behavior

- On success: the decorator returns the command function's return value (typically `None`). No ModelOps signal is sent (commands that need `set_success()` call it explicitly in their body).
- On `CLIError`: the decorator logs the error, sends the appropriate ModelOps signal, and calls `sys.exit()` with the error's exit code. The function does NOT return.
- On unexpected `Exception`: the decorator logs the full traceback via `logger.exception()`, sends `set_model_error()`, and calls `sys.exit(99)`.

### Error Handling

The decorator IS the error handling infrastructure. It must not raise its own exceptions except by re-raising Click exceptions.

## Acceptance Criteria

- [ ] Given `app/error_handler.py` exists, when `from app.error_handler import handle_cli_errors` is executed, then the import succeeds without error
- [ ] Given a function decorated with `@handle_cli_errors("test_cmd")`, when the function raises `ValidationError("bad input")`, then `ModelOpsCommands.set_model_error()` is called exactly once, `logger.error` is called with a message containing `"[ValidationError] test_cmd: bad input"`, and `sys.exit` is called with `2`
- [ ] Given a function decorated with `@handle_cli_errors("test_cmd")`, when the function raises `S3Error("bucket not found")`, then `ModelOpsCommands.set_data_error()` is called exactly once (not `set_model_error`), and `sys.exit` is called with `4`
- [ ] Given a function decorated with `@handle_cli_errors("test_cmd")`, when the function raises `click.BadParameter("invalid")`, then the exception propagates to the caller without being caught by the decorator
- [ ] Given a function decorated with `@handle_cli_errors("test_cmd")`, when the function raises `RuntimeError("unexpected")`, then `ModelOpsCommands.set_model_error()` is called, `logger.exception` is called (with traceback), and `sys.exit` is called with `99`

## Implementation Guide

### Suggested Approach

1. Create `app/error_handler.py`.
2. Import `logging`, `sys`, `functools`, `click`, all error classes from `app.errors`, and `ModelOpsCommands` from `app.utils.commands`.
3. Define `handle_cli_errors(command_name: str)` as a decorator factory that returns a decorator.
4. Inside the decorator, use `functools.wraps(func)` to preserve the wrapped function's metadata (important for Click's introspection).
5. The wrapper function:
   a. Calls `func(*args, **kwargs)` inside a `try` block.
   b. The `except` chain must be ordered: Click exceptions first (re-raise), then specific `CLIError` subclasses, then `CLIError` base, then `Exception`.
   c. For `S3Error`, call `set_data_error()`; for all other `CLIError` subclasses, call `set_model_error()`.
   d. Before logging, set `e.command_name = command_name` if `e.command_name` is empty.
6. Write unit tests in `tests/unit/test_error_handler.py`.

### Key Files to Modify

- `app/error_handler.py` (new file, ~70 lines)
- `tests/unit/test_error_handler.py` (new file, ~120 lines)

### Patterns to Follow

- Use `functools.wraps` for decorator metadata preservation — this is required because Click inspects function signatures for parameter binding.
- Use `logging.getLogger("hpc-model-utils")` to retrieve the logger, matching the name in `app/utils/log.py` line 16.
- Follow the "best-effort diagnostics" convention from Epic 02: if `SlurmError.completion_info` is present, log it; if absent, do not complain.

### Pitfalls to Avoid

- Do NOT catch `click.BadParameter` or `click.UsageError` — these must propagate to Click. Place the `except (click.BadParameter, click.UsageError, click.exceptions.Exit)` clause BEFORE the `except CLIError` clause and re-raise.
- Do NOT use `logger.exception()` for `CLIError` instances — use `logger.error()`. Reserve `logger.exception()` (which includes traceback) for unexpected `Exception` only. This avoids flooding logs with expected error tracebacks.
- Do NOT call `Log.configure_logger()` inside the decorator. The command function itself must call it before the decorated body executes. The decorator retrieves the already-configured logger via `logging.getLogger()`.
- Do NOT send `set_success()` from the decorator. Success signaling is the command's responsibility, not the error handler's.

## Testing Requirements

### Unit Tests

Test the decorator with mock functions that raise each error type:

- `ValidationError` -> `set_model_error()` called, `sys.exit(2)`
- `S3Error` -> `set_data_error()` called, `sys.exit(4)`
- `SlurmError` (with and without `completion_info`) -> `set_model_error()` called, `sys.exit(3)`
- `ModelExecutionError` -> `set_model_error()` called, `sys.exit(1)`
- `CLIError` (base) -> `set_model_error()` called, `sys.exit()` with provided code
- `RuntimeError` (unexpected) -> `set_model_error()` called, `sys.exit(99)`, `logger.exception` used
- `click.BadParameter` -> exception propagates, no ModelOps signal sent
- `click.UsageError` -> exception propagates, no ModelOps signal sent
- No exception -> function returns normally, no ModelOps signal, no `sys.exit`

Use `unittest.mock.patch` to mock `ModelOpsCommands`, `sys.exit`, and `logging.getLogger`.

### Integration Tests

None. The decorator is tested via unit tests with mock functions.

### E2E Tests

None.

## Dependencies

- **Blocked By**: ticket-008-define-error-hierarchy.md
- **Blocks**: ticket-010-replace-try-except-in-cli.md

## Effort Estimate

**Points**: 2
**Confidence**: High
