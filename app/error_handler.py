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
                if not e.command_name:
                    e.command_name = command_name
                _handle_error(
                    e,
                    command_name,
                    logger,
                    ModelOpsCommands.set_model_error,
                    annotation=_build_slurm_annotation(e),
                )
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
                try:
                    ModelOpsCommands.set_annotation(_build_annotation(wrapped))
                except Exception:
                    pass
                logger.exception(str(wrapped))
                sys.exit(EXIT_UNKNOWN_ERROR)

        return wrapper  # type: ignore[return-value]

    return decorator


def _handle_error(
    error: CLIError,
    command_name: str,
    logger: logging.Logger,
    signal_fn: Callable[[], None],
    annotation: str | None = None,
) -> None:
    """Helper to set command name, signal, annotate, log, and exit."""
    if not error.command_name:
        error.command_name = command_name
    signal_fn()
    try:
        ModelOpsCommands.set_annotation(
            annotation if annotation is not None else _build_annotation(error)
        )
    except Exception:
        pass
    logger.error(str(error))
    sys.exit(error.exit_code)


def _build_annotation(error: CLIError) -> str:
    """Build a plain-text annotation string from a CLIError, truncated to 500 chars."""
    return str(error)[:500]


def _build_slurm_annotation(error: SlurmError) -> str:
    """Build an enriched annotation for SlurmError, appending sacct fields when available."""
    base = _build_annotation(error)
    if error.completion_info is not None:
        info = error.completion_info
        suffix = f" | job_id={info.job_id} state={info.state} exit_code={info.exit_code}"
        return (base + suffix)[:500]
    return base
