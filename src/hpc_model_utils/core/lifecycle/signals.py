"""R45/ADR-024/ADR-005/R121/R91: D2 -- scancel ``run``'s own jobs on
SIGTERM, SIGHUP or a broken stdout pipe.

``Terminated`` derives from ``BaseException``, never ``Exception``, so
no ``except Exception`` inside ``follow``/``_ingest`` can swallow it
on its way out to this module's own handler. The critical sections in
``submit`` (ticket-037's ``blocked_signals``) hold a signal pending
until the matching ``ledger.add`` has already run, so the ids read
from ``ledger`` below are always complete for whichever job was being
submitted when the signal arrived.

``JobLedger`` is imported only under ``TYPE_CHECKING``: ``run.py``
imports ``cancel_on_termination`` from this module at runtime, so a
plain runtime import back would be circular. ``from __future__ import
annotations`` keeps every annotation lazy regardless, so this costs
nothing at runtime.

``run()`` is only ever called from the process's main thread in
production (``blocked_signals``'s own docstring already relies on
this). ``signal.signal`` itself only works there too -- calling it
from any other thread raises ``ValueError``. ``test_engine_end_to_
end.py``'s scenario (e) nonetheless drives ``run()`` from a background
thread to exercise ticket-041's separate ``cancel()`` path, so handler
install/restore below is skipped outside the main thread rather than
raising there; a ``BrokenPipeError`` is still handled regardless of
thread, since catching it needs no signal call.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType
from typing import TYPE_CHECKING

from hpc_model_utils.core.errors import signal_exit_code
from hpc_model_utils.core.state import StateStore
from hpc_model_utils.infra.errors import SchedulerCommandError
from hpc_model_utils.infra.slurm import Slurm

if TYPE_CHECKING:
    from hpc_model_utils.core.lifecycle.run import JobLedger

logger = logging.getLogger(__name__)

_HANDLED_SIGNALS = (signal.SIGTERM, signal.SIGHUP)


class Terminated(BaseException):
    def __init__(self, signum: int) -> None:
        self.signum = signum
        super().__init__(signum)


def _raise_terminated(signum: int, frame: FrameType | None) -> None:
    raise Terminated(signum)


@contextmanager
def cancel_on_termination(
    slurm: Slurm, ledger: JobLedger, store: StateStore, *, timeout: float
) -> Iterator[None]:
    """D2 (R45): on SIGTERM, SIGHUP or a broken stdout pipe, scancel
    every id in ``ledger`` plus every id already recorded in
    ``store``, then exit with the matching 128+n code (ADR-005/R121).
    No ``Reporter`` call, no hook, no status (R91): cancellation here
    is the platform's own call to make, not this process's.

    The previous SIGTERM/SIGHUP handlers are always restored on exit,
    including the signal/broken-pipe path, since the ``SystemExit``
    raised below still unwinds back out through this generator's own
    ``finally`` before reaching the caller. Off the main thread, no
    handler is installed or restored (see the module docstring); only
    a ``BrokenPipeError`` can reach the ``except`` below there.
    """
    on_main_thread = threading.current_thread() is threading.main_thread()
    previous = (
        {sig: signal.signal(sig, _raise_terminated) for sig in _HANDLED_SIGNALS}
        if on_main_thread
        else {}
    )
    try:
        yield
    except (Terminated, BrokenPipeError) as exc:
        # Ignored first: a second TERM from Slurm's own KillWait,
        # sent while this handler is already cancelling, must not
        # re-enter this block.
        if on_main_thread:
            for sig in _HANDLED_SIGNALS:
                signal.signal(sig, signal.SIG_IGN)
        if isinstance(exc, BrokenPipeError):
            signum: int = signal.SIGPIPE
            devnull_fd = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull_fd, 1)
            os.close(devnull_fd)
        else:
            signum = exc.signum
        recorded = store.load_optional()
        recorded_ids = (
            ()
            if recorded is None
            else tuple(job.job_id for job in recorded.jobs)
        )
        ids = dict.fromkeys(ledger.ids + recorded_ids)
        if ids:
            try:
                slurm.cancel(list(ids))
                slurm.wait_gone(list(ids), timeout=timeout)
            except SchedulerCommandError:
                logger.exception(
                    "cancel_on_termination: scancel failed for %s",
                    ", ".join(ids),
                )
        raise SystemExit(signal_exit_code(signum)) from exc
    finally:
        if on_main_thread:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
