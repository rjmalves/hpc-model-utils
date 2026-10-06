"""infra.slurm: the Slurm submission and outcome adapters (ADR-020,
ADR-022, R39, R11, R44, R43, R47, R110).

Submission uses ``sbatch --parsable`` for the model job, then the
finalize job with ``--dependency=afterany:<model>
--kill-on-invalid-dep=yes`` (R39). Queue state comes from
``squeue -h -j <id> -o <fmt>``. All calls go through
``infra.shell.run`` with argv lists only (ADR-028); nothing from the
child reaches the process stderr (R37).

``cancel()`` (R44) makes one ``scancel <ids...>`` call; an rc != 0
whose every error line contains ``Invalid job id`` or ``already
completing or completed`` is still treated as success, since those
jobs are already gone, but that exemption needs at least one such
line (an empty-output failure always raises). ``wait_gone()``
(ADR-046, R136) polls ``squeue -h -j <id1,id2,...> -o %i|%T`` until no
requested id is left in ``NON_TERMINAL_STATES``, or ``timeout``
expires. It fails closed: a timeout, or a non-zero rc with no
parsable rows and no ``Invalid job id`` line, leaves every id
not-gone for that poll rather than being read as "gone", and each
poll's own ``squeue`` call is bounded by ``min(self._timeout,
max(remaining, 0.1))`` so one hung call cannot overrun the overall
deadline. ``workdir()`` (ADR-024) is the cancellation ownership
check: ``squeue -h -j <id> -o %Z`` returns ``None`` only for an
unknown id or rc 0 with empty output; every other outcome raises,
because this check must never guess at a job's ownership.

``outcome()`` (ADR-022) asks ``sacct`` for a job's final state and
falls back to ``scontrol show job -o`` while the job is still inside
MinJobAge. In prd, Slurm accounting is permanently disabled
(``plans/v2-architecture/design/cluster-facts.md`` F1), so ``sacct``
always exits 1 and every attempt reaches the ``scontrol`` fallback;
the ``sacct`` path is exercised by the fake and by any future
slurmdbd-enabled cluster (R110). The default retry budget, 6 attempts
of 10 s (5 sleeps, 50 s), comes from F2's MinJobAge of 300 s and
KillWait of 30 s: it comfortably outlasts the TERM-to-KILL wait and
sits well inside the purge window.

``SQUEUE_FORMAT`` is ``%i|%T|%r|%l|%S``: job id, state, reason, time
limit, start time, in that fixed order with ``reason`` in the middle
(field 3 of 5). A reason can itself contain ``|``, so ``job_state``
never does one plain ``split("|")``: it splits job id and state off
the left with ``maxsplit=2`` (id, state, rest), then splits time
limit and start time off the right of ``rest`` with
``rsplit("|", 2)`` (reason, time_limit, start_time). Whatever is left
in the middle, pipes included, is the whole reason. Too few fields on
either side is a malformed line, and raises ``SchedulerCommandError``
loudly rather than guessing.

``submit``'s ``after`` and ``job_state``'s ``job_id`` are validated as
digits-only before any subprocess runs (ADR-028 defence in depth), and
a ``squeue``/``sbatch`` timeout always raises ``SchedulerCommandError``
rather than being read as "job gone" or "submission failed silently".
"""

from __future__ import annotations

import re
import shutil
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, NamedTuple

from hpc_model_utils.infra.errors import (
    SchedulerCommandError,
    ShellCommandError,
)
from hpc_model_utils.infra.shell import run

SQUEUE_FORMAT = "%i|%T|%r|%l|%S"
_WAIT_GONE_FORMAT = "%i|%T"
SACCT_FORMAT = "JobID,State,ExitCode,Elapsed,Timelimit,Start,End"
TERMINAL_STATES = frozenset(
    {
        "COMPLETED",
        "FAILED",
        "TIMEOUT",
        "CANCELLED",
        "NODE_FAIL",
        "BOOT_FAIL",
        "OUT_OF_MEMORY",
        "PREEMPTED",
        "DEADLINE",
    }
)
NON_TERMINAL_STATES = frozenset(
    {
        "PENDING",
        "CONFIGURING",
        "RUNNING",
        "COMPLETING",
        "REQUEUED",
        "SUSPENDED",
        "RESIZING",
        "REQUEUE_HOLD",
        "REQUEUE_FED",
        "STOPPED",
        "SIGNALING",
        "STAGE_OUT",
    }
)

