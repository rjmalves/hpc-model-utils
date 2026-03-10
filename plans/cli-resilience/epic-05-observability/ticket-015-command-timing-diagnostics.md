# ticket-015 Add Command Timing and Diagnostic Output

## Context

### Background

CLI commands currently have no timing instrumentation at the command level. The existing `time_and_log` context manager in `app/utils/timing.py` is used internally by some model methods but is not applied to the CLI command lifecycle. When a command takes unexpectedly long (or fails partway through), operators have no visibility into how long each phase took. This ticket adds a timing decorator that wraps each CLI command, logs the total elapsed time, and sends it to ModelOps as metadata.

### Relation to Epic

This is the second and final ticket of Epic 05 (Observability). While ticket-014 adds error annotations, this ticket adds timing metadata for all commands (both successful and failed). Together they provide operators with structured diagnostic information in ModelOps. The timing decorator sits as the outermost decorator (after `@click.command`) so it captures the full command lifecycle including validation failures and error handler execution.

### Current State

- `app/utils/timing.py` (~33 lines): `time_and_log` class used as a context manager. Takes optional `message_root`, `logger`, and `level`. Uses `time.perf_counter()` for measurement. Logs `"{message_root}: {elapsed:.2f} s"` on `__exit__`.
- `app/cli.py` (~247 lines): 12 commands, each decorated with `@click.command(...)` -> `@click.argument/option` -> `@handle_cli_errors(command_name)` -> function body. No timing decorator exists.
- `app/error_handler.py` (~102 lines + ticket-014 additions): `@handle_cli_errors` catches exceptions, signals ModelOps, logs, and calls `sys.exit()`. When `sys.exit()` is called, it raises `SystemExit`.
- `app/utils/commands.py`: `ModelOpsCommands.set_metadata(key, value)` wraps in `${CurrentExecution.SetMetadata("key", "value")}` and prints.

## Specification

### Requirements

