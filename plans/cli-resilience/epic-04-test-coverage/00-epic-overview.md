# Epic 04: Test Coverage

## Goal

Add comprehensive unit and integration tests for the new validation layer, SLURM monitoring logic, and structured error handling introduced in Epics 01-03. The existing test suite covers S3 operations and some model logic but has no coverage for input validation, SLURM monitoring, or error handling paths.

## Scope

- Unit tests for validation primitives and per-command validators
- Unit tests for SLURM monitoring functions (mocked subprocess/terminal calls)
- Integration tests using Click's test runner for CLI command validation
- Unit tests for error hierarchy and exit code mapping

## Out of Scope

- Tests requiring actual SLURM cluster access
- Tests requiring actual S3 access (existing integration tests cover this)
- Refactoring existing tests

## Tickets

| Ticket     | Title                                                               | Points |
| ---------- | ------------------------------------------------------------------- | ------ |
| ticket-011 | Add unit tests for validation primitives and per-command validators | 3      |
| ticket-012 | Add unit tests for SLURM monitoring functions                       | 3      |
| ticket-013 | Add CLI integration tests for error handling and exit codes         | 3      |

## Dependencies

- Depends on Epics 01, 02, and 03 being completed (tests the code they produce)

## Success Criteria

- Validation primitives have 100% branch coverage
- SLURM monitoring has tests for: fast job (<2s), normal job, timeout, sacct fallback
- CLI integration tests verify correct exit codes for each error category
