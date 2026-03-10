"""Centralized CLI error handling decorator.

The ``handle_cli_errors`` decorator wraps CLI commands with structured
error handling: each CLIError subclass maps to the correct signal and exit code.
Click exceptions are always re-raised so Click's own error formatting runs.
"""

from __future__ import annotations

import functools
import logging
import sys
from collections.abc import Callable
from typing import Any, TypeVar

import click

from app.errors import (
    EXIT_UNKNOWN_ERROR,
    CLIError,
    ModelExecutionError,
    S3Error,
    SlurmError,
    ValidationError,
)
from app.utils.commands import ModelOpsCommands

_F = TypeVar("_F", bound=Callable[..., Any])

_LOGGER_NAME = "hpc-model-utils"


def handle_cli_errors(command_name: str) -> Callable[[_F], _F]:
    """Decorator factory that wraps a CLI command with structured error handling.

    Parameters
    ----------
    command_name:
        The CLI command name used in error messages (e.g. ``"run"``).
    """

    def decorator(func: _F) -> _F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            logger = logging.getLogger(_LOGGER_NAME)
            try:
                return func(*args, **kwargs)
            except (
                click.BadParameter,
                click.UsageError,
                click.exceptions.Exit,
            ):
                raise
            except ValidationError as e:
                _handle_error(e, command_name, logger, ModelOpsCommands.set_model_error)
            except S3Error as e:
                _handle_error(e, command_name, logger, ModelOpsCommands.set_data_error)
            except SlurmError as e:
                _handle_error(e, command_name, logger, ModelOpsCommands.set_model_error)
                if e.completion_info is not None:
                    info = e.completion_info
                    logger.error(
                        "SlurmError completion_info: job_id=%s state=%s "
                        "exit_code=%s elapsed=%s max_rss=%s",
                        info.job_id,
                        info.state,
                        info.exit_code,
                        info.elapsed,
                        info.max_rss,
                    )
            except ModelExecutionError as e:
                _handle_error(e, command_name, logger, ModelOpsCommands.set_model_error)
            except CLIError as e:
                _handle_error(e, command_name, logger, ModelOpsCommands.set_model_error)
            except Exception as e:
                wrapped = CLIError(
                    str(e),
                    command_name=command_name,
                    exit_code=EXIT_UNKNOWN_ERROR,
                )
                ModelOpsCommands.set_model_error()
                logger.exception(str(wrapped))
                sys.exit(EXIT_UNKNOWN_ERROR)

        return wrapper  # type: ignore[return-value]

    return decorator


def _handle_error(
    error: CLIError,
    command_name: str,
    logger: logging.Logger,
    signal_fn: Callable[[], None],
) -> None:
    """Helper to set command name, signal, log, and exit."""
    if not error.command_name:
        error.command_name = command_name
    signal_fn()
    logger.error(str(error))
    sys.exit(error.exit_code)
