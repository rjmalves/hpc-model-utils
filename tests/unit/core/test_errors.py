"""ADR-005/ADR-002/R36/R95/R121/R122: the typed error taxonomy table tests."""

from __future__ import annotations

import signal

import pytest

from hpc_model_utils.core.diagnosis import RunStatus
from hpc_model_utils.core.errors import (
    DataError,
    ExitCode,
    Failure,
    HpcmuError,
    SchedulerError,
    StateFormatError,
    StorageError,
    UsageError,
    classify,
    signal_exit_code,
)
from hpc_model_utils.infra.errors import (
    InfraError,
    ObjectNotFoundError,
    SchedulerCommandError,
    ShellCommandError,
    StorageBackendError,
    UnsafeArchiveError,
)

HPCMU_TABLE: list[tuple[type[HpcmuError], RunStatus, ExitCode]] = [
    (UsageError, RunStatus.RUNTIME_ERROR, ExitCode.USAGE),
    (SchedulerError, RunStatus.RUNTIME_ERROR, ExitCode.SCHEDULER),
    (StorageError, RunStatus.RUNTIME_ERROR, ExitCode.STORAGE),
    (DataError, RunStatus.DATA_ERROR, ExitCode.DATA),
    (StateFormatError, RunStatus.RUNTIME_ERROR, ExitCode.INTERNAL),
]

INFRA_TABLE: list[tuple[type[InfraError], RunStatus, ExitCode]] = [
    (SchedulerCommandError, RunStatus.RUNTIME_ERROR, ExitCode.SCHEDULER),
    (ObjectNotFoundError, RunStatus.RUNTIME_ERROR, ExitCode.STORAGE),
    (StorageBackendError, RunStatus.RUNTIME_ERROR, ExitCode.STORAGE),
    (UnsafeArchiveError, RunStatus.DATA_ERROR, ExitCode.DATA),
    (ShellCommandError, RunStatus.RUNTIME_ERROR, ExitCode.INTERNAL),
]


@pytest.mark.parametrize(
    ("error_cls", "expected_status", "expected_exit_code"), HPCMU_TABLE
)
def test_classify_hpcmu_error_subclass_returns_table_pair(
    error_cls: type[HpcmuError],
    expected_status: RunStatus,
    expected_exit_code: ExitCode,
) -> None:
    failure = classify(error_cls("boom"))
    assert (failure.status, failure.exit_code) == (
        expected_status,
        expected_exit_code,
    )


@pytest.mark.parametrize(
    ("error_cls", "expected_status", "expected_exit_code"), INFRA_TABLE
)
def test_classify_infra_error_subclass_returns_table_pair(
    error_cls: type[InfraError],
    expected_status: RunStatus,
    expected_exit_code: ExitCode,
) -> None:
    failure = classify(error_cls("boom"))
    assert (failure.status, failure.exit_code) == (
        expected_status,
        expected_exit_code,
    )


def test_classify_unknown_exception_returns_internal_failure() -> None:
    assert classify(ValueError("a\nb")) == Failure(
        RunStatus.RUNTIME_ERROR, 99, "InternalError", "a b"
    )


def test_classify_across_full_table_excludes_infeasible() -> None:
    exceptions: list[BaseException] = [
        error_cls("x") for error_cls, _, _ in HPCMU_TABLE
    ]
    exceptions += [error_cls("x") for error_cls, _, _ in INFRA_TABLE]
    exceptions.append(ValueError("x"))
    statuses = {classify(exc).status for exc in exceptions}
    assert RunStatus.INFEASIBLE not in statuses


@pytest.mark.parametrize(
    ("signum", "expected"),
    [
        (signal.SIGTERM, 143),
        (signal.SIGHUP, 129),
        (signal.SIGPIPE, 141),
    ],
)
def test_signal_exit_code_known_signals_returns_128_plus_signum(
    signum: int, expected: int
) -> None:
    assert signal_exit_code(signum) == expected


def test_classify_empty_message_exception_falls_back_to_class_name() -> None:
    failure = classify(ShellCommandError(""))
    assert failure.message == "ShellCommandError"


class _CustomNotFound(ObjectNotFoundError):
    pass


def test_classify_custom_subclass_of_object_not_found_error_resolves_via_mro() -> (
    None
):
    failure = classify(_CustomNotFound("missing"))
    assert (failure.status, failure.exit_code) == (
        RunStatus.RUNTIME_ERROR,
        ExitCode.STORAGE,
    )


def test_classify_keyboard_interrupt_never_raises_maps_to_internal() -> None:
    failure = classify(KeyboardInterrupt())
    assert (failure.status, failure.exit_code, failure.category) == (
        RunStatus.RUNTIME_ERROR,
        ExitCode.INTERNAL,
        "InternalError",
    )


def test_classify_hpcmu_error_with_newline_message_flattens_to_single_line() -> (
    None
):
    failure = classify(SchedulerError("line1\nline2"))
    assert failure.message == "line1 line2"


def test_classify_infra_error_with_newline_message_flattens_to_single_line() -> (
    None
):
    failure = classify(StorageBackendError("line1\nline2"))
    assert failure.message == "line1 line2"


def test_classify_hpcmu_error_category_is_class_name() -> None:
    failure = classify(DataError("bad data"))
    assert failure.category == "DataError"


class Boom(Exception):
    def __str__(self) -> str:
        raise RuntimeError("str blew up")


def test_classify_str_raises_falls_back_to_class_name() -> None:
    assert classify(Boom()) == Failure(
        RunStatus.RUNTIME_ERROR, 99, "InternalError", "Boom"
    )