_MAX_ERROR_OUTPUT = 300
_MAX_RAW_OUTPUT = 500

_SBATCH_ID_RE = re.compile(r"(\d+)(;.*)?")
_JOB_ID_RE = re.compile(r"\d+")
_WAIT_ROW_RE = re.compile(r"(\d+)\|(\S+)")
_DAY_TIME_RE = re.compile(r"(\d+)-(\d{1,2}):(\d{2}):(\d{2})")
_HMS_RE = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})")
_MS_RE = re.compile(r"(\d{1,2}):(\d{2})")
_MIN_RE = re.compile(r"\d+")
_EXIT_CODE_RE = re.compile(r"(\d+):(\d+)")

_UNSET_TIME_LIMITS = frozenset({"UNLIMITED", "INVALID", "NOT_SET", ""})
_UNSET_TIMES = frozenset({"N/A", "Unknown", "None", ""})


def _truncate(text: str) -> str:
    return text[:_MAX_ERROR_OUTPUT]


@dataclass(frozen=True)
class JobState:
    job_id: str
    state: str
    reason: str
    time_limit: timedelta | None
    start_time: datetime | None


def parse_time_limit(text: str) -> timedelta | None:
    """Parse a Slurm ``TimeLimit``/``%l`` value.

    Accepts ``D-HH:MM:SS``, ``HH:MM:SS``, ``MM:SS`` and ``MM``.
    ``UNLIMITED``, ``INVALID``, ``NOT_SET`` and the empty string mean
    no limit and return ``None``. Any other malformed value raises
    ``ValueError``.
    """
    if text in _UNSET_TIME_LIMITS:
        return None
    match = _DAY_TIME_RE.fullmatch(text)
    if match is not None:
        days, hours, minutes, seconds = (int(g) for g in match.groups())
        return timedelta(
            days=days, hours=hours, minutes=minutes, seconds=seconds
        )
    match = _HMS_RE.fullmatch(text)
    if match is not None:
        hours, minutes, seconds = (int(g) for g in match.groups())
        return timedelta(hours=hours, minutes=minutes, seconds=seconds)
    match = _MS_RE.fullmatch(text)
    if match is not None:
        minutes, seconds = (int(g) for g in match.groups())
        return timedelta(minutes=minutes, seconds=seconds)
    match = _MIN_RE.fullmatch(text)
    if match is not None:
        return timedelta(minutes=int(text))
    raise ValueError(f"malformed Slurm time limit: {text!r}")


def parse_slurm_time(text: str) -> datetime | None:
    """Parse a Slurm ``StartTime``/``%S`` value.

    ``N/A``, ``Unknown``, ``None`` and the empty string return
    ``None``. Otherwise the value is parsed as a naive local
    ``%Y-%m-%dT%H:%M:%S`` timestamp and returned tz-aware via
    ``.astimezone()``. A malformed value raises ``ValueError``.
    """
    if text in _UNSET_TIMES:
        return None
    naive = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S")
    return naive.astimezone()


@dataclass(frozen=True)
class JobOutcome:
    job_id: str
    state: str
    exit_code: int | None
    signal: int | None
    elapsed: str | None
    time_limit: str | None
    oom: bool
    source: Literal["sacct", "scontrol", "none"]
    raw: str


class _SacctRow(NamedTuple):
    job_id: str
    state: str
    exit_code: str
    elapsed: str
    time_limit: str
    start: str
    end: str


def _first_token(text: str) -> str:
    tokens = text.split()
    return tokens[0] if tokens else text


def _parse_exit_code(text: str) -> tuple[int | None, int | None]:
    """Parse a Slurm ``rc:sig`` exit code into ``(rc, sig or None)``.

    A malformed value (missing, non-numeric, or some other shape)
    never raises: it means that source's exit code is unusable for
    this attempt, so both fields come back ``None`` while the state
    and ``oom`` the same row/line gave are still trusted.
    """
    match = _EXIT_CODE_RE.fullmatch(text)
    if match is None:
        return None, None
    rc, sig = int(match.group(1)), int(match.group(2))
    return rc, sig or None


