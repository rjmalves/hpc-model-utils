"""ADR-010/ADR-009/R24/R114/R123: the typed RunState and its atomic store."""

from __future__ import annotations

import enum
import importlib.metadata
import json
import logging
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from hpc_model_utils.core.diagnosis import Diagnosis
from hpc_model_utils.core.errors import StateFormatError, UsageError
from hpc_model_utils.core.outputs import RealizedOutputs
from hpc_model_utils.core.workspace import Phase, Workspace

logger = logging.getLogger(__name__)

STATE_KIND = "hpcmu.state"
STATE_SCHEMA_VERSION = 1
FINALIZE_KIND = "hpcmu.finalize"
FINALIZE_SCHEMA_VERSION = 1


class ExecutionSource(enum.StrEnum):
    CLUSTER = "CLUSTER"
    OFFLINE = "OFFLINE"


@dataclass(frozen=True, slots=True)
class ToolInfo:
    name: str
    version: str


@dataclass(frozen=True, slots=True)
class ModelInfo:
    name: str
    version: str | None


@dataclass(frozen=True, slots=True)
class InputsInfo:
    source: str
    parent_path: str


@dataclass(frozen=True, slots=True)
class ParentInfo:
    path: str
    model_name: str
    starting_date: str


@dataclass(frozen=True, slots=True)
class StudyInfo:
    name: str
    starting_date: str


@dataclass(frozen=True, slots=True)
class JobRecord:
    phase: Phase
    job_id: str
    submitted_at: str
    log: str


@dataclass(frozen=True, slots=True)
class StepRecord:
    command: str
    host: str
    started_at: str
    finished_at: str
    duration_seconds: float
    outcome: str


def _step_record_to_dict(step: StepRecord) -> dict[str, object]:
    return {
        "command": step.command,
        "host": step.host,
        "started_at": step.started_at,
        "finished_at": step.finished_at,
        "duration_seconds": step.duration_seconds,
        "outcome": step.outcome,
    }


_TOP_LEVEL_KEYS = frozenset(
    {
        "kind",
        "schema_version",
        "run_id",
        "plugin",
        "tool",
        "model",
        "inputs",
        "parent",
        "study",
        "execution_source",
        "input_files",
        "jobs",
        "reported_job_id",
        "diagnosis",
        "steps",
    }
)


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _field(data: Mapping[str, object], key: str, path: str) -> object:
    try:
        return data[key]
    except KeyError:
        raise StateFormatError(f"{_join(path, key)}: missing") from None


def _as_str(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise StateFormatError(
            f"{path}: must be str, got {type(value).__name__}"
        )
    return value


def _as_mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise StateFormatError(
            f"{path}: must be an object, got {type(value).__name__}"
        )
    return value


def _str_field(data: Mapping[str, object], key: str, path: str) -> str:
    return _as_str(_field(data, key, path), _join(path, key))


def _optional_str_field(
    data: Mapping[str, object], key: str, path: str
) -> str | None:
    value = _field(data, key, path)
    if value is None:
        return None
    return _as_str(value, _join(path, key))


def _number_field(data: Mapping[str, object], key: str, path: str) -> float:
    value = _field(data, key, path)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise StateFormatError(
            f"{_join(path, key)}: must be a number, got {type(value).__name__}"
        )
    return float(value)


def _mapping_field(
    data: Mapping[str, object], key: str, path: str
) -> Mapping[str, object]:
    return _as_mapping(_field(data, key, path), _join(path, key))


def _optional_mapping_field(
    data: Mapping[str, object], key: str, path: str
) -> Mapping[str, object] | None:
    value = _field(data, key, path)
    if value is None:
        return None
    return _as_mapping(value, _join(path, key))


def _list_field(
    data: Mapping[str, object], key: str, path: str
) -> list[object]:
    value = _field(data, key, path)
    if not isinstance(value, list):
        raise StateFormatError(
            f"{_join(path, key)}: must be a list, got {type(value).__name__}"
        )
    return value


def _str_tuple_field(
    data: Mapping[str, object], key: str, path: str
) -> tuple[str, ...]:
    items = _list_field(data, key, path)
    full_path = _join(path, key)
    result: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, str):
            raise StateFormatError(
                f"{full_path}[{index}]: must be str, got {type(item).__name__}"
            )
        result.append(item)
    return tuple(result)


