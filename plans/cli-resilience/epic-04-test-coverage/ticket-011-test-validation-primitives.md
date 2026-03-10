# ticket-011 Add Unit Tests for Validation Primitives and Click Types

## Context

### Background

Epics 01 and 03 created two validation layers: six primitive validators in `app/validation.py` and three custom `click.ParamType` subclasses in `app/click_types.py`. Per-command validators (which compose the primitives) already have thorough tests in `tests/unit/test_validation_per_command.py` (Epic 01, ticket-002). However, the primitives themselves and the Click types have zero dedicated test coverage. This ticket fills that gap.

### Relation to Epic

Epic 04 adds comprehensive test coverage for all new code produced in Epics 01-03. This ticket covers the lowest layer of the validation stack: the individual primitive functions and the Click parameter types. It should be completed before ticket-013 (CLI integration tests), because integration tests exercise these same functions indirectly and understanding their exact behavior at the unit level simplifies debugging integration test failures.

### Current State

- `app/validation.py` contains 6 primitive validators: `validate_model_name`, `validate_s3_path`, `validate_positive_int`, `validate_optional_positive_int`, `validate_queue_name`, `validate_path_not_empty`. All raise `click.BadParameter` on failure and return `None` on success.
- `app/click_types.py` contains 3 `click.ParamType` subclasses: `ModelNameType`, `S3PathType`, `PositiveIntType`. Each overrides `convert(self, value, param, ctx)`.
- `tests/unit/test_validation_per_command.py` exists (300 lines) and tests the 12 per-command validators. It uses an `autouse` fixture that injects a fake model into `ModelFactory()._models`.
- No test file exists for the 6 primitives or the 3 Click types.

## Specification

### Requirements

1. Create `tests/unit/test_validation_primitives.py` with unit tests for all 6 primitive validators in `app/validation.py`.
2. Create `tests/unit/test_click_types.py` with unit tests for all 3 Click type subclasses in `app/click_types.py`.
3. Each function/class must have both valid-input and invalid-input test cases covering all branches.

### Inputs/Props

- **`validate_model_name(value, param_name)`**: Needs `ModelFactory` singleton injection (same fixture pattern as `test_validation_per_command.py`). Branches: valid model name, invalid model name, empty registry.
- **`validate_s3_path(value, param_name)`**: Branches: valid `s3://bucket/key`, missing `s3://` prefix, empty bucket, missing key portion.
- **`validate_positive_int(value, param_name)`**: Branches: positive value, zero, negative value.
- **`validate_optional_positive_int(value, param_name)`**: Branches: `None` (returns immediately), positive value, zero, negative value.
- **`validate_queue_name(value, param_name)`**: Branches: valid alphanumeric+hyphen+underscore, empty string, string with invalid chars (spaces, dots, slashes).
- **`validate_path_not_empty(value, param_name)`**: Branches: non-empty string, empty string, whitespace-only string.
- **`ModelNameType.convert()`**: Branches: valid model str, invalid model str, non-string input, `ImportError` fallback path.
- **`S3PathType.convert()`**: Branches: valid S3 path str, missing prefix, empty bucket, empty key, non-string input.
- **`PositiveIntType.convert()`**: Branches: `None` passthrough, valid int, valid str, zero int, negative int, non-numeric str, bool input (`True`/`False` must be rejected).

### Outputs/Behavior

- All primitives return `None` on success and raise `click.BadParameter` on failure.
- Click types return the converted value on success and call `self.fail()` on failure (which raises `click.exceptions.BadParameter`).
- `PositiveIntType.convert()` returns an `int` value.
- `ModelNameType.convert()` returns the value string unchanged when `ImportError` occurs.

### Error Handling

- Tests must verify that `click.BadParameter` is raised (not `ValueError` or other exception types).
- Tests must verify that the `param_hint` in `BadParameter` matches the `param_name` argument for primitives.
- For Click types, `self.fail()` raises `click.exceptions.BadParameter`; tests use `pytest.raises(click.exceptions.BadParameter)`.

