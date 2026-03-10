# ticket-002 Implement Per-Command Validator Functions

## Context

### Background

With validation primitives available from ticket-001 (`app/validation.py`), this ticket composes them into per-command validation functions — one function per CLI command. Each function takes the same arguments as its CLI command and validates all of them before any business logic executes.

The 13 CLI commands in `app/cli.py` are:

1. `check_and_fetch_inputs(model_name, path, parent_path, delete)`
2. `check_and_fetch_executables(model_name, path)`
3. `extract_sanitize_inputs(model_name)`
4. `preprocess(model_name, execution_name)`
5. `run(model_name, queue, core_count, max_cores_per_node, max_job_time_hours, mpich_path, slurm_path, skip)`
6. `generate_execution_status(model_name, job_id)`
7. `postprocess(model_name)`
8. `output_compression_and_cleanup(model_name, num_cpus)`
9. `result_upload(model_name, path)`
10. `cancel_run(model_name, job_id, slurm_path)`
11. `download_executed_run(model_name, artifacts_path, fetch_inputs)`
12. `fetch_extract_raw_outputs(outputs_path)`

Note: Command 12 (`fetch_extract_raw_outputs`) does not take `model_name` — it operates on raw S3 paths directly.

### Relation to Epic

This is the second ticket in Epic 01. It builds on ticket-001's primitives to create the full validation logic for each command.

### Current State

- `app/validation.py` exists with 6 primitive validators (from ticket-001)
- No per-command validation functions exist
- Each CLI command in `app/cli.py` goes directly to `ModelFactory().factory()` and model methods without validation

## Specification

### Requirements

Add per-command validator functions to `app/validation.py`. Each function is named `validate_<command_name>` and accepts the same parameters as the corresponding CLI command. Each function composes the primitive validators to check all arguments.

### Per-Command Validators

1. **`validate_check_and_fetch_inputs(model_name, path, parent_path)`**
   - `validate_model_name(model_name)`
   - `validate_s3_path(path, "path")`
   - If `parent_path` is non-empty: `validate_s3_path(parent_path, "parent-path")`

2. **`validate_check_and_fetch_executables(model_name, path)`**
   - `validate_model_name(model_name)`
   - `validate_s3_path(path, "path")`

3. **`validate_extract_sanitize_inputs(model_name)`**
   - `validate_model_name(model_name)`

4. **`validate_preprocess(model_name)`**
   - `validate_model_name(model_name)`

5. **`validate_run(model_name, queue, core_count, max_cores_per_node, max_job_time_hours)`**
   - `validate_model_name(model_name)`
   - `validate_queue_name(queue)`
   - `validate_positive_int(core_count, "core_count")`
   - `validate_optional_positive_int(max_cores_per_node, "max-cores-per-node")`
   - `validate_optional_positive_int(max_job_time_hours, "max-job-time-hours")`

6. **`validate_generate_execution_status(model_name)`**
   - `validate_model_name(model_name)`

7. **`validate_postprocess(model_name)`**
   - `validate_model_name(model_name)`

8. **`validate_output_compression_and_cleanup(model_name, num_cpus)`**
   - `validate_model_name(model_name)`
   - `validate_positive_int(num_cpus, "num_cpus")`

9. **`validate_result_upload(model_name, path)`**
   - `validate_model_name(model_name)`
   - `validate_s3_path(path, "path")`

10. **`validate_cancel_run(model_name)`**
    - `validate_model_name(model_name)`

11. **`validate_download_executed_run(model_name, artifacts_path)`**
    - `validate_model_name(model_name)`
    - `validate_s3_path(artifacts_path, "artifacts_path")`

12. **`validate_fetch_extract_raw_outputs(outputs_path)`**
    - `validate_s3_path(outputs_path, "outputs_path")`

### Outputs/Behavior

- On valid inputs: returns `None`
- On first invalid input: raises `click.BadParameter` (from the primitive validator) — validation is fail-fast, not collect-all-errors

### Error Handling

Same as ticket-001: `click.BadParameter` propagates to Click which prints the error and exits with code 2.

## Acceptance Criteria

- [ ] Given `app/validation.py` contains primitive validators, when ticket-002 is implemented, then `app/validation.py` also contains 12 `validate_<command_name>` functions matching the 12 distinct CLI commands
- [ ] Given a call to `validate_run("newave", "normal", 64, None, None)` with models registered, when the function executes, then it returns `None`
- [ ] Given a call to `validate_run("newave", "normal", -1, None, None)`, when the function executes, then it raises `click.BadParameter` with a message about `core_count` being positive
- [ ] Given a call to `validate_check_and_fetch_inputs("newave", "not-s3-path", "")`, when the function executes, then it raises `click.BadParameter` with a message about S3 path format
- [ ] Given a call to `validate_fetch_extract_raw_outputs("s3://bucket/key/outputs.zip")`, when the function executes, then it returns `None`

## Implementation Guide

### Suggested Approach

1. Open `app/validation.py` (created in ticket-001)
2. Add 12 functions after the primitive validators
3. Each function composes primitives with a simple sequential call pattern — no need for complex orchestration
4. Document each function with a docstring listing which command it validates

### Key Files to Modify

- `app/validation.py` (append per-command validators)

### Patterns to Follow

- Keep each validator function short: just a sequence of primitive calls
- Use the exact parameter names from `app/cli.py` for consistency
- Commands that share the same parameter (e.g., `model_name`) use the same primitive with the same `param_name`

### Pitfalls to Avoid

- Do NOT validate parameters that are only used as flags (e.g., `delete`, `skip`, `fetch_inputs`) — boolean flags are already type-safe from Click
- Do NOT validate `execution_name` or `job_id` as these are free-form strings that can legitimately be empty
- Do NOT validate filesystem paths for existence here — the working directory at validation time is the correct directory (CWD), but file existence depends on prior workflow steps having completed
- Do NOT validate `mpich_path` and `slurm_path` for existence — these are system paths that vary per cluster and are validated implicitly when used

## Testing Requirements

### Unit Tests

- Test each per-command validator with all-valid args returns `None`
- Test each per-command validator with one invalid arg raises `click.BadParameter`
- For `validate_check_and_fetch_inputs`: test that empty `parent_path` skips S3 validation for that parameter

### Integration Tests

Not applicable (pure functions).

## Dependencies

- **Blocked By**: ticket-001-create-validation-primitives.md
- **Blocks**: ticket-003-integrate-validators-into-cli.md

## Effort Estimate

**Points**: 3
**Confidence**: High