1. Create a `time_command(command_name: str)` decorator factory in `app/utils/timing.py` that:
   - Records the start time using `time.perf_counter()` before calling the wrapped function.
   - On both normal return and `SystemExit` exception, computes elapsed time and logs it via `logging.getLogger("hpc-model-utils")`.
   - Calls `ModelOpsCommands.set_metadata("duration_seconds", f"{elapsed:.2f}")` to send timing to ModelOps.
   - Re-raises `SystemExit` after recording timing (so the error handler's exit code is preserved).
   - Catches `SystemExit` specifically (not bare `Exception`) to avoid interfering with the error handler's exception routing.
2. Apply `@time_command(command_name)` to all 12 CLI commands in `app/cli.py`, positioned between the last `@click.option/argument` and `@handle_cli_errors`:
   ```
   @click.command("run")
   @click.argument(...)
   @click.option(...)
   @time_command("run")
   @handle_cli_errors("run")
   def run(...):
   ```
3. The log message format is: `"Command '{command_name}' completed in {elapsed:.2f}s"` for normal returns, and `"Command '{command_name}' failed after {elapsed:.2f}s"` for `SystemExit`.
4. The `set_metadata` call must be wrapped in `try/except Exception: pass` (best-effort, same as annotation in ticket-014).

### Inputs/Props

- `command_name: str` -- passed to the decorator factory, same name used in `@handle_cli_errors`.

### Outputs/Behavior

- Every CLI command invocation logs a timing line (either "completed" or "failed") at `INFO` level.
- Every CLI command invocation sends a `duration_seconds` metadata entry to ModelOps.
- For error paths: `@handle_cli_errors` calls `sys.exit(N)` which raises `SystemExit`. The timing decorator catches `SystemExit`, records timing, then re-raises. The process exits with the correct code.
- For success paths: the function returns normally, timing is recorded, and no exit code is set.
- The existing `time_and_log` context manager is not modified or removed. The new `time_command` decorator is an independent function in the same module.

### Error Handling

- `SystemExit` raised by `handle_cli_errors` via `sys.exit()` is caught, timing is recorded, then `SystemExit` is re-raised.
- `ModelOpsCommands.set_metadata()` failure is silently ignored (best-effort).
- `logging.getLogger()` failure is not expected but would not be caught -- this is consistent with the error handler's approach.

## Acceptance Criteria

- [ ] Given a CLI command that succeeds, when the command completes, then the logger emits a message matching `"Command 'COMMAND_NAME' completed in X.XXs"` at INFO level
- [ ] Given a CLI command that raises `ValidationError`, when `@handle_cli_errors` calls `sys.exit(2)`, then the timing decorator logs `"Command 'COMMAND_NAME' failed after X.XXs"` and re-raises `SystemExit` with code 2
- [ ] Given a CLI command that succeeds, when the command completes, then `ModelOpsCommands.set_metadata` is called with key `"duration_seconds"` and a value matching the pattern `r"\d+\.\d{2}"`
- [ ] Given a CLI command where `set_metadata` raises `OSError`, when the command completes, then the timing log is still emitted and no exception propagates
- [ ] Given the `run` command in `app/cli.py`, when inspecting its decorator stack, then `@time_command("run")` appears between the Click decorators and `@handle_cli_errors("run")`

## Implementation Guide

### Suggested Approach

1. In `app/utils/timing.py`, add a new `time_command(command_name: str)` function below the existing `time_and_log` class:
   ```python
   def time_command(command_name: str) -> Callable:
       def decorator(func):
           @functools.wraps(func)
           def wrapper(*args, **kwargs):
               logger = logging.getLogger("hpc-model-utils")
               start = time.perf_counter()
               try:
                   result = func(*args, **kwargs)
                   elapsed = time.perf_counter() - start
                   logger.info(f"Command '{command_name}' completed in {elapsed:.2f}s")
                   _send_timing_metadata(elapsed)
                   return result
               except SystemExit:
                   elapsed = time.perf_counter() - start
                   logger.info(f"Command '{command_name}' failed after {elapsed:.2f}s")
                   _send_timing_metadata(elapsed)
                   raise
           return wrapper
       return decorator
   ```
2. Add a `_send_timing_metadata(elapsed: float) -> None` helper:
   ```python
   def _send_timing_metadata(elapsed: float) -> None:
       try:
           ModelOpsCommands.set_metadata("duration_seconds", f"{elapsed:.2f}")
       except Exception:
           pass
   ```
3. Add necessary imports at the top of `app/utils/timing.py`: `import functools`, `import logging`, `from collections.abc import Callable`, `from typing import Any`, and `from app.utils.commands import ModelOpsCommands`.
4. In `app/cli.py`, add `from app.utils.timing import time_command` to the imports.
5. For each of the 12 commands, insert `@time_command("command_name")` between the last `@click.option`/`@click.argument` and `@handle_cli_errors("command_name")`. For `fetch_extract_raw_outputs` which has no model and no `@handle_cli_errors`... actually, checking `app/cli.py`, all 12 commands already have `@handle_cli_errors`. So apply `@time_command` to all 12.

### Key Files to Modify

- `app/utils/timing.py` -- add `time_command()` decorator factory and `_send_timing_metadata()` helper
- `app/cli.py` -- add import and apply `@time_command` decorator to all 12 commands
- `tests/unit/test_timing.py` (new file) -- unit tests for `time_command` decorator

### Patterns to Follow

- Follow the `handle_cli_errors` decorator factory pattern: outer factory takes `command_name`, returns `decorator`, which wraps `func` with `functools.wraps`.
- Follow the `try/except Exception: pass` best-effort pattern from ticket-014 for `set_metadata`.
- Follow existing test patterns: `patch("app.utils.timing.ModelOpsCommands")` for ModelOps mock, `patch("app.utils.timing.logging")` for logger mock.
- Use `_make_raising_command`-style helpers in the test file for decorated functions.
- Test method naming: `test_<condition>_<outcome>`.

### Pitfalls to Avoid

- Do not catch bare `Exception` in the timing decorator -- only catch `SystemExit`. The error handler must see all other exceptions to route them correctly.
- Do not place `@time_command` inside (below) `@handle_cli_errors` -- it must be outside so that it captures error handling time and `sys.exit()` as `SystemExit`.
- Do not modify the existing `time_and_log` class -- it is used by model methods and has a different interface.
- Do not add `--verbose` flag in this ticket -- the epic overview mentions it but it was scoped as "optional" and this ticket focuses on timing only. Adding `--verbose` would require changes to all 12 command signatures and is a separate concern.
- Do not import `ModelOpsCommands` at the top of `timing.py` if it causes circular imports -- test this. If circular, use a lazy import inside `_send_timing_metadata()`.

## Testing Requirements

### Unit Tests

Create `tests/unit/test_timing.py` with the following test classes:

**`TestTimeCommand`**:

1. `test_successful_command_logs_completed_message` -- decorate a no-op function, call it, assert logger `.info()` called with string containing `"completed in"` and the command name.
2. `test_failed_command_logs_failed_message` -- decorate a function that raises `SystemExit(1)`, call it with `pytest.raises(SystemExit)`, assert logger `.info()` called with string containing `"failed after"`.
3. `test_system_exit_is_reraised` -- decorate a function that raises `SystemExit(3)`, call it, assert `SystemExit` propagates with code 3.
4. `test_successful_command_sends_metadata` -- assert `mock_ops.set_metadata` called with `"duration_seconds"` and a string matching `r"\d+\.\d{2}"`.
5. `test_failed_command_sends_metadata` -- same assertion but for `SystemExit` path.
6. `test_metadata_failure_does_not_propagate` -- set `mock_ops.set_metadata.side_effect = OSError`, assert no exception from the decorator on success path.
7. `test_preserves_function_name` -- assert `decorated.__name__` matches original function name.
8. `test_passes_args_and_kwargs` -- assert arguments reach the wrapped function.

**`TestExistingTimeAndLog`** (regression):

1. `test_time_and_log_still_works_as_context_manager` -- ensure the existing class is unmodified and functional.

### Integration Tests

No new integration tests. Timing is verified at unit level through mock assertions.

### E2E Tests

Not applicable.

## Dependencies

- **Blocked By**: ticket-014-structured-error-annotations.md (annotation changes to error_handler.py should land first to avoid merge conflicts)
- **Blocks**: None

## Effort Estimate

**Points**: 2
**Confidence**: High
