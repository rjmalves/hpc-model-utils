# Epic 04 Learnings: Test Coverage

## Patterns Established

- **Singleton fixture via direct dict mutation**: All three test files that touch `ModelFactory` use the same `autouse` fixture pattern — call `ModelFactory()`, write into `_models`, yield, then `pop` in teardown. Source files: `tests/unit/test_validation_primitives.py` (line 28-33), `tests/unit/test_click_types.py` (line 22-27), `tests/unit/test_cli_integration.py` (line 52-57). Future tests for any validator or command that resolves model names should copy this fixture verbatim.

- **`patch.dict("sys.modules", {...: None})` for `ImportError` fallback testing**: `TestModelNameType.test_import_error_fallback_returns_value_unchanged` in `tests/unit/test_click_types.py` (line 58-63) uses `patch.dict` with `None` as the value to simulate an `ImportError` from a lazy import. This is the idiomatic approach for testing `try/except ImportError` branches without uninstalling packages. Apply to any future lazy-import fallback path.

- **Module-level stub block in test files that import `app.cli`**: `test_cli_integration.py` (lines 17-40) installs `MagicMock()` entries into `sys.modules` for every optional dependency (`pandas`, `inewave`, `idecomp`, `boto3`, `cfinterface`, etc.) before importing `app.cli`. This was later extracted to `tests/unit/conftest.py` (lines 6-29) so that the stubs are available to all unit tests automatically.

- **`conftest.py` as the canonical stub injection point**: `tests/unit/conftest.py` was created during the simplification pass (commit `49f529e`) and now owns all `sys.modules` stub setup. Any new unit test that imports `app.cli` or any `app.adapter.*` module automatically benefits from the stubs without needing its own setup block.

- **`setup_method` for per-instance type instantiation in Click type tests**: `TestModelNameType`, `TestS3PathType`, and `TestPositiveIntType` in `tests/unit/test_click_types.py` (lines 31-32, 70-71, 114-115) use `setup_method` to create a fresh type instance per test. This avoids shared state between tests in the same class.

- **Constant blocks for mock return values at module scope**: `tests/unit/test_follow_submitted_job.py` (lines 17-44 and 584-591) defines all `run_in_terminal` response tuples and `JobOutputFiles` objects as module-level constants. Each constant name is prefixed with `_` and uses `UPPER_SNAKE` (e.g., `_SBATCH_SUCCESS`, `_SCANCEL_FAILURE`). This pattern makes side-effect sequences in `patch(side_effect=[...])` calls readable without inline literals.

- **Command list inspection via `mock.call_args[0][0]`**: `TestSubmitJob` in `tests/unit/test_follow_submitted_job.py` (lines 628-664) captures the command list passed to `run_in_terminal` by reading `mock_rit.call_args[0][0]` and asserting element presence with `any(... in arg for arg in command_list)`. This avoids brittle exact-length assertions when the function under test inserts empty strings for absent optional flags.

- **`autouse` fixtures for ModelOps patching**: `test_cli_integration.py` uses an `autouse=True` fixture `patch_modelops_commands` (lines 60-67) to disable `ModelOpsCommands.set_model_error` and `set_data_error` for every test in the file. This prevents side effects (file writes, shell calls) from leaking out during integration test runs.

## Architectural Decisions

- **SLURM tests kept in one file, not split**: ticket-012 appended `TestSubmitJob`, `TestCancelSubmittedJob`, and `TestWaitCancelledJob` to the existing `tests/unit/test_follow_submitted_job.py` rather than creating a new file. All SLURM scheduler tests (11 classes, 57 tests total) now reside in a single file. Rationale: all functions come from the same module (`app/utils/scheduler.py`); splitting would fragment related mock setup constants.

- **Integration tests placed in `tests/unit/` not `tests/integration/`**: The ticket deliberately placed `test_cli_integration.py` inside `tests/unit/` because the tests use `CliRunner` (no subprocess, no real I/O), fitting the existing test structure. No `tests/integration/` directory exists in the project. Future tickets should follow this convention unless real external resources (SLURM cluster, S3 bucket) are required.

