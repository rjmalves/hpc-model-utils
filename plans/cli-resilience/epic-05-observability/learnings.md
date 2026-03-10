# Epic 05 Learnings: Observability Improvements

## Patterns Established

- **`_build_annotation` + optional override parameter pattern** (ticket-014): Annotation logic
  lives in two private helpers in `app/error_handler.py` — `_build_annotation(error: CLIError)
-> str` for the common case and `_build_slurm_annotation(error: SlurmError) -> str` for the
  enriched case. `_handle_error()` accepts an optional `annotation: str | None = None`
  parameter; when `None`, it falls back to `_build_annotation(error)`. This keeps the single
  call site clean while allowing the `SlurmError` branch to pass the enriched annotation. See
  `app/error_handler.py` lines 102-120.

- **Best-effort side-effect pattern** (ticket-014, ticket-015): Any call that communicates
  outward (annotation, metadata) is wrapped in `try/except Exception: pass`. Established in
  `_handle_error()` (annotation call, line 113-118) and `_send_timing_metadata()` in
  `app/utils/timing.py` (lines 75-80). This is now a codebase-wide convention: all ModelOps
  calls that are informational rather than controlling should be wrapped this way.

- **Decorator factory for cross-cutting instrumentation** (ticket-015): `time_command(command_name)
-> Callable` in `app/utils/timing.py` follows the same triple-nesting pattern as
  `handle_cli_errors`: factory returns decorator, decorator wraps function with
  `functools.wraps`. Catches `SystemExit` specifically (not bare `Exception`) and re-raises it,
  recording timing on both success and failure paths. See `app/utils/timing.py` lines 40-72.

- **Decorator co-location pattern for same-module instrumentation** (ticket-015): `time_command`
  was added to `app/utils/timing.py` alongside the pre-existing `time_and_log` context manager
  class, rather than creating a new module. The two share `time.perf_counter()` measurement but
  serve different interfaces (decorator vs. context manager). Co-location is correct because
  they are both timing utilities. See `app/utils/timing.py` lines 11-80.

- **`SlurmError` annotation enrichment pre-computed, not post-exit** (ticket-014): The
  `_build_slurm_annotation(error)` call happens before `_handle_error()` (which calls
  `sys.exit()`) in the `except SlurmError` branch. This is the only way to compute additional
  annotation content before the process exits, since code after `_handle_error()` is unreachable
  on the `SlurmError` path that includes `completion_info`. See `app/error_handler.py` lines
  58-78.

## Architectural Decisions

- **`@time_command` positioned between Click decorators and `@handle_cli_errors`**: The decorator
  stack in `app/cli.py` is `@click.command` -> `@click.argument/option` -> `@time_command` ->
  `@handle_cli_errors` -> function. This ordering means the timing decorator captures the full
  command lifecycle including Click argument resolution and error handler execution. The timing
  decorator catches `SystemExit` (raised by `sys.exit()` inside `handle_cli_errors`), records
  timing, and re-raises. If `@time_command` were placed inside (below) `@handle_cli_errors`,
  it would only time the command body and miss validation and error-handling overhead. See
  `app/cli.py` lines 45-51 (first command example).

- **`--verbose` flag deferred, not included**: The epic overview mentioned an optional
  `--verbose` flag. Ticket-015 explicitly excluded it, noting that adding `--verbose` requires
  changes to all 12 command signatures and is a separate concern. The timing and annotation
  features are complete without it. If `--verbose` is added in a future epic, it must be a
  dedicated ticket covering all 12 commands plus `app/cli.py` group-level option propagation.

- **`ModelOpsCommands` imported at module top in `app/utils/timing.py`**: The ticket's
  implementation guide warned about potential circular imports and suggested a lazy import as a
  fallback. The actual implementation imports `ModelOpsCommands` at the top of the module
  (`app/utils/timing.py` line 8). No circular import occurred because `app/utils/commands.py`
  does not import from `app/utils/timing.py`. This is consistent with how `app/error_handler.py`
  also imports `ModelOpsCommands` at module level.

