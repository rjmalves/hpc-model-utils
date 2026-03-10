# ticket-004 Add Click Parameter Types for Semantic Validation

## Context

### Background

Click supports custom parameter types that validate at the argument parsing stage, before the command function body executes. After tickets 001-003 added explicit validation calls inside command functions, this ticket adds Click-native parameter types so that certain validations happen even earlier — at argument parsing time — and produce Click's standard error formatting.

This is a defense-in-depth improvement: the explicit validators from ticket-003 remain as the primary validation, but custom Click types provide earlier feedback for the most common input types.

### Relation to Epic

This is the final ticket in Epic 01. It polishes the validation layer by leveraging Click's native extension points.

### Current State

- `app/cli.py` uses `type=str` for model names, paths, and queue names, and `type=int` for core counts
- Validation from ticket-003 catches invalid values inside the command function body
- No custom Click parameter types exist in the codebase

## Specification

### Requirements

Create custom Click parameter types in a new module `app/click_types.py`:

1. **`ModelNameType(click.ParamType)`**
   - `name = "MODEL_NAME"`
   - `convert()` method: validates the value is a registered model name, returns the value if valid
   - On failure: calls `self.fail(f'Model "{value}" not found. Valid: {valid_names}', param, ctx)`

2. **`S3PathType(click.ParamType)`**
   - `name = "S3_PATH"`
   - `convert()` method: validates `s3://bucket/key` format, returns the value if valid
   - On failure: calls `self.fail(f'"{value}" is not a valid S3 path. Expected format: s3://bucket/key', param, ctx)`

3. **`PositiveIntType(click.ParamType)`**
   - `name = "POSITIVE_INT"`
   - `convert()` method: converts to int (if string), validates > 0, returns the int
   - On failure: calls `self.fail(f'"{value}" is not a positive integer', param, ctx)`

After creating the types, update `app/cli.py` to use them in command definitions:

- Replace `type=str` with `type=ModelNameType()` for all `model_name` arguments
- Replace `type=str` with `type=S3PathType()` for `path` and `artifacts_path` arguments that expect S3 paths
- Replace `type=int` with `type=PositiveIntType()` for `core_count` and `num_cpus` arguments

### Outputs/Behavior

- Valid inputs: no change — value is passed to the command function as before
- Invalid inputs: Click prints a formatted error and exits with code 2, before the command function body is entered

### Error Handling

Click's `ParamType.fail()` raises `click.BadParameter` internally, producing standard Click error output.

## Acceptance Criteria

- [ ] Given `app/click_types.py` does not exist, when ticket-004 is implemented, then `app/click_types.py` exists containing `ModelNameType`, `S3PathType`, and `PositiveIntType` classes
- [ ] Given the `run` command definition in `app/cli.py`, when ticket-004 is implemented, then the `model_name` argument uses `type=ModelNameType()` and the `core_count` argument uses `type=PositiveIntType()`
- [ ] Given a CLI invocation `hpc-model-utils run invalid_model normal 64`, when Click parses arguments, then `ModelNameType.convert()` fails and Click exits with code 2 before the `run` function body executes
- [ ] Given a CLI invocation `hpc-model-utils check_and_fetch_inputs newave not-s3-path`, when Click parses arguments, then `S3PathType.convert()` fails and Click exits with code 2

## Implementation Guide

### Suggested Approach

1. Create `app/click_types.py`
2. Implement `ModelNameType(click.ParamType)`:
   - In `convert()`, import `ModelFactory` and check `value in ModelFactory()._models`
   - Use a lazy import to avoid circular dependencies
3. Implement `S3PathType(click.ParamType)`:
   - In `convert()`, check starts with `s3://` and has a non-empty bucket+key
4. Implement `PositiveIntType(click.ParamType)`:
   - In `convert()`, handle both str and int inputs, validate > 0
5. Update `app/cli.py`:
   - Add `from app.click_types import ModelNameType, S3PathType, PositiveIntType`
   - Replace `type=str` with `type=ModelNameType()` for model_name arguments in all commands
   - Replace `type=str` with `type=S3PathType()` for S3 path arguments: `check_and_fetch_inputs` (path), `check_and_fetch_executables` (path), `result_upload` (path), `download_executed_run` (artifacts_path), `fetch_extract_raw_outputs` (outputs_path)
   - Replace `type=int` with `type=PositiveIntType()` for `core_count` in `run` and `num_cpus` in `output_compression_and_cleanup`
   - Do NOT change `parent_path` to `S3PathType` because it can be empty string (optional)

### Key Files to Modify

- `app/click_types.py` (new file — create)
- `app/cli.py` (update argument type declarations)

### Patterns to Follow

- Follow Click's documentation for custom types: subclass `click.ParamType`, override `name` and `convert(self, value, param, ctx)`
- Use `self.fail(message, param, ctx)` for validation failures (not `raise click.BadParameter`)

### Pitfalls to Avoid

- Do NOT remove the explicit validators from ticket-003 — the Click types are defense-in-depth, not a replacement. The explicit validators check combinations of parameters that Click types cannot
- Do NOT use `ModelNameType()` for `parent_path` or optional string parameters
- Do NOT make `PositiveIntType` reject `None` — Click handles `None` for optional parameters before calling `convert()`
- `ModelNameType.convert()` must handle the case where models are not yet registered — use a lazy import inside `convert()` and handle `ImportError` gracefully

## Testing Requirements

### Unit Tests

- Test each custom type's `convert()` with valid and invalid inputs
- Test that `ModelNameType` lists valid model names in failure message
- Test that `PositiveIntType` handles both string and int inputs

### Integration Tests

Not applicable (Click's built-in testing handles this).

## Dependencies

- **Blocked By**: ticket-003-integrate-validators-into-cli.md
- **Blocks**: None (final ticket in Epic 01)

## Effort Estimate

**Points**: 2
**Confidence**: High
