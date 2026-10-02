"""ADR-006/AM-001/AM-002: fd-level stdio isolation behind a neutralizing
stdout.

A line written through `sys.stdout`/`sys.stderr` after `install()` contains
no `${` and no `TRIGGER_PATTERNS` match. That guarantee is per line, and
conditional (SR-003/SR-022): it holds only while no `${` that bypassed
`neutralize` is still open on the task stream. It does not cover writers
outside `sys.stdout`/`sys.stderr` - a C-level write to fd 2, or a child
that inherits fd 2, is not neutralized by this module.
"""

from __future__ import annotations

import io
import logging
import os
import sys
from dataclasses import dataclass
from typing import TextIO

from hpc_model_utils.platform.encoding import neutralize


class NeutralizingStream(io.TextIOBase):
    """Text stream that relays lines written to it to `target` through
    `neutralize`.

    The guarantee is per line, and conditional: a line this stream wrote
    contains no `${` and no trigger match, but that is inert only while
    no `${` that bypassed `neutralize` is also open on `target` (SR-003).

    A partial line (no trailing newline yet) is held in memory until the
    next newline, `flush()`, or `close()`. `flush()` and `close()` do not
    hold anything back past that point: a non-empty partial line is
    forwarded as `neutralize(partial) + "\\n"` (AM-002/SR-015-SR-017),
    which can split what was one logical line on the wire into two.
    Writing after `close()` raises `ValueError`, matching a real file.
    """

    def __init__(self, target: TextIO) -> None:
        self._target: TextIO = target
        self._buffer: str = ""
        self._encoding: str = target.encoding
        self._errors: str | None = target.errors

    def write(self, s: str) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        self._buffer += s
        *lines, self._buffer = self._buffer.split("\n")
        if lines:
            for line in lines:
                self._target.write(neutralize(line) + "\n")
            self._target.flush()
        return len(s)

    def flush(self) -> None:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if self._buffer:
            self._target.write(neutralize(self._buffer) + "\n")
            self._buffer = ""
        self._target.flush()

    def close(self) -> None:
        if not self.closed:
            self.flush()
        super().close()

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    @property
    def encoding(self) -> str:
        return self._encoding

    @encoding.setter
    def encoding(self, value: str) -> None:
        self._encoding = value

    @property
    def errors(self) -> str | None:
        return self._errors

    @errors.setter
    def errors(self, value: str | None) -> None:
        self._errors = value


class RawProtocolChannel:
    """Writer of unneutralized protocol lines.

    `write_line` flushes `neutral`'s pending text first, so a partial
    log line already buffered is emitted before the protocol line.
    """

    def __init__(self, raw: TextIO, neutral: NeutralizingStream) -> None:
        self._raw: TextIO = raw
        self._neutral: NeutralizingStream = neutral

    def write_line(self, line: str) -> None:
        self._neutral.flush()
        self._raw.write(line + "\n")
        self._raw.flush()


@dataclass(frozen=True)
class StdioChannels:
    protocol: RawProtocolChannel
    fatal: TextIO
    saved_stdout: TextIO
    saved_stderr: TextIO
    saved_fd2: int
    stdout_wrapper: NeutralizingStream
    stderr_wrapper: NeutralizingStream
    retargeted_handlers: tuple[
        tuple["logging.StreamHandler[TextIO]", TextIO], ...
    ]
    prior_capture_warnings: bool


_installed = False


def _logging_handlers() -> list[logging.Handler]:
    """Every handler on the root logger and on every real (non-`PlaceHolder`)
    logger in `logging.Logger.manager.loggerDict`.
    """
    handlers: list[logging.Handler] = list(logging.root.handlers)
    for logger in logging.Logger.manager.loggerDict.values():
        if isinstance(logger, logging.Logger):
            handlers.extend(logger.handlers)
    return handlers


def _retarget_stream_handlers(
    saved_stdout: TextIO,
    saved_stderr: TextIO,
    new_stdout: NeutralizingStream,
    new_stderr: NeutralizingStream,
) -> list[tuple["logging.StreamHandler[TextIO]", TextIO]]:
    """Point every pre-existing `StreamHandler` writing to the saved
    `sys.stdout`/`sys.stderr` at the matching wrapper instead (SR-018),
    and return the `(handler, original_stream)` pairs so `restore` can
    point them back.
    """
    retargeted: list[tuple[logging.StreamHandler[TextIO], TextIO]] = []
    for handler in _logging_handlers():
        if not isinstance(handler, logging.StreamHandler):
            continue
        if handler.stream is saved_stdout:
            retargeted.append((handler, saved_stdout))
            handler.setStream(new_stdout)
        elif handler.stream is saved_stderr:
            retargeted.append((handler, saved_stderr))
            handler.setStream(new_stderr)
    return retargeted


