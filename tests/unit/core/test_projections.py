"""R25/ADR-011: unit coverage of the legacy projection presence rules."""

from __future__ import annotations

import json
from pathlib import Path

from hpc_model_utils.core.diagnosis import Diagnosis, RunStatus
from hpc_model_utils.core.state import (
    ExecutionSource,
    InputsInfo,
    ModelInfo,
    ParentInfo,
    RunState,
    StudyInfo,
    ToolInfo,
    render_metadata,
    render_status,
    write_projections,
)
from hpc_model_utils.core.workspace import Workspace

_TOOL = ToolInfo(name="hpc-model-utils", version="2.0.0")


def _state(
    *,
    model: ModelInfo | None = None,
    inputs: InputsInfo | None = None,
    parent: ParentInfo | None = None,
    study: StudyInfo | None = None,
    execution_source: ExecutionSource = ExecutionSource.CLUSTER,
    reported_job_id: str | None = None,
    diagnosis: Diagnosis | None = None,
) -> RunState:
    return RunState(
        run_id="r",
        plugin="newave",
        tool=_TOOL,
        model=model,
        inputs=inputs,
        parent=parent,
        study=study,
        execution_source=execution_source,
        reported_job_id=reported_job_id,
        diagnosis=diagnosis,
    )


def test_metadata_items_empty_state_returns_empty_list() -> None:
    assert render_metadata(_state(), always_write_parent_path=True) is None


def test_metadata_items_model_set_includes_model_name_and_version() -> None:
    state = _state(model=ModelInfo(name="NEWAVE", version="30.0.4"))
    rendered = render_metadata(state, always_write_parent_path=True)
    assert rendered == '{"model_name": "NEWAVE", "model_version": "30.0.4"}'


def test_metadata_items_model_without_version_omits_model_version() -> None:
    state = _state(model=ModelInfo(name="NEWAVE", version=None))
    rendered = render_metadata(state, always_write_parent_path=True)
    assert rendered == '{"model_name": "NEWAVE"}'


def test_metadata_items_always_write_parent_path_true_uses_inputs_parent_path() -> (
    None
):
    state = _state(inputs=InputsInfo(source="cluster", parent_path=""))
    rendered = render_metadata(state, always_write_parent_path=True)
    assert rendered == '{"parent_path": ""}'


def test_metadata_items_always_write_parent_path_true_no_inputs_omits_parent_path() -> (
    None
):
    state = _state()
    rendered = render_metadata(state, always_write_parent_path=True)
    assert rendered is None


def test_metadata_items_always_write_parent_path_false_no_parent_omits_parent_path() -> (
    None
):
    state = _state(inputs=InputsInfo(source="cluster", parent_path="/parent"))
    rendered = render_metadata(state, always_write_parent_path=False)
    assert rendered is None


def test_metadata_items_always_write_parent_path_false_uses_parent_path() -> (
    None
):
    state = _state(
        parent=ParentInfo(
            path="/parent", model_name="DECOMP", starting_date="2025-11-01"
        )
    )
    rendered = render_metadata(state, always_write_parent_path=False)
    assert (
        rendered
        == '{"parent_path": "/parent", "parent_starting_date": "2025-11-01"}'
    )


def test_metadata_items_parent_set_includes_parent_starting_date() -> None:
    state = _state(
        parent=ParentInfo(
            path="/parent", model_name="DECOMP", starting_date="2025-11-01"
        )
    )
    rendered = render_metadata(state, always_write_parent_path=True)
    assert rendered == '{"parent_starting_date": "2025-11-01"}'


def test_metadata_items_study_set_includes_study_starting_date_and_name() -> (
    None
):
    state = _state(study=StudyInfo(name="case-01", starting_date="2025-11-01"))
    rendered = render_metadata(state, always_write_parent_path=True)
    assert (
        rendered
        == '{"study_starting_date": "2025-11-01", "study_name": "case-01"}'
    )


def test_metadata_items_execution_source_offline_includes_key() -> None:
    state = _state(execution_source=ExecutionSource.OFFLINE)
    rendered = render_metadata(state, always_write_parent_path=True)
    assert rendered == '{"execution_source": "OFFLINE"}'


