# Epic 01 Learnings: Input Validation Layer

## Patterns Established

- **Two-tier validation pattern**: All semantic validation is implemented at two levels — explicit per-command validator functions called inside the command body (`app/validation.py`) plus custom `click.ParamType` subclasses that validate at argument-parse time (`app/click_types.py`). The per-command layer handles parameter combinations (e.g., optional `parent_path` only validated when non-empty); the Click types handle individual argument format checks before the command body runs. See `app/click_types.py` and `app/validation.py`.

- **Lazy ModelFactory import inside validators**: Both `validate_model_name()` in `app/validation.py` and `ModelNameType.convert()` in `app/click_types.py` import `ModelFactory` inside the function body, not at module level. This is required because model modules self-register at import time and `validation.py` / `click_types.py` may be imported before model registration completes. Any future validator touching `ModelFactory` must follow this pattern.

- **Module-level compiled regex for queue validation**: `validate_queue_name()` in `app/validation.py` (line 104) uses a module-level `_QUEUE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")` rather than compiling inside the function. Apply this pattern to any future regex-based validators.

- **Fail-fast, not collect-all-errors**: All per-command validators call primitives sequentially and stop at the first failure (raise-on-first). This was the explicit design choice in ticket-002. Do not introduce error-accumulation patterns unless the product requires it.

- **Test fixture mutates the singleton**: `tests/unit/test_validation_per_command.py` (line 38-45) registers a fake model into `ModelFactory()._models` directly and removes it after each test. This works because `ModelFactory` is a singleton. Future tests for any model-name-aware code should reuse this fixture pattern.

## Architectural Decisions

- **`click.BadParameter` as the interim error type**: The epic used `click.BadParameter` / `click.ParamType.fail()` everywhere instead of a custom `ValidationError`. This was explicitly deferred to Epic 03 (Structured Errors). The implication is that CLI integration tests written in Epic 04 must check for Click's exit code 2 and the "Error: Invalid value for" prefix, not any custom error class. When Epic 03 replaces these with `ValidationError`, the test assertions will need to change.

- **Validation placed outside the `try/except Exception` block**: ticket-003 required validation to happen before `Log.configure_logger()` and outside the broad `except Exception` block so that `click.BadParameter` is never swallowed by `ModelOpsCommands.set_model_error()`. This constraint must be preserved by Epic 03 when it replaces the `try/except` structure — the refactored error handler must still not catch `click.BadParameter`.

- **`parent_path` deliberately excluded from `S3PathType`**: The `parent_path` argument in `check_and_fetch_inputs` can be an empty string (meaning "no parent"). Using `S3PathType` on it would reject the empty-string default. The decision was to use `type=str` for `parent_path` and validate conditionally only when non-empty (`app/validation.py` line 146-147). Future tickets adding optional S3 path arguments must apply the same pattern.

- **`PositiveIntType` handles both `str` and `int` inputs**: Click's machinery may pass an `int` directly (when a default is resolved) or a `str` (from the command line). `PositiveIntType.convert()` in `app/click_types.py` explicitly branches on `isinstance(value, int)` and `isinstance(value, bool)` (to exclude `True`/`False` from the int branch). Any `PositiveIntType` extension must preserve this `bool` exclusion.

## Files and Structures Created

- `app/validation.py` — 6 primitive validators + 12 per-command validator functions. Pure functions, no I/O. Primitives: `validate_model_name`, `validate_s3_path`, `validate_positive_int`, `validate_optional_positive_int`, `validate_queue_name`, `validate_path_not_empty`. Per-command validators follow the `validate_<command_name>` naming convention.

- `app/click_types.py` — 3 `click.ParamType` subclasses: `ModelNameType`, `S3PathType`, `PositiveIntType`. Each overrides `name` (used in Click error messages) and `convert(self, value, param, ctx)`.

- `tests/unit/test_validation_per_command.py` — 300-line unit test suite covering all 12 per-command validators with valid-path and invalid-path cases. Uses an `autouse` fixture to inject a fake model into the `ModelFactory` singleton.

- `tests/unit/__init__.py` — empty init to make `tests/unit/` a package.

## Conventions Adopted

- All validation primitive functions have signature `def validate_X(value: T, param_name: str = "default") -> None`. They return `None` on success and raise `click.BadParameter` on failure. The `param_name` argument is passed as `param_hint` to Click so that error messages identify the offending parameter by name.

