"""R93/ADR-048: byte equality of the legacy projections against v1.1.2."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

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

GOLDEN_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "goldens"
    / "v1_1_2"
    / "projections.json"
)

_INPUTS_RE = re.compile(r'check_and_fetch_inputs\("([^"]*)", "([^"]*)"')
_JOB_ID_RE = re.compile(r'generate_execution_status\("([^"]*)"\)')

REPRODUCED_SEQUENCES = (
    "decomp_no_parent",
    "decomp_with_parent",
    "newave_no_parent",
    "newave_offline",
    "newave_toolbox_bare",
    "newave_toolbox_no_job_id",
    "newave_with_parent",
)


def _load_golden() -> dict[str, object]:
    data = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert data["_meta"]["upload_newave_order"] == "executables-first"
    sequences = data["sequences"]
    assert isinstance(sequences, dict)
    return sequences


def state_for(sequence: dict[str, object], newave: bool) -> RunState:
    commands = sequence["commands"]
    assert isinstance(commands, list)
    metadata_text = sequence["metadata.modelops"]
    assert isinstance(metadata_text, str)
    status_text = sequence["status.modelops"]
    assert isinstance(status_text, str)
    meta = json.loads(metadata_text)
    joined = " ".join(commands)

    model = None
    if "check_and_fetch_executables" in joined:
        model = ModelInfo(
            name=meta["model_name"], version=meta.get("model_version")
        )

    inputs = None
    parent_arg = ""
    for command in commands:
        match = _INPUTS_RE.search(command)
        if match is not None:
            parent_arg = match.group(2)
            inputs = InputsInfo(source=match.group(1), parent_path=parent_arg)

    study = None
    if "extract_sanitize_inputs" in joined or "ingest_offline_run" in joined:
        study = StudyInfo(
            name=meta["study_name"], starting_date=meta["study_starting_date"]
        )

    parent = None
    if "extract_sanitize_inputs" in joined and parent_arg:
        parent = ParentInfo(
            path=parent_arg,
            model_name=meta["model_name"],
            starting_date=meta["parent_starting_date"],
        )

    execution_source = ExecutionSource.CLUSTER
    if "ingest_offline_run" in joined:
        execution_source = ExecutionSource.OFFLINE

    reported_job_id = None
    diagnosis = None
    for command in commands:
        match = _JOB_ID_RE.search(command)
        if match is not None:
            reported_job_id = match.group(1) or None
            diagnosis = Diagnosis(
                status=RunStatus(status_text),
                rule_id="newave.success" if newave else "decomp.success",
                reason="",
            )

    return RunState(
        run_id="r",
        plugin="newave" if newave else "decomp",
        tool=ToolInfo(name="hpc-model-utils", version="2.0.0"),
        model=model,
        inputs=inputs,
        parent=parent,
        study=study,
        execution_source=execution_source,
        reported_job_id=reported_job_id,
        diagnosis=diagnosis,
    )


@pytest.mark.parametrize("name", REPRODUCED_SEQUENCES)
def test_render_projections_reproduced_sequence_matches_v1_1_2_bytes(
    name: str,
) -> None:
    sequences = _load_golden()
    sequence = sequences[name]
    assert isinstance(sequence, dict)
    newave = name.startswith("newave")
    state = state_for(sequence, newave)

    rendered_metadata = render_metadata(state, always_write_parent_path=newave)
    rendered_status = render_status(state)

    assert rendered_metadata == sequence["metadata.modelops"]
    assert rendered_status == sequence["status.modelops"]


def test_write_projections_newave_toolbox_existing_metadata_discards_stale_keys(
    tmp_path: Path,
) -> None:
    """README row: `newave_toolbox_existing_metadata` intended difference.

    v1 merges the pre-existing metadata.modelops into the new one; v2 never
    reads a projection back (ADR-011), so it overwrites with only the items
    the current RunState records.
    """
    sequences = _load_golden()
    sequence = sequences["newave_toolbox_existing_metadata"]
    assert isinstance(sequence, dict)

    ws = Workspace.at(tmp_path)
    ws.legacy_metadata_path.write_text(
        '{"model_name": "NEWAVE", "study_name": "old"}', encoding="ascii"
    )

    state = RunState(
        run_id="r",
        plugin="newave",
        tool=ToolInfo(name="hpc-model-utils", version="2.0.0"),
        reported_job_id="778",
        diagnosis=Diagnosis(
            status=RunStatus.SUCCESS, rule_id="newave.success", reason=""
        ),
    )

    write_projections(ws, state, always_write_parent_path=True)

    assert (
        ws.legacy_metadata_path.read_bytes()
        == b'{"job_id": "778", "status": "SUCCESS"}'
    )
    assert ws.legacy_status_path.read_bytes() == sequence[
        "status.modelops"
    ].encode("ascii")