def _decode_tool(mapping: Mapping[str, object], path: str) -> ToolInfo:
    return ToolInfo(
        name=_str_field(mapping, "name", path),
        version=_str_field(mapping, "version", path),
    )


def _decode_model(mapping: Mapping[str, object], path: str) -> ModelInfo:
    return ModelInfo(
        name=_str_field(mapping, "name", path),
        version=_optional_str_field(mapping, "version", path),
    )


def _decode_inputs(mapping: Mapping[str, object], path: str) -> InputsInfo:
    return InputsInfo(
        source=_str_field(mapping, "source", path),
        parent_path=_str_field(mapping, "parent_path", path),
    )


def _decode_parent(mapping: Mapping[str, object], path: str) -> ParentInfo:
    return ParentInfo(
        path=_str_field(mapping, "path", path),
        model_name=_str_field(mapping, "model_name", path),
        starting_date=_str_field(mapping, "starting_date", path),
    )


def _decode_study(mapping: Mapping[str, object], path: str) -> StudyInfo:
    return StudyInfo(
        name=_str_field(mapping, "name", path),
        starting_date=_str_field(mapping, "starting_date", path),
    )


def _decode_phase(value: object, path: str) -> Phase:
    token = _as_str(value, path)
    try:
        return Phase(token)
    except ValueError:
        raise StateFormatError(
            f"{path}: must be one of {[member.value for member in Phase]}, got {token!r}"
        ) from None


def _decode_job(value: object, path: str) -> JobRecord:
    mapping = _as_mapping(value, path)
    phase = _decode_phase(_field(mapping, "phase", path), _join(path, "phase"))
    return JobRecord(
        phase=phase,
        job_id=_str_field(mapping, "job_id", path),
        submitted_at=_str_field(mapping, "submitted_at", path),
        log=_str_field(mapping, "log", path),
    )


def _decode_jobs(items: list[object], path: str) -> tuple[JobRecord, ...]:
    return tuple(
        _decode_job(item, f"{path}[{index}]")
        for index, item in enumerate(items)
    )


def _decode_step(value: object, path: str) -> StepRecord:
    mapping = _as_mapping(value, path)
    return StepRecord(
        command=_str_field(mapping, "command", path),
        host=_str_field(mapping, "host", path),
        started_at=_str_field(mapping, "started_at", path),
        finished_at=_str_field(mapping, "finished_at", path),
        duration_seconds=_number_field(mapping, "duration_seconds", path),
        outcome=_str_field(mapping, "outcome", path),
    )


def _decode_steps(items: list[object], path: str) -> tuple[StepRecord, ...]:
    return tuple(
        _decode_step(item, f"{path}[{index}]")
        for index, item in enumerate(items)
    )


def _decode_execution_source(value: object, path: str) -> ExecutionSource:
    token = _as_str(value, path)
    try:
        return ExecutionSource(token)
    except ValueError:
        raise StateFormatError(
            f"{path}: must be one of "
            f"{[member.value for member in ExecutionSource]}, got {token!r}"
        ) from None


def _decode_diagnosis(mapping: Mapping[str, object], path: str) -> Diagnosis:
    try:
        return Diagnosis.from_dict(mapping)
    except (KeyError, TypeError, ValueError) as exc:
        raise StateFormatError(f"{path}: {exc}") from exc