- **`ModelFactory.factory` patched at the call site, not `ModelFactory.__init__`**: `TestValidationErrorPath` and `TestHappyPath` in `test_cli_integration.py` patch `app.cli.ModelFactory.factory` (lines 134, 157) to return a mock or raise `ValueError`. Patching the `factory` method rather than the constructor means `ModelFactory()` still initialises normally, preserving singleton semantics and the `_models` registry populated by the `register_fake_model` fixture.

- **`sys.modules` stub block duplicated in `test_cli_integration.py` before `conftest.py` was created**: The original specialist implementation inlined the stub block at the top of `test_cli_integration.py`. The simplification pass later extracted it to `conftest.py`. The duplication was safe (the `if _mod_name not in sys.modules` guard prevents double-registration), but the `conftest.py` is the right long-term home. All future files that need these stubs should rely on `conftest.py` and not inline a duplicate block.

- **Regex verification test in `TestSubmitJob`**: `test_pattern_constant_matches_sbatch_output` in `tests/unit/test_follow_submitted_job.py` (line 666-673) directly tests `SLURM_SUBMISSION_REGEX_PATTERN` from `app/utils/constants.py` against a known sbatch output string. This is a deliberate design choice: the regex is a project-level constant and the test pins its expected match group to `"67890"`, making regex regressions immediately visible.

## Files and Structures Created or Significantly Modified

- `tests/unit/test_validation_primitives.py` — 243 lines, 6 test classes covering all 6 primitive validators in `app/validation.py`. One class per function, testing the valid return-`None` path and every invalid branch.
- `tests/unit/test_click_types.py` — 177 lines, 3 test classes covering `ModelNameType`, `S3PathType`, and `PositiveIntType` from `app/click_types.py`. Includes `ImportError` fallback test and `bool` rejection test.
- `tests/unit/test_follow_submitted_job.py` — Extended from 33 tests to 57 tests by appending 3 new classes (`TestSubmitJob` 8 tests, `TestCancelSubmittedJob` 2 tests, `TestWaitCancelledJob` 3 tests) in lines 584-721.
- `tests/unit/test_cli_integration.py` — 165 lines, 3 test classes (`TestClickTypeValidation` 5 tests, `TestValidationErrorPath` 1 test, `TestHappyPath` 1 test) using `click.testing.CliRunner` to exercise the full pipeline.
- `tests/unit/conftest.py` — 29 lines, installs `sys.modules` stubs for optional dependencies (`pandas`, `inewave`, `idecomp`, `boto3`, `cfinterface`). Created during simplification to centralise stub setup for all unit tests.

## Conventions Adopted

- Use `pytest.raises(click.BadParameter)` (not `click.exceptions.BadParameter`) for primitive validator failure assertions; use `pytest.raises(click.exceptions.BadParameter)` for Click type `self.fail()` assertions. Both are the same class but the ticket documentation distinguished them by import path. Either form works in practice.
- Unique `FAKE_MODEL` constants per test file to avoid cross-file singleton collisions: `"test_primitive_model_abc123"` in `test_validation_primitives.py`, `"test_click_types_model_xyz987"` in `test_click_types.py`, `"integration_test_model_abc123"` in `test_cli_integration.py`.
- Test method names follow the pattern `test_<condition>_<outcome>` (e.g., `test_valid_s3_path_returns_none`, `test_missing_s3_prefix_raises_bad_parameter`). Avoids generic names like `test_valid` or `test_invalid`.
- Verify `param_hint` independently from the exception type: separate test methods assert `exc_info.value.param_hint == param_name` rather than combining this into the `match=` string of `pytest.raises`. Keeps each test method focused on one assertion.
- `CliRunner` assertions check `result.exit_code` as the primary signal; `result.output` is checked only when verifying error message text. Never use `pytest.raises(SystemExit)` in integration tests — `CliRunner` captures `SystemExit` automatically.

## Surprises and Deviations

