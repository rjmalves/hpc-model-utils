# Accumulated Learnings: cli-resilience plan (through Epic 03)

## Patterns Established

- **Two-tier validation pattern** (Epic 01): Semantic validation in `app/validation.py` (per-command) + `click.ParamType` subclasses in `app/click_types.py` (argument-parse time). Now fully wired into `app/cli.py` as of Epic 03 (ticket-010).
- **Lazy ModelFactory import inside validators** (Epic 01): Both `validate_model_name()` and `ModelNameType.convert()` import `ModelFactory` inside the function body. Required because model modules self-register at import time. Any future validator touching `ModelFactory` must follow this.
- **Module-level compiled regex** (Epic 01): `validate_queue_name()` uses `_QUEUE_NAME_RE = re.compile(...)` at module scope. Apply to any future regex validators.
- **Fail-fast validation** (Epic 01): Per-command validators stop at the first failure. Do not introduce error-accumulation patterns.
- **Decorator-based error dispatch** (Epic 03): All CLI error handling is centralised in `@handle_cli_errors(command_name)` in `app/error_handler.py`. Exception chain ordering is critical: Click exceptions re-raised first, then typed `CLIError` subclasses, then `CLIError` base, then bare `Exception`. Reversing the order swallows Click errors.
- **`S3Error` routes to `set_data_error()`, all others to `set_model_error()`** (Epic 03): Single asymmetry in the ModelOps signal mapping. Future infrastructure error classes must explicitly determine which signal they map to. See `app/error_handler.py` line 74.
- **`command_name` injected at handler time, not raise time** (Epic 03): `CLIError` subclasses are raised without `command_name`; the decorator sets it when empty. Callers inside model methods do not need to know the CLI command name. See `app/error_handler.py` lines 66-67.
- **`logger.error()` for expected, `logger.exception()` for unexpected** (Epic 03): Typed `CLIError` subclasses use `logger.error()` (no traceback). Bare `Exception` uses `logger.exception()` (with traceback). Prevents log flooding on routine validation failures.
- **Decomposed SLURM monitoring** (Epic 02): `follow_submitted_job()` replaced the fragile compound shell one-liner with three distinct Python helper functions. See `app/utils/scheduler.py` lines 191-244.
- **`deque(fh, maxlen=N)` for line-capped file reads** (Epic 02): `read_job_output_files()` caps at 10,000 lines using `collections.deque`. See `app/utils/scheduler.py` line 108.
- **Best-effort diagnostics return `None`, never raise** (Epic 02): `get_job_completion_info()` wraps its entire body in `try/except Exception` and returns `None` on failure. All post-completion diagnostic functions must follow this contract.
- **`@dataclass` for structured return types** (Epic 02): `JobCompletionInfo` and `JobOutputFiles` are module-level dataclasses with typed fields. `SlurmError` carries `JobCompletionInfo` as an optional field for structured log enrichment.

## Architectural Decisions

- **Decorator ordering in `app/cli.py`**: `@click.command` -> `@click.argument`/`@click.option` -> `@handle_cli_errors` -> function. Click resolves arguments first; the error handler wraps the resolved body. Swapping the last two levels breaks Click argument binding.
- **`Log.configure_logger()` stays inside the command body, before the decorator's try scope**: The decorator retrieves the already-configured logger via `logging.getLogger("hpc-model-utils")`. Moving `configure_logger()` into the decorator would make logging unavailable during argument resolution.
- **`ValidationError` does not inherit from `click.BadParameter`**: Two separate exit-code-2 paths coexist — Click's own formatter for argument-parse failures, `ValidationError` via the decorator for `ModelFactory` lookup failures. No shared base class needed.
- **`ModelFactory.factory()` ValueError wrapped locally at each call site**: 11 `try/except ValueError` blocks in `app/cli.py` wrap `ModelFactory().factory()` and re-raise as `ValidationError`. This is correct but repetitive. Changing `ModelFactory.factory()` to raise `ValidationError` directly would eliminate all 11 wrappers but is a breaking change requiring a dedicated ticket.
- **`click.BadParameter` as interim error type** (Epic 01, now partially superseded): Validation errors from Click type checks still produce `click.BadParameter`. Only `ModelFactory` lookup failures produce `ValidationError`. Epic 04 integration tests must handle both paths.
- **Post-completion read always executed, even if monitoring loop never ran** (Epic 02): `read_job_output_files()` and `get_job_completion_info()` are called unconditionally after the `while` loop exits.
- **`squeue` failure raises, `sacct` failure silently returns `None`** (Epic 02): Asymmetry is intentional. `squeue` failure during monitoring is fatal; `sacct` failure post-completion is degraded diagnostics.

## Files and Structures Created or Significantly Modified

