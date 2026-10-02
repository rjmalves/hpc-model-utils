"""ADR-008/ADR-014: published RunStatus tokens and the persisted Diagnosis record."""

from __future__ import annotations

import enum
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

_MAX_EVIDENCE_ITEMS = 20
_MAX_DETAIL_LENGTH = 500
_MAX_ANNOTATION_LENGTH = 500


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
        return text[:_MAX_ANNOTATION_LENGTH]

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