- **`set_annotation` called exactly once per error path via `_handle_error`**: Rather than the
  ticket's implementation guide suggestion of calling `set_annotation` twice for `SlurmError`
  (once in `_handle_error`, once enriched in the except branch), the actual implementation
  passes the enriched annotation as a parameter to `_handle_error` via the `annotation=` kwarg.
  Only one `set_annotation` call fires per error path. This avoids ModelOps receiving two
  annotations and eliminates the "last value wins" ambiguity. See `app/error_handler.py` lines
  61-67.

## Files and Structures Created

- `app/error_handler.py` — Modified from Epic 03 state. Added `_build_annotation()` (line 123),
  `_build_slurm_annotation()` (line 128), optional `annotation: str | None = None` parameter to
  `_handle_error()` (line 107), and `set_annotation()` call inside `_handle_error()` (lines
  113-118). Also added annotation call to the bare-`Exception` branch (lines 90-93). Net addition
  ~34 lines.

- `app/utils/timing.py` — Added `time_command()` decorator factory (lines 40-72) and
  `_send_timing_metadata()` helper (lines 75-80) to the existing 33-line file. New imports:
  `functools`, `logging`, `from app.utils.commands import ModelOpsCommands`. File grew from
  ~33 to ~81 lines.

- `app/cli.py` — Added `from app.utils.timing import time_command` import (line 8) and
  `@time_command("command_name")` decorator to all 12 CLI commands. Net addition 13 lines
  (1 import + 12 decorators). Decorator ordering confirmed as per architectural decision above.

- `tests/unit/test_error_handler.py` — Added `TestAnnotationSending` class with 7 tests
  (lines 593-691). Covers: each CLIError subclass sends correct annotation, SlurmError
  enrichment with and without `completion_info`, bare exception sends annotation, annotation
  failure does not mask exit code, Click exceptions do not trigger annotation.

- `tests/unit/test_timing.py` — New file, 164 lines. Two test classes: `TestTimeCommand`
  (8 tests covering success/failure logging, metadata sending, SystemExit re-raise,
  failure isolation, functools.wraps, args passthrough) and `TestExistingTimeAndLog`
  (1 regression test verifying the pre-existing context manager is unmodified).

## Conventions Adopted

- **Annotation helpers are private and module-level, not inline**: `_build_annotation()` and
  `_build_slurm_annotation()` are private helpers defined at module level in
  `app/error_handler.py`, not inlined in the exception branches. This keeps `_handle_error()`
  readable and makes the annotation format testable in isolation. Consistent with the
  `_handle_error()` helper pattern established in Epic 03.

- **Timing metadata key is a string literal `"duration_seconds"`**: The key name is not a
  module-level constant in `app/utils/timing.py`. It appears only in `_send_timing_metadata()`
  at line 78. If additional metadata keys are added in future, introduce a `_METADATA_KEYS`
  module-level dict or constants at that point.

- **Test patching path for timing module is `app.utils.timing.ModelOpsCommands`**: Tests for
  `time_command` patch at `app.utils.timing.ModelOpsCommands`, not at
  `app.utils.commands.ModelOpsCommands`, because the timing module holds its own reference. See
  `tests/unit/test_timing.py` lines 41-43. This is consistent with patching conventions already
  established in `test_error_handler.py` (`app.error_handler.ModelOpsCommands`).

- **Regression test class for unmodified neighbours**: `TestExistingTimeAndLog` in
  `tests/unit/test_timing.py` verifies the pre-existing `time_and_log` class still works as a
  context manager. Use this pattern whenever a new feature is added to a module that already
  contains tested code: one regression class confirms the existing interface is intact.

- **`time_command` log level is INFO, not DEBUG**: Both "completed in" and "failed after" timing
  messages use `logger.info()`. This is consistent with the operational visibility goal — timing
  info is relevant to operators, not only to developers debugging. See `app/utils/timing.py`
  lines 61, 66.

## Surprises and Deviations

