"""ADR-046/R133(b): the relay-completeness checker (ticket-064a).

Proves, for one run's relayed stdout, that it has no lost or
duplicated line compared with the job's own output. The "expected"
sequence is each ``saidas/logs/<phase>-<jobid>.out`` job log read
through ``core.follow.LogTail`` (``poll()`` then ``close()``) and
mapped through ``platform.encoding.neutralize``, exactly as the CLI's
own relay path produces it. The "relayed" sequence is the operator's
exported relayed-output file, with the CLI's own ``[hpcmu] `` markers
classified as informational (dropped, counted) or an anomaly (dropped,
reported) rather than real output.

Two export formats are read. The prefixed format (v2.0.x and v2.1.0)
carries each relayed line behind ``<ts> INFO hpc_model_utils.job: ``; it
is recognised when any line contains that marker, and the text after the
marker is the relayed line. The verbatim format (v2.2.0 on) carries each
relayed line exactly as the job printed it; the relayed lines are then
the ones between the last ``Submitted batch job <id>`` line and the first
``${CurrentExecution.`` hook line, minus ModelOps's own ``Parâmetro
dinâmico do tipo "`` lines. Anything else in that window (a login-side
line, say) is not dropped: it surfaces as an ``extra`` divergence.

On a mismatch, the first divergence's *kind* comes from the first
non-equal ``difflib.SequenceMatcher`` opcode over a bounded window
starting at that index, rather than a length tiebreak: a ``delete``
is ``lost`` (the expected line is missing), an ``insert`` is ``extra``
(a line was added or duplicated), and a ``replace`` is ``changed`` (a
line was altered in place -- lost and extra at the same index). This
correctly attributes the *local* fault at the divergence index even
when a later, unrelated fault (e.g. a second lost line) sits further
along in the same window.

Run as ``uv run python -m deploy.modelops.check_relay --relayed
<file> --job-log <file> [--job-log <file> ...]``, job logs in relay
order (model, then finalize).
"""

from __future__ import annotations

import difflib
import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import click

from hpc_model_utils.core.follow import LogTail
from hpc_model_utils.platform.encoding import neutralize

_RELAY_MARKER = " INFO hpc_model_utils.job: "
_EXECUTION_OUTPUT = "[ExecutionOutput] "
_SUBMITTED_RE = re.compile(r"Submitted batch job \d+")
_HOOK_PREFIX = "${CurrentExecution."
_MODELOPS_PARAMETER_PREFIX = 'Parâmetro dinâmico do tipo "'
_AMBIGUOUS_REASON = "ambiguous marker in job log"
_CONTEXT_LINES = 3
_CONTEXT_CHARS = 200
_DIFF_WINDOW = 500

_INFO_PREFIXES = ("[hpcmu] heartbeat: ", "[hpcmu] squeue failed")
_INFO_JOB_STATUS_RE = re.compile(r"^\[hpcmu\] job \S+ (?:requeued|restarted)\b")
_ANOMALY_PREFIXES = (
    "[hpcmu] log truncated: ",
    "[hpcmu] log rotated: ",
    "[hpcmu] log reopen failed: ",
    "[hpcmu] log open failed: ",
    "[hpcmu] log never appeared: ",
)
_LINE_SPLIT_RE = re.compile(r"\r\n|\r|\n")


class CheckRelayError(Exception):
    pass


@dataclass(frozen=True)
class RelayedResult:
    lines: list[str]
    informational: int
    anomalies: list[str]


@dataclass(frozen=True)
class Divergence:
    index: int
    kind: Literal["lost", "extra", "changed"]
    expected_context: list[str]
    relayed_context: list[str]


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str | None = None
    divergence: Divergence | None = None


def expected_lines(paths: Sequence[Path]) -> list[str]:
    lines: list[str] = []
    for path in paths:
        tail = LogTail(path)
        collected = tail.poll() + tail.close()
        lines.extend(neutralize(line) for line in collected)
    return lines


def _split_lines(text: str) -> list[str]:
    parts = _LINE_SPLIT_RE.split(text)
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts


def _payload(raw: str) -> str:
    start = raw.find(_EXECUTION_OUTPUT)
    if start != -1:
        rest = raw[start + len(_EXECUTION_OUTPUT) :]
        sep = rest.find(": ")
        if sep != -1:
            return rest[sep + 2 :]
    return raw


def _verbatim_window(payloads: list[str]) -> list[str]:
    submitted = [
        i for i, p in enumerate(payloads) if _SUBMITTED_RE.fullmatch(p)
    ]
    if not submitted:
        raise CheckRelayError(
            "no 'Submitted batch job' line: not a run Task export"
        )
    start = submitted[-1] + 1
    end = next(
        (
            i
            for i in range(start, len(payloads))
            if payloads[i].startswith(_HOOK_PREFIX)
        ),
        len(payloads),
    )
    return [
        p
        for p in payloads[start:end]
        if not p.startswith(_MODELOPS_PARAMETER_PREFIX)
    ]


def _prefixed_messages(raws: Sequence[str]) -> list[str]:
    messages: list[str] = []
    for raw in raws:
        idx = raw.find(_RELAY_MARKER)
        if idx != -1:
            messages.append(raw[idx + len(_RELAY_MARKER) :])
    return messages