@dataclass(frozen=True, slots=True)
class RunState:
    run_id: str
    plugin: str
    tool: ToolInfo
    model: ModelInfo | None = None
    inputs: InputsInfo | None = None
    parent: ParentInfo | None = None
    study: StudyInfo | None = None
    execution_source: ExecutionSource = ExecutionSource.CLUSTER
    input_files: tuple[str, ...] = ()
    jobs: tuple[JobRecord, ...] = ()
    reported_job_id: str | None = None
    diagnosis: Diagnosis | None = None
    steps: tuple[StepRecord, ...] = ()

    @classmethod
    def new(cls, plugin: str, tool: ToolInfo) -> RunState:
        return cls(run_id=uuid.uuid4().hex, plugin=plugin, tool=tool)

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": STATE_KIND,
            "schema_version": STATE_SCHEMA_VERSION,
            "run_id": self.run_id,
            "plugin": self.plugin,
            "tool": {"name": self.tool.name, "version": self.tool.version},
            "model": (
                None
                if self.model is None
                else {"name": self.model.name, "version": self.model.version}
            ),
            "inputs": (
                None
                if self.inputs is None
                else {
                    "source": self.inputs.source,
                    "parent_path": self.inputs.parent_path,
                }
            ),
            "parent": (
                None
                if self.parent is None
                else {
                    "path": self.parent.path,
                    "model_name": self.parent.model_name,
                    "starting_date": self.parent.starting_date,
                }
            ),
            "study": (
                None
                if self.study is None
                else {
                    "name": self.study.name,
                    "starting_date": self.study.starting_date,
                }
            ),
            "execution_source": str(self.execution_source),
            "input_files": list(self.input_files),
            "jobs": [
                {
                    "phase": str(job.phase),
                    "job_id": job.job_id,
                    "submitted_at": job.submitted_at,
                    "log": job.log,
                }
                for job in self.jobs
            ],
            "reported_job_id": self.reported_job_id,
            "diagnosis": None
            if self.diagnosis is None
            else self.diagnosis.to_dict(),
            "steps": [_step_record_to_dict(step) for step in self.steps],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> RunState:
        extra = set(data) - _TOP_LEVEL_KEYS
        if extra:
            raise StateFormatError(f"unknown top-level keys: {sorted(extra)}")

        kind = data.get("kind")
        if kind != STATE_KIND:
            raise StateFormatError(
                f"kind: expected {STATE_KIND!r}, got {kind!r}"
            )

        schema_version = data.get("schema_version")
        if isinstance(schema_version, bool) or not isinstance(
            schema_version, int
        ):
            raise StateFormatError(
                f"schema_version: must be int, got {type(schema_version).__name__}"
            )
        if schema_version != STATE_SCHEMA_VERSION:
            raise StateFormatError(
                f"schema_version: expected {STATE_SCHEMA_VERSION}, got {schema_version}"
            )

        model_raw = _optional_mapping_field(data, "model", "")
        inputs_raw = _optional_mapping_field(data, "inputs", "")
        parent_raw = _optional_mapping_field(data, "parent", "")
        study_raw = _optional_mapping_field(data, "study", "")
        diagnosis_raw = _optional_mapping_field(data, "diagnosis", "")

        return cls(
            run_id=_str_field(data, "run_id", ""),
            plugin=_str_field(data, "plugin", ""),
            tool=_decode_tool(_mapping_field(data, "tool", ""), "tool"),
            model=None
            if model_raw is None
            else _decode_model(model_raw, "model"),
            inputs=None
            if inputs_raw is None
            else _decode_inputs(inputs_raw, "inputs"),
            parent=None
            if parent_raw is None
            else _decode_parent(parent_raw, "parent"),
            study=None
            if study_raw is None
            else _decode_study(study_raw, "study"),
            execution_source=_decode_execution_source(
                _field(data, "execution_source", ""), "execution_source"
            ),
            input_files=_str_tuple_field(data, "input_files", ""),
            jobs=_decode_jobs(_list_field(data, "jobs", ""), "jobs"),
            reported_job_id=_optional_str_field(data, "reported_job_id", ""),
            diagnosis=(
                None
                if diagnosis_raw is None
                else _decode_diagnosis(diagnosis_raw, "diagnosis")
            ),
            steps=_decode_steps(_list_field(data, "steps", ""), "steps"),
        )


def current_tool() -> ToolInfo:
    return ToolInfo(
        "hpc-model-utils", importlib.metadata.version("hpc-model-utils")
    )


def write_atomic(path: Path, data: bytes) -> None:
    tmp_path = path.parent / f".{path.name}.{os.getpid()}.tmp"
    try:
        with tmp_path.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise

    try:
        dir_fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        logger.debug("directory fsync skipped for %s", path.parent)
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        logger.debug("directory fsync failed for %s", path.parent)
    finally:
        os.close(dir_fd)


def metadata_items(
    state: RunState, *, always_write_parent_path: bool
) -> list[tuple[str, str]]:
    """R25/ADR-011: the v1.1.2 metadata.modelops keys, in canonical order."""
    items: list[tuple[str, str]] = []
    if state.model is not None:
        items.append(("model_name", state.model.name))
        if state.model.version is not None:
            items.append(("model_version", state.model.version))
    if always_write_parent_path:
        if state.inputs is not None:
            items.append(("parent_path", state.inputs.parent_path))
    elif state.parent is not None:
        items.append(("parent_path", state.parent.path))
    if state.parent is not None:
        items.append(("parent_starting_date", state.parent.starting_date))
    if state.study is not None:
        items.append(("study_starting_date", state.study.starting_date))
        items.append(("study_name", state.study.name))
    if state.execution_source is ExecutionSource.OFFLINE:
        items.append(("execution_source", state.execution_source.value))
    if state.diagnosis is not None:
        items.append(("job_id", state.reported_job_id or ""))
        items.append(("status", state.diagnosis.status.value))
    return items


