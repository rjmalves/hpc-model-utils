"""ADR-008/ADR-014: published RunStatus tokens and the persisted Diagnosis record."""

from __future__ import annotations

import enum
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from hpc_model_utils.infra.slurm import JobOutcome

_MAX_EVIDENCE_ITEMS = 20
_MAX_DETAIL_LENGTH = 500
ANNOTATION_MAX_LENGTH = 500


class RunStatus(enum.StrEnum):
    SUCCESS = "SUCCESS"
    INFEASIBLE = "INFEASIBLE"
    DATA_ERROR = "DATA_ERROR"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    TIMEOUT = "TIMEOUT"
    INFRA_ERROR = "INFRA_ERROR"
    LICENSE_ERROR = "LICENSE_ERROR"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def parse(cls, token: str) -> RunStatus:
        stripped = token.strip()
        try:
            return cls(stripped)
        except ValueError:
            return cls.UNKNOWN


EvidenceLayer = Literal["slurm", "log", "guard", "plugin"]


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    layer: EvidenceLayer
    source: str
    detail: str

    def __post_init__(self) -> None:
        if len(self.detail) > _MAX_DETAIL_LENGTH:
            object.__setattr__(self, "detail", self.detail[:_MAX_DETAIL_LENGTH])


RULE_ID_PATTERN = re.compile(r"^(slurm|core|newave|decomp|cobre)\.[a-z0-9_]+$")


def _parse_evidence_layer(value: object) -> EvidenceLayer:
    if not isinstance(value, str):
        raise TypeError(f"layer must be str, got {type(value)!r}")
    if value == "slurm":
        return "slurm"
    if value == "log":
        return "log"
    if value == "guard":
        return "guard"
    if value == "plugin":
        return "plugin"
    raise ValueError(f"layer {value!r} is not a valid EvidenceLayer")