def install() -> StdioChannels:
    """Point fd 2 at fd 1 and wrap `sys.stdout`/`sys.stderr` (and
    `sys.__stdout__`/`sys.__stderr__`, SR-018) so a line written through
    them is passed through `neutralize` before it reaches fd 1. Also
    retargets every pre-existing `logging.StreamHandler` bound to the
    original `sys.stdout`/`sys.stderr` (SR-018).

    Raises `RuntimeError` if already installed (SR-019): this module
    manages process-global fd state, so a second concurrent install
    would double-dup fd 2 and lose the first caller's `restore` path.

    The protocol channel and the wrapped target are opened as an
    explicit UTF-8 `TextIOWrapper` over fd 1 with `errors="strict"`
    (SR-008), rather than relying on the ambient `sys.stdout`, because a
    hook line can carry a non-ASCII word joiner and the process may run
    under a `PYTHONIOENCODING` that cannot represent it. `sys.stdout`
    and `sys.stderr` both wrap this single object rather than two
    independent buffered wrappers over the same fd; flushing the
    original streams before replacing them keeps that handover ordered.

    Returns the channels needed to write the single allowed fatal line
    and to undo the redirect with `restore`.
    """
    global _installed
    if _installed:
        raise RuntimeError("stdio isolation is already installed")

    saved_stdout = sys.stdout
    saved_stderr = sys.stderr
    saved_stdout.flush()
    saved_stderr.flush()
    # logging.captureWarnings has no getter (SR-020); _warnings_showwarning
    # is the module's own private flag for whether it is already active,
    # read via getattr so a future stdlib without it fails open (False)
    # rather than with an AttributeError.
    prior_capture_warnings = (
        getattr(logging, "_warnings_showwarning", None) is not None
    )

    fatal = os.fdopen(
        os.dup(2), "w", encoding="utf-8", errors="replace", buffering=1
    )
    saved_fd2 = os.dup(2)

    os.dup2(1, 2)

    fd1_text: TextIO = open(
        1, "w", encoding="utf-8", errors="strict", newline="\n", closefd=False
    )
    neutral_stdout = NeutralizingStream(fd1_text)
    neutral_stderr = NeutralizingStream(fd1_text)
    sys.stdout = neutral_stdout
    sys.stderr = neutral_stderr
    # sys.__stdout__/__stderr__ are typed Final[TextIOWrapper | None] in
    # typeshed, which reflects neither the real (plain, reassignable)
    # module attribute nor this ticket's requirement to rebind them,
    # hence setattr rather than attribute assignment.
    setattr(sys, "__stdout__", neutral_stdout)
    setattr(sys, "__stderr__", neutral_stderr)

    retargeted = _retarget_stream_handlers(
        saved_stdout, saved_stderr, neutral_stdout, neutral_stderr
    )

    logging.captureWarnings(True)
    _installed = True

    return StdioChannels(
        protocol=RawProtocolChannel(fd1_text, neutral_stdout),
        fatal=fatal,
        saved_stdout=saved_stdout,
        saved_stderr=saved_stderr,
        saved_fd2=saved_fd2,
        stdout_wrapper=neutral_stdout,
        stderr_wrapper=neutral_stderr,
        retargeted_handlers=tuple(retargeted),
        prior_capture_warnings=prior_capture_warnings,
    )


def restore(ch: StdioChannels) -> None:
    """Undo `install`. Idempotent (SR-020): a call made while not
    installed, including a second call for the same `ch`, is a no-op.

    Closes the wrapper objects `install` created (not whatever
    `sys.stdout`/`sys.stderr` happen to be now), which releases any text
    they are still holding through `neutralize`, restores
    `sys.stdout`/`sys.stderr`/`sys.__stdout__`/`sys.__stderr__`, points
    the retargeted handlers back at their original streams, and restores
    the prior `logging.captureWarnings` state.
    """
    global _installed
    if not _installed:
        return

    ch.stdout_wrapper.close()
    ch.stderr_wrapper.close()
    for handler, original in ch.retargeted_handlers:
        handler.setStream(original)

    os.dup2(ch.saved_fd2, 2)
    sys.stdout = ch.saved_stdout
    sys.stderr = ch.saved_stderr
    setattr(sys, "__stdout__", ch.saved_stdout)
    setattr(sys, "__stderr__", ch.saved_stderr)
    ch.fatal.close()
    os.close(ch.saved_fd2)
    logging.captureWarnings(ch.prior_capture_warnings)
    _installed = False


_fatal_sent = False


def _disable_fatal_in_child() -> None:
    global _fatal_sent
    _fatal_sent = True


os.register_at_fork(after_in_child=_disable_fatal_in_child)

# The same code points `csharp_literal` flattens to a space before
# encoding a literal: all of C0, DEL, NEL, and the two Unicode line/
# paragraph separators, with lone surrogates mapped to U+FFFD. Defined
# locally (SR-021) rather than imported from `encoding.py`, whose
# equivalent table is a private module constant: importing a private
# name would couple this module to `encoding.py`'s internals instead of
# its public `neutralize`/`csharp_literal` contract.
_FATAL_CONTROL_CODEPOINTS = (*range(0x20), 0x7F, 0x85, 0x2028, 0x2029)
_FATAL_SURROGATE_CODEPOINTS = range(0xD800, 0xE000)
_FATAL_CONTROL_TO_SPACE = str.maketrans(
    {
        **{cp: chr(0xFFFD) for cp in _FATAL_SURROGATE_CODEPOINTS},
        **{cp: " " for cp in _FATAL_CONTROL_CODEPOINTS},
    }
)


def write_fatal(ch: StdioChannels, line: str) -> None:
    """Write exactly one line to the real stderr.

    The first call wins; every later call in this process, including
    one from a forked child (SR-021, via `os.register_at_fork`), is a
    no-op. `line` is flattened through the same control-to-space table
    `csharp_literal` uses and then through `neutralize`, so the single
    allowed fatal line cannot itself open a `${` capture.
    """
    global _fatal_sent
    if _fatal_sent:
        return
    _fatal_sent = True
    ch.fatal.write(neutralize(line.translate(_FATAL_CONTROL_TO_SPACE)) + "\n")
    ch.fatal.flush()