- **Missing test for `cancel_run` / `wait_cancelled_job` RuntimeError path via the decorator**: Epic 03's `epic-03-summary.md` (Recommendations, line 65) explicitly requested a test confirming `cancel_run` exits with code 99 when `wait_cancelled_job()` raises `RuntimeError`. This was not implemented in Epic 04. The `wait_cancelled_job` RuntimeError path is tested in isolation in `TestWaitCancelledJob` but there is no integration test that routes a `RuntimeError` from `wait_cancelled_job` through `@handle_cli_errors` to verify exit code 99. This gap remains.

- **`conftest.py` created as an unplanned deliverable**: The test specialist created `tests/unit/conftest.py` during the simplification pass to fix an import-isolation problem discovered while running `test_cli_integration.py`. None of the three tickets specified `conftest.py` as a deliverable. The file is beneficial (centralises stubs, prevents future files from duplicating setup), but it was not in the plan. See `tests/unit/conftest.py`.

- **`test_empty_registry_shows_none_registered_fallback` had a pre-existing defect**: The initial specialist implementation of this test in `test_validation_primitives.py` directly called `factory._models.clear()` without saving state first, which risked leaving the singleton with an empty registry if the test failed mid-execution. The simplification pass (commit `49f529e`) fixed this by saving `saved_models = factory._models.copy()` before clearing and using a `try/finally` for restoration (lines 57-64). The fix was unplanned but important for test isolation.

- **`sys.modules` stub block initially duplicated**: The stub block was written inline in `test_cli_integration.py` before anyone noticed it should be in `conftest.py`. The duplication was safe but messy. The move to `conftest.py` happened during simplification, not as a planned ticket step.

- **`submit_job()` empty-string command list elements not filtered**: The ticket pitfall note (ticket-012, line 80) warned that `submit_job()` inserts `""` into the command list for absent optional parameters. The tests work around this by using `any(... in arg for arg in command_list)` rather than asserting exact list contents. This is the correct approach; however, the upstream function in `app/utils/scheduler.py` should be cleaned up to not insert empty strings (a separate refactor concern).

- **No mypy baseline added**: The epic-03 accumulated summary flagged `type_safety: 0.5` across all tickets as a recurring gap due to missing mypy configuration. Epic 04 did not add mypy. All three new tickets scored `type_safety: 0.5`. This persists into Epic 05.

## Recommendations for Future Epics

- **Add `tests/unit/conftest.py` to the Key Files section of any ticket that creates unit tests touching `app.cli` or model adapters**: The conftest stub is now load-bearing — removing or corrupting it will break `test_cli_integration.py` silently (imports succeed but model adapter methods return `MagicMock` objects). Document this dependency in the ticket.

- **Epic 05 ticket-014 and ticket-015 integration tests**: Reuse the `TestHappyPath` fixture pattern from `tests/unit/test_cli_integration.py` (lines 147-164) — patch `ModelFactory.factory` to return a `MagicMock` model and patch `Log.configure_logger`. Verify `result.exit_code == 0` and assert the expected model method was called with `mock_model.<method>.assert_called_once()`.

- **Add the `cancel_run` + `wait_cancelled_job` RuntimeError path to an Epic 05 integration test**: The missing cross-layer test from the Epic 03 recommendation (routing `RuntimeError` from `wait_cancelled_job` through `@handle_cli_errors` to exit code 99) should be included in any Epic 05 observability or error enrichment ticket that touches `cancel_run` behaviour.

- **Extract `register_fake_model` into `conftest.py` if three or more test files share it**: Currently the fixture is duplicated in three files. If a fourth test file needs model name injection, extract it into `tests/unit/conftest.py` as a non-autouse session-scoped or function-scoped fixture and import it rather than duplicating.

- **Address `submit_job()` empty-string command list in a refactor ticket before Epic 05**: The empty-string padding (`""` elements) in the sbatch command list produced by `app/utils/scheduler.py` (lines 148-150) is a latent correctness risk — `sbatch` may interpret an empty string as a positional argument. A refactor ticket to filter falsy elements from the command list before passing to `run_in_terminal` would also remove the workaround in `TestSubmitJob`.