def relayed_lines(path: Path) -> RelayedResult:
    text = path.read_bytes().decode("utf-8", errors="replace")
    raws = _split_lines(text)
    if any(_RELAY_MARKER in raw for raw in raws):
        messages = _prefixed_messages(raws)
    else:
        messages = _verbatim_window([_payload(raw) for raw in raws])
    lines: list[str] = []
    informational = 0
    anomalies: list[str] = []
    for message in messages:
        if message.startswith(_INFO_PREFIXES) or _INFO_JOB_STATUS_RE.match(
            message
        ):
            informational += 1
            continue
        if message.startswith(_ANOMALY_PREFIXES):
            anomalies.append(message)
            continue
        lines.append(message)
    return RelayedResult(lines, informational, anomalies)


def ambiguous_marker(expected: Sequence[str]) -> bool:
    return any(line.startswith("[hpcmu] ") for line in expected)


def _first_divergence(expected: list[str], relayed: list[str]) -> int:
    for index, (exp, rel) in enumerate(zip(expected, relayed)):
        if exp != rel:
            return index
    return min(len(expected), len(relayed))


def _classify(
    expected: list[str], relayed: list[str], index: int
) -> Literal["lost", "extra", "changed"]:
    """The local fault at ``index``, from the first non-equal opcode of a
    bounded-window diff, so a later, unrelated fault further along in the
    window never overrides the cause right at the divergence point."""
    exp_window = expected[index : index + _DIFF_WINDOW]
    rel_window = relayed[index : index + _DIFF_WINDOW]
    matcher = difflib.SequenceMatcher(
        None, exp_window, rel_window, autojunk=False
    )
    for tag, _i1, _i2, _j1, _j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            return "lost"
        if tag == "insert":
            return "extra"
        return "changed"
    return "changed"


def _context(lines: list[str], index: int) -> list[str]:
    return [
        line[:_CONTEXT_CHARS] for line in lines[index : index + _CONTEXT_LINES]
    ]


def compare(expected: list[str], relayed: list[str]) -> Verdict:
    if ambiguous_marker(expected):
        return Verdict(ok=False, reason=_AMBIGUOUS_REASON)
    if expected == relayed:
        return Verdict(ok=True)
    index = _first_divergence(expected, relayed)
    kind = _classify(expected, relayed, index)
    return Verdict(
        ok=False,
        divergence=Divergence(
            index=index,
            kind=kind,
            expected_context=_context(expected, index),
            relayed_context=_context(relayed, index),
        ),
    )


def format_report(
    ok: bool, verdict: Verdict, expected: list[str], relayed: RelayedResult
) -> str:
    expected_hash = hashlib.sha256(
        "\n".join(expected).encode("utf-8")
    ).hexdigest()
    relayed_hash = hashlib.sha256(
        "\n".join(relayed.lines).encode("utf-8")
    ).hexdigest()
    status = "PASS" if ok else "FAIL"
    lines = [
        f"relay: {status} expected={len(expected)} "
        f"relayed={len(relayed.lines)} informational={relayed.informational} "
        f"anomalies={len(relayed.anomalies)} sha256(expected)={expected_hash} "
        f"sha256(relayed)={relayed_hash}"
    ]
    if ok:
        return lines[0]
    if verdict.reason is not None:
        lines.append(f"reason: {verdict.reason}")
    if verdict.divergence is not None:
        d = verdict.divergence
        lines.append(f"divergence: index={d.index} kind={d.kind}")
        if d.kind == "changed":
            lines.append("  (changed: lost and extra at the same index)")
        lines.append("expected context:")
        lines.extend(f"  {line}" for line in d.expected_context)
        lines.append("relayed context:")
        lines.extend(f"  {line}" for line in d.relayed_context)
    if relayed.anomalies:
        lines.append("anomalies:")
        lines.extend(f"  {line}" for line in relayed.anomalies)
    return "\n".join(lines)


def _ensure_readable(path: Path) -> None:
    with path.open("rb"):
        pass


def _check(
    relayed_path: Path, job_log_paths: tuple[Path, ...]
) -> tuple[str, int]:
    if not job_log_paths:
        raise CheckRelayError("no --job-log given")
    for path in job_log_paths:
        _ensure_readable(path)
    expected = expected_lines(job_log_paths)
    relayed = relayed_lines(relayed_path)
    verdict = compare(expected, relayed.lines)
    ok = verdict.ok and not relayed.anomalies
    return format_report(ok, verdict, expected, relayed), (0 if ok else 1)


@click.command()
@click.option("--relayed", required=True, type=click.Path(path_type=Path))
@click.option(
    "--job-log", "job_logs", multiple=True, type=click.Path(path_type=Path)
)
def main(relayed: Path, job_logs: tuple[Path, ...]) -> None:
    try:
        text, code = _check(relayed, job_logs)
    except (CheckRelayError, OSError) as exc:
        click.echo(f"check_relay: {exc}", err=True)
        raise click.exceptions.Exit(2) from exc
    click.echo(text)
    raise click.exceptions.Exit(code)


if __name__ == "__main__":
    main()