def _parse_sacct(
    lines: Sequence[str], job_id: str
) -> tuple[_SacctRow | None, list[_SacctRow]]:
    """Parse ``sacct --parsable2 --format=<SACCT_FORMAT>`` output.

    Only lines that split on ``"|"`` into exactly 7 fields count as
    rows; anything else (a warning, a blank line) is ignored here,
    though the caller still keeps it in ``raw``. The job row is the
    one whose first field exactly equals ``job_id``; a step row's
    first field starts with ``f"{job_id}."``, so job ``12``'s rows
    never match job ``123``'s.
    """
    job_row: _SacctRow | None = None
    step_rows: list[_SacctRow] = []
    for line in lines:
        fields = line.split("|")
        if len(fields) != 7:
            continue
        row = _SacctRow(
            job_id=fields[0],
            state=fields[1],
            exit_code=fields[2],
            elapsed=fields[3],
            time_limit=fields[4],
            start=fields[5],
            end=fields[6],
        )
        if row.job_id == job_id:
            job_row = row
        elif row.job_id.startswith(f"{job_id}."):
            step_rows.append(row)
    return job_row, step_rows


def _parse_scontrol(line: str) -> dict[str, str]:
    """Parse one ``scontrol show job -o`` line into a field map.

    Tokens are split on whitespace, then each one on its first
    ``=``. A token with no ``=`` continues the previous key's value,
    joined with a single space: this is how ``Reason=(launch failed
    requeued held)`` and a ``WorkDir=`` path containing spaces
    survive the whitespace split intact.
    """
    fields: dict[str, str] = {}
    last_key: str | None = None
    for token in line.split():
        key, sep, value = token.partition("=")
        if sep:
            fields[key] = value
            last_key = key
        elif last_key is not None:
            fields[last_key] = f"{fields[last_key]} {token}"
    return fields


def _parse_wait_gone_rows(
    output: Sequence[str], requested: frozenset[str]
) -> dict[str, str]:
    """Parse ``squeue -o %i|%T`` output into ``{job_id: state}`` for
    the ids in ``requested``.

    A line that does not fullmatch ``\\d+\\|\\S+`` (a warning or blank
    line merged in from stderr) is ignored, and so is a row for an id
    that was not requested.
    """
    rows: dict[str, str] = {}
    for line in output:
        match = _WAIT_ROW_RE.fullmatch(line.strip())
        if match is None or match.group(1) not in requested:
            continue
        rows[match.group(1)] = _first_token(match.group(2))
    return rows


