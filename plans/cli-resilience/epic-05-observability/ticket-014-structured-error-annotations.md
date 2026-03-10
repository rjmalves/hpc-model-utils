# ticket-014 Add Structured Error Annotations to ModelOps Signaling

## Context

### Background

When a CLI command fails, the `@handle_cli_errors` decorator in `app/error_handler.py` signals ModelOps via `set_model_error()` or `set_data_error()`, but these are binary signals with no diagnostic context. The operator sees "model error" or "data error" in ModelOps with no indication of what went wrong, which command failed, or what resource was affected. The `ModelOpsCommands.set_annotation(content)` method exists in `app/utils/commands.py` (line 28) but is never called from the error handler. This ticket adds a `set_annotation()` call alongside every error signal so that ModelOps receives a human-readable error summary.

### Relation to Epic

This is the first ticket of Epic 05 (Observability). It enhances the error reporting pipeline established in Epic 03 (ticket-008 error hierarchy, ticket-009 error handler decorator, ticket-010 CLI integration) by adding structured annotations to the existing error signals. The annotation is injected at the same point where `set_model_error()`/`set_data_error()` are already called, requiring changes only to `app/error_handler.py`.

### Current State

- `app/error_handler.py` (~102 lines): `_handle_error()` calls `signal_fn()`, `logger.error(str(error))`, and `sys.exit(error.exit_code)`. The bare-`Exception` branch calls `ModelOpsCommands.set_model_error()`, `logger.exception(str(wrapped))`, and `sys.exit(EXIT_UNKNOWN_ERROR)`. Neither branch calls `set_annotation()`.
- `app/errors.py` (~133 lines): `CLIError.__str__()` returns `[Category] command: message`. Subclasses carry `command_name`, `detail`, and `exit_code` fields. `SlurmError` also has `completion_info: JobCompletionInfo | None`.
- `app/utils/commands.py` (line 28-29): `set_annotation(content: str)` wraps its argument in `${CurrentExecution.SetAnnotation("...")}` and prints it via `_send_command()`.
- `tests/unit/test_error_handler.py` (~599 lines, 9 test classes): Comprehensive tests for all exception branches using `_make_raising_command()` helper and `patch("app.error_handler.ModelOpsCommands")`.

## Specification

### Requirements

1. Add a `_build_annotation(error: CLIError) -> str` helper function in `app/error_handler.py` that produces a plain-text annotation string with the format: `[ErrorCategory] command_name: message` (i.e., the same as `str(error)`), truncated to 500 characters if longer.
2. In `_handle_error()`, call `ModelOpsCommands.set_annotation(_build_annotation(error))` immediately after `signal_fn()` and before `logger.error()`.
3. For `SlurmError` with `completion_info`, append sacct fields to the annotation: `" | job_id=X state=Y exit_code=Z"`.
4. In the bare-`Exception` branch, call `ModelOpsCommands.set_annotation(_build_annotation(wrapped))` after `set_model_error()` and before `logger.exception()`.

### Inputs/Props

- `error: CLIError` (or wrapped `CLIError` for bare exceptions) -- the structured error instance already available in each handler branch.

### Outputs/Behavior

- `ModelOpsCommands.set_annotation()` is called exactly once per error path, with a plain-text string not exceeding 500 characters.
- The annotation content matches `str(error)` for all `CLIError` subclasses (e.g., `[ValidationError] run: model 'foo' not found`).
- For `SlurmError` with `completion_info`, the annotation appends sacct fields after a `|` separator.
- No annotation is sent for successful command completions (out of scope for this ticket).
- No annotation is sent when Click exceptions propagate (they are re-raised before any signal logic).

### Error Handling

- If `set_annotation()` raises (e.g., I/O failure on `print()`), the exception must not mask the original error. Wrap the `set_annotation()` call in a `try/except Exception` that silently passes -- the error signal and exit code are more important than the annotation.
- Truncation to 500 characters uses `annotation[:500]` to avoid issues with ModelOps character limits.

## Acceptance Criteria

- [ ] Given a `ValidationError("bad input")` raised in a decorated command, when the error handler runs, then `ModelOpsCommands.set_annotation` is called once with a string containing `[ValidationError]` and `bad input`
- [ ] Given an `S3Error("bucket missing")` raised in a decorated command, when the error handler runs, then `ModelOpsCommands.set_annotation` is called once with a string containing `[S3Error]` and `bucket missing`
- [ ] Given a `SlurmError("job failed", completion_info=info)` with `info.job_id="42"` and `info.state="FAILED"`, when the error handler runs, then the annotation string contains `job_id=42` and `state=FAILED`
- [ ] Given a `SlurmError("job failed")` without `completion_info`, when the error handler runs, then the annotation string does not contain `job_id=` or `state=`
- [ ] Given a bare `RuntimeError("unexpected")` raised in a decorated command, when the error handler runs, then `ModelOpsCommands.set_annotation` is called once with a string containing `[CLIError]` and `unexpected`

## Implementation Guide

### Suggested Approach

