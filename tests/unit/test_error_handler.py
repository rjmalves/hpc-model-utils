"""Unit tests for app/error_handler.py — handle_cli_errors decorator."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import click
import pytest

from app.error_handler import handle_cli_errors
from app.errors import (
    EXIT_MODEL_ERROR,
    EXIT_S3_ERROR,
    EXIT_SLURM_ERROR,
    EXIT_UNKNOWN_ERROR,
    EXIT_VALIDATION_ERROR,
    CLIError,
    ModelExecutionError,
    S3Error,
    SlurmError,
    ValidationError,
)
from app.utils.scheduler import JobCompletionInfo


# Helpers


def _make_completion_info(**kwargs: str) -> JobCompletionInfo:
    defaults: dict[str, str] = {
        "job_id": "42",
        "state": "FAILED",
        "exit_code": "1:0",
        "elapsed": "00:02:00",
        "max_rss": "256K",
        "raw_output": "42|FAILED|1:0|00:02:00|256K",
    }
    defaults.update(kwargs)
    return JobCompletionInfo(**defaults)


def _make_raising_command(exc: BaseException) -> None:

    @handle_cli_errors("test_cmd")
    def cmd() -> None:
        raise exc

    return cmd  # type: ignore[return-value]


# No exception — happy path


class TestNoException:
    def test_returns_none_when_function_returns_none(self) -> None:
        @handle_cli_errors("test_cmd")
        def cmd() -> None:
            pass

        with (
            patch("app.error_handler.ModelOpsCommands") as mock_ops,
            patch("sys.exit") as mock_exit,
        ):
            result = cmd()

        assert result is None
        mock_ops.set_model_error.assert_not_called()
        mock_ops.set_data_error.assert_not_called()
        mock_ops.set_success.assert_not_called()
        mock_exit.assert_not_called()

    def test_returns_command_return_value(self) -> None:
        @handle_cli_errors("test_cmd")
        def cmd() -> int:
            return 42

        with patch("sys.exit"):
            result = cmd()

        assert result == 42

    def test_passes_args_and_kwargs_through(self) -> None:
        received: dict[str, object] = {}

        @handle_cli_errors("test_cmd")
        def cmd(a: int, b: str = "default") -> None:
            received["a"] = a
            received["b"] = b

        cmd(1, b="hello")

        assert received == {"a": 1, "b": "hello"}


# ValidationError


class TestValidationError:
    def test_calls_set_model_error(self) -> None:
        cmd = _make_raising_command(ValidationError("bad input"))

        with (
            patch("app.error_handler.ModelOpsCommands") as mock_ops,
            patch("sys.exit"),
        ):
            cmd()

        mock_ops.set_model_error.assert_called_once()
        mock_ops.set_data_error.assert_not_called()

    def test_exits_with_validation_exit_code(self) -> None:
        cmd = _make_raising_command(ValidationError("bad input"))

        with (
            patch("app.error_handler.ModelOpsCommands"),
            patch("sys.exit") as mock_exit,
        ):
            cmd()

        mock_exit.assert_called_once_with(EXIT_VALIDATION_ERROR)

    def test_logs_error_message_contains_category_and_command(self) -> None:
        cmd = _make_raising_command(ValidationError("bad input"))

        with (
            patch("app.error_handler.ModelOpsCommands"),
            patch("sys.exit"),
            patch("app.error_handler.logging") as mock_logging,
        ):
            mock_logger = MagicMock()
            mock_logging.getLogger.return_value = mock_logger
            cmd()

        mock_logger.error.assert_called_once()
        logged_msg = mock_logger.error.call_args[0][0]
        assert "[ValidationError]" in logged_msg
        assert "test_cmd" in logged_msg
        assert "bad input" in logged_msg

    def test_injects_command_name_when_empty(self) -> None:
        err = ValidationError("bad input")
        assert err.command_name == ""

        cmd = _make_raising_command(err)

        with (
            patch("app.error_handler.ModelOpsCommands"),
            patch("sys.exit"),
        ):
            cmd()

        assert err.command_name == "test_cmd"

    def test_does_not_overwrite_existing_command_name(self) -> None:
        err = ValidationError("bad input", command_name="original_cmd")
        cmd = _make_raising_command(err)

        with (
            patch("app.error_handler.ModelOpsCommands"),
            patch("sys.exit"),
        ):
            cmd()

        assert err.command_name == "original_cmd"

    def test_does_not_call_logger_exception(self) -> None:
        cmd = _make_raising_command(ValidationError("bad input"))

        with (
            patch("app.error_handler.ModelOpsCommands"),
            patch("sys.exit"),
            patch("app.error_handler.logging") as mock_logging,
        ):
            mock_logger = MagicMock()
            mock_logging.getLogger.return_value = mock_logger
            cmd()

        mock_logger.exception.assert_not_called()


# S3Error


class TestS3Error:
    def test_calls_set_data_error_not_set_model_error(self) -> None:
        cmd = _make_raising_command(S3Error("bucket not found"))

        with (
            patch("app.error_handler.ModelOpsCommands") as mock_ops,
            patch("sys.exit"),
        ):
            cmd()

        mock_ops.set_data_error.assert_called_once()
        mock_ops.set_model_error.assert_not_called()

    def test_exits_with_s3_exit_code(self) -> None:
        cmd = _make_raising_command(S3Error("bucket not found"))

        with (
            patch("app.error_handler.ModelOpsCommands"),
            patch("sys.exit") as mock_exit,
        ):
            cmd()

        mock_exit.assert_called_once_with(EXIT_S3_ERROR)

    def test_logs_error_message(self) -> None:
        cmd = _make_raising_command(S3Error("bucket not found"))

        with (
            patch("app.error_handler.ModelOpsCommands"),
            patch("sys.exit"),
            patch("app.error_handler.logging") as mock_logging,
        ):
            mock_logger = MagicMock()
            mock_logging.getLogger.return_value = mock_logger
            cmd()

        mock_logger.error.assert_called_once()
        logged_msg = mock_logger.error.call_args[0][0]
        assert "[S3Error]" in logged_msg
        assert "bucket not found" in logged_msg


# SlurmError


class TestSlurmError:
    def test_calls_set_model_error(self) -> None:
        cmd = _make_raising_command(SlurmError("job failed"))

        with (
            patch("app.error_handler.ModelOpsCommands") as mock_ops,
            patch("sys.exit"),
        ):
            cmd()

        mock_ops.set_model_error.assert_called_once()
        mock_ops.set_data_error.assert_not_called()

    def test_exits_with_slurm_exit_code(self) -> None:
        cmd = _make_raising_command(SlurmError("job failed"))

        with (
            patch("app.error_handler.ModelOpsCommands"),
            patch("sys.exit") as mock_exit,
        ):
            cmd()

        mock_exit.assert_called_once_with(EXIT_SLURM_ERROR)

    def test_without_completion_info_logs_only_message(self) -> None:
        cmd = _make_raising_command(SlurmError("job failed"))

        with (
            patch("app.error_handler.ModelOpsCommands"),
            patch("sys.exit"),
            patch("app.error_handler.logging") as mock_logging,
        ):
            mock_logger = MagicMock()
            mock_logging.getLogger.return_value = mock_logger
            cmd()

        # Only one logger.error call — the main message; no completion_info line
        assert mock_logger.error.call_count == 1

    def test_with_completion_info_logs_extra_fields(self) -> None:
        info = _make_completion_info(state="TIMEOUT", job_id="99")
        err = SlurmError("timed out", completion_info=info)
        cmd = _make_raising_command(err)

        with (
            patch("app.error_handler.ModelOpsCommands"),
            patch("sys.exit"),
            patch("app.error_handler.logging") as mock_logging,
        ):
            mock_logger = MagicMock()
            mock_logging.getLogger.return_value = mock_logger
            cmd()

        # Two logger.error calls: main message + completion_info
        assert mock_logger.error.call_count == 2
        completion_log_args = mock_logger.error.call_args_list[1]
        fmt = completion_log_args[0][0]
        assert "completion_info" in fmt
        # positional args include the job_id and state values
        positional = completion_log_args[0][1:]
        assert "99" in positional
        assert "TIMEOUT" in positional

    def test_completion_info_logs_all_fields(self) -> None:
        info = _make_completion_info(
            job_id="55",
            state="FAILED",
            exit_code="2:0",
            elapsed="00:10:00",
            max_rss="1024K",
        )
        err = SlurmError("failure", completion_info=info)
        cmd = _make_raising_command(err)

        with (
            patch("app.error_handler.ModelOpsCommands"),
            patch("sys.exit"),
            patch("app.error_handler.logging") as mock_logging,
        ):
            mock_logger = MagicMock()
            mock_logging.getLogger.return_value = mock_logger
            cmd()

        second_call_args = mock_logger.error.call_args_list[1][0]
        positional_values = second_call_args[1:]
        assert "55" in positional_values
        assert "FAILED" in positional_values
        assert "2:0" in positional_values
        assert "00:10:00" in positional_values
        assert "1024K" in positional_values


# ModelExecutionError


class TestModelExecutionError:
    def test_calls_set_model_error(self) -> None:
        cmd = _make_raising_command(ModelExecutionError("model crashed"))

        with (
            patch("app.error_handler.ModelOpsCommands") as mock_ops,
            patch("sys.exit"),
        ):
            cmd()

        mock_ops.set_model_error.assert_called_once()
        mock_ops.set_data_error.assert_not_called()

    def test_exits_with_model_exit_code(self) -> None:
        cmd = _make_raising_command(ModelExecutionError("model crashed"))

        with (
            patch("app.error_handler.ModelOpsCommands"),
            patch("sys.exit") as mock_exit,
        ):
            cmd()

        mock_exit.assert_called_once_with(EXIT_MODEL_ERROR)

    def test_logs_error_message(self) -> None:
        cmd = _make_raising_command(ModelExecutionError("model crashed"))

        with (
            patch("app.error_handler.ModelOpsCommands"),
            patch("sys.exit"),
            patch("app.error_handler.logging") as mock_logging,
        ):
            mock_logger = MagicMock()
            mock_logging.getLogger.return_value = mock_logger
            cmd()

        mock_logger.error.assert_called_once()
        logged_msg = mock_logger.error.call_args[0][0]
        assert "[ModelExecutionError]" in logged_msg
        assert "model crashed" in logged_msg


# CLIError base (raised directly, not as a subclass)


class TestCLIErrorBase:
    def test_calls_set_model_error(self) -> None:
        cmd = _make_raising_command(CLIError("generic error", exit_code=7))

        with (
            patch("app.error_handler.ModelOpsCommands") as mock_ops,
            patch("sys.exit"),
        ):
            cmd()

        mock_ops.set_model_error.assert_called_once()

    def test_exits_with_provided_exit_code(self) -> None:
        cmd = _make_raising_command(CLIError("generic error", exit_code=7))

        with (
            patch("app.error_handler.ModelOpsCommands"),
            patch("sys.exit") as mock_exit,
        ):
            cmd()

        mock_exit.assert_called_once_with(7)

    def test_exits_with_exit_code_1_when_model_error_code(self) -> None:
        cmd = _make_raising_command(CLIError("generic error", exit_code=EXIT_MODEL_ERROR))

        with (
            patch("app.error_handler.ModelOpsCommands"),
            patch("sys.exit") as mock_exit,
        ):
            cmd()

        mock_exit.assert_called_once_with(EXIT_MODEL_ERROR)


# Unexpected Exception (RuntimeError, etc.)


class TestUnexpectedException:
    def test_calls_set_model_error(self) -> None:
        cmd = _make_raising_command(RuntimeError("unexpected"))

        with (
            patch("app.error_handler.ModelOpsCommands") as mock_ops,
            patch("sys.exit"),
        ):
            cmd()

        mock_ops.set_model_error.assert_called_once()
        mock_ops.set_data_error.assert_not_called()

    def test_exits_with_unknown_error_code(self) -> None:
        cmd = _make_raising_command(RuntimeError("unexpected"))

        with (
            patch("app.error_handler.ModelOpsCommands"),
            patch("sys.exit") as mock_exit,
        ):
            cmd()

        mock_exit.assert_called_once_with(EXIT_UNKNOWN_ERROR)

    def test_uses_logger_exception_not_logger_error(self) -> None:
        cmd = _make_raising_command(RuntimeError("unexpected"))

        with (
            patch("app.error_handler.ModelOpsCommands"),
            patch("sys.exit"),
            patch("app.error_handler.logging") as mock_logging,
        ):
            mock_logger = MagicMock()
            mock_logging.getLogger.return_value = mock_logger
            cmd()

        mock_logger.exception.assert_called_once()
        mock_logger.error.assert_not_called()

    def test_logger_exception_message_contains_command_name(self) -> None:
        cmd = _make_raising_command(RuntimeError("unexpected"))

        with (
            patch("app.error_handler.ModelOpsCommands"),
            patch("sys.exit"),
            patch("app.error_handler.logging") as mock_logging,
        ):
            mock_logger = MagicMock()
            mock_logging.getLogger.return_value = mock_logger
            cmd()

        logged_msg = mock_logger.exception.call_args[0][0]
        assert "test_cmd" in logged_msg

    def test_value_error_treated_as_unexpected(self) -> None:
        cmd = _make_raising_command(ValueError("bad value"))

        with (
            patch("app.error_handler.ModelOpsCommands") as mock_ops,
            patch("sys.exit") as mock_exit,
        ):
            cmd()

        mock_ops.set_model_error.assert_called_once()
        mock_exit.assert_called_once_with(EXIT_UNKNOWN_ERROR)


# Click exceptions — must propagate


class TestClickExceptionsPropagation:
    def test_bad_parameter_propagates(self) -> None:
        cmd = _make_raising_command(click.BadParameter("invalid"))

        with (
            patch("app.error_handler.ModelOpsCommands") as mock_ops,
            patch("sys.exit") as mock_exit,
        ):
            with pytest.raises(click.BadParameter):
                cmd()

        mock_ops.set_model_error.assert_not_called()
        mock_ops.set_data_error.assert_not_called()
        mock_exit.assert_not_called()

    def test_usage_error_propagates(self) -> None:
        cmd = _make_raising_command(click.UsageError("wrong usage"))

        with (
            patch("app.error_handler.ModelOpsCommands") as mock_ops,
            patch("sys.exit") as mock_exit,
        ):
            with pytest.raises(click.UsageError):
                cmd()

        mock_ops.set_model_error.assert_not_called()
        mock_exit.assert_not_called()

    def test_exit_exception_propagates(self) -> None:
        cmd = _make_raising_command(click.exceptions.Exit(0))

        with (
            patch("app.error_handler.ModelOpsCommands") as mock_ops,
            patch("sys.exit") as mock_exit,
        ):
            with pytest.raises(click.exceptions.Exit):
                cmd()

        mock_ops.set_model_error.assert_not_called()
        mock_exit.assert_not_called()

    def test_bad_parameter_is_subclass_of_usage_error_still_propagates(
        self,
    ) -> None:
        """click.BadParameter is a UsageError subclass; ensure both are re-raised."""
        assert issubclass(click.BadParameter, click.UsageError)

        cmd = _make_raising_command(click.BadParameter("x"))

        with patch("app.error_handler.ModelOpsCommands"):
            with pytest.raises(click.BadParameter):
                cmd()


# Decorator metadata preservation (functools.wraps)


class TestDecoratorMetadata:
    def test_wraps_preserves_function_name(self) -> None:
        @handle_cli_errors("test_cmd")
        def my_special_command() -> None:
            pass

        assert my_special_command.__name__ == "my_special_command"

    def test_wraps_preserves_docstring(self) -> None:
        @handle_cli_errors("test_cmd")
        def cmd() -> None:
            """My command docstring."""

        assert cmd.__doc__ == "My command docstring."

    def test_wraps_preserves_wrapped_attribute(self) -> None:
        @handle_cli_errors("test_cmd")
        def cmd() -> None:
            pass

        assert hasattr(cmd, "__wrapped__")


# Logger retrieval


class TestLoggerRetrieval:
    def test_uses_hpc_model_utils_logger_name(self) -> None:
        @handle_cli_errors("test_cmd")
        def cmd() -> None:
            raise ValidationError("error")

        with (
            patch("app.error_handler.ModelOpsCommands"),
            patch("sys.exit"),
            patch("app.error_handler.logging") as mock_logging,
        ):
            mock_logger = MagicMock()
            mock_logging.getLogger.return_value = mock_logger
            cmd()

        mock_logging.getLogger.assert_called_once_with("hpc-model-utils")

