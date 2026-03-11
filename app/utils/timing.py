import functools
import logging
import time
from collections.abc import Callable
from logging import INFO, Logger
from typing import Any

from app.utils.commands import ModelOpsCommands


class time_and_log:
    def __init__(
        self,
        message_root: str | None = None,
        logger: Logger | None = None,
        level: int = INFO,
    ) -> None:
        self.message_root = message_root
        self.logger = logger
        self.level = level

    def __enter__(
        self,
    ):
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        end_time = time.perf_counter()
        run_time = end_time - self.start_time
        if self.logger:
            message_with_root = (
                f"{self.message_root}: {run_time:.2f} s"
                if self.message_root
                else f"Finished in {run_time:.2f} s"
            )
            self.logger.log(self.level, message_with_root)


def time_command(command_name: str) -> Callable[..., Any]:
    """Decorator factory that wraps a CLI command with timing instrumentation.

    Records the elapsed time for both successful and failed (``SystemExit``)
    command invocations, logs the result, and sends the duration as ModelOps
    metadata on a best-effort basis.

    Parameters
    ----------
    command_name:
        The CLI command name used in log messages (e.g. ``"run"``).
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            logger = logging.getLogger("hpc-model-utils")
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                elapsed = time.perf_counter() - start
                logger.info(f"Command '{command_name}' completed in {elapsed:.2f}s")
                _send_timing_metadata(elapsed)
                return result
            except SystemExit:
                elapsed = time.perf_counter() - start
                logger.info(f"Command '{command_name}' failed after {elapsed:.2f}s")
                _send_timing_metadata(elapsed)
                raise

        return wrapper

    return decorator


def _send_timing_metadata(elapsed: float) -> None:
    """Send timing metadata to ModelOps on a best-effort basis."""
    try:
        ModelOpsCommands.set_metadata("duration_seconds", f"{elapsed:.2f}")
    except Exception:
        pass
