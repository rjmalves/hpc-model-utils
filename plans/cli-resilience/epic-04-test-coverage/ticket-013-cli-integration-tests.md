# ticket-013 Add CLI Integration Tests for Error Handling and Exit Codes

## Context

### Background

Epics 01 and 03 built the full validation and error handling pipeline: Click types validate at argument-parse time, per-command validators validate in the command body, `_get_model_with_logger` wraps `ModelFactory` ValueError as `ValidationError`, and `@handle_cli_errors` catches all exceptions and maps them to exit codes and ModelOps signals. All individual layers have unit tests, but no test exercises the full path from CLI invocation through to exit code. This ticket adds integration tests using Click's `CliRunner` to verify the assembled pipeline.

### Relation to Epic

Epic 04 adds test coverage for Epics 01-03. Ticket-013 is the integration test layer that sits above the unit tests in tickets 011 and 012. It tests the CLI commands as a user would invoke them, verifying that the decorators, validators, Click types, and error handler work together correctly.

### Current State

- `app/cli.py` has 12 commands, each decorated with `@handle_cli_errors`, using `ModelNameType`/`S3PathType`/`PositiveIntType` argument types, and calling per-command validators.
- `app/error_handler.py` maps exception types to exit codes: `ValidationError` -> 2, `SlurmError` -> 3, `S3Error` -> 4, `ModelExecutionError` -> 1, bare `Exception` -> 99. Click exceptions (`BadParameter`, `UsageError`) are re-raised (Click handles them with exit code 2).
- `_get_model_with_logger()` in `app/cli.py` catches `ValueError` from `ModelFactory().factory()` and re-raises as `ValidationError`.
- No CLI integration test file exists. The `cli` group object is importable from `app.cli`.
- `tests/unit/test_error_handler.py` has 35 tests for the decorator in isolation (not through Click).

## Specification

### Requirements

1. Create `tests/unit/test_cli_integration.py` with integration tests using `click.testing.CliRunner`.
2. Test the two exit-code-2 paths: (a) Click type rejection at argument-parse time (e.g., invalid model name rejected by `ModelNameType`), and (b) `ValidationError` from `ModelFactory().factory()` failure routed through the error handler.
3. Test at least one command that requires `S3PathType` validation (e.g., `check_and_fetch_inputs` with an invalid S3 path).
4. Test at least one command that requires `PositiveIntType` validation (e.g., `output_compression_and_cleanup` with a zero or negative value).
5. Test the happy path for at least one simple command (model method is mocked, exit code 0).

### Inputs/Props

- `CliRunner` invokes commands by name through the `cli` group: `runner.invoke(cli, ["command_name", "arg1", "arg2"])`.
- `ModelFactory` singleton must be populated with a fake model for tests that need valid model names. The fake model class must have the methods called by the command under test (mock them with `MagicMock`).
- `ModelOpsCommands.set_model_error` and `ModelOpsCommands.set_data_error` must be patched to prevent side effects.
- `Log.configure_logger` must be patched to return a mock logger.

### Outputs/Behavior

- `result.exit_code` from `CliRunner.invoke()` reflects the exit code set by the error handler or Click.
- `result.output` contains stdout text (error messages, log output).
- When Click type validation fails (argument-parse time), Click itself formats the error and sets exit code 2. The error handler never runs because the command body is never entered.
- When `ValidationError` is raised inside the command body, the error handler calls `sys.exit(2)`. `CliRunner` captures this as exit code 2.

### Error Handling

- `CliRunner` catches `SystemExit` and reports it as `result.exit_code`. No need to patch `sys.exit` in integration tests.
- `CliRunner` does not propagate exceptions by default (it catches them). Use `catch_exceptions=False` only if you need to debug; otherwise leave the default.

## Acceptance Criteria