1. Add `_build_annotation(error: CLIError) -> str` at module level in `app/error_handler.py`, below `_handle_error()`. It calls `str(error)` and truncates to 500 characters.
2. Add `_build_slurm_annotation(error: SlurmError) -> str` that calls `_build_annotation(error)` and, if `error.completion_info is not None`, appends ` | job_id={info.job_id} state={info.state} exit_code={info.exit_code}`, then truncates the combined result to 500 characters.
3. In `_handle_error()`, after `signal_fn()` (line 99), insert:
   ```python
   try:
       ModelOpsCommands.set_annotation(_build_annotation(error))
   except Exception:
       pass
   ```
4. In the `SlurmError` branch of `wrapper()` (after `_handle_error(e, ...)` on line 59), replace the annotation call: instead of using `_handle_error` for annotation, handle annotation specially. The cleanest approach: add an optional `annotation_override` parameter to `_handle_error()`, or call `set_annotation` directly in the `SlurmError` branch after `_handle_error` but before the `completion_info` logging. Since `_handle_error` calls `sys.exit()`, the override approach is better: add the annotation call inside `_handle_error` and pass the annotation string as a parameter.
   - Alternative: Add a `build_annotation_fn` callback to `_handle_error`. Simplest: just build the annotation inside `_handle_error` using `str(error)`, and handle the `SlurmError` enrichment by overriding `SlurmError.__str__` or by adding annotation logic to the `SlurmError` branch before calling `_handle_error`.
   - Recommended: Keep `_handle_error` simple. Add the `_build_annotation` call inside `_handle_error` for the common case. For `SlurmError`, override the annotation in the `except SlurmError` branch by calling `set_annotation` with the enriched string before calling `_handle_error` (and have `_handle_error` skip annotation if one was already sent). Simplest implementation: just call `set_annotation` inside `_handle_error` unconditionally, and for `SlurmError` with `completion_info`, call it again with the enriched annotation in the `except SlurmError` branch (ModelOps will use the last value). Actually, the simplest approach: add annotation to `_handle_error` with an optional `annotation` parameter that defaults to `None` (meaning use `str(error)`). The `SlurmError` branch passes the enriched annotation.
5. In the bare-`Exception` branch (line 76-83), insert annotation after `set_model_error()`:
   ```python
   try:
       ModelOpsCommands.set_annotation(_build_annotation(wrapped))
   except Exception:
       pass
   ```

### Key Files to Modify

- `app/error_handler.py` -- add `_build_annotation()`, modify `_handle_error()`, modify bare-`Exception` branch
- `tests/unit/test_error_handler.py` -- add tests for annotation calls in each error branch

### Patterns to Follow

- Follow the existing `_handle_error()` helper pattern: keep annotation logic in a private helper, not inline.
- Follow the `try/except Exception: pass` pattern from `get_job_completion_info()` in `app/utils/scheduler.py` for best-effort operations.
- Follow the existing test pattern: `patch("app.error_handler.ModelOpsCommands") as mock_ops` then assert `mock_ops.set_annotation.assert_called_once_with(...)`.
- Use `_make_raising_command(exc)` helper for test commands, same as all existing test classes.
- Test method naming: `test_<condition>_<outcome>` (e.g., `test_validation_error_sends_annotation_with_category`).

### Pitfalls to Avoid

- Do not change the exception ordering in the `wrapper()` function -- Click exceptions must be re-raised first.
- Do not call `set_annotation()` after `sys.exit()` -- it will never execute. Annotation must precede exit.
- Do not modify `CLIError.__str__()` or any error class -- annotation formatting is the error handler's responsibility.
- Do not send annotations for successful commands -- that is out of scope.
- Do not add `set_annotation` to individual command functions in `app/cli.py` -- all annotation logic belongs in `app/error_handler.py`.

## Testing Requirements

### Unit Tests

Add a new test class `TestAnnotationSending` in `tests/unit/test_error_handler.py` with the following tests:

1. `test_validation_error_sends_annotation_with_category_and_message` -- assert `mock_ops.set_annotation` called once, arg contains `[ValidationError]` and the error message.
2. `test_s3_error_sends_annotation_with_category` -- assert annotation contains `[S3Error]`.
3. `test_slurm_error_without_completion_info_sends_basic_annotation` -- assert annotation contains `[SlurmError]`, does not contain `job_id=`.
4. `test_slurm_error_with_completion_info_sends_enriched_annotation` -- assert annotation contains `job_id=42` and `state=FAILED`.
5. `test_unexpected_exception_sends_annotation` -- assert annotation contains `[CLIError]`.
6. `test_annotation_failure_does_not_mask_original_error` -- configure `mock_ops.set_annotation.side_effect = OSError("io fail")`, assert `sys.exit` is still called with the correct exit code.
7. `test_click_exception_does_not_send_annotation` -- raise `click.BadParameter`, assert `mock_ops.set_annotation` not called.

### Integration Tests

No new integration tests needed. The annotation call is verified at the unit level through mock assertions on `ModelOpsCommands`.

### E2E Tests

Not applicable.

## Dependencies

- **Blocked By**: ticket-010-replace-try-except-in-cli.md (completed -- error handler decorator exists)
- **Blocks**: None

## Effort Estimate

**Points**: 2
**Confidence**: High
