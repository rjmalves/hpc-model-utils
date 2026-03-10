# Accumulated Learnings: cli-resilience plan (through Epic 05)

## Patterns Established

- **Two-tier validation pattern** (Epic 01): Semantic validation in `app/validation.py` + `click.ParamType` subclasses in `app/click_types.py`. Fully wired into `app/cli.py` via `@handle_cli_errors` as of Epic 03.
- **Lazy ModelFactory import inside validators** (Epic 01): `validate_model_name()` and `ModelNameType.convert()` import `ModelFactory` inside the function body. Required due to self-registration at import time. All future validators touching `ModelFactory` must follow this.
- **Module-level compiled regex** (Epic 01): `validate_queue_name()` uses `_QUEUE_NAME_RE = re.compile(...)` at module scope. Apply to all future regex validators.
- **Fail-fast validation** (Epic 01): Per-command validators stop at the first failure. Do not introduce error-accumulation patterns.
- **Decorator-based error dispatch** (Epics 03/05): All CLI error handling is centralised in `@handle_cli_errors(command_name)` in `app/error_handler.py`. Exception chain ordering is critical: Click exceptions re-raised first, typed `CLIError` subclasses second, bare `Exception` last.
- **`S3Error` routes to `set_data_error()`, all others to `set_model_error()`** (Epic 03): Single asymmetry in the ModelOps signal mapping. See `app/error_handler.py` lines 56-57.
- **`logger.error()` for expected, `logger.exception()` for unexpected** (Epic 03): Typed `CLIError` subclasses use `logger.error()`. Bare `Exception` uses `logger.exception()` with full traceback.
- **Decomposed SLURM monitoring** (Epic 02): `follow_submitted_job()` replaced with distinct Python helpers. See `app/utils/scheduler.py` lines 191-244.
- **`deque(fh, maxlen=N)` for line-capped file reads** (Epic 02): `read_job_output_files()` caps at 10,000 lines using `collections.deque`. See `app/utils/scheduler.py` line 108.
- **Best-effort diagnostics return `None`, never raise** (Epic 02): `get_job_completion_info()` wraps its body in `try/except Exception`, returns `None` on failure. All post-completion diagnostics must follow this.
- **Best-effort side-effect pattern** (Epics 02/05): All outbound-informational ModelOps calls (annotation, metadata) are wrapped in `try/except Exception: pass`. See `app/error_handler.py` lines 113-118 and `app/utils/timing.py` lines 75-80.
- **`_build_annotation` + optional override parameter pattern** (Epic 05): Annotation helpers `_build_annotation()` and `_build_slurm_annotation()` are private, module-level in `app/error_handler.py`. `_handle_error()` accepts `annotation: str | None = None`; `None` falls back to `_build_annotation(error)`. The `SlurmError` branch passes the enriched annotation. See `app/error_handler.py` lines 102-135.
- **Decorator factory for cross-cutting instrumentation** (Epic 05): `time_command(command_name)` in `app/utils/timing.py` follows the same triple-nesting pattern as `handle_cli_errors`. Catches `SystemExit` specifically (not bare `Exception`) and re-raises it. See `app/utils/timing.py` lines 40-72.
- **Singleton fixture via direct dict mutation** (Epic 04): `autouse` fixtures calling `ModelFactory()._models[name] = mock`, yield, then `pop`. See `tests/unit/test_validation_primitives.py` lines 28-33.
- **`conftest.py` owns `sys.modules` stubs** (Epic 04): `tests/unit/conftest.py` installs `MagicMock()` for all optional dependencies before any `app.*` import.
- **Module-level mock constants in SLURM tests** (Epic 04): All `run_in_terminal` response tuples defined as `_UPPER_SNAKE` constants in `tests/unit/test_follow_submitted_job.py`.
- **Regression test class for unmodified neighbours** (Epic 05): `TestExistingTimeAndLog` in `tests/unit/test_timing.py` verifies `time_and_log` is unmodified. Use this pattern whenever a new feature is added to a module containing already-tested code.

## Architectural Decisions

- **Full decorator stack in `app/cli.py`**: `@click.command` -> `@click.argument/option` -> `@time_command` -> `@handle_cli_errors` -> function. `@time_command` is outside `@handle_cli_errors` so it captures the full lifecycle including `sys.exit()` as `SystemExit`. See `app/cli.py` lines 45-51.
- **`Log.configure_logger()` stays inside the command body, before the decorator's try scope**: The decorator retrieves the already-configured logger via `logging.getLogger("hpc-model-utils")`.
- **`ValidationError` does not inherit from `click.BadParameter`**: Two separate exit-code-2 paths coexist. Click formats argument-parse failures; the decorator handles `ModelFactory` lookup failures.
- **`ModelOpsCommands` imported at module top in `app/utils/timing.py`**: No circular import occurred. Consistent with `app/error_handler.py`. See `app/utils/timing.py` line 8.
- **`set_annotation` called exactly once per error path**: `SlurmError` annotation is pre-computed via `_build_slurm_annotation()` before calling `_handle_error()` (which calls `sys.exit()`). No double-annotation. See `app/error_handler.py` lines 61-67.
- **SLURM tests kept in one file** (Epic 04): All 57 SLURM tests in `tests/unit/test_follow_submitted_job.py`.
- **Integration tests in `tests/unit/`** (Epic 04): `test_cli_integration.py` lives in `tests/unit/`; no `tests/integration/` directory exists.