class Slurm:
    def __init__(
        self, bin_dir: Path | None = None, *, timeout: float = 30.0
    ) -> None:
        self._bin_dir = bin_dir
        self._timeout = timeout

    def _exe(self, name: str) -> str:
        if self._bin_dir is not None:
            candidate = self._bin_dir / name
            if candidate.exists():
                return str(candidate)
            raise SchedulerCommandError(f"{name} not found")
        found = shutil.which(name)
        if found is None:
            raise SchedulerCommandError(f"{name} not found")
        return found

    def submit(self, script: Path, *, after: str | None = None) -> str:
        r"""Submit ``script`` with ``sbatch --parsable``, returning the
        bare job id. When ``after`` is given, the job is submitted
        with ``--dependency=afterany:<after> --kill-on-invalid-dep=yes``.
        ``after`` must fullmatch ``\d+``; a malformed value raises
        ``ValueError`` before any subprocess runs (ADR-028 defence in
        depth: a value such as ``"1,singleton"`` must never splice an
        extra token onto the dependency flag).

        ``shell.run`` merges sbatch's stderr into its output, so a
        real ``sbatch: warning: ...`` line can precede or follow the
        ``--parsable`` id even on a successful submission (rc 0). The
        id is therefore taken from the *unique* stripped non-empty
        output line that fullmatches ``(\d+)(;.*)?``. Zero or more
        than one such matching line raises ``SchedulerCommandError``
        exactly like a failed submission, because a job that may
        already have been submitted must never be silently retried
        or orphaned.
        """
        if after is not None and _JOB_ID_RE.fullmatch(after) is None:
            raise ValueError(f"not a Slurm job id: {after!r}")
        argv = [self._exe("sbatch"), "--parsable"]
        if after is not None:
            argv.append(f"--dependency=afterany:{after}")
            argv.append("--kill-on-invalid-dep=yes")
        argv.append(str(script))
        try:
            result = run(argv, timeout=self._timeout)
        except ShellCommandError as exc:
            raise SchedulerCommandError(str(exc)) from exc
        non_empty = [line.strip() for line in result.output if line.strip()]
        matches: list[re.Match[str]] = []
        for line in non_empty:
            match = _SBATCH_ID_RE.fullmatch(line)
            if match is not None:
                matches.append(match)
        if result.timed_out or result.returncode != 0 or len(matches) != 1:
            last_line = result.output[-1] if result.output else ""
            raise SchedulerCommandError(
                f"sbatch failed ({result.returncode}): {_truncate(last_line)}"
            )
        return matches[0].group(1)

    def job_state(self, job_id: str) -> JobState | None:
        r"""Query queue state for ``job_id`` via ``squeue -h -j -o``.

        ``job_id`` must fullmatch ``\d+``; a malformed value raises
        ``ValueError`` before any subprocess runs (ADR-028 defence in
        depth).

        Returns ``None`` when the id is unknown to the queue (rc 1
        with ``Invalid job id`` in the output), or when rc is 0 with
        empty output. A timeout always raises ``SchedulerCommandError``,
        and so does every other non-zero exit: treating empty output
        as "job gone" for a hung or erroring ``squeue`` would mislead
        the follower (ticket-034) into thinking the job had left the
        queue. Output that does not parse as exactly one well-formed
        line for this job id, or whose time limit/start time fields
        are malformed, also raises ``SchedulerCommandError``.
        """
        if _JOB_ID_RE.fullmatch(job_id) is None:
            raise ValueError(f"not a Slurm job id: {job_id!r}")
        argv = [
            self._exe("squeue"),
            "-h",
            "-j",
            job_id,
            "-o",
            SQUEUE_FORMAT,
        ]
        try:
            result = run(argv, timeout=self._timeout)
        except ShellCommandError as exc:
            raise SchedulerCommandError(str(exc)) from exc
        joined = "\n".join(result.output)
        if result.timed_out:
            raise SchedulerCommandError(
                f"squeue timed out: {_truncate(joined)}"
            )
        if result.returncode == 1 and any(
            "Invalid job id" in line for line in result.output
        ):
            return None
        if result.returncode != 0:
            raise SchedulerCommandError(
                f"squeue failed ({result.returncode}): {_truncate(joined)}"
            )
        non_empty = [line for line in result.output if line.strip()]
        if not non_empty:
            return None
        if len(non_empty) != 1:
            raise SchedulerCommandError(
                f"squeue returned {len(non_empty)} lines for job "
                f"{job_id}: {_truncate(joined)}"
            )
        line = non_empty[0]
        left = line.split("|", 2)
        right = left[2].rsplit("|", 2) if len(left) == 3 else []
        if len(left) != 3 or len(right) != 3 or left[0] != job_id:
            raise SchedulerCommandError(
                f"squeue returned malformed output for job {job_id}: "
                f"{_truncate(line)}"
            )
        raw_id, raw_state = left[0], left[1]
        reason, raw_time_limit, raw_start = right
        state = _first_token(raw_state)
        try:
            time_limit = parse_time_limit(raw_time_limit)
            start_time = parse_slurm_time(raw_start)
        except ValueError as exc:
            raise SchedulerCommandError(
                f"squeue returned malformed output for job {job_id}: "
                f"{_truncate(str(exc))}"
            ) from exc
        return JobState(
            job_id=raw_id,
            state=state,
            reason=reason,
            time_limit=time_limit,
            start_time=start_time,
        )

    def outcome(
        self,
        job_id: str,
        *,
        attempts: int = 6,
        backoff: float = 10.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> JobOutcome:
        r"""Resolve ``job_id``'s final outcome (ADR-022), retrying
        ``sacct`` with a ``scontrol show job -o`` fallback.

        ``job_id`` must fullmatch ``\d+`` and ``attempts`` must be at
        least 1, or ``ValueError`` is raised before any subprocess
        runs. Both ``sacct`` and ``scontrol`` are resolved up front,
        so a missing executable raises ``SchedulerCommandError``
        immediately rather than only once the fallback is first
        reached; an exec failure (``ShellCommandError``) maps to the
        same error. Every other failure for a given attempt --- a
        timeout, a non-zero exit, no rows, a non-terminal state, or a
        malformed field --- is absorbed: that source simply "did not
        answer" this attempt, and no exception ever escapes from
        parsing.

        Per attempt: run ``sacct``. A job row whose (first-token
        normalized) state is terminal returns a ``source="sacct"``
        outcome, with ``oom`` true when any job/step row's state
        token is ``OUT_OF_MEMORY`` (a cgroup OOM often leaves the
        job-level state at ``FAILED``). Otherwise, when ``sacct``
        hard-failed (non-zero rc or timeout) or found an explicit
        non-terminal job row, fall back to ``scontrol show job -o``
        in the *same* attempt; a terminal ``JobState=`` there returns
        a ``source="scontrol"`` outcome, with ``oom = (state ==
        "OUT_OF_MEMORY")`` --- step-level OOM is visible only through
        ``sacct`` (ticket-031 must know this when it consumes a
        ``scontrol``-sourced outcome).

        A clean ``sacct`` answer with *zero* rows (accounting lag) is
        handled differently: every attempt but the last retries
        ``sacct`` alone and skips ``scontrol``, preferring ``sacct``
        because it is the only source that carries step rows (a
        step-level OOM would be invisible through ``scontrol``), and
        because the fake's (and a real cluster's) ``scontrol`` reads
        the same live job record with no simulated MinJobAge delay,
        so falling back to it on every lagging attempt would always
        win the race before the lag clears. But always skipping
        ``scontrol`` while rows stay empty would silently discard a
        known outcome once the lag outlasts the whole retry budget
        --- a fail-open toward ``UNKNOWN`` --- so the *last* attempt
        consults ``scontrol`` even on a zero-rows ``sacct`` answer, as
        a safety net. In prd, accounting is permanently disabled
        (cluster fact F1), so ``sacct`` never returns rc 0 with zero
        rows there; this narrowing only affects the fake and a future
        slurmdbd-enabled cluster (R110). Unless this is the last
        attempt, ``sleep(backoff)`` runs between attempts.

        ``ExitCode``/``ExitCode=`` values of the shape ``"rc:sig"``
        parse to ``(int(rc), int(sig) or None)``; any other shape
        (missing, or malformed) is absorbed as ``(None, None)``
        rather than raising, leaving the rest of that row's/line's
        answer trusted. ``elapsed`` and ``time_limit`` are the raw
        ``sacct``/``scontrol`` text, not parsed into a ``timedelta``.
        ``scontrol``'s ``RunTime=`` field is read for ``elapsed`` when
        present; it is absent from the fake, so a ``scontrol``-sourced
        outcome's ``elapsed`` is ``None`` there.

        After the last attempt answers nothing, returns
        ``JobOutcome(job_id, "UNKNOWN", None, None, None, None, False,
        "none", raw=...)``, with ``raw`` the last attempt's combined
        ``sacct``/``scontrol`` output. ``raw`` is truncated to 500
        characters. ``source="none"`` is not mapped to ``UNKNOWN`` by
        this ticket; ticket-031 records it as evidence and falls
        through (ADR-022).
        """
        if _JOB_ID_RE.fullmatch(job_id) is None:
            raise ValueError(f"not a Slurm job id: {job_id!r}")
        if attempts < 1:
            raise ValueError(f"attempts must be at least 1: {attempts!r}")
        sacct_exe = self._exe("sacct")
        scontrol_exe = self._exe("scontrol")

        sacct_raw = ""
        scontrol_raw = ""
        for attempt in range(attempts):
            sacct_argv = [
                sacct_exe,
                "-j",
                job_id,
                "--noheader",
                "--parsable2",
                f"--format={SACCT_FORMAT}",
            ]
            try:
                sacct_result = run(sacct_argv, timeout=self._timeout)
            except ShellCommandError as exc:
                raise SchedulerCommandError(str(exc)) from exc
            sacct_raw = "\n".join(sacct_result.output)
            sacct_failed = (
                sacct_result.timed_out or sacct_result.returncode != 0
            )
            job_row: _SacctRow | None = None
            step_rows: list[_SacctRow] = []
            if not sacct_failed:
                job_row, step_rows = _parse_sacct(sacct_result.output, job_id)
                if job_row is not None:
                    state = _first_token(job_row.state)
                    if state in TERMINAL_STATES:
                        oom = any(
                            _first_token(row.state) == "OUT_OF_MEMORY"
                            for row in (job_row, *step_rows)
                        )
                        exit_code, signal = _parse_exit_code(job_row.exit_code)
                        return JobOutcome(
                            job_id=job_id,
                            state=state,
                            exit_code=exit_code,
                            signal=signal,
                            elapsed=job_row.elapsed or None,
                            time_limit=job_row.time_limit or None,
                            oom=oom,
                            source="sacct",
                            raw=sacct_raw[:_MAX_RAW_OUTPUT],
                        )

            scontrol_raw = ""
            is_last_attempt = attempt == attempts - 1
            if sacct_failed or job_row is not None or is_last_attempt:
                scontrol_argv = [
                    scontrol_exe,
                    "show",
                    "job",
                    "-o",
                    job_id,
                ]
                try:
                    scontrol_result = run(scontrol_argv, timeout=self._timeout)
                except ShellCommandError as exc:
                    raise SchedulerCommandError(str(exc)) from exc
                scontrol_raw = "\n".join(scontrol_result.output)
                if (
                    not scontrol_result.timed_out
                    and scontrol_result.returncode == 0
                ):
                    fields: dict[str, str] | None = None
                    for line in scontrol_result.output:
                        if not line.strip():
                            continue
                        parsed = _parse_scontrol(line)
                        if parsed.get("JobId") == job_id:
                            fields = parsed
                            break
                    if fields is not None:
                        state = _first_token(fields.get("JobState", ""))
                        if state in TERMINAL_STATES:
                            exit_code, signal = _parse_exit_code(
                                fields.get("ExitCode", "")
                            )
                            return JobOutcome(
                                job_id=job_id,
                                state=state,
                                exit_code=exit_code,
                                signal=signal,
                                elapsed=fields.get("RunTime") or None,
                                time_limit=fields.get("TimeLimit") or None,
                                oom=state == "OUT_OF_MEMORY",
                                source="scontrol",
                                raw=scontrol_raw[:_MAX_RAW_OUTPUT],
                            )

            if not is_last_attempt:
                sleep(backoff)

        raw_parts = [part for part in (sacct_raw, scontrol_raw) if part]
        return JobOutcome(
            job_id,
            "UNKNOWN",
            None,
            None,
            None,
            None,
            False,
            "none",
            raw="\n".join(raw_parts)[:_MAX_RAW_OUTPUT],
        )

    def cancel(self, job_ids: Sequence[str]) -> None:
        r"""Cancel every id in ``job_ids`` with one ``scancel`` call
        (R44), never one call per id.

        Every id must fullmatch ``\d+``, checked before any
        subprocess runs (ADR-028 defence in depth); a malformed id
        raises ``ValueError`` and no ``scancel`` call happens at all,
        even when other ids in the batch are well-formed. With no
        ids, returns at once.

        On rc 0, returns normally. On a non-zero rc whose error
        output has at least one non-blank line and *every* such line
        contains ``Invalid job id`` or ``already completing or
        completed``, also returns normally, because those jobs are
        already gone; an rc != 0 with no non-blank output at all does
        not qualify (vacuously "every line is benign" would otherwise
        excuse it) and raises ``SchedulerCommandError``, same as any
        other failure. A timeout also raises.
        """
        for job_id in job_ids:
            if _JOB_ID_RE.fullmatch(job_id) is None:
                raise ValueError(f"not a Slurm job id: {job_id!r}")
        if not job_ids:
            return
        argv = [self._exe("scancel"), *job_ids]
        try:
            result = run(argv, timeout=self._timeout)
        except ShellCommandError as exc:
            raise SchedulerCommandError(str(exc)) from exc
        joined = "\n".join(result.output)
        if result.timed_out:
            raise SchedulerCommandError(
                f"scancel timed out: {_truncate(joined)}"
            )
        if result.returncode == 0:
            return
        lines = [line for line in result.output if line.strip()]
        if lines and all(
            "Invalid job id" in line
            or "already completing or completed" in line
            for line in lines
        ):
            return
        raise SchedulerCommandError(
            f"scancel failed ({result.returncode}): {_truncate(joined)}"
        )

    def wait_gone(
        self,
        job_ids: Sequence[str],
        *,
        timeout: float,
        poll: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> bool:
        r"""Poll ``squeue -h -j <ids> -o %i|%T`` (ADR-046, R136) until
        no id in ``job_ids`` is left in ``NON_TERMINAL_STATES``, or
        ``timeout`` seconds have passed.

        Every id must fullmatch ``\d+``, checked before any
        subprocess runs; a malformed id raises ``ValueError``. With
        no ids, returns ``True`` at once, without polling.

        Each poll is bounded by its own ``squeue`` timeout of
        ``min(self._timeout, max(remaining, 0.1))``, so one hung call
        cannot overrun the overall ``timeout``. A poll counts as
        answered, and its rows trusted, only when: rc is 0; or rc !=
        0 and the output contains an ``Invalid job id`` line (ids
        absent from the rows are then taken as gone); or rc != 0 but
        every requested id appears in a parsable ``%i|%T`` row (the
        rows are the truth even without an ``Invalid job id`` line,
        since none is missing). Any other poll -- a timeout, or rc !=
        0 with some requested id missing from the rows and no
        ``Invalid job id`` line -- fails closed: it is not read as
        "gone" and polling continues. A row for an id that was not
        requested is ignored. This method never raises on a failing
        ``squeue``; it only returns ``True`` once an answered poll
        shows every requested id gone, and ``False`` once ``now() -
        start >= timeout``.
        """
        for job_id in job_ids:
            if _JOB_ID_RE.fullmatch(job_id) is None:
                raise ValueError(f"not a Slurm job id: {job_id!r}")
        if not job_ids:
            return True
        requested = frozenset(job_ids)
        squeue_exe = self._exe("squeue")
        start = now()
        while True:
            remaining = timeout - (now() - start)
            call_timeout = min(self._timeout, max(remaining, 0.1))
            argv = [
                squeue_exe,
                "-h",
                "-j",
                ",".join(job_ids),
                "-o",
                _WAIT_GONE_FORMAT,
            ]
            try:
                result = run(argv, timeout=call_timeout)
            except ShellCommandError as exc:
                raise SchedulerCommandError(str(exc)) from exc
            if not result.timed_out:
                rows = _parse_wait_gone_rows(result.output, requested)
                has_invalid = any(
                    "Invalid job id" in line for line in result.output
                )
                answered = (
                    result.returncode == 0
                    or has_invalid
                    or requested <= rows.keys()
                )
                if answered and all(
                    rows.get(job_id, "") not in NON_TERMINAL_STATES
                    for job_id in requested
                ):
                    return True
            if now() - start >= timeout:
                return False
            sleep(poll)

    def workdir(self, job_id: str) -> Path | None:
        r"""Return ``job_id``'s working directory via
        ``squeue -h -j <id> -o %Z`` (ADR-024's ownership check: a
        ``--job-id`` scraped from stdout cannot be trusted on its
        own, so the caller confirms it names a job whose WorkDir is
        the expected workspace before cancelling it).

        ``job_id`` must fullmatch ``\d+``, checked before any
        subprocess runs. Returns ``None`` only when the id is unknown
        (rc 1 with ``Invalid job id`` in the output) or when rc is 0
        with empty output. A timeout, any other non-zero rc, or more
        than one non-empty line raises ``SchedulerCommandError``,
        because this check must never guess at a job's ownership.
        The returned path is the line exactly as ``squeue`` printed
        it (``infra.shell.run`` already strips only the trailing
        ``\r``/``\n``), since a WorkDir can itself contain spaces.
        """
        if _JOB_ID_RE.fullmatch(job_id) is None:
            raise ValueError(f"not a Slurm job id: {job_id!r}")
        argv = [self._exe("squeue"), "-h", "-j", job_id, "-o", "%Z"]
        try:
            result = run(argv, timeout=self._timeout)
        except ShellCommandError as exc:
            raise SchedulerCommandError(str(exc)) from exc
        joined = "\n".join(result.output)
        if result.timed_out:
            raise SchedulerCommandError(
                f"squeue timed out: {_truncate(joined)}"
            )
        if result.returncode == 1 and any(
            "Invalid job id" in line for line in result.output
        ):
            return None
        if result.returncode != 0:
            raise SchedulerCommandError(
                f"squeue failed ({result.returncode}): {_truncate(joined)}"
            )
        non_empty = [line for line in result.output if line.strip()]
        if not non_empty:
            return None
        if len(non_empty) != 1:
            raise SchedulerCommandError(
                f"squeue returned {len(non_empty)} lines for job "
                f"{job_id}: {_truncate(joined)}"
            )
        return Path(non_empty[0])