def _require_str(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be str, got {type(value)!r}")
    return value


def _evidence_item_from_dict(data: object) -> EvidenceItem:
    if not isinstance(data, Mapping):
        raise TypeError(f"evidence item must be a mapping, got {type(data)!r}")
    layer = _parse_evidence_layer(data["layer"])
    source = _require_str(data["source"], "source")
    detail = _require_str(data["detail"], "detail")
    return EvidenceItem(layer=layer, source=source, detail=detail)


def _str_tuple(values: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(values, list):
        raise TypeError(f"{field_name} must be list, got {type(values)!r}")
    for entry in values:
        if not isinstance(entry, str):
            raise TypeError(
                f"{field_name} entries must be str, got {type(entry)!r}"
            )
    return tuple(values)


@dataclass(frozen=True, slots=True)
class Diagnosis:
    status: RunStatus
    rule_id: str
    reason: str
    evidence: tuple[EvidenceItem, ...] = ()
    matched: tuple[str, ...] = ()
    job_id: str | None = None
    at: str = ""

    def __post_init__(self) -> None:
        if not RULE_ID_PATTERN.fullmatch(self.rule_id):
            raise ValueError(
                f"rule_id {self.rule_id!r} must match {RULE_ID_PATTERN.pattern}"
            )
        for entry in self.matched:
            if not RULE_ID_PATTERN.fullmatch(entry):
                raise ValueError(
                    f"matched entry {entry!r} must match {RULE_ID_PATTERN.pattern}"
                )
        if len(self.evidence) > _MAX_EVIDENCE_ITEMS:
            object.__setattr__(
                self, "evidence", self.evidence[:_MAX_EVIDENCE_ITEMS]
            )

    def annotation(self) -> str:
        text = f"{self.status}: {self.reason} [{self.rule_id}]"
        return text[:ANNOTATION_MAX_LENGTH]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": str(self.status),
            "rule_id": self.rule_id,
            "reason": self.reason,
            "evidence": [
                {
                    "layer": item.layer,
                    "source": item.source,
                    "detail": item.detail,
                }
                for item in self.evidence
            ],
            "matched": list(self.matched),
            "job_id": self.job_id,
            "at": self.at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Diagnosis:
        status_token = _require_str(data["status"], "status")
        rule_id = _require_str(data["rule_id"], "rule_id")
        reason = _require_str(data["reason"], "reason")
        evidence_raw = data["evidence"]
        if not isinstance(evidence_raw, list):
            raise TypeError(
                f"evidence must be list, got {type(evidence_raw)!r}"
            )
        evidence = tuple(
            _evidence_item_from_dict(item) for item in evidence_raw
        )
        matched = _str_tuple(data["matched"], "matched")
        job_id = data["job_id"]
        if job_id is not None and not isinstance(job_id, str):
            raise TypeError(f"job_id must be str or None, got {type(job_id)!r}")
        at = _require_str(data["at"], "at")
        return cls(
            status=RunStatus.parse(status_token),
            rule_id=rule_id,
            reason=reason,
            evidence=evidence,
            matched=matched,
            job_id=job_id,
            at=at,
        )


def utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


_LOG_READ_CHUNK = 1 << 20


@dataclass(frozen=True, slots=True)
class LogPattern:
    rule_id: str
    pattern: str
    status: RunStatus
    reason: str
    compiled: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if RULE_ID_PATTERN.fullmatch(self.rule_id) is None:
            raise ValueError(
                f"rule_id {self.rule_id!r} must match {RULE_ID_PATTERN.pattern}"
            )
        object.__setattr__(self, "compiled", re.compile(self.pattern))


@dataclass(frozen=True, slots=True)
class JobReport:
    outcome: JobOutcome | None
    process_exit: int | None
    log_paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class Verdict:
    status: RunStatus
    rule_id: str
    reason: str
    evidence: tuple[EvidenceItem, ...] = ()
    matched: tuple[str, ...] = ()


# ADR-014 L1 table: sacct/scontrol state -> (RunStatus, rule_id, reason
# template). `oom` is checked ahead of this table (evaluate() handles it
# directly), and COMPLETED/FAILED/anything unlisted fall through to L2.
SLURM_RULES: Mapping[str, tuple[RunStatus, str, str]] = {
    "TIMEOUT": (
        RunStatus.TIMEOUT,
        "slurm.timeout",
        "job {job_id} hit its {time_limit} limit",
    ),
    "DEADLINE": (
        RunStatus.TIMEOUT,
        "slurm.timeout",
        "job {job_id} hit its {time_limit} limit",
    ),
    "OUT_OF_MEMORY": (
        RunStatus.INFRA_ERROR,
        "slurm.out_of_memory",
        "job {job_id} was killed by the OOM killer after {elapsed}",
    ),
    "NODE_FAIL": (
        RunStatus.INFRA_ERROR,
        "slurm.node_fail",
        "job {job_id} failed because its node failed",
    ),
    "BOOT_FAIL": (
        RunStatus.INFRA_ERROR,
        "slurm.boot_fail",
        "job {job_id} failed because its node failed to boot",
    ),
    "PREEMPTED": (
        RunStatus.INFRA_ERROR,
        "slurm.preempted",
        "job {job_id} was preempted",
    ),
    "CANCELLED": (
        RunStatus.CANCELLED,
        "slurm.cancelled",
        "job {job_id} was cancelled",
    ),
}


def _render_reason(template: str, outcome: JobOutcome) -> str:
    return template.format(
        job_id=outcome.job_id,
        time_limit="?" if outcome.time_limit is None else outcome.time_limit,
        elapsed="?" if outcome.elapsed is None else outcome.elapsed,
    )


def _evaluate_l1(
    outcome: JobOutcome | None,
) -> tuple[Verdict | None, tuple[EvidenceItem, ...]]:
    if outcome is None:
        return None, ()
    if outcome.source == "none":
        return None, (
            EvidenceItem("slurm", "accounting", "no sacct/scontrol record"),
        )
    if outcome.oom:
        status, rule_id, template = SLURM_RULES["OUT_OF_MEMORY"]
        return Verdict(status, rule_id, _render_reason(template, outcome)), ()
    rule = SLURM_RULES.get(outcome.state)
    if rule is None:
        return None, ()
    status, rule_id, template = rule
    return Verdict(status, rule_id, _render_reason(template, outcome)), ()


def _evaluate_l2(
    log_paths: Sequence[Path], log_patterns: Sequence[LogPattern]
) -> tuple[Verdict | None, tuple[EvidenceItem, ...], tuple[str, ...]]:
    evidence: list[EvidenceItem] = []
    matched: list[str] = []
    candidate: Verdict | None = None
    for path in log_paths:
        if log_patterns and len(matched) == len(log_patterns):
            break
        try:
            with path.open("rb") as handle:
                if not log_patterns:
                    continue
                while len(matched) < len(log_patterns):
                    chunk = handle.readline(_LOG_READ_CHUNK)
                    if not chunk:
                        break
                    line = chunk.decode("utf-8", errors="replace").rstrip(
                        "\r\n"
                    )
                    for log_pattern in log_patterns:
                        if log_pattern.rule_id in matched:
                            continue
                        if log_pattern.compiled.search(line) is None:
                            continue
                        matched.append(log_pattern.rule_id)
                        if candidate is None:
                            candidate = Verdict(
                                log_pattern.status,
                                log_pattern.rule_id,
                                log_pattern.reason,
                            )
        except OSError as exc:
            evidence.append(
                EvidenceItem(
                    "log",
                    path.name,
                    f"unreadable: {exc.strerror or type(exc).__name__}",
                )
            )
    return candidate, tuple(evidence), tuple(matched)


def _evaluate_guard(primary_evidence: Sequence[Path]) -> Verdict | None:
    missing = [path.name for path in primary_evidence if not path.exists()]
    if not missing:
        return None
    names = ", ".join(missing)
    return Verdict(
        status=RunStatus.RUNTIME_ERROR,
        rule_id="core.missing_output",
        reason=f"missing output file(s): {names}",
        evidence=(
            EvidenceItem("guard", "primary_evidence", f"missing: {names}"),
        ),
    )


def _evaluate_l3(rules: Callable[[], Verdict]) -> Verdict:
    try:
        return rules()
    except Exception as exc:
        # Plugin L3 code is untrusted for robustness: a crash there must
        # never mask the run outcome, only degrade it to UNKNOWN. This is
        # the one sanctioned broad catch in this module (never
        # BaseException, so KeyboardInterrupt/SystemExit still propagate).
        detail = str(exc)
        return Verdict(
            status=RunStatus.UNKNOWN,
            rule_id="core.diagnosis_exception",
            reason=f"diagnosis plugin raised {type(exc).__name__}: {detail}",
            evidence=(EvidenceItem("plugin", "rules", detail),),
        )


def evaluate(
    report: JobReport,
    *,
    log_patterns: Sequence[LogPattern],
    primary_evidence: Sequence[Path],
    rules: Callable[[], Verdict],
    job_id: str | None,
) -> Diagnosis:
    l1_verdict, l1_evidence = _evaluate_l1(report.outcome)
    l2_verdict, l2_evidence, matched = _evaluate_l2(
        report.log_paths, log_patterns
    )
    guard_verdict = _evaluate_guard(primary_evidence)
    guard_evidence = guard_verdict.evidence if guard_verdict is not None else ()

    winner = l1_verdict or l2_verdict or guard_verdict
    l3_evidence: tuple[EvidenceItem, ...] = ()
    if winner is None:
        winner = _evaluate_l3(rules)
        l3_evidence = winner.evidence

    evidence = (*l1_evidence, *l2_evidence, *guard_evidence, *l3_evidence)
    return Diagnosis(
        status=winner.status,
        rule_id=winner.rule_id,
        reason=winner.reason,
        evidence=evidence,
        matched=(*matched, *winner.matched),
        job_id=job_id,
        at=utc_now_iso(),
    )
