# Epic 01: Input Validation Layer

## Goal

Add upfront input validation to all 13 CLI commands so that invalid inputs are caught and reported before any side-effects (S3 downloads, file writes, SLURM submissions) begin. Currently, validation is either absent or happens deep inside model methods, causing late failures that kill HPC tasks without clear diagnostics.

## Scope

- Create reusable validation primitive functions (model name, S3 path, positive integer, queue name, file path existence)
- Create per-command validation functions that compose the primitives
- Integrate validation calls into `cli.py` before `ModelFactory().factory()` and model method calls
- Raise `ValidationError` (from Epic 03) or, in interim, raise `click.BadParameter` / `click.UsageError` with clear messages

## Out of Scope

- Error hierarchy and structured error handling (Epic 03)
- SLURM monitoring changes (Epic 02)
- Test coverage (Epic 04)

## Tickets

| Ticket     | Title                                             | Points |
| ---------- | ------------------------------------------------- | ------ |
| ticket-001 | Create validation primitives module               | 3      |
| ticket-002 | Implement per-command validator functions         | 3      |
| ticket-003 | Integrate validators into CLI commands            | 2      |
| ticket-004 | Add Click parameter types for semantic validation | 2      |

## Dependencies

- No dependencies on other epics
- Epic 03 (Structured Errors) will later replace `click.UsageError` with `ValidationError`, but the validation logic itself is independently useful

## Success Criteria

- Every CLI command rejects invalid inputs before any I/O or SLURM operation
- Error messages include the invalid value, the expected format, and a suggestion
- `ModelFactory().factory()` is never called with an unregistered model name
- S3 paths are validated for `s3://bucket/key` structure before any S3 API call
