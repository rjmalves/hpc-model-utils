"""CLI integration tests using Click's CliRunner.

These tests exercise the full pipeline from argument parsing through the error
handler to the final exit code, verifying that Click types, per-command
validators, _get_model_with_logger, and handle_cli_errors cooperate correctly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from app.adapter.repository.abstractmodel import ModelFactory
from app.cli import cli

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
