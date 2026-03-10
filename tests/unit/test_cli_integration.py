"""CLI integration tests using Click's CliRunner.

These tests exercise the full pipeline from argument parsing through the error
handler to the final exit code, verifying that Click types, per-command
validators, _get_model_with_logger, and handle_cli_errors cooperate correctly.

sys.modules stubs are installed before any app.* import so that optional model
adapter dependencies (pandas, inewave, idecomp, etc.) do not need to be present
in the unit-test environment.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

_STUB_MODULES = [
    "pandas",
    "pytz",
    "inewave",
    "inewave.newave",
    "idecomp",
    "idecomp.decomp",
    "idessem",
    "boto3",
    "botocore",
    "cfinterface",
    "cfinterface.components",
    "cfinterface.components.defaultblock",
]

for _mod_name in _STUB_MODULES:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

sys.modules["inewave"].newave = sys.modules["inewave.newave"]
sys.modules["cfinterface"].components = sys.modules["cfinterface.components"]
sys.modules["cfinterface.components"].defaultblock = sys.modules[
    "cfinterface.components.defaultblock"
]

import pytest  # noqa: E402
from click.testing import CliRunner  # noqa: E402

from app.adapter.repository.abstractmodel import ModelFactory  # noqa: E402
from app.cli import cli  # noqa: E402

FAKE_MODEL = "integration_test_model_abc123"
VALID_S3 = "s3://my-bucket/some/key"


@pytest.fixture(autouse=True)
def register_fake_model():
    factory = ModelFactory()
    factory._models[FAKE_MODEL] = object  # type: ignore[assignment]
    yield
    factory._models.pop(FAKE_MODEL, None)


@pytest.fixture(autouse=True)
def patch_modelops_commands():
    from unittest.mock import patch

    with patch("app.error_handler.ModelOpsCommands.set_model_error"), patch(
        "app.error_handler.ModelOpsCommands.set_data_error"
    ):
        yield


@pytest.fixture()
def mock_logger() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


class TestClickTypeValidation:
    def test_model_name_type_rejects_unknown_name(
        self, runner: CliRunner
    ) -> None:
        result = runner.invoke(
            cli, ["extract_sanitize_inputs", "nonexistent_model"]
        )

        assert result.exit_code == 2
        assert (
            "not found" in result.output
            or "not a valid" in result.output
            or "Invalid value" in result.output
        )

    def test_s3_path_type_rejects_non_s3_path(
        self, runner: CliRunner
    ) -> None:
        result = runner.invoke(
            cli, ["check_and_fetch_inputs", FAKE_MODEL, "not-s3-path"]
        )

        assert result.exit_code == 2

    def test_positive_int_type_rejects_zero(self, runner: CliRunner) -> None:
        result = runner.invoke(
            cli, ["output_compression_and_cleanup", FAKE_MODEL, "0"]
        )

        assert result.exit_code == 2

    def test_positive_int_type_rejects_negative_value(
        self, runner: CliRunner
    ) -> None:
        result = runner.invoke(
            cli, ["output_compression_and_cleanup", FAKE_MODEL, "-5"]
        )

        assert result.exit_code == 2

    def test_s3_path_type_rejects_bucket_only(self, runner: CliRunner) -> None:
        result = runner.invoke(
            cli, ["check_and_fetch_inputs", FAKE_MODEL, "s3://bucket-only"]
        )

        assert result.exit_code == 2


class TestValidationErrorPath:
    def test_factory_value_error_produces_exit_code_2(
        self, runner: CliRunner, mock_logger: MagicMock
    ) -> None:
        from unittest.mock import patch

        with patch(
            "app.cli.ModelFactory.factory",
            side_effect=ValueError(
                f"Model with name {FAKE_MODEL} not found"
            ),
        ), patch("app.cli.Log.configure_logger", return_value=mock_logger):
            result = runner.invoke(
                cli, ["extract_sanitize_inputs", FAKE_MODEL]
            )

        assert result.exit_code == 2


class TestHappyPath:
    def test_extract_sanitize_inputs_exits_zero_on_success(
        self, runner: CliRunner, mock_logger: MagicMock
    ) -> None:
        from unittest.mock import patch

        mock_model = MagicMock()
        mock_model.extract_sanitize_inputs.return_value = None

        with patch(
            "app.cli.ModelFactory.factory", return_value=mock_model
        ), patch("app.cli.Log.configure_logger", return_value=mock_logger):
            result = runner.invoke(
                cli, ["extract_sanitize_inputs", FAKE_MODEL]
            )

        assert result.exit_code == 0
        mock_model.extract_sanitize_inputs.assert_called_once()