- [ ] Given `tests/unit/test_cli_integration.py` does not exist, when this ticket is implemented, then the file is created and `pytest tests/unit/test_cli_integration.py` exits with code 0
- [ ] Given `ModelNameType` rejects unknown model names at argument-parse time, when `runner.invoke(cli, ["extract_sanitize_inputs", "nonexistent_model"])` is called with a registered fake model, then `result.exit_code` is 2 and `result.output` contains "not found" or "not a valid"
- [ ] Given `S3PathType` rejects non-S3 paths, when `runner.invoke(cli, ["check_and_fetch_inputs", FAKE_MODEL, "not-s3-path"])` is called, then `result.exit_code` is 2
- [ ] Given `PositiveIntType` rejects zero, when `runner.invoke(cli, ["output_compression_and_cleanup", FAKE_MODEL, "0"])` is called, then `result.exit_code` is 2
- [ ] Given a valid model with a mocked method, when `runner.invoke(cli, ["extract_sanitize_inputs", FAKE_MODEL])` is called, then `result.exit_code` is 0

## Implementation Guide

### Suggested Approach

1. Create `tests/unit/test_cli_integration.py`.
2. Import `click.testing.CliRunner` and the `cli` group from `app.cli`.
3. Create a fixture that registers a fake model into `ModelFactory()._models`. The fake model class needs mock methods for each command being tested. Use a class with `MagicMock` attributes or `unittest.mock.create_autospec`. Register it like `test_validation_per_command.py` does (line 38-45).
4. Create a fixture that patches `ModelOpsCommands` methods to no-ops (they write files and call shell commands).
5. Create a fixture that patches `Log.configure_logger` to return a `MagicMock()` logger.
6. Create a fixture for `ModelFactory().factory()` — since the real `factory()` method tries to instantiate the model class with a logger, you need to either: (a) register a real callable in `_models` that returns a mock when called with `(model_name, logger)`, or (b) patch `ModelFactory.factory` to return a mock model instance. Option (b) is simpler and decouples integration tests from `ModelFactory` internals.
7. Test classes:
   - `TestClickTypeValidation`: Tests where Click types reject input before the command body runs (exit code 2 from Click's own formatter).
   - `TestValidationErrorPath`: Tests where `ModelFactory().factory()` raises `ValueError`, which `_get_model_with_logger` wraps as `ValidationError` (exit code 2 from the error handler).
   - `TestHappyPath`: Tests where a command succeeds with all valid inputs and mocked model methods (exit code 0).

### Key Files to Modify

- `tests/unit/test_cli_integration.py` (new, ~180 lines)

### Patterns to Follow

- Use `runner.invoke(cli, ["command_name", ...])` to invoke commands through the group, not by calling command functions directly.
- Patch `app.error_handler.ModelOpsCommands` (the import target in the error handler module) to prevent ModelOps signal side effects.
- Use `monkeypatch` or `unittest.mock.patch` for `Log.configure_logger`.
- Follow the `register_fake_model` fixture pattern from `test_validation_per_command.py`.

### Pitfalls to Avoid

- Do not test every single command. Test 2-3 representative commands that exercise distinct code paths (model-name-only, S3 path, positive int). Exhaustive per-command coverage is already handled by `test_validation_per_command.py`.
- `CliRunner.invoke()` does not raise `SystemExit`; it captures it in `result.exit_code`. Do not use `pytest.raises(SystemExit)`.
- The `cli` group is defined at module level in `app/cli.py`. Importing it triggers `ModelFactory` imports and model registration. The `register_fake_model` fixture must run after this import completes.
- `_get_model_with_logger()` calls `Log.configure_logger()` which creates real log files. Patch it or the tests will have side effects.
- When `ModelNameType.convert()` encounters an `ImportError`, it silently passes the value through (line 48-52 of `click_types.py`). In the test environment where `ModelFactory` IS importable, the type will actually validate. Make sure the fake model is registered before invoking commands that use `ModelNameType`.

## Testing Requirements

### Unit Tests

Not applicable; this ticket is integration-level tests.

### Integration Tests

This ticket IS the integration tests. The deliverable is one new test file.

### E2E Tests

Not applicable.

## Dependencies

- **Blocked By**: ticket-010-replace-try-except-in-cli.md (error handler wiring must be complete; completed)
- **Blocks**: None

## Effort Estimate

**Points**: 3
**Confidence**: High