def render_metadata(
    state: RunState, *, always_write_parent_path: bool
) -> str | None:
    items = metadata_items(
        state, always_write_parent_path=always_write_parent_path
    )
    if not items:
        return None
    return json.dumps(dict(items))


def render_status(state: RunState) -> str | None:
    if state.diagnosis is None:
        return None
    return state.diagnosis.status.value


def write_projections(
    ws: Workspace, state: RunState, *, always_write_parent_path: bool
) -> None:
    """R25/ADR-011: project RunState onto the legacy files; never read them back."""
    metadata = render_metadata(
        state, always_write_parent_path=always_write_parent_path
    )
    if metadata is not None:
        write_atomic(ws.legacy_metadata_path, metadata.encode("ascii"))
    status = render_status(state)
    if status is not None:
        write_atomic(ws.legacy_status_path, status.encode("ascii"))


def _read_json_document(path: Path, label: str) -> dict[str, object] | None:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StateFormatError(f"{label} is not valid UTF-8: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StateFormatError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise StateFormatError(
            f"{label} top-level value must be an object, got "
            f"{type(data).__name__}"
        )
    return data


class StateStore:
    def __init__(self, ws: Workspace) -> None:
        self._ws = ws

    def load(self) -> RunState | None:
        data = _read_json_document(self._ws.state_path, "state.json")
        if data is None:
            return None
        return RunState.from_dict(data)

    def save(self, state: RunState) -> None:
        if not self._ws.hpcmu_dir.is_dir():
            raise StateFormatError("workspace layout missing")
        payload = (
            json.dumps(state.to_dict(), indent=2, ensure_ascii=False) + "\n"
        )
        write_atomic(self._ws.state_path, payload.encode("utf-8"))

    def load_or_create(self, plugin: str) -> RunState:
        self._ws.ensure_layout()
        existing = self.load()
        if existing is not None:
            if existing.plugin != plugin:
                raise UsageError(
                    f"workspace plugin mismatch: expected {existing.plugin!r}, got {plugin!r}"
                )
            return existing
        state = RunState.new(plugin, current_tool())
        self.save(state)
        return state

    def load_optional(self) -> RunState | None:
        return self.load()


_FINALIZE_TOP_LEVEL_KEYS = frozenset(
    {
        "kind",
        "schema_version",
        "run_id",
        "diagnosis",
        "postprocess",
        "synthesis",
        "outputs",
        "steps",
    }
)


def _bool_field(data: Mapping[str, object], key: str, path: str) -> bool:
    value = _field(data, key, path)
    if not isinstance(value, bool):
        raise StateFormatError(
            f"{_join(path, key)}: must be bool, got {type(value).__name__}"
        )
    return value


@dataclass(frozen=True, slots=True)
class StepOutcome:
    name: str
    ok: bool
    detail: str
    duration_seconds: float


def _step_outcome_to_dict(outcome: StepOutcome) -> dict[str, object]:
    return {
        "name": outcome.name,
        "ok": outcome.ok,
        "detail": outcome.detail,
        "duration_seconds": outcome.duration_seconds,
    }


def _decode_step_outcome(value: Mapping[str, object], path: str) -> StepOutcome:
    return StepOutcome(
        name=_str_field(value, "name", path),
        ok=_bool_field(value, "ok", path),
        detail=_str_field(value, "detail", path),
        duration_seconds=_number_field(value, "duration_seconds", path),
    )


def _realized_outputs_to_dict(outputs: RealizedOutputs) -> dict[str, object]:
    """``core/outputs.py`` is out of scope for this ticket, so
    ``RealizedOutputs`` is encoded/decoded here rather than gaining its
    own ``to_dict``/``from_dict`` pair."""
    return {
        "deck": outputs.deck,
        "archives": list(outputs.archives),
        "raw": [[source, dest] for source, dest in outputs.raw],
    }


