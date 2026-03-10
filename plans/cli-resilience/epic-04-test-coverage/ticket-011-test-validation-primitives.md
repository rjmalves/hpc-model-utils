# ticket-011 Add Unit Tests for Validation Primitives and Per-Command Validators

> **[OUTLINE]** This ticket requires refinement before execution.
> It will be refined with learnings from earlier epics.

## Objective

Create comprehensive unit tests for all validation primitives (`validate_model_name`, `validate_s3_path`, `validate_positive_int`, `validate_optional_positive_int`, `validate_queue_name`, `validate_path_not_empty`) and all 12 per-command validator functions in `app/validation.py`. Tests should achieve 100% branch coverage on the validation module.

## Anticipated Scope

- **Files likely to be modified**: `tests/test_validation.py` (new)
- **Key decisions needed**:
  - How to handle `ModelFactory` registration in tests — whether to use a fixture that imports model modules or to mock the factory
  - Whether to use `pytest.raises(click.BadParameter)` or a helper function for asserting validation failures
- **Open questions**:
  - What are the edge cases for S3 path validation discovered during Epic 01 implementation?
  - Did the Click parameter types (ticket-004) change how validation errors are raised?

## Dependencies

- **Blocked By**: ticket-004-add-click-parameter-types.md (Epic 01 must be complete)
- **Blocks**: None

## Effort Estimate

**Points**: 3
**Confidence**: Low (will be re-estimated during refinement)
