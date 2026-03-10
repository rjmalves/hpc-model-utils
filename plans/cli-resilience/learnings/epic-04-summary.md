# Accumulated Learnings: cli-resilience plan (through Epic 04)

## Patterns Established

- **Two-tier validation pattern** (Epic 01): Semantic validation in `app/validation.py` (per-command) + `click.ParamType` subclasses in `app/click_types.py` (argument-parse time). Fully wired into `app/cli.py` as of Epic 03 (ticket-010).
- **Lazy ModelFactory import inside validators** (Epic 01): Both `validate_model_name()` and `ModelNameType.convert()` import `ModelFactory` inside the function body. Required because model modules self-register at import time. Any future validator touching `ModelFactory` must follow this.
- **Module-level compiled regex** (Epic 01): `validate_queue_name()` uses `_QUEUE_NAME_RE = re.compile(...)` at module scope. Apply to any future regex validators.
- **Fail-fast validation** (Epic 01): Per-command validators stop at the first failure. Do not introduce error-accumulation patterns.
- **Decorator-based error dispatch** (Epic 03): All CLI error handling is centralised in `@handle_cli_errors(command_name)` in `app/error_handler.py`. Exception chain ordering is critical: Click exceptions re-raised first, then typed `CLIError` subclasses, then bare `Exception`. Reversing the order swallows Click errors.
- **`S3Error` routes to `set_data_error()`, all others to `set_model_error()`** (Epic 03): Single asymmetry in the ModelOps signal mapping. See `app/error_handler.py` line 74.
- **`logger.error()` for expected, `logger.exception()` for unexpected** (Epic 03): Typed `CLIError` subclasses use `logger.error()` (no traceback). Bare `Exception` uses `logger.exception()` (with traceback).
- **Decomposed SLURM monitoring** (Epic 02): `follow_submitted_job()` replaced the fragile compound shell one-liner with distinct Python helper functions. See `app/utils/scheduler.py` lines 191-244.
- **`deque(fh, maxlen=N)` for line-capped file reads** (Epic 02): `read_job_output_files()` caps at 10,000 lines using `collections.deque`. See `app/utils/scheduler.py` line 108.
- **Best-effort diagnostics return `None`, never raise** (Epic 02): `get_job_completion_info()` wraps its entire body in `try/except Exception` and returns `None` on failure. All post-completion diagnostic functions must follow this contract.
- **`@dataclass` for structured return types** (Epic 02): `JobCompletionInfo` and `JobOutputFiles` are module-level dataclasses with typed fields. `SlurmError` carries `JobCompletionInfo` as an optional field.
- **Singleton fixture via direct dict mutation** (Epic 04): `autouse` fixtures in all test files that touch model names call `ModelFactory()`, write into `_models`, yield, then `pop` in teardown. See `tests/unit/test_validation_primitives.py` (line 28-33), `tests/unit/test_click_types.py` (line 22-27), `tests/unit/test_cli_integration.py` (line 52-57).
- **`conftest.py` owns `sys.modules` stubs** (Epic 04): `tests/unit/conftest.py` installs `MagicMock()` entries for all optional dependencies (`pandas`, `inewave`, `idecomp`, `boto3`, `cfinterface`) before any `app.*` import. All unit tests that import `app.cli` benefit automatically.
- **Module-level mock constants in SLURM tests** (Epic 04): All `run_in_terminal` response tuples are defined as `_UPPER_SNAKE` module-level constants in `tests/unit/test_follow_submitted_job.py`. Makes `side_effect=[...]` lists readable without inline literals.
- **Command list inspection via `mock.call_args[0][0]`** (Epic 04): Tests for `submit_job()` in `tests/unit/test_follow_submitted_job.py` (lines 628-664) assert flag presence using `any(... in arg for arg in command_list)` rather than exact list equality, accommodating the empty-string padding in optional flags.
- **`patch.dict("sys.modules", {...: None})` for `ImportError` fallback testing** (Epic 04): Used in `TestModelNameType.test_import_error_fallback_returns_value_unchanged` (`tests/unit/test_click_types.py` line 58-63) to simulate a lazy-import `ImportError` without uninstalling packages.