## Files and Structures

- `app/errors.py` — `CLIError` base + 4 subclasses + 6 exit code constants. `SlurmError` carries optional `JobCompletionInfo`. ~139 lines.
- `app/error_handler.py` — `handle_cli_errors()` decorator + `_handle_error()` + `_build_annotation()` + `_build_slurm_annotation()`. ~136 lines after Epic 05 additions.
- `app/utils/timing.py` — `time_and_log` context manager (pre-existing) + `time_command()` decorator factory + `_send_timing_metadata()` helper. ~81 lines after Epic 05.
- `app/cli.py` — All 12 commands with `@time_command` + `@handle_cli_errors`, Click types, validator calls, and `ModelFactory` ValueError wrapping.
- `app/utils/scheduler.py` — Rewritten `follow_submitted_job()`, `JobCompletionInfo`, `get_job_completion_info()`, `JobOutputFiles`, `read_job_output_files()`.
- `app/validation.py` — 6 primitives + 12 per-command validators.
- `app/click_types.py` — `ModelNameType`, `S3PathType`, `PositiveIntType`.
- `tests/unit/conftest.py` — `sys.modules` stub block for all unit tests.
- `tests/unit/test_error_handler.py` — 691 lines, 10 test classes including new `TestAnnotationSending` (Epic 05, 7 tests).
- `tests/unit/test_timing.py` — 164 lines, 2 test classes: `TestTimeCommand` (8 tests) + `TestExistingTimeAndLog` (1 regression test). Created Epic 05.
- `tests/unit/test_follow_submitted_job.py` — 57 tests, 11 classes.
- `tests/unit/test_cli_integration.py` — 165 lines, 3 test classes, `CliRunner`-based.
- `tests/unit/test_errors.py` — 338 lines, 7 test classes.

## Conventions Adopted

- Error class constructors use keyword-only arguments after `message`. All five classes in `app/errors.py`.
- `_make_completion_info(**kwargs)` helper defined locally in test files that need `JobCompletionInfo` with defaults. See `tests/unit/test_error_handler.py` line 26.
- `_make_raising_command(exc)` helper: used in `test_error_handler.py`. Adapt to `_make_raising_command(exit_code)` for timing tests (`tests/unit/test_timing.py` line 22).
- Test method names follow `test_<condition>_<outcome>`.
- Patch timing module at `app.utils.timing.ModelOpsCommands`, not `app.utils.commands.ModelOpsCommands`.
- Annotation helpers are private and module-level, not inlined in exception branches.
- `time_command` log level is INFO for both success and failure paths.
- Timing metadata key is the string literal `"duration_seconds"` (no constant). Add constants if more metadata keys are introduced.

## Persistent Gaps and Deviations

- **`SlurmMonitoringError` not introduced**: `follow_submitted_job()` still raises bare `RuntimeError` for `squeue` failures; routes to exit code 99. Highest-priority structural gap. Target: `app/utils/scheduler.py` lines where `RuntimeError` is raised.
- **`cancel_run` + `wait_cancelled_job` -> exit code 99 path not integration-tested**: Still missing after Epics 03, 04, 05.
- **No mypy baseline**: All 15 tickets scored `type_safety: 0.5` (neutral). `type: ignore[return-value]` in `app/click_types.py` line 37 is unverified at CI time.
- **`ModelFactory.factory()` ValueError wrapped at 11 call sites in `app/cli.py`**: Future ticket should change `ModelFactory.factory()` to raise `ValidationError` directly and eliminate the wrappers.
- **`--verbose` flag mentioned in epic-05 overview never assigned to any ticket**: A planning gap — optional features in an epic overview must either be assigned or explicitly deferred.
- **Epics 03 and 05 share a commit boundary**: Implementation was batched into commit `44e0451`. Future plans should enforce one named commit per epic (`feat: complete epic-NN`) for accurate range-based extraction.

## Recommendations for Next Work

- **Introduce `SlurmMonitoringError`** in `app/errors.py` and update `app/utils/scheduler.py` to raise it instead of bare `RuntimeError`. Update `app/error_handler.py` exception chain (before `CLIError`) to route it to exit code 3.
- **Add the `cancel_run` exit-code-99 integration test** in any ticket that modifies `cancel_run` or `wait_cancelled_job`.
- **Add mypy to CI** with `app/` as the target. Fix `type: ignore[return-value]` in `app/click_types.py` line 37 in the same ticket.
- **If `--verbose` is planned**, use `@click.pass_context` with a group-level option rather than per-command flags. Do not modify all 12 command signatures individually.
- **Extract `register_fake_model` fixture to `conftest.py`** when a fourth test file needs it.
