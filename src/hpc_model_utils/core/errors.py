"""ADR-005/ADR-002/R36/R95/R121/R122: the typed error taxonomy and exit codes."""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar

from hpc_model_utils.core.diagnosis import RunStatus
from hpc_model_utils.infra.errors import (
    InfraError,
    ObjectNotFoundError,
    SchedulerCommandError,
    ShellCommandError,
    StorageBackendError,
    UnsafeArchiveError,
)


class ExitCode(enum.IntEnum):
    OK = 0
    USAGE = 2
    SCHEDULER = 3
    STORAGE = 4
    DATA = 5
    INTERNAL = 99


def signal_exit_code(signum: int) -> int:
    return 128 + signum


class HpcmuError(Exception):
    status: ClassVar[RunStatus]
    exit_code: ClassVar[ExitCode]

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class UsageError(HpcmuError):
    status: ClassVar[RunStatus] = RunStatus.RUNTIME_ERROR
    exit_code: ClassVar[ExitCode] = ExitCode.USAGE


class SchedulerError(HpcmuError):
    status: ClassVar[RunStatus] = RunStatus.RUNTIME_ERROR
    exit_code: ClassVar[ExitCode] = ExitCode.SCHEDULER


class StorageError(HpcmuError):
    status: ClassVar[RunStatus] = RunStatus.RUNTIME_ERROR
    exit_code: ClassVar[ExitCode] = ExitCode.STORAGE


class DataError(HpcmuError):
    status: ClassVar[RunStatus] = RunStatus.DATA_ERROR
    exit_code: ClassVar[ExitCode] = ExitCode.DATA


class StateFormatError(HpcmuError):
    status: ClassVar[RunStatus] = RunStatus.RUNTIME_ERROR
    exit_code: ClassVar[ExitCode] = ExitCode.INTERNAL


@dataclass(frozen=True)
class Failure:
    status: RunStatus
    exit_code: int
    category: str
    message: str


_INFRA_DEFAULT_TABLE: Mapping[type[InfraError], tuple[RunStatus, int]] = {
    SchedulerCommandError: (RunStatus.RUNTIME_ERROR, ExitCode.SCHEDULER),
    ObjectNotFoundError: (RunStatus.RUNTIME_ERROR, ExitCode.STORAGE),
    StorageBackendError: (RunStatus.RUNTIME_ERROR, ExitCode.STORAGE),
    UnsafeArchiveError: (RunStatus.DATA_ERROR, ExitCode.DATA),
    ShellCommandError: (RunStatus.RUNTIME_ERROR, ExitCode.INTERNAL),
}


def _flatten_message(exc: BaseException) -> str:
    try:
        text = str(exc).replace("\n", " ")
    except Exception:
        text = ""
    return text if text else type(exc).__name__


def classify(exc: BaseException) -> Failure:
    message = _flatten_message(exc)
    if isinstance(exc, HpcmuError):
        return Failure(exc.status, exc.exit_code, type(exc).__name__, message)
    for klass in type(exc).__mro__:
        mapping = _INFRA_DEFAULT_TABLE.get(klass)
        if mapping is not None:
            status, exit_code = mapping
            return Failure(status, exit_code, type(exc).__name__, message)
    return Failure(
        RunStatus.RUNTIME_ERROR, ExitCode.INTERNAL, "InternalError", message
    )