def test_metadata_items_execution_source_cluster_omits_key() -> None:
    state = _state(execution_source=ExecutionSource.CLUSTER)
    rendered = render_metadata(state, always_write_parent_path=True)
    assert rendered is None


def test_metadata_items_diagnosis_set_includes_job_id_and_status() -> None:
    state = _state(
        reported_job_id="777",
        diagnosis=Diagnosis(
            status=RunStatus.SUCCESS, rule_id="newave.success", reason=""
        ),
    )
    rendered = render_metadata(state, always_write_parent_path=True)
    assert rendered == '{"job_id": "777", "status": "SUCCESS"}'


def test_metadata_items_diagnosis_without_reported_job_id_renders_empty_string() -> (
    None
):
    state = _state(
        reported_job_id=None,
        diagnosis=Diagnosis(
            status=RunStatus.SUCCESS, rule_id="newave.success", reason=""
        ),
    )
    rendered = render_metadata(state, always_write_parent_path=True)
    assert rendered == '{"job_id": "", "status": "SUCCESS"}'


def test_metadata_items_no_diagnosis_omits_job_id_and_status() -> None:
    state = _state(model=ModelInfo(name="NEWAVE", version=None))
    rendered = render_metadata(state, always_write_parent_path=True)
    assert rendered == '{"model_name": "NEWAVE"}'
    assert render_status(state) is None


def test_render_metadata_study_name_with_quote_and_accent_is_ascii_escaped() -> (
    None
):
    state = _state(
        study=StudyInfo(name='Revisão "X"', starting_date="2025-11-01")
    )
    rendered = render_metadata(state, always_write_parent_path=True)
    assert rendered is not None
    assert rendered.isascii()
    assert 'Revis\\u00e3o \\"X\\"' in rendered
    assert json.loads(rendered)["study_name"] == 'Revisão "X"'


def test_render_status_no_diagnosis_returns_none() -> None:
    assert render_status(_state()) is None


def test_render_status_diagnosis_set_returns_bare_token() -> None:
    state = _state(
        diagnosis=Diagnosis(
            status=RunStatus.INFEASIBLE, rule_id="newave.success", reason=""
        )
    )
    assert render_status(state) == "INFEASIBLE"


def test_write_projections_empty_state_writes_no_files(tmp_path: Path) -> None:
    ws = Workspace.at(tmp_path)
    write_projections(ws, _state(), always_write_parent_path=True)
    assert not ws.legacy_metadata_path.exists()
    assert not ws.legacy_status_path.exists()


def test_write_projections_stale_metadata_is_overwritten_without_stale_key(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    ws.legacy_metadata_path.write_text('{"stale_key": "x"}', encoding="ascii")

    state = _state(model=ModelInfo(name="NEWAVE", version=None))
    write_projections(ws, state, always_write_parent_path=True)

    written = ws.legacy_metadata_path.read_text(encoding="ascii")
    assert "stale_key" not in written
    assert written == '{"model_name": "NEWAVE"}'
    assert not ws.legacy_status_path.exists()


def test_write_projections_never_reads_existing_files(tmp_path: Path) -> None:
    ws = Workspace.at(tmp_path)
    ws.legacy_metadata_path.write_bytes(b"\xff\xfe not valid utf-8 or json")

    state = _state(model=ModelInfo(name="NEWAVE", version=None))
    write_projections(ws, state, always_write_parent_path=True)

    assert ws.legacy_metadata_path.read_bytes() == b'{"model_name": "NEWAVE"}'


def test_write_projections_writes_both_files_without_trailing_newline(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    state = _state(
        model=ModelInfo(name="NEWAVE", version=None),
        reported_job_id="777",
        diagnosis=Diagnosis(
            status=RunStatus.SUCCESS, rule_id="newave.success", reason=""
        ),
    )
    write_projections(ws, state, always_write_parent_path=True)

    metadata_bytes = ws.legacy_metadata_path.read_bytes()
    status_bytes = ws.legacy_status_path.read_bytes()
    assert not metadata_bytes.endswith(b"\n")
    assert not status_bytes.endswith(b"\n")
    assert status_bytes == b"SUCCESS"
