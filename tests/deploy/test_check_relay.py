"""Tests for deploy.modelops.check_relay (ticket-064a).

Acceptance criteria, selected with ``-k <keyword>``:
``pass_case``, ``lost`` / ``duplicated``, ``anomaly`` / ``ambiguous``,
``two_jobs``.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterable, Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from deploy.modelops.check_relay import (
    ambiguous_marker,
    compare,
    main,
    relayed_lines,
)
from hpc_model_utils.core.follow import LogTail
from hpc_model_utils.platform.encoding import neutralize

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_OUTER_PREFIX = "2026-10-03T12:00:00Z [ExecutionOutput] run: "
_LOGGER_NAME = "hpc_model_utils.job"

_JOB_LOG_BYTES = (
    b"starting\n"
    b"export HOME=${HOME}/work\n"
    b"Submitted batch job 7\n"
    b"bad byte \xff here\n"
    b"final line no newline"
)


@pytest.fixture
def relay_logger() -> Iterator[tuple[logging.Logger, io.StringIO]]:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    logger = logging.getLogger(_LOGGER_NAME)
    previous_level = logger.level
    previous_propagate = logger.propagate
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        yield logger, buf
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate


def _relay(logger: logging.Logger, buf: io.StringIO, message: str) -> str:
    before = buf.tell()
    logger.info(message)
    buf.seek(before)
    formatted = buf.read().rstrip("\n")
    return _OUTER_PREFIX + neutralize(formatted)


def _raw_job_lines(path: Path) -> list[str]:
    tail = LogTail(path)
    return tail.poll() + tail.close()


def _relayed_text_lines(
    logger: logging.Logger, buf: io.StringIO, raw_lines: Iterable[str]
) -> list[str]:
    return [_relay(logger, buf, raw) for raw in raw_lines]


def _write_relayed(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------- AC: pass_case


def test_check_relay_pass_case_exits_zero(
    tmp_path: Path, relay_logger: tuple[logging.Logger, io.StringIO]
) -> None:
    logger, buf = relay_logger
    job_log = tmp_path / "model-7.out"
    job_log.write_bytes(_JOB_LOG_BYTES)
    raw_lines = _raw_job_lines(job_log)
    content_lines = _relayed_text_lines(logger, buf, raw_lines)
    heartbeat_line = _relay(
        logger, buf, "[hpcmu] heartbeat: job 7 RUNNING None"
    )
    lines = [content_lines[0], heartbeat_line, *content_lines[1:]]
    relayed_path = tmp_path / "relayed.txt"
    _write_relayed(relayed_path, lines)

    result = CliRunner().invoke(
        main, ["--relayed", str(relayed_path), "--job-log", str(job_log)]
    )

    assert result.exit_code == 0, result.output
    assert "relay: PASS" in result.output
    assert "informational=1" in result.output
    assert "anomalies=0" in result.output


# ------------------------------------------------------------ AC: lost/duplicated


def test_check_relay_lost_line_fails(
    tmp_path: Path, relay_logger: tuple[logging.Logger, io.StringIO]
) -> None:
    logger, buf = relay_logger
    job_log = tmp_path / "model-7.out"
    job_log.write_bytes(_JOB_LOG_BYTES)
    raw_lines = _raw_job_lines(job_log)
    content_lines = _relayed_text_lines(logger, buf, raw_lines)
    removed_index = 1
    lines = content_lines[:removed_index] + content_lines[removed_index + 1 :]
    relayed_path = tmp_path / "relayed.txt"
    _write_relayed(relayed_path, lines)

    result = CliRunner().invoke(
        main, ["--relayed", str(relayed_path), "--job-log", str(job_log)]
    )

    assert result.exit_code == 1, result.output
    assert "relay: FAIL" in result.output
    assert f"index={removed_index}" in result.output
    assert "kind=lost" in result.output


def test_check_relay_duplicated_line_fails(
    tmp_path: Path, relay_logger: tuple[logging.Logger, io.StringIO]
) -> None:
    logger, buf = relay_logger
    job_log = tmp_path / "model-7.out"
    job_log.write_bytes(_JOB_LOG_BYTES)
    raw_lines = _raw_job_lines(job_log)
    content_lines = _relayed_text_lines(logger, buf, raw_lines)
    dup_index = 1
    lines = (
        content_lines[: dup_index + 1]
        + [content_lines[dup_index]]
        + content_lines[dup_index + 1 :]
    )
    relayed_path = tmp_path / "relayed.txt"
    _write_relayed(relayed_path, lines)

    result = CliRunner().invoke(
        main, ["--relayed", str(relayed_path), "--job-log", str(job_log)]
    )

    assert result.exit_code == 1, result.output
    assert "relay: FAIL" in result.output
    assert f"index={dup_index + 1}" in result.output
    assert "kind=extra" in result.output


# ------------------------------------------------------------- AC: anomaly/ambiguous


def test_check_relay_anomaly_marker_fails(
    tmp_path: Path, relay_logger: tuple[logging.Logger, io.StringIO]
) -> None:
    logger, buf = relay_logger
    job_log = tmp_path / "model-7.out"
    job_log.write_bytes(_JOB_LOG_BYTES)
    raw_lines = _raw_job_lines(job_log)
    content_lines = _relayed_text_lines(logger, buf, raw_lines)
    anomaly_line = _relay(logger, buf, "[hpcmu] log truncated: model-7.out")
    relayed_path = tmp_path / "relayed.txt"
    _write_relayed(relayed_path, [*content_lines, anomaly_line])

    result = CliRunner().invoke(
        main, ["--relayed", str(relayed_path), "--job-log", str(job_log)]
    )

    assert result.exit_code == 1, result.output
    assert "relay: FAIL" in result.output
    assert "anomalies=1" in result.output
    assert "log truncated: model-7.out" in result.output


def test_check_relay_log_never_appeared_marker_is_an_anomaly_and_fails(
    tmp_path: Path, relay_logger: tuple[logging.Logger, io.StringIO]
) -> None:
    logger, buf = relay_logger
    job_log = tmp_path / "model-7.out"
    job_log.write_bytes(_JOB_LOG_BYTES)
    raw_lines = _raw_job_lines(job_log)
    content_lines = _relayed_text_lines(logger, buf, raw_lines)
    marker = "[hpcmu] log never appeared: model-1.out"
    anomaly_line = _relay(logger, buf, marker)
    relayed_path = tmp_path / "relayed.txt"
    _write_relayed(relayed_path, [*content_lines, anomaly_line])

    result = CliRunner().invoke(
        main, ["--relayed", str(relayed_path), "--job-log", str(job_log)]
    )

    assert result.exit_code == 1, result.output
    assert "relay: FAIL" in result.output
    assert "anomalies=1" in result.output
    assert f"anomalies:\n  {marker}" in result.output


def test_check_relay_ambiguous_marker_in_job_log_fails(
    tmp_path: Path, relay_logger: tuple[logging.Logger, io.StringIO]
) -> None:
    logger, buf = relay_logger
    job_log = tmp_path / "model-7.out"
    job_log.write_bytes(b"[hpcmu] not really a cli marker\nsecond line\n")
    raw_lines = _raw_job_lines(job_log)
    content_lines = _relayed_text_lines(logger, buf, raw_lines)
    relayed_path = tmp_path / "relayed.txt"
    _write_relayed(relayed_path, content_lines)

    result = CliRunner().invoke(
        main, ["--relayed", str(relayed_path), "--job-log", str(job_log)]
    )

    assert result.exit_code == 1, result.output
    assert "relay: FAIL" in result.output
    assert "ambiguous marker in job log" in result.output


# -------------------------------------------------------------------- AC: two_jobs


def test_check_relay_two_jobs_in_order_passes_reversed_fails(
    tmp_path: Path, relay_logger: tuple[logging.Logger, io.StringIO]
) -> None:
    logger, buf = relay_logger
    model_log = tmp_path / "model-7.out"
    finalize_log = tmp_path / "finalize-8.out"
    model_log.write_bytes(b"model line one\nmodel line two\n")
    finalize_log.write_bytes(b"finalize line one\nfinalize line two\n")

    model_relayed = _relayed_text_lines(logger, buf, _raw_job_lines(model_log))
    finalize_relayed = _relayed_text_lines(
        logger, buf, _raw_job_lines(finalize_log)
    )
    relayed_path = tmp_path / "relayed.txt"
    _write_relayed(relayed_path, [*model_relayed, *finalize_relayed])

    ok_result = CliRunner().invoke(
        main,
        [
            "--relayed",
            str(relayed_path),
            "--job-log",
            str(model_log),
            "--job-log",
            str(finalize_log),
        ],
    )
    assert ok_result.exit_code == 0, ok_result.output
    assert "relay: PASS" in ok_result.output

    reversed_result = CliRunner().invoke(
        main,
        [
            "--relayed",
            str(relayed_path),
            "--job-log",
            str(finalize_log),
            "--job-log",
            str(model_log),
        ],
    )
    assert reversed_result.exit_code == 1, reversed_result.output
    assert "relay: FAIL" in reversed_result.output


# ----------------------------------------------------------- usage errors (exit 2)


def test_check_relay_no_job_log_is_usage_error(tmp_path: Path) -> None:
    relayed_path = tmp_path / "relayed.txt"
    relayed_path.write_text("", encoding="utf-8")

    result = CliRunner().invoke(main, ["--relayed", str(relayed_path)])

    assert result.exit_code == 2, result.stderr
    assert result.stderr.startswith("check_relay:")


def test_check_relay_unreadable_job_log_is_usage_error(tmp_path: Path) -> None:
    relayed_path = tmp_path / "relayed.txt"
    relayed_path.write_text("", encoding="utf-8")
    missing_job_log = tmp_path / "missing.out"

    result = CliRunner().invoke(
        main,
        ["--relayed", str(relayed_path), "--job-log", str(missing_job_log)],
    )

    assert result.exit_code == 2, result.stderr
    assert result.stderr.startswith("check_relay:")


def test_check_relay_missing_relayed_file_is_usage_error(
    tmp_path: Path,
) -> None:
    job_log = tmp_path / "model-1.out"
    job_log.write_text("line\n", encoding="utf-8")
    missing_relayed = tmp_path / "missing-relayed.txt"

    result = CliRunner().invoke(
        main,
        ["--relayed", str(missing_relayed), "--job-log", str(job_log)],
    )

    assert result.exit_code == 2, result.stderr
    assert result.stderr.startswith("check_relay:")


# --------------------------------------------------------- unit: relayed_lines


def test_relayed_lines_extracts_message_without_outer_prefix(
    tmp_path: Path,
) -> None:
    path = tmp_path / "relayed.txt"
    path.write_text(
        "2026-10-03 00:00:00,000 INFO hpc_model_utils.job: hello\n",
        encoding="utf-8",
    )

    result = relayed_lines(path)

    assert result.lines == ["hello"]
    assert result.informational == 0
    assert result.anomalies == []


def test_relayed_lines_extracts_message_with_outer_prefix(
    tmp_path: Path,
) -> None:
    path = tmp_path / "relayed.txt"
    path.write_text(
        "2026-10-03T00:00:00Z [ExecutionOutput] run: "
        "2026-10-03 00:00:00,000 INFO hpc_model_utils.job: hello\n",
        encoding="utf-8",
    )

    result = relayed_lines(path)

    assert result.lines == ["hello"]


def test_relayed_lines_classifies_informational_and_anomaly_markers(
    tmp_path: Path,
) -> None:
    path = tmp_path / "relayed.txt"
    path.write_text(
        "\n".join(
            [
                "t INFO hpc_model_utils.job: "
                "[hpcmu] heartbeat: job 1 RUNNING None",
                "t INFO hpc_model_utils.job: [hpcmu] squeue failed (1/3): boom",
                "t INFO hpc_model_utils.job: "
                "[hpcmu] job 1 requeued (PENDING): deadline cleared",
                "t INFO hpc_model_utils.job: "
                "[hpcmu] job 1 restarted (attempt 2): deadline reset",
                "t INFO hpc_model_utils.job: "
                "[hpcmu] log truncated: model-1.out",
                "t INFO hpc_model_utils.job: [hpcmu] log rotated: model-1.out",
                "t INFO hpc_model_utils.job: "
                "[hpcmu] log reopen failed: model-1.out: boom",
                "t INFO hpc_model_utils.job: "
                "[hpcmu] log open failed: model-1.out: boom",
                "t INFO hpc_model_utils.job: real output",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = relayed_lines(path)

    assert result.lines == ["real output"]
    assert result.informational == 4
    assert len(result.anomalies) == 4


# --------------------------------------------------------------- unit: compare


def test_compare_equal_sequences_pass() -> None:
    verdict = compare(["a", "b"], ["a", "b"])

    assert verdict.ok
    assert verdict.divergence is None


def test_compare_lost_line_reports_index_and_kind() -> None:
    verdict = compare(["a", "b", "c"], ["a", "c"])

    assert not verdict.ok
    assert verdict.divergence is not None
    assert verdict.divergence.index == 1
    assert verdict.divergence.kind == "lost"


def test_compare_extra_duplicated_line_reports_index_and_kind() -> None:
    verdict = compare(["a", "b", "c"], ["a", "b", "b", "c"])

    assert not verdict.ok
    assert verdict.divergence is not None
    assert verdict.divergence.index == 2
    assert verdict.divergence.kind == "extra"


def test_compare_reordered_sequence_fails() -> None:
    verdict = compare(["a", "b"], ["b", "a"])

    assert not verdict.ok


def test_compare_duplicated_before_later_lost_line_reports_extra_kind() -> None:
    """A duplicate sits right at the first divergence; an unrelated lost
    line further along in the window must not override that local cause."""
    verdict = compare(["a", "b", "c", "d", "e"], ["a", "b", "c", "c", "d"])

    assert not verdict.ok
    assert verdict.divergence is not None
    assert verdict.divergence.index == 3
    assert verdict.divergence.kind == "extra"


def test_compare_lost_before_later_duplicated_line_reports_lost_kind() -> None:
    """A lost line sits right at the first divergence; an unrelated
    duplicate further along in the window must not override that cause."""
    verdict = compare(["a", "b", "c", "d", "e"], ["a", "c", "d", "d", "e"])

    assert not verdict.ok
    assert verdict.divergence is not None
    assert verdict.divergence.index == 1
    assert verdict.divergence.kind == "lost"


def test_compare_altered_line_is_changed_not_lost_or_duplicated() -> None:
    verdict = compare(["a", "b", "c"], ["a", "X", "c"])

    assert not verdict.ok
    assert verdict.divergence is not None
    assert verdict.divergence.index == 1
    assert verdict.divergence.kind == "changed"


def test_compare_ambiguous_marker_short_circuits() -> None:
    verdict = compare(["[hpcmu] oops"], ["anything"])

    assert not verdict.ok
    assert verdict.reason == "ambiguous marker in job log"
    assert verdict.divergence is None


# --------------------------------------------------------- unit: ambiguous_marker


def test_ambiguous_marker_detects_hpcmu_prefixed_job_line() -> None:
    assert ambiguous_marker(["normal line", "[hpcmu] oops"])
    assert not ambiguous_marker(["normal line", "other"])
