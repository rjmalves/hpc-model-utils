# ticket-003 Integrate Validators into CLI Commands

## Context

### Background

Tickets 001 and 002 created validation primitives and per-command validators in `app/validation.py`. This ticket wires them into the actual CLI commands in `app/cli.py` so that validation runs before any business logic.

Currently, every CLI command in `app/cli.py` follows this pattern:

```python
@click.command("command_name")
def command_name(args):
    logger = Log.configure_logger()
    try:
        model_type = ModelFactory().factory(model_name, logger)
        model_type.method(args)
    except Exception as e:
        ModelOpsCommands.set_model_error()
        logger.exception(str(e))
```

After this ticket, each command will call its validator before the `try` block.

### Relation to Epic

This is the third ticket in Epic 01. It connects the validation logic (tickets 001-002) to the CLI entry points, making validation active for users.

### Current State

- `app/cli.py` has 12 commands (listed in ticket-002), none calling validators
- `app/validation.py` has all per-command validators ready to use
- The `try/except Exception` blocks remain unchanged (Epic 03 will refactor them)

## Specification

### Requirements

For each of the 12 CLI commands in `app/cli.py`, add a call to the corresponding `validate_<command_name>()` function as the first action inside the command function body, before `Log.configure_logger()` and before the `try` block.

The validation call must happen **outside** the `try/except Exception` block so that `click.BadParameter` exceptions are NOT caught by the broad exception handler. Click will handle `BadParameter` by printing a user-friendly error message and exiting with code 2.

### Outputs/Behavior

- Valid inputs: no change in behavior — command proceeds as before
- Invalid inputs: Click prints error message to stderr and exits with code 2, before any logger setup, ModelFactory calls, or side effects
- `ModelOpsCommands.set_model_error()` is NOT called for validation failures (the error is a caller mistake, not a model error)

### Error Handling

`click.BadParameter` raised by validators propagates to Click's built-in error handling. This produces output like:

```
Error: Invalid value for 'MODEL_NAME': Model "foo" not found. Valid models: decomp, dessem, gevazp, newave
```

## Acceptance Criteria

- [ ] Given the `run` command is invoked via `hpc-model-utils run invalid_model normal 64`, when Click dispatches to the `run` function, then `validate_run` raises `click.BadParameter` before `Log.configure_logger()` is called, and Click exits with code 2
- [ ] Given the `check_and_fetch_inputs` command is invoked with a valid model name but invalid S3 path `"not-s3"`, when Click dispatches to the function, then `validate_check_and_fetch_inputs` raises `click.BadParameter` before `ModelFactory().factory()` is called
- [ ] Given the `run` command is invoked with `hpc-model-utils run newave normal 64`, when Click dispatches to the `run` function, then validation passes and the command proceeds to `Log.configure_logger()` and the `try` block as before
- [ ] Given any CLI command is invoked with invalid inputs, when validation fails, then `ModelOpsCommands.set_model_error()` is NOT called (verified by checking that the `try` block is never entered)

## Implementation Guide

### Suggested Approach

1. Add `from app.validation import (validate_check_and_fetch_inputs, validate_check_and_fetch_executables, ...)` at the top of `app/cli.py`
2. For each command function, add the validation call as the first line, before `logger = Log.configure_logger()`
3. Example transformation for the `run` command:

```python
@click.command("run")
@click.argument("model_name", type=str)
@click.argument("queue", type=str)
@click.argument("core_count", type=int)
# ... options ...
def run(model_name, queue, core_count, max_cores_per_node, max_job_time_hours, mpich_path, slurm_path, skip):
    validate_run(model_name, queue, core_count, max_cores_per_node, max_job_time_hours)  # NEW
    logger = Log.configure_logger()
    try:
        # ... existing logic unchanged ...
```

4. For `fetch_extract_raw_outputs` (no model_name): call `validate_fetch_extract_raw_outputs(outputs_path)`

### Key Files to Modify

- `app/cli.py` (add import and 12 validation calls)

### Patterns to Follow

- Place validation call as the absolute first statement in each command function
- Keep it outside the `try/except` block
- Pass only the arguments that the validator needs (skip flags like `delete`, `skip`, `fetch_inputs`)

### Pitfalls to Avoid

- Do NOT move the validation call inside the `try/except Exception` block — this would swallow the `click.BadParameter` and call `set_model_error()` for a validation failure, which is incorrect behavior
- Do NOT change the existing `try/except` block structure — that refactoring belongs to Epic 03
- Do NOT add `sys.exit()` calls — Click's `BadParameter` handling already exits correctly
- Do NOT validate after `Log.configure_logger()` — validation should be the first thing that happens, before any I/O

## Testing Requirements

### Unit Tests

- Use Click's `CliRunner` to invoke each command with invalid arguments and verify exit code is 2
- Verify that valid arguments pass validation and reach the `try` block (mock `ModelFactory` to avoid needing actual models)

### Integration Tests

Not applicable for this ticket (Click test runner is sufficient).

## Dependencies

- **Blocked By**: ticket-002-implement-per-command-validators.md
- **Blocks**: ticket-004-add-click-parameter-types.md

## Effort Estimate

**Points**: 2
**Confidence**: High