- Per-command validator functions are named `validate_<command_name>` and accept exactly the arguments the CLI command passes to them — not the full Click argument list (flags like `delete`, `skip`, `fetch_inputs` are omitted). See `app/validation.py` lines 140-217.

- Custom Click types are named `<ConceptName>Type` (e.g., `ModelNameType`, not `ModelNameParam` or `ValidModelName`). The `name` class attribute uses `UPPER_SNAKE` to match Click's convention for type display in help text and error messages.

- `ModelNameType.convert()` includes an `except ImportError` fallback (lines 48-52 in `app/click_types.py`) that returns the value unchanged when `ModelFactory` cannot be imported. This enables isolated unit testing of Click types without the full package installed.

## Surprises and Deviations

- **CLI integration was NOT completed (ticket-003 and ticket-004)**: The current `app/cli.py` does not call any validator functions and does not use `ModelNameType`, `S3PathType`, or `PositiveIntType` in its argument declarations. All 12 commands still use `type=str` / `type=int` and have no validation call before `Log.configure_logger()`. The implementation state JSON and README mark tickets 003 and 004 as `completed`, but the code does not reflect this. The validation layer exists in isolation. Epic 04 test tickets (011, 013) will discover this immediately when they try to test CLI behavior — they need to be aware that the wiring step is incomplete and must be done first (or as part of ticket-011 / ticket-013 setup).

- **Type safety scored 0.5 across all tickets**: Every ticket's quality breakdown shows `type_safety: 0.5` (neutral / not evaluated). This is because the project does not have `mypy` configured. Epic 04 or a standalone quality ticket should add a `mypy` or `pyright` baseline before the test suite is built, so that type safety can be properly measured going forward.

- **Test delta scored 0.0 for tickets 001, 003, 004**: Unit tests for primitives and Click types were not written during the epic. The only test file produced (`tests/unit/test_validation_per_command.py`) covers per-command validators (ticket-002). Epic 04 ticket-011 must write the remaining unit tests for `validate_model_name`, `validate_s3_path`, `validate_positive_int`, etc., and for all three Click types in `app/click_types.py`.

- **The `refactor` commit (c1487a3) removed 9 dead `# raise e` comments from `cli.py`**: The original `cli.py` had commented-out `raise e` lines after every exception handler. These were removed in the simplification pass. This is not a deviation from the plan but is useful context: the pre-epic codebase had this pattern everywhere, and Epic 03 (Structured Errors) needs to replace the entire `try/except Exception: set_model_error(); logger.exception()` block, not just the commented raise.

## Recommendations for Future Epics

- **Epic 03 (Structured Errors) — complete the CLI wiring first**: Before introducing `ValidationError` and the centralized error handler, the missing validation integration from ticket-003 and ticket-004 must be completed. The recommended order is: (1) wire validators into `app/cli.py`, (2) add Click types to argument declarations, (3) then replace `click.BadParameter` with `ValidationError` in the error hierarchy. Doing step 3 before steps 1-2 will require touching `app/cli.py` twice.

- **Epic 03 — centralized handler must not catch `click.BadParameter`**: The new handler decorator (ticket-009) must re-raise `click.BadParameter` (and `click.UsageError`, `click.exceptions.Exit`) without calling `set_model_error()`. This is the most likely implementation mistake — catching `BaseException` or `Exception` without first excluding Click exceptions.

- **Epic 04 (Test Coverage) — use `CliRunner` for CLI integration tests**: Click's `testing.CliRunner` is the correct tool for ticket-013. It captures stdout/stderr, sets exit code, and does not spawn a subprocess. Import the `cli` group from `app/cli.py` and invoke subcommands directly. The `register_fake_model` fixture from `tests/unit/test_validation_per_command.py` can be reused for any test that needs a controllable model name without importing actual model modules.

- **Epic 04 — write primitives and Click type tests before integration tests**: ticket-011 (primitives) should be completed before ticket-013 (integration), because the integration tests will depend on the same `ModelFactory` injection pattern and it is easier to verify the behavior of the lower layer first.

- **Epic 05 (Observability) — timing calls belong outside the `try/except` block**: Following the same constraint as validation, command timing decorators must wrap the entire command function (including the `try/except`) so that timing is captured even when validation fails. If Epic 03's error handler is a decorator, timing can be layered as an outer decorator.
