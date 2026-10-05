"""ADR-046/R41/R80/R136: the LogTail reader.

``LogTail`` tails one ``.hpcmu/logs/<phase>-<jobid>.out`` file while a
Slurm job runs. Bytes are split on ``\\n`` *before* a UTF-8
``errors="replace"`` decode, so a multibyte character split across two
reads is reassembled rather than becoming two replacement characters
(the v1 bug, E3). A rewrite that shrinks the file, or that keeps the
same size but changes the first line, is detected as truncation; a
path replaced by a new inode is detected as rotation. Each event is
reported as one marker line interleaved with the real output, so no
line is lost or repeated.

The caller holds one ``LogTail`` per job log for the run's lifetime,
calling ``poll()`` on an interval, and calls ``close()`` exactly once,
after the job has left the queue and the settle window has elapsed,
for the terminal drain. ``close()`` reopens the path with a fresh
``os.open`` so NFS close-to-open revalidation applies: reads through
an already-open fd trust the attribute cache (F15), which can lag the
writer by up to ``acregmax``, but a fresh ``open()`` forces a
revalidation and therefore sees every byte once the job has actually
finished writing.

Close-to-open does not cover a path that was never opened. While a
job is PENDING, ``poll()`` keeps trying to open the missing log, so the
NFS client caches the negative lookup ("no such file") and revalidates
it only when the parent directory's cached attributes expire, up to
``acdirmax`` (60 s, F15). A fast-failing job whose log appears just
before it ends can therefore stay invisible past the settle window
(M14, run ``e60d6b97``: 30 lines lost). ``ever_opened`` lets the settle
loop keep polling such a log for ``missing_log_grace``, and ``close()``
emits a ``log never appeared`` marker instead of losing it silently.
A job cancelled before it ever ran writes no log, so ``follow()`` skips
the grace for it (``CANCELLED`` and never seen past PENDING); the
marker still reports the missing log.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from hpc_model_utils.core.settings import EngineSettings
from hpc_model_utils.infra.errors import SchedulerCommandError
from hpc_model_utils.infra.slurm import NON_TERMINAL_STATES, JobState

_READ_CHUNK_BYTES = 64 * 1024
_MAX_LINE_BYTES = 1024 * 1024


def _decode_line(raw: bytes) -> str:
    if raw.endswith(b"\r"):
        raw = raw[:-1]
    return raw.decode("utf-8", errors="replace")


def _consume(buf: bytearray, chunk: bytes) -> tuple[list[str], bytearray]:
    """Split complete lines off ``buf + chunk``.

    A span with no ``\\n`` within the next ``_MAX_LINE_BYTES`` bytes is
    flushed as an undelimited piece of that size, so a line longer
    than that bound is returned in several pieces instead of growing
    the buffer without limit while its terminating newline is still
    pending.
    """
    data = bytes(buf) + chunk
    lines: list[str] = []
    start = 0
    n = len(data)
    while start < n:
        idx = data.find(b"\n", start)
        if idx != -1 and idx - start <= _MAX_LINE_BYTES:
            lines.append(_decode_line(data[start:idx]))
            start = idx + 1
            continue
        if n - start > _MAX_LINE_BYTES:
            piece_end = start + _MAX_LINE_BYTES
            lines.append(
                data[start:piece_end].decode("utf-8", errors="replace")
            )
            start = piece_end
            continue
        break
    return lines, bytearray(data[start:])


def _flush_partial(buf: bytearray) -> list[str]:
    if not buf:
        return []
    return [bytes(buf).decode("utf-8", errors="replace")]


def _grow_head(
    prefix: bytearray, chunk: bytes, head_bytes: int
) -> tuple[bytearray, bytes | None]:
    if len(prefix) < head_bytes:
        prefix.extend(chunk[: head_bytes - len(prefix)])
    idx = prefix.find(b"\n")
    if idx != -1:
        return prefix, bytes(prefix[:idx])
    if len(prefix) >= head_bytes:
        return prefix, bytes(prefix[:head_bytes])
    return prefix, None


def _open_fd(path: Path) -> tuple[int, tuple[int, int]]:
    """Open ``path`` and fstat it, raising ``OSError`` (including
    ``FileNotFoundError``) on failure instead of swallowing it, so the
    caller can distinguish a missing path from a persistent error."""
    fd = os.open(path, os.O_RDONLY)
    try:
        st = os.fstat(fd)
    except OSError:
        os.close(fd)
        raise
    return fd, (st.st_dev, st.st_ino)


def _truncated_marker(name: str) -> str:
    return f"[hpcmu] log truncated: {name}"


def _rotated_marker(name: str) -> str:
    return f"[hpcmu] log rotated: {name}"


def _reopen_failed_marker(name: str, error: OSError) -> str:
    return f"[hpcmu] log reopen failed: {name}: {error}"


def _open_failed_marker(name: str, error: OSError) -> str:
    return f"[hpcmu] log open failed: {name}: {error}"


def _never_appeared_marker(name: str) -> str:
    return f"[hpcmu] log never appeared: {name}"


class LogTail:
    """Tail ``path``, surviving truncation and rotation.

    ``head_bytes`` bounds how many leading bytes of the file's first
    line are remembered to detect a same-size rewrite.
    """

    def __init__(self, path: Path, *, head_bytes: int = 256) -> None:
        self._path = path
        self._head_bytes = head_bytes
        self._fd: int | None = None
        self._ident: tuple[int, int] | None = None
        self._offset = 0
        self._buf = bytearray()
        self._head: bytes | None = None
        self._head_prefix = bytearray()
        self._closed = False
        self._last_errno: int | None = None
        self._ever_opened = False

    @property
    def ever_opened(self) -> bool:
        return self._ever_opened

    def poll(self) -> list[str]:
        if self._closed:
            return []
        lines: list[str] = []
        if self._fd is None:
            lines.extend(self._open_initial())
            if self._fd is None:
                return lines
        rotated, rotation_error_lines = self._check_rotated()
        lines.extend(rotation_error_lines)
        if rotated:
            lines.extend(self._handle_rotation())
            if self._fd is None:
                return lines
        lines.extend(self._handle_truncation())
        lines.extend(self._read_available())
        return lines

    def close(self) -> list[str]:
        if self._closed:
            return []
        self._closed = True
        name = self._path.name
        try:
            fresh_fd = os.open(self._path, os.O_RDONLY)
        except FileNotFoundError:
            if not self._ever_opened:
                return [_never_appeared_marker(name)]
            lines = self._drain_held()
            self._close_held()
            return lines
        except OSError as exc:
            lines = [_reopen_failed_marker(name, exc)]
            lines.extend(self._drain_held())
            self._close_held()
            return lines
        self._ever_opened = True
        try:
            lines = self._drain_fresh(fresh_fd, name)
        finally:
            self._close_held()
            try:
                os.close(fresh_fd)
            except OSError:
                pass
        return lines

    def _open_initial(self) -> list[str]:
        try:
            self._fd, self._ident = _open_fd(self._path)
        except FileNotFoundError:
            return []
        except OSError as exc:
            return self._report_open_error(exc)
        self._ever_opened = True
        self._last_errno = None
        return []

    def _check_rotated(self) -> tuple[bool, list[str]]:
        try:
            st = os.stat(self._path)
        except FileNotFoundError:
            return True, []
        except OSError as exc:
            return False, self._report_open_error(exc)
        self._last_errno = None
        return (st.st_dev, st.st_ino) != self._ident, []

    def _report_open_error(self, exc: OSError) -> list[str]:
        if exc.errno == self._last_errno:
            return []
        self._last_errno = exc.errno
        return [_open_failed_marker(self._path.name, exc)]

    def _handle_rotation(self) -> list[str]:
        name = self._path.name
        lines = self._drain_held()
        lines.append(_rotated_marker(name))
        self._close_held()
        self._reset_epoch()
        try:
            self._fd, self._ident = _open_fd(self._path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            lines.extend(self._report_open_error(exc))
        else:
            self._last_errno = None
        return lines

    def _handle_truncation(self) -> list[str]:
        if self._fd is None:
            return []
        try:
            size = os.fstat(self._fd).st_size
        except OSError:
            return []
        truncated = size < self._offset
        if not truncated and self._head is not None:
            try:
                current_head = os.pread(self._fd, len(self._head), 0)
            except OSError:
                current_head = self._head
            truncated = current_head != self._head
        if not truncated:
            return []
        lines = _flush_partial(self._buf)
        lines.append(_truncated_marker(self._path.name))
        self._reset_epoch()
        return lines

    def _read_available(self) -> list[str]:
        if self._fd is None:
            return []
        chunk = self._read_fd(self._fd, self._offset)
        if not chunk:
            return []
        if self._head is None:
            self._head_prefix, head = _grow_head(
                self._head_prefix, chunk, self._head_bytes
            )
            if head is not None:
                self._head = head
        lines, self._buf = _consume(self._buf, chunk)
        self._offset += len(chunk)
        return lines

    def _drain_held(self) -> list[str]:
        if self._fd is None:
            return []
        chunk = self._read_fd(self._fd, self._offset)
        lines, self._buf = _consume(self._buf, chunk)
        self._offset += len(chunk)
        lines.extend(_flush_partial(self._buf))
        self._buf = bytearray()
        return lines

    def _close_held(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        self._ident = None

    def _drain_fresh(self, fresh_fd: int, name: str) -> list[str]:
        try:
            fresh_st = os.fstat(fresh_fd)
            fresh_ident: tuple[int, int] | None = (
                fresh_st.st_dev,
                fresh_st.st_ino,
            )
        except OSError:
            fresh_ident = None
        identity_matches = self._ident is None or fresh_ident == self._ident

        head_matches = True
        if identity_matches and self._head is not None:
            try:
                fresh_head = os.pread(fresh_fd, len(self._head), 0)
            except OSError:
                fresh_head = b""
            head_matches = fresh_head == self._head

        lines: list[str] = []
        if identity_matches and head_matches:
            read_offset = self._offset
            buf = self._buf
        elif not identity_matches:
            lines.extend(self._drain_held())
            lines.append(_rotated_marker(name))
            read_offset = 0
            buf = bytearray()
        else:
            lines.extend(_flush_partial(self._buf))
            lines.append(_truncated_marker(name))
            read_offset = 0
            buf = bytearray()

        chunk = self._read_fd(fresh_fd, read_offset)
        new_lines, buf = _consume(buf, chunk)
        lines.extend(new_lines)
        lines.extend(_flush_partial(buf))
        return lines

    def _read_fd(self, fd: int, offset: int) -> bytes:
        """Read new bytes on ``fd`` from ``offset`` to its current EOF,
        in ``_READ_CHUNK_BYTES`` pieces, never raising.

        Test-only seam: tests may replace this per-instance to make
        reads on a chosen fd (typically the held fd) report a stale
        size, simulating an NFS attribute-cache lag without a real
        NFS mount.
        """
        chunks: list[bytes] = []
        try:
            size = os.fstat(fd).st_size
            pos = offset
            while pos < size:
                piece = os.pread(fd, min(_READ_CHUNK_BYTES, size - pos), pos)
                if not piece:
                    break
                chunks.append(piece)
                pos += len(piece)
        except OSError:
            pass
        return b"".join(chunks)

    def _reset_epoch(self) -> None:
        self._offset = 0
        self._buf = bytearray()
        self._head = None
        self._head_prefix = bytearray()


class SlurmLike(Protocol):
    """Structural seam for ``follow``'s ``slurm`` argument. A real
    ``infra.slurm.Slurm`` satisfies this without inheriting from it;
    tests pass a scripted double instead of a live scheduler."""

    def job_state(self, job_id: str) -> JobState | None: ...


NEVER_CLEARING = ("PartitionDown", "PartitionInactive", "BadConstraints")
PENDING_LIKE = frozenset({"PENDING", "REQUEUED", "REQUEUE_HOLD", "REQUEUE_FED"})

_REQ_NODE_NOT_AVAIL = "ReqNodeNotAvail"
_UNAVAILABLE_NODES = "UnavailableNodes:"


def _never_clearing(reason: str) -> bool:
    """R120: a pending reason that will never clear on its own."""
    if reason in NEVER_CLEARING:
        return True
    if not reason.startswith(_REQ_NODE_NOT_AVAIL):
        return False
    idx = reason.find(_UNAVAILABLE_NODES)
    if idx == -1:
        return True
    return reason[idx + len(_UNAVAILABLE_NODES) :].strip() == ""


class FollowAborted(Exception):
    def __init__(
        self,
        kind: Literal["deadline", "pending_cap", "pending_reason"],
        reason: str,
        job_id: str,
    ) -> None:
        self.kind = kind
        self.reason = reason
        self.job_id = job_id
        super().__init__(f"job {job_id} aborted ({kind}): {reason}")


@dataclass(frozen=True)
class FollowResult:
    job_id: str
    last_state: str | None
    attempts: int


def _settle(
    job_id: str,
    last_state: str | None,
    tail: LogTail,
    emit: Callable[[str], None],
    attempts: int,
    *,
    settle_window: float,
    missing_log_grace: float,
    poll_interval: float,
    now: Callable[[], float],
    sleep: Callable[[float], None],
) -> FollowResult:
    """ADR-046: full settle, then reopen. Keeps polling the held tail
    for the whole ``settle_window``, never stopping on the first empty
    poll, then drains the terminal content via ``tail.close()``. A log
    that was never opened is polled on for ``missing_log_grace``, so a
    cached NFS negative lookup can expire."""
    settle_start = now()
    while (elapsed := now() - settle_start) < settle_window or (
        not tail.ever_opened and elapsed < missing_log_grace
    ):
        for line in tail.poll():
            emit(line)
        sleep(poll_interval)
    for line in tail.close():
        emit(line)
    return FollowResult(job_id, last_state, attempts)


def follow(
    job_id: str,
    tail: LogTail,
    slurm: SlurmLike,
    emit: Callable[[str], None],
    *,
    settings: EngineSettings,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> FollowResult:
    """ADR-046/R38/R46/R95/R120/R136: follow ``job_id`` until it
    leaves the queue, relaying its log and raising ``FollowAborted``
    on a deadline or a fail-fast pending condition. Never scancels;
    the caller owns cancellation."""
    pending_since: float | None = None
    started: float | None = None
    attempt_start_time: datetime | None = None
    deadline: float | None = None
    last_output = now()
    failures = 0
    attempts = 0
    prev_state: str | None = None

    while True:
        try:
            st = slurm.job_state(job_id)
        except SchedulerCommandError as exc:
            failures += 1
            emit(
                f"[hpcmu] squeue failed "
                f"({failures}/{settings.squeue_failure_budget}): {exc}"
            )
            if failures > settings.squeue_failure_budget:
                raise
            sleep(settings.poll_interval)
            continue
        failures = 0

        for line in tail.poll():
            emit(line)
            last_output = now()

        if st is None or st.state not in NON_TERMINAL_STATES:
            # A job cancelled before it ran has no log by construction,
            # so it skips the missing-log grace. Any other terminal
            # state, and a job already gone from the queue (None),
            # keep it: a fast-failing job is FAILED or COMPLETED.
            never_started_cancel = (
                st is not None and st.state == "CANCELLED" and started is None
            )
            return _settle(
                job_id,
                st.state if st is not None else None,
                tail,
                emit,
                attempts,
                settle_window=settings.settle_window,
                missing_log_grace=(
                    0.0 if never_started_cancel else settings.missing_log_grace
                ),
                poll_interval=settings.poll_interval,
                now=now,
                sleep=sleep,
            )

        if st.state in PENDING_LIKE:
            if started is not None:
                emit(
                    f"[hpcmu] job {job_id} requeued "
                    f"({st.state}): deadline cleared"
                )
                started = None
                deadline = None
            if prev_state not in PENDING_LIKE:
                pending_since = now()
            if _never_clearing(st.reason):
                raise FollowAborted("pending_reason", st.reason, job_id)
            assert pending_since is not None
            if now() - pending_since > settings.pending_cap:
                raise FollowAborted(
                    "pending_cap",
                    f"pending for more than {settings.pending_cap}s",
                    job_id,
                )
        else:
            is_new_attempt = started is None or (
                st.start_time is not None
                and st.start_time != attempt_start_time
            )
            if is_new_attempt:
                is_boot = prev_state == "CONFIGURING" and started is not None
                attempt_start_time = st.start_time
                started = now()
                deadline = (
                    started
                    + st.time_limit.total_seconds()
                    + settings.follow_margin
                    if st.time_limit is not None
                    else None
                )
                if not is_boot:
                    attempts += 1
                    if attempts > 1:
                        emit(
                            f"[hpcmu] job {job_id} restarted "
                            f"(attempt {attempts}): deadline reset"
                        )

        prev_state = st.state

        if deadline is not None and now() > deadline:
            raise FollowAborted("deadline", "job exceeded its deadline", job_id)

        if now() - last_output >= settings.heartbeat_after:
            emit(f"[hpcmu] heartbeat: job {job_id} {st.state} {st.reason}")
            last_output = now()

        sleep(settings.poll_interval)