def _decode_realized_outputs(
    value: Mapping[str, object], path: str
) -> RealizedOutputs:
    archives = _str_tuple_field(value, "archives", path)
    raw_items = _list_field(value, "raw", path)
    raw_path = _join(path, "raw")
    raw: list[tuple[str, str]] = []
    for index, item in enumerate(raw_items):
        pair_path = f"{raw_path}[{index}]"
        if not isinstance(item, list) or len(item) != 2:
            raise StateFormatError(
                f"{pair_path}: must be a 2-item list, got {type(item).__name__}"
            )
        source, dest = item
        if not isinstance(source, str) or not isinstance(dest, str):
            raise StateFormatError(f"{pair_path}: must be a list of two str")
        raw.append((source, dest))
    return RealizedOutputs(
        deck=_optional_str_field(value, "deck", path),
        archives=archives,
        raw=tuple(raw),
    )


@dataclass(frozen=True, slots=True)
class FinalizeRecord:
    run_id: str
    diagnosis: Diagnosis
    postprocess: StepOutcome | None = None
    synthesis: StepOutcome | None = None
    outputs: RealizedOutputs | None = None
    steps: tuple[StepRecord, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": FINALIZE_KIND,
            "schema_version": FINALIZE_SCHEMA_VERSION,
            "run_id": self.run_id,
            "diagnosis": self.diagnosis.to_dict(),
            "postprocess": (
                None
                if self.postprocess is None
                else _step_outcome_to_dict(self.postprocess)
            ),
            "synthesis": (
                None
                if self.synthesis is None
                else _step_outcome_to_dict(self.synthesis)
            ),
            "outputs": (
                None
                if self.outputs is None
                else _realized_outputs_to_dict(self.outputs)
            ),
            "steps": [_step_record_to_dict(step) for step in self.steps],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> FinalizeRecord:
        extra = set(data) - _FINALIZE_TOP_LEVEL_KEYS
        if extra:
            raise StateFormatError(f"unknown top-level keys: {sorted(extra)}")

        kind = data.get("kind")
        if kind != FINALIZE_KIND:
            raise StateFormatError(
                f"kind: expected {FINALIZE_KIND!r}, got {kind!r}"
            )

        schema_version = data.get("schema_version")
        if isinstance(schema_version, bool) or not isinstance(
            schema_version, int
        ):
            raise StateFormatError(
                f"schema_version: must be int, got "
                f"{type(schema_version).__name__}"
            )
        if schema_version != FINALIZE_SCHEMA_VERSION:
            raise StateFormatError(
                f"schema_version: expected {FINALIZE_SCHEMA_VERSION}, "
                f"got {schema_version}"
            )

        postprocess_raw = _optional_mapping_field(data, "postprocess", "")
        synthesis_raw = _optional_mapping_field(data, "synthesis", "")
        outputs_raw = _optional_mapping_field(data, "outputs", "")

        return cls(
            run_id=_str_field(data, "run_id", ""),
            diagnosis=_decode_diagnosis(
                _mapping_field(data, "diagnosis", ""), "diagnosis"
            ),
            postprocess=(
                None
                if postprocess_raw is None
                else _decode_step_outcome(postprocess_raw, "postprocess")
            ),
            synthesis=(
                None
                if synthesis_raw is None
                else _decode_step_outcome(synthesis_raw, "synthesis")
            ),
            outputs=(
                None
                if outputs_raw is None
                else _decode_realized_outputs(outputs_raw, "outputs")
            ),
            steps=_decode_steps(_list_field(data, "steps", ""), "steps"),
        )


def write_finalize(ws: Workspace, rec: FinalizeRecord) -> None:
    """ADR-010/amendment 2: one atomic write per finalize run, no lock.

    A requeued finalize rerun calls this again and atomically replaces
    whatever a previous attempt left.
    """
    payload = json.dumps(rec.to_dict(), indent=2, ensure_ascii=False) + "\n"
    write_atomic(ws.finalize_path, payload.encode("utf-8"))


def load_finalize(ws: Workspace, run_id: str) -> FinalizeRecord | None:
    data = _read_json_document(ws.finalize_path, "finalize.json")
    if data is None:
        return None
    record = FinalizeRecord.from_dict(data)
    if record.run_id != run_id:
        raise StateFormatError(
            f"run_id: expected {run_id!r}, got {record.run_id!r}"
        )
    return record