- **The git range provided for this epic (eab1aa6..HEAD) captured only one post-epic cleanup
  commit**: The actual epic-05 implementation (`app/error_handler.py` annotation additions,
  `app/utils/timing.py` decorator, `app/cli.py` decorator wiring, `tests/unit/test_timing.py`,
  `tests/unit/test_error_handler.py` annotation tests) all landed in the same commit as the
  epic-03 work (`44e0451`). The state file shows both tickets 014 and 015 as completed with
  scores recorded. This means epics 03 and 05 share a commit boundary — the implementation
  was likely batched rather than committed per-epic. Future plans should enforce one commit per
  epic completion to enable accurate range-based learning extraction.

- **`_handle_error` gained an optional parameter rather than a callback**: The ticket's
  implementation guide offered multiple approaches for handling `SlurmError` annotation
  enrichment (annotation override parameter, `build_annotation_fn` callback, calling
  `set_annotation` twice). The actual implementation chose the simplest: an `annotation:
str | None = None` parameter on `_handle_error()`. This is cleaner than a callback and
  avoids double-annotation. The parameter defaults to `None` meaning "use `_build_annotation`".
  See `app/error_handler.py` line 107.

- **`TestAnnotationSending` was appended to the existing test file rather than placed in a new
  file**: Ticket-014 specified adding a new `TestAnnotationSending` class to the existing
  `tests/unit/test_error_handler.py`. The implementation followed this exactly, appending the
  class at line 593. The file grew from 599 to 691 lines. This is consistent with the
  established pattern of keeping all error handler tests in one file.

- **The `--verbose` flag mentioned in the epic overview was never present in any ticket**: Neither
  ticket-014 nor ticket-015 included a `--verbose` flag, and ticket-015 explicitly noted it was
  out of scope. The epic overview described it as "optional". This is a planning gap — optional
  features mentioned in an epic overview but not assigned to any ticket silently disappear. Future
  epic overviews should either assign all mentioned features to tickets or explicitly list them
  in an "Out of Scope" or "Deferred" section.

## Recommendations for Future Epics

- **If a follow-on epic adds `--verbose`, use the `@click.pass_context` pattern to propagate it**:
  A group-level `--verbose` option stored in `click.Context.obj` avoids modifying all 12
  command signatures. Do not add `--verbose` as a per-command option — it would require changing
  all 12 decorators in `app/cli.py`. See Click's documentation on `pass_context` for the pattern.

- **Introduce `SlurmMonitoringError` to close the exit-code-99 gap for SLURM infrastructure
  failures**: `follow_submitted_job()` in `app/utils/scheduler.py` still raises bare
  `RuntimeError` for `squeue` failures, routing to exit code 99 via the bare `Exception`
  handler. Wrapping these as `SlurmError` would yield exit code 3 and trigger the structured
  SLURM log line with `completion_info`. This is the highest-priority structural gap remaining
  in the error handling chain. Target: `app/utils/scheduler.py` lines where `RuntimeError` is
  raised.

- **Enforce one-commit-per-epic to enable accurate learning extraction**: The git commit history
  shows epics 03 and 05 sharing a commit, making range-based analysis unreliable. Introduce a
  convention of a named commit (`chore: complete epic-NN`) at each epic boundary, consistent with
  the existing `feat: complete epic-04 test coverage` (commit `eab1aa6`) pattern.

- **Add mypy to CI before any future epic**: All 15 tickets scored `type_safety: 0.5` (neutral)
  because no mypy baseline existed. The `type: ignore[return-value]` suppression in
  `app/click_types.py` line 37 and the `_F = TypeVar` pattern in `app/error_handler.py` line 28
  are not verified at CI time. A mypy configuration targeting `app/` would resolve this gap and
  enable accurate type safety scores on future tickets.

- **Consider extracting `register_fake_model` fixture to `conftest.py`**: The singleton mutation
  pattern (`ModelFactory()._models[name] = mock; yield; del ...`) appears in three test files.
  Epic 04 learnings recommended extraction at the fourth use. If any future ticket adds a fourth
  test file touching `ModelFactory`, move the fixture to `tests/unit/conftest.py`.
