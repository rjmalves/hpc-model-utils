"""Unit tests for app/utils/timing.py — time_command decorator and time_and_log."""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest

from app.utils.timing import time_and_log, time_command


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _noop() -> None:
    """No-op function used as a decorated target."""


def _make_raising_command(exit_code: int):
    """Return a function that raises SystemExit with the given code."""

    def _raising() -> None:
        raise SystemExit(exit_code)

    return _raising


# ---------------------------------------------------------------------------
# TestTimeCommand
# ---------------------------------------------------------------------------


class TestTimeCommand:
    """Tests for the time_command decorator factory."""

    def test_successful_command_logs_completed_message(self) -> None:
        """On normal return, INFO log must contain 'completed in' and command name."""
        with patch("app.utils.timing.logging") as mock_logging, patch(
            "app.utils.timing.ModelOpsCommands"
        ):
            mock_logger = MagicMock()
            mock_logging.getLogger.return_value = mock_logger

            decorated = time_command("mycommand")(_noop)
            decorated()

            mock_logger.info.assert_called_once()
            message = mock_logger.info.call_args[0][0]
            assert "mycommand" in message
            assert "completed in" in message

    def test_failed_command_logs_failed_message(self) -> None:
        """On SystemExit, INFO log must contain 'failed after' and command name."""
        with patch("app.utils.timing.logging") as mock_logging, patch(
            "app.utils.timing.ModelOpsCommands"
        ):
            mock_logger = MagicMock()
            mock_logging.getLogger.return_value = mock_logger

            decorated = time_command("mycommand")(_make_raising_command(1))
            with pytest.raises(SystemExit):
                decorated()

            mock_logger.info.assert_called_once()
            message = mock_logger.info.call_args[0][0]
            assert "mycommand" in message
            assert "failed after" in message

    def test_system_exit_is_reraised(self) -> None:
        """SystemExit must propagate with the original exit code intact."""
        with patch("app.utils.timing.logging"), patch("app.utils.timing.ModelOpsCommands"):
            decorated = time_command("mycommand")(_make_raising_command(3))
            with pytest.raises(SystemExit) as exc_info:
                decorated()
            assert exc_info.value.code == 3

    def test_successful_command_sends_metadata(self) -> None:
        """On normal return, set_metadata must be called with 'duration_seconds' and a
        string matching the pattern r'\\d+\\.\\d{2}'."""
        with patch("app.utils.timing.logging"), patch(
            "app.utils.timing.ModelOpsCommands"
        ) as mock_ops:
            decorated = time_command("mycommand")(_noop)
            decorated()

            mock_ops.set_metadata.assert_called_once()
            key, value = mock_ops.set_metadata.call_args[0]
            assert key == "duration_seconds"
            assert re.match(r"\d+\.\d{2}", value), f"Unexpected value format: {value!r}"

    def test_failed_command_sends_metadata(self) -> None:
        """On SystemExit, set_metadata must be called with 'duration_seconds' and a
        string matching the pattern r'\\d+\\.\\d{2}'."""
        with patch("app.utils.timing.logging"), patch(
            "app.utils.timing.ModelOpsCommands"
        ) as mock_ops:
            decorated = time_command("mycommand")(_make_raising_command(1))
            with pytest.raises(SystemExit):
                decorated()

            mock_ops.set_metadata.assert_called_once()
            key, value = mock_ops.set_metadata.call_args[0]
            assert key == "duration_seconds"
            assert re.match(r"\d+\.\d{2}", value), f"Unexpected value format: {value!r}"

    def test_metadata_failure_does_not_propagate(self) -> None:
        """If set_metadata raises OSError, the decorator must not propagate it."""
        with patch("app.utils.timing.logging"), patch(
            "app.utils.timing.ModelOpsCommands"
        ) as mock_ops:
            mock_ops.set_metadata.side_effect = OSError("write failed")
            decorated = time_command("mycommand")(_noop)
            # Must not raise
            decorated()

    def test_preserves_function_name(self) -> None:
        """functools.wraps must preserve the original function's __name__."""

        def my_original_function() -> None:
            pass

        with patch("app.utils.timing.ModelOpsCommands"):
            decorated = time_command("mycommand")(my_original_function)
            assert decorated.__name__ == "my_original_function"

    def test_passes_args_and_kwargs(self) -> None:
        """Positional and keyword arguments must reach the wrapped function unchanged."""
        received: list[tuple] = []

        def capturing(a: int, b: int, *, flag: bool = False) -> None:
            received.append((a, b, flag))

        with patch("app.utils.timing.logging"), patch("app.utils.timing.ModelOpsCommands"):
            decorated = time_command("mycommand")(capturing)
            decorated(1, 2, flag=True)

        assert received == [(1, 2, True)]


# ---------------------------------------------------------------------------
# TestExistingTimeAndLog (regression)
# ---------------------------------------------------------------------------


class TestExistingTimeAndLog:
    """Regression tests to verify time_and_log is unmodified and still functional."""

    def test_time_and_log_still_works_as_context_manager(self) -> None:
        """The existing time_and_log context manager must log the elapsed time message."""
        mock_logger = MagicMock()

        with time_and_log(message_root="TestOp", logger=mock_logger):
            pass

        mock_logger.log.assert_called_once()
        logged_message: str = mock_logger.log.call_args[0][1]
        assert "TestOp" in logged_message
        assert re.search(r"\d+\.\d{2} s", logged_message), (
            f"Expected elapsed-time pattern in: {logged_message!r}"
        )
