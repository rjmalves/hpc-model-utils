# ticket-001 Create Validation Primitives Module

## Context

### Background

The `hpc-model-utils` CLI application has 13 Click commands that accept arguments like model names, S3 paths, queue names, core counts, and file paths. Currently, Click only validates types (str, int, bool). There is no semantic validation: an invalid model name like `"foo"` passes through Click and only fails deep inside `ModelFactory().factory()` with a generic `ValueError`. S3 paths are validated only for the `s3://` prefix in one place (`path_to_bucket_and_key` in `app/utils/s3.py`). Core counts, queue names, and path arguments have no validation at all.

This ticket creates the foundational validation primitives that will be composed by per-command validators in ticket-002.

### Relation to Epic

This is the first ticket in Epic 01 (Input Validation). It provides the reusable building blocks that all subsequent validation work depends on.

### Current State

- `app/utils/s3.py` line 14-16: `path_to_bucket_and_key()` checks `s3://` prefix, but the check is embedded in business logic rather than being a reusable validator
- `app/adapter/repository/abstractmodel.py` line 100-104: `ModelFactory.factory()` raises `ValueError` for unknown models, but this happens after logger setup and is not a fast-fail validation
- No validation module exists in the codebase
- Registered model names are: `newave`, `decomp`, `dessem`, `gevazp` (registered at module bottom of each model file)

## Specification

### Requirements

Create a new module `app/validation.py` containing pure validation functions. Each function takes a value and returns `None` on success or raises `click.BadParameter` with a descriptive message on failure. The functions must be pure (no I/O, no side effects) and composable.

### Inputs/Props

Each validation function takes the value to validate and optional context (parameter name, command name) for error message construction.

### Outputs/Behavior

- On valid input: returns `None`
- On invalid input: raises `click.BadParameter(message, param_hint=param_name)`

### Error Handling

All validation functions raise `click.BadParameter` which Click handles by printing the error message and exiting with code 2. This is the correct interim behavior until Epic 03 introduces `ValidationError`.

### Validators to Implement

1. **`validate_model_name(value: str, param_name: str = "model_name")`**
   - Valid: value is in `ModelFactory()._models` (registered models)
   - Invalid: raise with message listing valid model names
   - Note: Must import `ModelFactory` — the factory is a singleton populated at import time by model module registration

2. **`validate_s3_path(value: str, param_name: str = "path")`**
   - Valid: starts with `s3://`, has at least a bucket name after the prefix (i.e., `s3://bucket` minimum), and key portion is non-empty
   - Invalid: raise with message showing expected format `s3://bucket/key/path`

3. **`validate_positive_int(value: int, param_name: str = "value")`**
   - Valid: value > 0
   - Invalid: raise with message stating value must be positive

4. **`validate_optional_positive_int(value: int | None, param_name: str = "value")`**
   - Valid: value is `None` or value > 0
   - Invalid: raise with message stating value must be positive when provided

5. **`validate_queue_name(value: str, param_name: str = "queue")`**
   - Valid: non-empty string, contains only alphanumeric characters, hyphens, and underscores
   - Invalid: raise with message showing allowed characters

6. **`validate_path_not_empty(value: str, param_name: str = "path")`**
   - Valid: non-empty string after stripping whitespace
   - Invalid: raise with message stating path must not be empty

## Acceptance Criteria

- [ ] Given `app/validation.py` does not exist, when ticket-001 is implemented, then `app/validation.py` exists and contains all 6 validator functions listed in the specification
- [ ] Given a call to `validate_model_name("newave")` after model modules are imported, when the function executes, then it returns `None` without raising
- [ ] Given a call to `validate_model_name("invalid_model")`, when the function executes, then it raises `click.BadParameter` with a message containing `"invalid_model"` and listing the valid model names
- [ ] Given a call to `validate_s3_path("s3://my-bucket/some/key")`, when the function executes, then it returns `None`
- [ ] Given a call to `validate_s3_path("not-an-s3-path")`, when the function executes, then it raises `click.BadParameter` with a message containing `"s3://"` as the expected format

## Implementation Guide

### Suggested Approach

1. Create `app/validation.py`
2. Import `click` and `ModelFactory` (lazy import of ModelFactory inside the function to avoid circular imports, since model modules import from `abstractmodel.py`)
3. Implement each validator as a standalone function with the signature `def validate_X(value, param_name="default") -> None`
4. Use `click.BadParameter(message, param_hint=param_name)` for all validation failures
5. Document each function with a docstring explaining valid/invalid inputs

### Key Files to Modify

- `app/validation.py` (new file — create)

### Patterns to Follow

- Follow the existing style in `app/utils/s3.py` for simple validation (see `path_to_bucket_and_key` lines 14-16)
- Use Python 3.10 union syntax (`int | None`) as used throughout the codebase
- Use `click.BadParameter` as this is the idiomatic Click way to report parameter validation failures

### Pitfalls to Avoid

- Do NOT import model classes directly (newave, decomp, etc.) — use `ModelFactory()._models.keys()` to get registered names dynamically
- Do NOT use `ModelFactory` at module level — it must be called inside the function body because model registration happens at import time of model modules, and `validation.py` may be imported before models are registered
- Do NOT add file existence checks here — those belong in per-command validators (ticket-002) because they depend on the working directory context

## Testing Requirements

### Unit Tests

- Test each validator with valid and invalid inputs
- Test `validate_model_name` with all 4 registered models and with an invalid name
- Test `validate_s3_path` with valid paths, missing prefix, empty bucket, empty key
- Test `validate_positive_int` with positive, zero, and negative values
- Note: Full test implementation is in Epic 04, but implementer should verify manually

### Integration Tests

Not applicable for this ticket (pure functions).

## Dependencies

- **Blocked By**: None (first ticket in the plan)
- **Blocks**: ticket-002-implement-per-command-validators.md, ticket-003-integrate-validators-into-cli.md

## Effort Estimate

**Points**: 3
**Confidence**: High