- `app/errors.py` — `CLIError` base + 4 subclasses (`ValidationError`, `SlurmError`, `S3Error`, `ModelExecutionError`) + 6 exit code constants. `SlurmError` carries optional `JobCompletionInfo`. ~139 lines.
- `app/error_handler.py` — `handle_cli_errors(command_name)` decorator factory. TypeVar for type-safe wrapping. `_LOGGER_NAME` constant. ~119 lines.
- `app/cli.py` — All 12 commands now have `@handle_cli_errors`, `ModelNameType`/`S3PathType`/`PositiveIntType` argument types, per-command validator calls, and `ModelFactory` ValueError wrapping. No bare `except Exception` blocks.
- `app/utils/scheduler.py` — Rewritten `follow_submitted_job()`, `JobCompletionInfo` dataclass, `get_job_completion_info()`, `JobOutputFiles` dataclass, `read_job_output_files()` (Epics 02-03).
- `app/validation.py` — 6 primitives + 12 per-command validators (Epic 01).
- `app/click_types.py` — `ModelNameType`, `S3PathType`, `PositiveIntType` (Epic 01).
- `tests/unit/test_errors.py` — 338 lines, 7 test classes for error hierarchy.
- `tests/unit/test_error_handler.py` — 599 lines, 9 test classes for the decorator.
- `tests/unit/test_follow_submitted_job.py` — 33 tests for SLURM monitoring (Epic 02).
- `tests/unit/test_validation_per_command.py` — ~300 lines for per-command validators (Epic 01).

## Conventions Adopted

- Error class constructors use keyword-only arguments after `message`: `(self, message: str, *, command_name: str = "", detail: str = "", exit_code: int = DEFAULT)`. Prevents positional misuse.
- `_make_completion_info(**kwargs)` local helper pattern in test files: builds `JobCompletionInfo` with sensible defaults overridable via kwargs. Both `test_errors.py` and `test_error_handler.py` use this pattern.
- Decorator test structure: each exception type gets its own test class; each class tests ModelOps signal, exit code, and log call independently (not combined).
- `_make_raising_command(exc)` helper: constructs a decorated function raising a given exception. Avoids repeated boilerplate across 9 test classes.
- Validation primitives: `def validate_X(value: T, param_name: str = "default") -> None`, raise `click.BadParameter` on failure.
- Custom Click types: named `<ConceptName>Type`, `name` attribute uses `UPPER_SNAKE`.
- SLURM output file names (`stdout.modelops`, `stderr.modelops`) are local constants in the functions that use them; change in both `submit_job()` flags and `read_job_output_files()` defaults together.

## Surprises and Deviations

- **Epic 01 CLI wiring gap resolved inside Epic 03** (not via a separate ticket): ticket-010 completed the CLI wiring (validator calls + Click types) in the same pass as the error handler refactor. Epic 01 quality scores for tickets 003/004 were marked `completed` in the state file but the code was absent until Epic 03.
- **`SlurmMonitoringError` subclass not introduced** (Epic 03): `follow_submitted_job()` still raises bare `RuntimeError` for squeue failures; the decorator catches these as exit code 99. The Epic 02 recommendation was not acted on. This is the highest-priority gap going into Epic 05.
- **ticket-009 quality 0.725 (BELOW GATE)**: `scope_adherence: 0.5` and `lint_cleanliness: 0.5` due to the decorator and test files being larger than estimated (119 vs 70 lines, 599 vs 120 test lines). The over-delivery is positive (more test coverage) but scored as scope deviation.
- **`type_safety: 0.5` across all tickets in all three epics**: No `mypy`/`pyright` baseline exists. Scores are neutral throughout. Adding mypy to CI before Epic 04 would allow proper type safety scoring.
- **`test_delta: 0.0` for Epic 01 tickets 001/003/004** (persists): Tests for validation primitives and Click types still not written. ticket-011 owns this.
- **`submit_job()` walrus operator refactor** (Epic 02, not in any ticket): Latent `UnboundLocalError` risk fixed as a side effect. ticket-012 should add a `submit_job()` test.
- **`wait_cancelled_job()` untouched** (Epic 02, persists): Still uses compound shell one-liner; no tests. ticket-012 scope.

## Recommendations for Future Epics

- **Epic 04 ticket-013 CLI integration tests**: Use `click.testing.CliRunner`. `sys.exit()` calls from the decorator are captured as `result.exit_code`. Test both exit-code-2 paths: Click type rejection (argument parse) and `ValidationError` from `ModelFactory`. Import the `cli` group from `app/cli.py` directly. Reuse the `register_fake_model` fixture from `tests/unit/test_validation_per_command.py`.
- **Epic 04 ticket-011**: Focus on validation primitives (`app/validation.py`) and Click types (`app/click_types.py`) — neither has a dedicated test file. Do not duplicate the already-thorough `test_error_handler.py` tests.
- **Epic 04 ticket-012**: Must cover `wait_cancelled_job()` and `cancel_submitted_job()` (untested). Add a test confirming `cancel_run` exits with code 99 when `wait_cancelled_job()` raises `RuntimeError` (current fallback path via the decorator).
- **Epic 05 ticket-014 annotation injection**: Modify `app/error_handler.py`, not individual commands. The handler already has error category, command name, and exit code; add `ModelOpsCommands.set_annotation(...)` alongside existing `set_model_error()` calls in each branch.
- **Epic 05 ticket-015 timing decorator ordering**: Timing must be the outermost decorator (above `@handle_cli_errors`). Order: `@click.command` -> `@time_command` -> `@handle_cli_errors` -> function.
- **Introduce `SlurmError` wrapping in `follow_submitted_job()`**: Changing `RuntimeError` raises in `follow_submitted_job()` to `SlurmError` raises would give exit code 3 (not 99) for SLURM infrastructure failures and trigger the structured `completion_info` log block in the handler. This is the highest-value single change not covered by any existing ticket.
