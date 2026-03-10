"""Typed error hierarchy and exit code constants for CLI commands."""

from __future__ import annotations

from app.utils.scheduler import JobCompletionInfo

EXIT_SUCCESS: int = 0
EXIT_MODEL_ERROR: int = 1
EXIT_VALIDATION_ERROR: int = 2
EXIT_SLURM_ERROR: int = 3
EXIT_S3_ERROR: int = 4
EXIT_UNKNOWN_ERROR: int = 99


class CLIError(Exception):
    """Base class for all structured CLI errors.

    Parameters
    ----------
    message:
        Human-readable description of what went wrong.
    command_name:
        The CLI command that raised the error (e.g. ``"run"``).
        May be empty when the error originates outside a named command.
    detail:
        Optional additional context (stack trace fragment, file path, etc.).
    exit_code:
        Numeric exit code the process should use. Each subclass provides
        its own default; callers may override.
    """

    def __init__(
        self,
        message: str,
        *,
        command_name: str = "",
        detail: str = "",
        exit_code: int,
    ) -> None:
        self.message = message
        self.command_name = command_name
        self.detail = detail
        self.exit_code = exit_code
        super().__init__(message)

    def __str__(self) -> str:
        category = type(self).__name__
        if self.command_name:
            return f"[{category}] {self.command_name}: {self.message}"
        return f"[{category}] {self.message}"


class ValidationError(CLIError):
    """Raised when user-supplied input fails validation (exit code 2)."""

    def __init__(
        self,
        message: str,
        *,
        command_name: str = "",
        detail: str = "",
        exit_code: int = EXIT_VALIDATION_ERROR,
    ) -> None:
        super().__init__(
            message,
            command_name=command_name,
            detail=detail,
            exit_code=exit_code,
        )


class SlurmError(CLIError):
    """Raised when a SLURM operation fails (exit code 3).

    The optional ``completion_info`` field carries structured sacct output
    for context logging without string parsing.
    """

    def __init__(
        self,
        message: str,
        *,
        command_name: str = "",
        detail: str = "",
        exit_code: int = EXIT_SLURM_ERROR,
        completion_info: JobCompletionInfo | None = None,
    ) -> None:
        self.completion_info = completion_info
        super().__init__(
            message,
            command_name=command_name,
            detail=detail,
            exit_code=exit_code,
        )


class S3Error(CLIError):
    """Raised when an S3 operation fails (exit code 4)."""

    def __init__(
        self,
        message: str,
        *,
        command_name: str = "",
        detail: str = "",
        exit_code: int = EXIT_S3_ERROR,
    ) -> None:
        super().__init__(
            message,
            command_name=command_name,
            detail=detail,
            exit_code=exit_code,
        )


class ModelExecutionError(CLIError):
    """Raised when a model execution step fails (exit code 1)."""

    def __init__(
        self,
        message: str,
        *,
        command_name: str = "",
        detail: str = "",
        exit_code: int = EXIT_MODEL_ERROR,
    ) -> None:
        super().__init__(
            message,
            command_name=command_name,
            detail=detail,
            exit_code=exit_code,
        )