## Architectural Decisions

- **Decorator ordering in `app/cli.py`**: `@click.command` -> `@click.argument`/`@click.option` -> `@handle_cli_errors` -> function. Swapping the last two levels breaks Click argument binding.
- **`Log.configure_logger()` stays inside the command body, before the decorator's try scope**: The decorator retrieves the already-configured logger via `logging.getLogger("hpc-model-utils")`. Moving `configure_logger()` into the decorator would make logging unavailable during argument resolution.
- **`ValidationError` does not inherit from `click.BadParameter`**: Two separate exit-code-2 paths coexist — Click's own formatter for argument-parse failures, `ValidationError` via the decorator for `ModelFactory` lookup failures.
- **`ModelFactory.factory()` ValueError wrapped locally at each call site**: 11 `try/except ValueError` blocks in `app/cli.py` re-raise as `ValidationError`. Changing `ModelFactory.factory()` to raise `ValidationError` directly would eliminate all 11 wrappers but is a breaking change requiring a dedicated ticket.
- **Post-completion read always executed, even if monitoring loop never ran** (Epic 02): `read_job_output_files()` and `get_job_completion_info()` called unconditionally after the `while` loop exits.
- **`squeue` failure raises, `sacct` failure silently returns `None`** (Epic 02): `squeue` failure during monitoring is fatal; `sacct` failure post-completion is degraded diagnostics.
- **SLURM tests kept in one file** (Epic 04): All 57 SLURM scheduler tests (11 classes) reside in `tests/unit/test_follow_submitted_job.py`. All functions come from `app/utils/scheduler.py`; splitting would fragment related mock constants.
- **Integration tests in `tests/unit/`** (Epic 04): `test_cli_integration.py` lives in `tests/unit/` despite being integration-level, because `CliRunner` requires no real external resources. No `tests/integration/` directory exists.
- **`ModelFactory.factory` patched, not `ModelFactory.__init__`** (Epic 04): Patching `app.cli.ModelFactory.factory` preserves singleton semantics and the `_models` registry populated by fixtures. See `tests/unit/test_cli_integration.py` lines 134, 157.

## Files and Structures

- `app/errors.py` — `CLIError` base + 4 subclasses + 6 exit code constants. `SlurmError` carries optional `JobCompletionInfo`. ~139 lines.
- `app/error_handler.py` — `handle_cli_errors(command_name)` decorator factory. ~119 lines.
- `app/cli.py` — All 12 commands with `@handle_cli_errors`, Click types, validator calls, and `ModelFactory` ValueError wrapping.
- `app/utils/scheduler.py` — Rewritten `follow_submitted_job()`, `JobCompletionInfo`, `get_job_completion_info()`, `JobOutputFiles`, `read_job_output_files()`.
- `app/validation.py` — 6 primitives + 12 per-command validators.
- `app/click_types.py` — `ModelNameType`, `S3PathType`, `PositiveIntType`.
- `tests/unit/conftest.py` — 29-line `sys.modules` stub block for all unit tests. Created in Epic 04 as an unplanned deliverable.
- `tests/unit/test_validation_primitives.py` — 243 lines, 6 test classes, all 6 primitives covered (Epic 04).
- `tests/unit/test_click_types.py` — 177 lines, 3 test classes, all 3 Click types covered (Epic 04).
- `tests/unit/test_follow_submitted_job.py` — Extended to 57 tests (11 classes) by Epic 04 additions for `submit_job`, `cancel_submitted_job`, `wait_cancelled_job`.
- `tests/unit/test_cli_integration.py` — 165 lines, 3 test classes, `CliRunner`-based integration tests (Epic 04).
- `tests/unit/test_errors.py` — 338 lines, 7 test classes (Epic 03).
- `tests/unit/test_error_handler.py` — 599 lines, 9 test classes (Epic 03).
- `tests/unit/test_validation_per_command.py` — ~300 lines for per-command validators (Epic 01).

