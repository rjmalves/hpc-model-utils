"""ADR-046/R41/R80/R136: LogTail truncation/rotation replay tests (E3)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hpc_model_utils.core.follow import LogTail

_NAME = "model-1.out"


def test_log_tail_poll_before_file_exists_returns_empty_list(
    tmp_path: Path,
) -> None:
    tail = LogTail(tmp_path / _NAME)
    assert tail.poll() == []


def test_log_tail_poll_line_without_newline_yet_returns_empty_list(
    tmp_path: Path,
) -> None:
    path = tmp_path / _NAME
    path.write_bytes(b"partial, no newline")
    tail = LogTail(path)
    assert tail.poll() == []


def test_log_tail_poll_missing_path_emits_no_marker(
    tmp_path: Path,
) -> None:
    tail = LogTail(tmp_path / _NAME)
    assert tail.poll() == []
    assert tail.poll() == []


def test_log_tail_poll_open_failure_emits_one_marker_until_it_clears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / _NAME
    real_open = os.open
    calls = {"n": 0}

    def flaky_open(file: Path, flags: int) -> int:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(13, "Permission denied")
        return real_open(file, flags)

    monkeypatch.setattr(os, "open", flaky_open)
    tail = LogTail(path)

    first = tail.poll()
    second = tail.poll()
    assert first == [
        f"[hpcmu] log open failed: {_NAME}: [Errno 13] Permission denied"
    ]
    assert second == []

    monkeypatch.setattr(os, "open", real_open)
    path.write_text("L1\n")
    assert tail.poll() == ["L1"]


def test_log_tail_e3_shorter_rewrite_emits_truncated_marker(
    tmp_path: Path,
) -> None:
    path = tmp_path / _NAME
    path.write_text("HPCMU_START a\nL1\nL2\nL3\n")
    tail = LogTail(path)
    first = tail.poll()
    path.write_text("HPCMU_START b\nM1\n")
    second = tail.poll()
    assert first + second == [
        "HPCMU_START a",
        "L1",
        "L2",
        "L3",
        f"[hpcmu] log truncated: {_NAME}",
        "HPCMU_START b",
        "M1",
    ]


def test_log_tail_e3_longer_rewrite_with_changed_head_emits_truncated_marker(
    tmp_path: Path,
) -> None:
    path = tmp_path / _NAME
    original = "HPCMU_START a\nL1\nL2\nL3\n"
    path.write_text(original)
    tail = LogTail(path)
    tail.poll()

    longer = "HPCMU_START c\n" + "".join(f"N{i}\n" for i in range(10))
    assert len(longer) > len(original)
    path.write_text(longer)

    result = tail.poll()
    assert result == [
        f"[hpcmu] log truncated: {_NAME}",
        "HPCMU_START c",
        *(f"N{i}" for i in range(10)),
    ]
    assert "L1" not in result
    assert "L2" not in result
    assert "L3" not in result


def test_log_tail_poll_size_only_truncation_with_unchanged_head(
    tmp_path: Path,
) -> None:
    path = tmp_path / _NAME
    path.write_text("HEAD\nL1\nL2\nL3\n")
    tail = LogTail(path)
    tail.poll()
    path.write_text("HEAD\nM1\n")
    result = tail.poll()
    assert result == [f"[hpcmu] log truncated: {_NAME}", "HEAD", "M1"]


def test_log_tail_poll_rotation_emits_old_tail_marker_then_new_lines(
    tmp_path: Path,
) -> None:
    path = tmp_path / _NAME
    path.write_text("OLD1\n")
    tail = LogTail(path)
    assert tail.poll() == ["OLD1"]

    with path.open("a") as f:
        f.write("OLD2\n")
    os.rename(path, path.with_suffix(".1"))
    path.write_text("NEW1\nNEW2\n")

    assert tail.poll() == [
        "OLD2",
        f"[hpcmu] log rotated: {_NAME}",
        "NEW1",
        "NEW2",
    ]


def test_log_tail_poll_rotation_new_file_not_yet_written_drains_old_only(
    tmp_path: Path,
) -> None:
    path = tmp_path / _NAME
    path.write_text("OLD1\n")
    tail = LogTail(path)
    assert tail.poll() == ["OLD1"]

    os.rename(path, path.with_suffix(".1"))
    assert tail.poll() == [f"[hpcmu] log rotated: {_NAME}"]
    assert tail.poll() == []

    path.write_text("NEW1\n")
    assert tail.poll() == ["NEW1"]


def test_log_tail_poll_multibyte_split_across_writes_reassembles_char(
    tmp_path: Path,
) -> None:
    path = tmp_path / _NAME
    path.write_bytes(b"caf\xc3")
    tail = LogTail(path)
    assert tail.poll() == []

    with path.open("ab") as f:
        f.write(b"\xa9 ok\n\xff\n")
    assert tail.poll() == ["café ok", "�"]


def test_log_tail_poll_line_over_one_mebibyte_flushed_in_pieces(
    tmp_path: Path,
) -> None:
    path = tmp_path / _NAME
    huge = "x" * (2 * 1024 * 1024 + 500)
    path.write_text(huge + "\n")
    tail = LogTail(path)

    result = tail.poll()

    assert len(result) == 3
    assert len(result[0]) == 1024 * 1024
    assert len(result[1]) == 1024 * 1024
    assert len(result[2]) == 500
    assert "".join(result) == huge


def test_log_tail_poll_oserror_mid_read_returns_partial_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / _NAME
    path.write_text("L1\nL2\n")
    tail = LogTail(path)

    real_pread = os.pread

    def failing_pread(fd: int, n: int, offset: int) -> bytes:
        raise OSError("simulated transient I/O error")

    monkeypatch.setattr(os, "pread", failing_pread)
    assert tail.poll() == []

    monkeypatch.setattr(os, "pread", real_pread)
    assert tail.poll() == ["L1", "L2"]


def test_log_tail_poll_head_recorded_after_truncation_prevents_false_positive(
    tmp_path: Path,
) -> None:
    path = tmp_path / _NAME
    path.write_text("HEAD_A\nL1\n")
    tail = LogTail(path)
    tail.poll()

    path.write_text("HEAD_B\nM1\n")
    truncated_result = tail.poll()
    assert truncated_result[0] == f"[hpcmu] log truncated: {_NAME}"

    with path.open("a") as f:
        f.write("M2\n")
    assert tail.poll() == ["M2"]


def test_log_tail_close_never_created_path_returns_never_appeared_marker(
    tmp_path: Path,
) -> None:
    tail = LogTail(tmp_path / _NAME)
    assert tail.close() == [f"[hpcmu] log never appeared: {_NAME}"]
    assert tail.ever_opened is False


def test_log_tail_close_opened_then_deleted_path_drains_held_fd_without_marker(
    tmp_path: Path,
) -> None:
    path = tmp_path / _NAME
    path.write_text("L1\n")
    tail = LogTail(path)
    assert tail.poll() == ["L1"]

    with path.open("a") as f:
        f.write("L2\nEND")
    os.remove(path)

    assert tail.close() == ["L2", "END"]


def test_log_tail_ever_opened_is_false_while_path_is_missing(
    tmp_path: Path,
) -> None:
    tail = LogTail(tmp_path / _NAME)
    assert tail.ever_opened is False
    assert tail.poll() == []
    assert tail.ever_opened is False


def test_log_tail_ever_opened_turns_true_on_first_open_and_survives_rotation(
    tmp_path: Path,
) -> None:
    path = tmp_path / _NAME
    tail = LogTail(path)
    assert tail.poll() == []

    path.write_text("OLD1\n")
    assert tail.poll() == ["OLD1"]
    assert tail.ever_opened is True

    replacement = tmp_path / "replacement"
    replacement.write_text("NEW1\n")
    os.replace(replacement, path)
    assert tail.poll() == [f"[hpcmu] log rotated: {_NAME}", "NEW1"]
    assert tail.ever_opened is True


def test_log_tail_ever_opened_turns_true_when_close_opens_the_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / _NAME
    path.write_text("L1\n")
    tail = LogTail(path)
    assert tail.ever_opened is False
    assert tail.close() == ["L1"]
    assert tail.ever_opened is True


def test_log_tail_close_called_twice_second_call_returns_empty_list(
    tmp_path: Path,
) -> None:
    path = tmp_path / _NAME
    path.write_text("L1\n")
    tail = LogTail(path)
    tail.poll()
    assert tail.close() == []
    assert tail.close() == []


def test_log_tail_close_flushes_partial_line_without_trailing_newline(
    tmp_path: Path,
) -> None:
    path = tmp_path / _NAME
    path.write_text("L1\npartial-tail")
    tail = LogTail(path)
    assert tail.poll() == ["L1"]
    assert tail.close() == ["partial-tail"]


def test_log_tail_close_after_inode_replacement_emits_rotation_marker(
    tmp_path: Path,
) -> None:
    path = tmp_path / _NAME
    path.write_text("OLD1\n")
    tail = LogTail(path)
    assert tail.poll() == ["OLD1"]

    os.remove(path)
    path.write_text("NEW1\nNEW2\n")

    assert tail.close() == [
        f"[hpcmu] log rotated: {_NAME}",
        "NEW1",
        "NEW2",
    ]


def test_log_tail_close_head_change_same_inode_emits_truncated_marker(
    tmp_path: Path,
) -> None:
    path = tmp_path / _NAME
    path.write_text("HPCMU_START a\nL1\nL2\nL3\n")
    tail = LogTail(path)
    assert tail.poll() == ["HPCMU_START a", "L1", "L2", "L3"]

    path.write_text("HPCMU_START b\nM1\n")

    assert tail.close() == [
        f"[hpcmu] log truncated: {_NAME}",
        "HPCMU_START b",
        "M1",
    ]


def test_log_tail_close_reopen_oserror_emits_reopen_failed_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / _NAME
    path.write_text("L1\n")
    tail = LogTail(path)
    tail.poll()

    real_open = os.open

    def failing_open(file: Path, flags: int) -> int:
        if Path(file) == path:
            raise PermissionError(13, "Permission denied")
        return real_open(file, flags)

    monkeypatch.setattr(os, "open", failing_open)

    result = tail.close()
    assert result == [
        f"[hpcmu] log reopen failed: {_NAME}: [Errno 13] Permission denied"
    ]


def test_log_tail_close_stale_held_fd_reads_through_fresh_descriptor(
    tmp_path: Path,
) -> None:
    path = tmp_path / _NAME
    path.write_text("L1\n")
    tail = LogTail(path)
    assert tail.poll() == ["L1"]

    held_fd = tail._fd  # test-only seam, see LogTail._read_fd docstring
    assert held_fd is not None
    real_read_fd = LogTail._read_fd

    def stale_held_read(fd: int, offset: int) -> bytes:
        if fd == held_fd:
            return b""
        return real_read_fd(tail, fd, offset)

    tail._read_fd = stale_held_read  # type: ignore[method-assign]

    with path.open("a") as f:
        f.write("L2\nEND")

    assert tail.close() == ["L2", "END"]
