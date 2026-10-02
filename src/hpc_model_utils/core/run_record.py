"""ADR-012/ADR-008/R55/R56: the published saidas/run.json RunRecord."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from hpc_model_utils.core.state import RunState

RUN_KIND = "hpcmu.run"
RUN_SCHEMA_VERSION = 1

_STRUCTURED_KEYS = (
    "run_id",
    "tool",
    "model",
    "study",
    "parent",
    "execution_source",
    "jobs",
    "steps",
    "artifacts",
    "reuse",
    "published_at",
)


@dataclass(frozen=True, slots=True)
class ArtifactEntry:
    path: str
    bytes: int


@dataclass(frozen=True, slots=True)
class ReuseRecord:
    detected: bool
    previous_run_id: str | None


def _check_no_absolute_path(value: object, field: str) -> None:
    if isinstance(value, str):
        if value.startswith("/"):
            raise ValueError(
                f"{field}: must not be an absolute path, got {value!r}"
            )
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _check_no_absolute_path(item, f"{field}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _check_no_absolute_path(item, f"{field}[{index}]")


def build_run_record(
    state: RunState,
    *,
    artifacts: Sequence[ArtifactEntry],
    reuse: ReuseRecord | None,
    published_at: str,
) -> dict[str, object]:
    record: dict[str, object] = {
        "kind": RUN_KIND,
        "schema_version": RUN_SCHEMA_VERSION,
        "run_id": state.run_id,
        "tool": {"name": state.tool.name, "version": state.tool.version},
        "model": {
            "plugin": state.plugin,
            "name": None if state.model is None else state.model.name,
            "version": None if state.model is None else state.model.version,
        },
        "study": (
            None
            if state.study is None
            else {
                "name": state.study.name,
                "starting_date": state.study.starting_date,
            }
        ),
        "parent": (
            None
            if state.parent is None
            else {
                "path": state.parent.path,
                "model_name": state.parent.model_name,
                "starting_date": state.parent.starting_date,
            }
        ),
        "execution_source": state.execution_source.value,
        "jobs": [
            {
                "phase": job.phase.value,
                "job_id": job.job_id,
                "submitted_at": job.submitted_at,
                "log": f"saidas/logs/{job.phase.value}-{job.job_id}.out",
            }
            for job in state.jobs
        ],
        "diagnosis": (
            None if state.diagnosis is None else state.diagnosis.to_dict()
        ),
        "steps": [
            {
                "command": step.command,
                "host": step.host,
                "started_at": step.started_at,
                "finished_at": step.finished_at,
                "duration_seconds": step.duration_seconds,
                "outcome": step.outcome,
            }
            for step in state.steps
        ],
        "artifacts": [
            {"path": entry.path, "bytes": entry.bytes}
            for entry in sorted(artifacts, key=lambda entry: entry.path)
        ],
        "reuse": (
            None
            if reuse is None
            else {
                "detected": reuse.detected,
                "previous_run_id": reuse.previous_run_id,
            }
        ),
        "published_at": published_at,
    }

    for key in _STRUCTURED_KEYS:
        _check_no_absolute_path(record[key], key)

    return record


def render_run_json(record: Mapping[str, object]) -> str:
    return json.dumps(record, indent=2, ensure_ascii=False) + "\n"


def read_previous_run_id(data: bytes) -> str | None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("kind") != RUN_KIND:
        return None
    schema_version = parsed.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        return None
    if schema_version < 1:
        return None
    run_id = parsed.get("run_id")
    if not isinstance(run_id, str):
        return None
    return run_id