## Conventions Adopted

- Error class constructors use keyword-only arguments after `message`. Prevents positional misuse.
- `_make_completion_info(**kwargs)` local helper in test files: builds `JobCompletionInfo` with sensible defaults. Used in `test_errors.py` and `test_error_handler.py`.
- `_make_raising_command(exc)` helper: constructs a decorated function raising a given exception, avoiding repeated boilerplate across test classes.
- Unique `FAKE_MODEL` string constants per test file to avoid cross-file singleton collisions.
- Test method names follow `test_<condition>_<outcome>` (e.g., `test_missing_s3_prefix_raises_bad_parameter`).
- `param_hint` verified in a separate test method, not combined into the `match=` string of `pytest.raises`.
- `CliRunner` assertions check `result.exit_code` as the primary signal; `result.output` checked only for message content. Never use `pytest.raises(SystemExit)` in integration tests.
- Validation primitives: `def validate_X(value: T, param_name: str = "default") -> None`, raise `click.BadParameter`.
- Custom Click types: named `<ConceptName>Type`, `name` attribute uses `UPPER_SNAKE`.
- SLURM output file constants (`stdout.modelops`, `stderr.modelops`) are local variables in the functions that use them, not in `constants.py`.

## Persistent Gaps and Deviations

- **`SlurmMonitoringError` not introduced**: `follow_submitted_job()` still raises bare `RuntimeError` for `squeue` failures; the decorator routes these to exit code 99. Epic 03 recommendation was not acted on. Highest-priority gap for Epic 05.
- **`cancel_run` + `wait_cancelled_job` -> exit code 99 path not integration-tested**: The cross-layer test routing `RuntimeError` from `wait_cancelled_job` through `@handle_cli_errors` to exit code 99 was recommended in Epic 03 and not implemented in Epic 04. Still missing.
- **`submit_job()` inserts empty strings in command list**: `app/utils/scheduler.py` (lines 148-150) inserts `""` for absent optional flags. Tests work around with `any(... in arg ...)`. Refactor ticket needed before Epic 05.
- **No mypy baseline**: `type_safety: 0.5` (neutral) across all tickets in all four epics. Adding mypy to CI before Epic 05 would enable proper type safety scoring.
- **`type: ignore[return-value]` in `app/click_types.py` line 37**: Remnant of the `ModelNameType.convert()` non-string branch having no clean return path after `self.fail()`. Mypy would flag this; currently suppressed.

## Recommendations for Epic 05

- **Add `tests/unit/conftest.py` to Key Files in any test ticket**: The conftest stub is load-bearing for all unit tests importing `app.cli`. Its absence or corruption silently breaks test isolation.
- **Reuse `TestHappyPath` fixture pattern for Epic 05 test additions**: Patch `app.cli.ModelFactory.factory` to return a `MagicMock` model and patch `app.cli.Log.configure_logger`. See `tests/unit/test_cli_integration.py` lines 147-164.
- **Add the `cancel_run` exit-code-99 integration test in the first Epic 05 ticket that modifies `cancel_run` behaviour**.
- **Epic 05 timing decorator must be the outermost decorator**: Order is `@click.command` -> `@time_command` -> `@handle_cli_errors` -> function. Timing must capture even validation-failure paths.
- **Annotation injection (ticket-014) belongs in `app/error_handler.py`**: Add `ModelOpsCommands.set_annotation(...)` alongside existing `set_model_error()` calls. No changes to individual command functions needed.
- **Extract `register_fake_model` into `conftest.py` if a fourth test file needs it**: Currently duplicated in three test files; extract to `conftest.py` as a function-scoped fixture at that threshold.
