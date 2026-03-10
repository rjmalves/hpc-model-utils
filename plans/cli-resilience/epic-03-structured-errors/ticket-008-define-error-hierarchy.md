# ticket-008 Define Error Hierarchy and Exit Code Mapping

## Context

### Background

All 12 CLI commands in `app/cli.py` currently catch bare `Exception` and call `ModelOpsCommands.set_model_error()` indiscriminately, regardless of whether the error is a validation failure, an S3 connectivity issue, a SLURM scheduling problem, or a model execution bug. There is no categorization of errors, no distinct exit codes, and no structured context attached to exceptions. This ticket creates the typed error hierarchy that subsequent tickets (009, 010) will use to replace the catch-all pattern.

### Relation to Epic

This is the foundational ticket for Epic 03. It defines the error classes and exit code constants that ticket-009 (error handler decorator) and ticket-010 (CLI refactor) depend on. Without these classes, the handler has nothing to dispatch on.

### Current State

- `app/errors.py` does not exist.
- `app/validation.py` raises `click.BadParameter` for validation failures (Epic 01). This was an interim choice; Epic 03 introduces `ValidationError` as a separate class that the error handler catches and converts.
- `app/utils/scheduler.py` raises bare `RuntimeError` for squeue failures and monitoring timeouts (Epic 02). These need a `SlurmError` wrapper.
- `app/utils/s3.py` raises `ValueError` from `path_to_bucket_and_key()` and lets boto3 `ClientError` propagate from S3 operations. These need an `S3Error` wrapper.
- `ModelFactory.factory()` raises `ValueError` when a model name is not found (see `app/adapter/repository/abstractmodel.py` line 104).
- `app/utils/commands.py` defines `ModelOpsCommands` with `set_model_error()`, `set_data_error()`, and `set_success()` static methods that print `${...}` protocol strings.

## Specification

### Requirements

1. Create `app/errors.py` with a `CLIError` base class and four subclasses: `ValidationError`, `SlurmError`, `S3Error`, `ModelExecutionError`.
2. `CLIError` must carry structured context fields: `message` (str), `command_name` (str, default `""`), `detail` (str, default `""`), and `exit_code` (int).
3. Each subclass sets its own default `exit_code` via the class definition, matching the epic's exit code mapping: `ValidationError` = 2, `SlurmError` = 3, `S3Error` = 4, `ModelExecutionError` = 1.
4. Define module-level constants for exit codes: `EXIT_SUCCESS = 0`, `EXIT_MODEL_ERROR = 1`, `EXIT_VALIDATION_ERROR = 2`, `EXIT_SLURM_ERROR = 3`, `EXIT_S3_ERROR = 4`, `EXIT_UNKNOWN_ERROR = 99`.
5. `SlurmError` must accept an optional `completion_info: JobCompletionInfo | None` field so the error handler can log structured SLURM context without string parsing. Import `JobCompletionInfo` from `app.utils.scheduler`.
6. `ValidationError` must NOT inherit from `click.BadParameter`. It is a separate class. The error handler (ticket-009) will catch `click.BadParameter` separately and re-raise it so Click's built-in formatting is preserved.
7. All error classes must have a `__str__` that returns a human-readable message including the category and command name when available.

### Inputs/Props

Each error class constructor accepts keyword arguments for its fields. `CLIError.__init__` signature: `(self, message: str, *, command_name: str = "", detail: str = "", exit_code: int)`.

Subclass constructors call `super().__init__()` with their category-specific default `exit_code`, allowing callers to override if needed.

### Outputs/Behavior

- Instantiating `ValidationError("bad model name", command_name="run")` produces an object where `str(err)` returns `"[ValidationError] run: bad model name"` (or `"[ValidationError] bad model name"` when `command_name` is empty).
- `err.exit_code` returns `2`.
- `err.detail` returns `""` (default) or the detail string if provided.
- `SlurmError("job failed", completion_info=info)` stores the `JobCompletionInfo` dataclass instance for downstream use.

### Error Handling

These classes ARE the error handling infrastructure. They do not catch or raise other exceptions. They are pure data carriers.

