"""infra.shell: argv-only subprocess runner with merged-output line relay.

ADR-028 requires argv-only process creation (no shell flag, no string
joining) so that no deck member name or model argument can ever reach a
shell. ADR-006 and R37 require that children never inherit fd 1 raw:
stdout and stderr are merged into one pipe and relayed line by line
through ``on_line``.

Thread model: a single daemon reader thread decodes the child's merged
output and calls ``on_line`` once per line, in order. For a single
``run()`` call, calls to ``on_line`` are therefore already serialized and
never concurrent with each other, since the reader thread invokes them
one at a time and waits for each call to return before reading the next
line. If the same callback is shared across *concurrent* ``run()`` calls
(each with its own reader thread), that callback itself must be
thread-safe; prefer logging through the standard ``logging`` module,
whose handlers hold a lock, over writing to ``sys.stdout`` directly.
"""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from hpc_model_utils.infra.errors import ShellCommandError

_POLL_INTERVAL = 0.1
_POST_EXIT_IDLE_TIMEOUT = 0.5
_POST_EXIT_HARD_CAP = 10.0
_READER_SAFETY_MARGIN = 1.0


def _decode(raw_line: bytes) -> str:
    return raw_line.decode("utf-8", errors="replace").rstrip("\r")


@dataclass(frozen=True)
class ShellResult:
    argv: tuple[str, ...]
    returncode: int
    timed_out: bool
    output: tuple[str, ...]


def run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    on_line: Callable[[str], None] | None = None,
    keep_output: bool = True,
) -> ShellResult:
    """Run argv with no shell, merging the child's stderr into stdout.

    Every decoded line is relayed through ``on_line`` with no
    deduplication, and kept in ``output`` when ``keep_output`` is true. A
    line is decoded with ``errors="replace"`` and only its trailing
    ``\\r``/``\\n`` is stripped; a bare ``\\r`` in the middle of a line is
    passed through verbatim. A non-zero exit is not an error; callers
    decide what it means. On timeout, the whole process group is killed
    and the result reports ``timed_out=True`` with ``returncode=-9``.

    If ``on_line`` raises, the reader records the exception, stops
    relaying immediately (no further lines are decoded or delivered),
    and the main thread kills the child's whole process group the same
    way a timeout does. ``run()`` then re-raises that exception
    unchanged instead of returning a ``ShellResult``, so the caller sees
    its own error. This is checked even when ``timeout`` is ``None``, so
    a callback that raises cannot leave a chatty child blocked forever
    writing into a full pipe that nothing drains.

    Once the direct child has exited or been killed, the reader keeps
    draining and relaying everything already in the pipe, however long
    that takes: it only gives up once the pipe has been idle (``select``
    reports nothing readable) for ``_POST_EXIT_IDLE_TIMEOUT`` seconds, or
    once ``_POST_EXIT_HARD_CAP`` seconds have passed since the child
    exited, whichever comes first. The hard cap exists for a grandchild
    that escapes the process group (for example via its own
    ``setsid()``) and keeps writing to the merged pipe forever; the idle
    timeout is what lets a normal, fast-exiting child's already-buffered
    output drain in full even when ``on_line`` is slow, instead of being
    cut off by a fixed post-exit deadline. Lines decoded before the
    reader gives up are delivered to ``on_line`` and kept in ``output``;
    anything the pipe still holds or receives afterward is discarded and
    never reaches ``on_line`` or ``output``, and the abandoned daemon
    thread exits on its own, within about one idle timeout or at the
    hard cap, without calling ``on_line`` again.
    """
    if not argv or any(not isinstance(item, str) for item in argv):
        raise ValueError("argv must be a non-empty sequence of str")

    try:
        proc: subprocess.Popen[bytes] = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            env=None if env is None else dict(env),
            start_new_session=True,
        )
    except (FileNotFoundError, PermissionError) as err:
        raise ShellCommandError(f"cannot execute {argv[0]}: {err}") from err

    stdout = proc.stdout
    assert stdout is not None
    fd = stdout.fileno()

    lines: list[str] = []
    lock = threading.Lock()
    child_exited = threading.Event()
    callback_failed = threading.Event()
    callback_error: BaseException | None = None

    def _emit(line: str) -> None:
        nonlocal callback_error
        with lock:
            if on_line is not None:
                try:
                    on_line(line)
                except BaseException as exc:
                    callback_error = exc
                    callback_failed.set()
                    raise
            if keep_output:
                lines.append(line)

    def _reader() -> None:
        buffer = b""
        exited_at: float | None = None
        idle_since: float | None = None
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(fd, selectors.EVENT_READ)
                while True:
                    if exited_at is None and child_exited.is_set():
                        exited_at = time.monotonic()
                    if exited_at is not None:
                        now = time.monotonic()
                        if now - exited_at >= _POST_EXIT_HARD_CAP:
                            break
                        if (
                            idle_since is not None
                            and now - idle_since >= _POST_EXIT_IDLE_TIMEOUT
                        ):
                            break
                    if not selector.select(timeout=_POLL_INTERVAL):
                        if exited_at is not None and idle_since is None:
                            idle_since = time.monotonic()
                        continue
                    idle_since = None
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        break
                    buffer += chunk
                    *complete, buffer = buffer.split(b"\n")
                    for raw_line in complete:
                        _emit(_decode(raw_line))
            if buffer:
                _emit(_decode(buffer))
        except BaseException:
            pass

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()

    timed_out = False
    deadline = None if timeout is None else time.monotonic() + timeout
    returncode: int | None = None
    while True:
        wait_slice = _POLL_INTERVAL
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            wait_slice = min(_POLL_INTERVAL, remaining)
        try:
            returncode = proc.wait(timeout=wait_slice)
            break
        except subprocess.TimeoutExpired:
            if callback_failed.is_set():
                break

    if returncode is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        returncode = -9

    child_exited.set()
    reader.join(_POST_EXIT_HARD_CAP + _READER_SAFETY_MARGIN)
    stdout.close()

    with lock:
        output = tuple(lines)

    if callback_error is not None:
        raise callback_error

    return ShellResult(
        argv=tuple(argv),
        returncode=returncode,
        timed_out=timed_out,
        output=output,
    )
