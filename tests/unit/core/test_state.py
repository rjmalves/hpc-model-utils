"""ADR-010/ADR-009/R24/R114/R123: RunState codec and StateStore atomic I/O."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from hpc_model_utils.core.diagnosis import Diagnosis, EvidenceItem, RunStatus
from hpc_model_utils.core.errors import StateFormatError, UsageError
from hpc_model_utils.core.state import (
    STATE_KIND,
    STATE_SCHEMA_VERSION,
    ExecutionSource,
    InputsInfo,
    JobRecord,
    ModelInfo,
    ParentInfo,
    RunState,
    StateStore,
    StepRecord,
    StudyInfo,
    ToolInfo,
    current_tool,
    write_atomic,
)
from hpc_model_utils.core.workspace import Phase, Workspace


def _full_run_state() -> RunState:
    tool = ToolInfo(name="hpc-model-utils", version="1.2.3")
    model = ModelInfo(name="NEWAVE", version="29")
    inputs = InputsInfo(source="cluster", parent_path="/data/parent")
    parent = ParentInfo(
        path="/data/parent", model_name="NEWAVE", starting_date="2024-01-01"
    )
    study = StudyInfo(name="case-01", starting_date="2024-01-01")
    jobs = (
        JobRecord(
            phase=Phase.MODEL,
            job_id="111",
            submitted_at="2024-01-01T00:00:00+00:00",
            log=".hpcmu/logs/model-111.out",
        ),
        JobRecord(
            phase=Phase.FINALIZE,
            job_id="222",
            submitted_at="2024-01-01T01:00:00+00:00",
            log=".hpcmu/logs/finalize-222.out",
        ),
    )
    diagnosis = Diagnosis(
        status=RunStatus.SUCCESS,
        rule_id="core.ok",
        reason="model converged",
        evidence=(
            EvidenceItem(layer="log", source="model.out", detail="done"),
        ),
        matched=("core.ok",),
        job_id="222",
        at="2024-01-01T02:00:00+00:00",
    )
    steps = (
        StepRecord(
            command="submit-model",
            host="login01",
            started_at="2024-01-01T00:00:00+00:00",
            finished_at="2024-01-01T00:00:05+00:00",
            duration_seconds=5.0,
            outcome="ok",
        ),
    )
    return RunState(
        run_id="fixed-run-id",
        plugin="newave",
        tool=tool,
        model=model,
        inputs=inputs,
        parent=parent,
        study=study,
        execution_source=ExecutionSource.OFFLINE,
        input_files=("dadger.rv0", "vazoes.dat"),
        jobs=jobs,
        reported_job_id="222",
        diagnosis=diagnosis,
        steps=steps,
    )


def test_run_state_new_called_twice_returns_unique_run_id() -> None:
    tool = ToolInfo(name="hpc-model-utils", version="1.0.0")
    first = RunState.new("newave", tool)
    second = RunState.new("newave", tool)
    assert first.run_id != second.run_id


def test_current_tool_returns_hpc_model_utils_name() -> None:
    tool = current_tool()
    assert tool.name == "hpc-model-utils"
    assert tool.version


def test_state_store_save_and_load_full_round_trip_equals_original(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    ws.ensure_layout()
    store = StateStore(ws)
    original = _full_run_state()

    store.save(original)
    reloaded = store.load()

    assert reloaded == original

    raw = json.loads(ws.state_path.read_text(encoding="utf-8"))
    keys = list(raw.keys())
    assert keys[0] == "kind"
    assert keys[1] == "schema_version"
    assert keys[2] == "run_id"
    assert set(raw["tool"].keys()) == {"name", "version"}


def test_state_store_load_missing_file_returns_none(tmp_path: Path) -> None:
    ws = Workspace.at(tmp_path)
    store = StateStore(ws)
    assert store.load() is None


def test_state_store_load_invalid_json_raises_state_format_error(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    ws.ensure_layout()
    ws.state_path.write_text("{not json", encoding="utf-8")
    store = StateStore(ws)
    with pytest.raises(StateFormatError):
        store.load()


def test_state_store_load_invalid_utf8_raises_state_format_error(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    ws.ensure_layout()
    ws.state_path.write_bytes(b"\xff\xfe\x00")
    store = StateStore(ws)
    with pytest.raises(StateFormatError):
        store.load()


def test_state_store_load_non_object_top_level_raises_state_format_error(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    ws.ensure_layout()
    ws.state_path.write_text("[1, 2, 3]", encoding="utf-8")
    store = StateStore(ws)
    with pytest.raises(StateFormatError):
        store.load()


def test_run_state_from_dict_wrong_kind_raises_state_format_error() -> None:
    with pytest.raises(StateFormatError, match="kind"):
        RunState.from_dict(
            {"kind": "bogus", "schema_version": STATE_SCHEMA_VERSION}
        )


def test_run_state_from_dict_wrong_schema_version_raises_state_format_error() -> (
    None
):
    with pytest.raises(StateFormatError, match="schema_version"):
        RunState.from_dict({"kind": STATE_KIND, "schema_version": 2})


def test_run_state_from_dict_unknown_top_level_key_raises_state_format_error() -> (
    None
):
    data = _full_run_state().to_dict()
    data["bogus"] = "x"
    with pytest.raises(StateFormatError, match="unknown"):
        RunState.from_dict(data)


def test_run_state_from_dict_ill_typed_job_field_raises_state_format_error_with_path() -> (
    None
):
    data = _full_run_state().to_dict()
    jobs = data["jobs"]
    assert isinstance(jobs, list)
    job_one = jobs[1]
    assert isinstance(job_one, dict)
    job_one["job_id"] = 123
    with pytest.raises(StateFormatError, match=re.escape("jobs[1].job_id")):
        RunState.from_dict(data)


def test_run_state_from_dict_invalid_diagnosis_wraps_as_state_format_error() -> (
    None
):
    data = _full_run_state().to_dict()
    diagnosis = data["diagnosis"]
    assert isinstance(diagnosis, dict)
    del diagnosis["reason"]
    with pytest.raises(StateFormatError, match="diagnosis"):
        RunState.from_dict(data)


def test_state_store_load_optional_bare_workspace_returns_none_without_creating_hpcmu(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    store = StateStore(ws)
    assert store.load_optional() is None
    assert (tmp_path / ".hpcmu").exists() is False


def test_state_store_load_or_create_bare_workspace_creates_state_and_is_idempotent(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    store = StateStore(ws)

    first = store.load_or_create("newave")
    assert ws.state_path.exists()

    second = store.load_or_create("newave")
    assert second.run_id == first.run_id


def test_state_store_load_or_create_plugin_mismatch_raises_usage_error(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    store = StateStore(ws)
    store.load_or_create("newave")
    with pytest.raises(UsageError):
        store.load_or_create("decomp")


def test_state_store_save_without_layout_raises_state_format_error(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    store = StateStore(ws)
    with pytest.raises(StateFormatError, match="workspace layout missing"):
        store.save(_full_run_state())
    assert (tmp_path / ".hpcmu").exists() is False


def test_write_atomic_success_leaves_no_tmp_file(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    write_atomic(target, b"{}\n")
    assert target.read_bytes() == b"{}\n"
    assert list(tmp_path.glob(".*.tmp")) == []


def test_write_atomic_os_replace_failure_cleans_up_tmp_and_leaves_target_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "state.json"
    target.write_bytes(b"original")

    def _fail_replace(src: object, dst: object) -> None:
        raise OSError("boom")

    monkeypatch.setattr(os, "replace", _fail_replace)

    with pytest.raises(OSError):
        write_atomic(target, b"new-data")

    assert target.read_bytes() == b"original"
    assert list(tmp_path.glob(".*.tmp")) == []