## Acceptance Criteria

- [ ] Given `tests/unit/test_validation_primitives.py` does not exist, when this ticket is implemented, then the file is created and `pytest tests/unit/test_validation_primitives.py` exits with code 0
- [ ] Given `tests/unit/test_click_types.py` does not exist, when this ticket is implemented, then the file is created and `pytest tests/unit/test_click_types.py` exits with code 0
- [ ] Given `validate_s3_path` has 4 distinct branches (valid, no prefix, empty bucket, no key), when the test file is run, then each branch is exercised by at least one test case (4 test methods minimum in `TestValidateS3Path`)
- [ ] Given `PositiveIntType.convert()` must reject `bool` inputs, when `convert(True, None, None)` is called, then `click.exceptions.BadParameter` is raised
- [ ] Given `ModelNameType.convert()` has an `ImportError` fallback, when `ModelFactory` import raises `ImportError`, then `convert()` returns the input string unchanged

## Implementation Guide

### Suggested Approach

**File 1: `tests/unit/test_validation_primitives.py`**

1. Import all 6 primitives from `app.validation`.
2. Import `ModelFactory` from `app.adapter.repository.abstractmodel` and create a `register_fake_model` `autouse` fixture identical to the one in `tests/unit/test_validation_per_command.py` (lines 38-45).
3. Create one test class per primitive (6 classes). Each class tests the valid path (returns `None`) and all invalid paths (raises `click.BadParameter` with the expected `param_hint`).
4. For `validate_model_name`, add a test with an empty registry (pop the fake model before calling) to verify the `"(none registered)"` fallback message.

**File 2: `tests/unit/test_click_types.py`**

1. Import all 3 types from `app.click_types`.
2. For `ModelNameType`, reuse the same `ModelFactory` singleton fixture. Add a test that patches the import to raise `ImportError` to exercise the fallback path (lines 48-52 of `app/click_types.py`).
3. For `PositiveIntType`, test the `isinstance(value, bool)` exclusion explicitly: `convert(True, None, None)` must raise, `convert(False, None, None)` must raise.
4. For `S3PathType`, test valid path, missing prefix, empty bucket (`s3:///key`), and empty key (`s3://bucket`).
5. Click types' `self.fail()` raises `click.exceptions.BadParameter`. Use `pytest.raises(click.exceptions.BadParameter)` in assertions.

### Key Files to Modify

- `tests/unit/test_validation_primitives.py` (new, ~150 lines)
- `tests/unit/test_click_types.py` (new, ~150 lines)

### Patterns to Follow

- Use the `register_fake_model` fixture pattern from `tests/unit/test_validation_per_command.py` (lines 38-45): directly mutate `ModelFactory()._models` in setup, pop in teardown.
- Use `pytest.raises(click.BadParameter, match="...")` for asserting error messages contain expected substrings.
- One test class per function/class under test, matching the convention in `test_validation_per_command.py` and `test_errors.py`.

### Pitfalls to Avoid

- Do not duplicate per-command validator tests (those are already in `test_validation_per_command.py`). This ticket tests only the 6 primitives and 3 Click types.
- `PositiveIntType.convert()` returns `None` unchanged when `value is None` (line 124-125 of `app/click_types.py`). This is intentional for Click's optional parameter handling. Test it with a `None` input and assert the return is `None`.
- `ModelNameType.convert()` calls `self.fail()` which has `return type -> Never`. The `type: ignore[return-value]` comment on line 37 means the non-string branch technically has no clean return path; the test just needs to verify it raises.

## Testing Requirements

### Unit Tests

This ticket IS the unit tests. The deliverables are two new test files.

### Integration Tests

Not applicable. Integration tests are covered by ticket-013.

### E2E Tests

Not applicable.

## Dependencies

- **Blocked By**: ticket-004-add-click-parameter-types.md (Click types must exist; completed)
- **Blocks**: None

## Effort Estimate

**Points**: 2
**Confidence**: High