## Acceptance Criteria

- [ ] Given the file `app/errors.py` does not exist, when ticket-008 is implemented, then `app/errors.py` exists and is importable with `from app.errors import CLIError, ValidationError, SlurmError, S3Error, ModelExecutionError`
- [ ] Given `app/errors.py` is imported, when `ValidationError("bad input", command_name="run")` is instantiated, then `err.exit_code == 2` and `str(err) == "[ValidationError] run: bad input"`
- [ ] Given `app/errors.py` is imported, when `SlurmError("timeout", completion_info=JobCompletionInfo(...))` is instantiated, then `err.completion_info` is the provided `JobCompletionInfo` instance and `err.exit_code == 3`
- [ ] Given `app/errors.py` is imported, when `ModelExecutionError("segfault")` is instantiated with no `command_name`, then `str(err) == "[ModelExecutionError] segfault"` and `err.exit_code == 1`
- [ ] Given `app/errors.py` is imported, when the module-level constants are accessed, then `EXIT_SUCCESS == 0`, `EXIT_VALIDATION_ERROR == 2`, `EXIT_SLURM_ERROR == 3`, `EXIT_S3_ERROR == 4`, `EXIT_MODEL_ERROR == 1`, `EXIT_UNKNOWN_ERROR == 99`

## Implementation Guide

### Suggested Approach

1. Create `app/errors.py`.
2. Define exit code constants at module level.
3. Define `CLIError(Exception)` with `__init__(self, message, *, command_name="", detail="", exit_code)` storing all fields as instance attributes. Implement `__str__` returning `f"[{type(self).__name__}] {self.command_name}: {self.message}"` (omitting the command_name prefix when empty).
4. Define `ValidationError(CLIError)` with `__init__` that sets `exit_code=EXIT_VALIDATION_ERROR` as default, calling `super().__init__()`.
5. Define `SlurmError(CLIError)` with an additional `completion_info: JobCompletionInfo | None = None` parameter, stored as `self.completion_info`. Default `exit_code=EXIT_SLURM_ERROR`.
6. Define `S3Error(CLIError)` with default `exit_code=EXIT_S3_ERROR`.
7. Define `ModelExecutionError(CLIError)` with default `exit_code=EXIT_MODEL_ERROR`.
8. Write unit tests in `tests/unit/test_errors.py`.

### Key Files to Modify

- `app/errors.py` (new file, ~80 lines)
- `tests/unit/test_errors.py` (new file, ~60 lines)

### Patterns to Follow

- Use `@dataclass`-style field storage (explicit `self.field = field` in `__init__`), consistent with `JobCompletionInfo` and `JobOutputFiles` in `app/utils/scheduler.py`.
- Import `JobCompletionInfo` at module level in `app/errors.py` — this is safe because `scheduler.py` does not import from `errors.py`, so there is no circular dependency risk.

### Pitfalls to Avoid

- Do NOT make `ValidationError` inherit from `click.BadParameter`. The two must remain separate so Click's own error formatting is not disrupted.
- Do NOT add error-raising logic to this module. This ticket only defines the classes. Raising them in CLI commands is ticket-010's scope.
- Do NOT modify `app/validation.py` or `app/cli.py` — those files are changed in ticket-010.

## Testing Requirements

### Unit Tests

- Test each error class instantiation with all fields (message, command_name, detail, exit_code).
- Test `__str__` output for each class with and without `command_name`.
- Test `SlurmError` with and without `completion_info`.
- Test that each subclass is an instance of both its own type and `CLIError`.
- Test that default exit codes match the constants.

### Integration Tests

None. This ticket creates pure data classes with no I/O or side effects.

### E2E Tests

None.

## Dependencies

- **Blocked By**: ticket-007-capture-stderr-and-final-stdout.md (Epic 02 must be complete so `JobCompletionInfo` exists in `app/utils/scheduler.py`)
- **Blocks**: ticket-009-create-cli-error-handler.md, ticket-010-replace-try-except-in-cli.md

## Effort Estimate

**Points**: 2
**Confidence**: High
