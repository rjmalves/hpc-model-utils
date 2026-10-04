"""ADR-012/ADR-008: unit and golden coverage for the RunRecord projection."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from hpc_model_utils.core.diagnosis import (
    Diagnosis,
    EvidenceItem,
    JobReport,
    RunStatus,
    Verdict,
    evaluate,
)
from hpc_model_utils.core.run_record import (
    RUN_KIND,
    RUN_SCHEMA_VERSION,
    ArtifactEntry,
    ReuseRecord,
    build_run_record,
    read_previous_run_id,
    render_run_json,
)
from hpc_model_utils.core.state import (
    ExecutionSource,
    JobRecord,
    ModelInfo,
    ParentInfo,
    RunState,
    StepRecord,
    StudyInfo,
    ToolInfo,
)
from hpc_model_utils.core.workspace import Phase

_REGEN_ENV = "HPCMU_REGEN_GOLDENS"
_GOLDEN_PATH = (
    Path(__file__).resolve().parents[2]
    / "goldens"
    / "v2"
    / "run_json_success.json"
)
_LOG_KEY_PATTERN = re.compile(r"^saidas/logs/(model|finalize)-\d+\.out$")


def _tool() -> ToolInfo:
    return ToolInfo(name="hpc-model-utils", version="2.0.0")


def _full_run_state() -> RunState:
    model = ModelInfo(name="NEWAVE", version="29")
    parent = ParentInfo(
        path="s3://hpcmu-parents/case-00",
        model_name="NEWAVE",
        starting_date="2024-01-01",
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
        tool=_tool(),
        model=model,
        parent=parent,
        study=study,
        execution_source=ExecutionSource.OFFLINE,
        jobs=jobs,
        reported_job_id="222",
        diagnosis=diagnosis,
        steps=steps,
    )


def _string_leaves(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [
            leaf for item in value.values() for leaf in _string_leaves(item)
        ]
    if isinstance(value, list):
        return [leaf for item in value for leaf in _string_leaves(item)]
    return []


# -- AC1/AC2: the regenerable golden -----------------------------------------


def test_build_run_record_success_state_matches_golden(tmp_path: Path) -> None:
    state = _full_run_state()
    artifacts = (
        ArtifactEntry(path="pmo.dat", bytes=4096),
        ArtifactEntry(path="cortes.zip", bytes=65536),
    )
    record = build_run_record(
        state,
        artifacts=artifacts,
        reuse=None,
        published_at="2026-01-01T00:00:00+00:00",
    )
    rendered = render_run_json(record)

    if os.environ.get(_REGEN_ENV) == "1":
        _GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        _GOLDEN_PATH.write_text(rendered, encoding="utf-8")
        pytest.fail(
            f"{_REGEN_ENV}=1: regenerated {_GOLDEN_PATH}; "
            "re-run without it to verify"
        )

    expected = _GOLDEN_PATH.read_text(encoding="utf-8")
    assert rendered == expected


# -- AC4: recursive string-leaf scan -----------------------------------------


def test_build_run_record_success_state_has_no_absolute_path_leaves() -> None:
    state = _full_run_state()
    artifacts = (
        ArtifactEntry(path="pmo.dat", bytes=4096),
        ArtifactEntry(path="cortes.zip", bytes=65536),
    )
    record = build_run_record(
        state,
        artifacts=artifacts,
        reuse=None,
        published_at="2026-01-01T00:00:00+00:00",
    )
    parsed = json.loads(render_run_json(record))
    for leaf in _string_leaves(parsed):
        assert not leaf.startswith("/")
    for job in parsed["jobs"]:
        assert _LOG_KEY_PATTERN.fullmatch(job["log"])


def test_build_run_record_fixed_l2_evidence_has_no_absolute_path_leaves(
    tmp_path: Path,
) -> None:
    missing_log = tmp_path / "absent.log"
    report = JobReport(outcome=None, process_exit=0, log_paths=(missing_log,))
    diagnosis = evaluate(
        report,
        log_patterns=(),
        primary_evidence=(),
        rules=lambda: Verdict(RunStatus.SUCCESS, "newave.ok", "converged"),
        job_id=None,
    )
    state = RunState(
        run_id="r2", plugin="newave", tool=_tool(), diagnosis=diagnosis
    )
    record = build_run_record(
        state,
        artifacts=(),
        reuse=None,
        published_at="2026-01-01T00:00:00+00:00",
    )
    for leaf in _string_leaves(record):
        assert not leaf.startswith("/")
        assert str(tmp_path) not in leaf


# -- AC5: vocabulary and tool-key checks --------------------------------------


def test_build_run_record_success_state_diagnosis_status_is_run_status_token() -> (  # noqa: E501
    None
):
    state = _full_run_state()
    record = build_run_record(
        state,
        artifacts=(),
        reuse=None,
        published_at="2026-01-01T00:00:00+00:00",
    )
    rendered = render_run_json(record)
    parsed = json.loads(rendered)
    assert parsed["diagnosis"]["status"] in {
        member.value for member in RunStatus
    }
    assert sorted(parsed["tool"]) == ["name", "version"]


# -- null study and null parent ----------------------------------------------


def test_build_run_record_minimal_state_returns_null_study_and_parent() -> None:
    state = RunState(run_id="r1", plugin="newave", tool=_tool())
    record = build_run_record(
        state,
        artifacts=(),
        reuse=None,
        published_at="2026-01-01T00:00:00+00:00",
    )
    assert record["study"] is None
    assert record["parent"] is None
    assert record["model"] == {
        "plugin": "newave",
        "name": None,
        "version": None,
    }
    assert record["diagnosis"] is None
    assert record["reuse"] is None
    assert record["jobs"] == []
    assert record["artifacts"] == []


def test_build_run_record_reuse_detected_returns_reuse_object() -> None:
    state = RunState(run_id="r1", plugin="newave", tool=_tool())
    record = build_run_record(
        state,
        artifacts=(),
        reuse=ReuseRecord(detected=True, previous_run_id="old-run"),
        published_at="2026-01-01T00:00:00+00:00",
    )
    assert record["reuse"] == {
        "detected": True,
        "previous_run_id": "old-run",
    }


# -- the OFFLINE source -------------------------------------------------------


def test_build_run_record_offline_execution_source_maps_to_offline_token() -> (
    None
):
    state = RunState(
        run_id="r1",
        plugin="newave",
        tool=_tool(),
        execution_source=ExecutionSource.OFFLINE,
    )
    record = build_run_record(
        state,
        artifacts=(),
        reuse=None,
        published_at="2026-01-01T00:00:00+00:00",
    )
    assert record["execution_source"] == "OFFLINE"


# -- the log key format --------------------------------------------------


def test_build_run_record_jobs_log_uses_s3_key_not_state_log_path() -> None:
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
    state = RunState(run_id="r1", plugin="newave", tool=_tool(), jobs=jobs)
    record = build_run_record(
        state,
        artifacts=(),
        reuse=None,
        published_at="2026-01-01T00:00:00+00:00",
    )
    rendered_jobs = record["jobs"]
    assert rendered_jobs == [
        {
            "phase": "model",
            "job_id": "111",
            "submitted_at": "2024-01-01T00:00:00+00:00",
            "log": "saidas/logs/model-111.out",
        },
        {
            "phase": "finalize",
            "job_id": "222",
            "submitted_at": "2024-01-01T01:00:00+00:00",
            "log": "saidas/logs/finalize-222.out",
        },
    ]


# -- manifest sorting ----------------------------------------------------


def test_build_run_record_artifacts_sorted_by_path_regardless_of_input_order() -> (  # noqa: E501
    None
):
    state = RunState(run_id="r1", plugin="newave", tool=_tool())
    artifacts = (
        ArtifactEntry(path="z.txt", bytes=2),
        ArtifactEntry(path="a.txt", bytes=1),
    )
    record = build_run_record(
        state,
        artifacts=artifacts,
        reuse=None,
        published_at="2026-01-01T00:00:00+00:00",
    )
    assert record["artifacts"] == [
        {"path": "a.txt", "bytes": 1},
        {"path": "z.txt", "bytes": 2},
    ]


# -- the absolute-path guard (Requirement 6 / amendment) ---------------------


def test_build_run_record_parent_path_starting_with_slash_raises_value_error() -> (  # noqa: E501
    None
):
    state = RunState(
        run_id="r1",
        plugin="newave",
        tool=_tool(),
        parent=ParentInfo(
            path="/abs/parent",
            model_name="NEWAVE",
            starting_date="2024-01-01",
        ),
    )
    with pytest.raises(ValueError, match="parent"):
        build_run_record(
            state,
            artifacts=(),
            reuse=None,
            published_at="2026-01-01T00:00:00+00:00",
        )


def test_build_run_record_model_name_starting_with_slash_raises_value_error() -> (  # noqa: E501
    None
):
    state = RunState(
        run_id="r1",
        plugin="newave",
        tool=_tool(),
        model=ModelInfo(name="/NEWAVE", version=None),
    )
    with pytest.raises(ValueError, match="model"):
        build_run_record(
            state,
            artifacts=(),
            reuse=None,
            published_at="2026-01-01T00:00:00+00:00",
        )


def test_build_run_record_diagnosis_evidence_starting_with_slash_does_not_raise() -> (  # noqa: E501
    None
):
    diagnosis = Diagnosis(
        status=RunStatus.RUNTIME_ERROR,
        rule_id="core.missing_output",
        reason="missing output file(s): pmo.dat",
        evidence=(
            EvidenceItem(
                layer="guard",
                source="/abs/workspace/pmo.dat",
                detail="missing: pmo.dat",
            ),
        ),
    )
    state = RunState(
        run_id="r1", plugin="newave", tool=_tool(), diagnosis=diagnosis
    )
    record = build_run_record(
        state,
        artifacts=(),
        reuse=None,
        published_at="2026-01-01T00:00:00+00:00",
    )
    rendered_diagnosis = record["diagnosis"]
    assert isinstance(rendered_diagnosis, dict)
    assert (
        rendered_diagnosis["evidence"][0]["source"] == "/abs/workspace/pmo.dat"
    )


# -- render_run_json exact format --------------------------------------------


def test_render_run_json_uses_indent_two_and_trailing_newline() -> None:
    rendered = render_run_json({"a": 1})
    assert rendered == '{\n  "a": 1\n}\n'


# -- the lenient-reader table (AC3) ------------------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"not json", None),
        (
            json.dumps(
                {
                    "kind": RUN_KIND,
                    "schema_version": 7,
                    "run_id": "abc",
                    "future": 1,
                }
            ).encode("utf-8"),
            "abc",
        ),
        (
            json.dumps({"kind": "other", "run_id": "x"}).encode("utf-8"),
            None,
        ),
        (b"\xff\xfe\x00", None),
        (json.dumps([1, 2, 3]).encode("utf-8"), None),
        (
            json.dumps(
                {
                    "kind": RUN_KIND,
                    "schema_version": True,
                    "run_id": "abc",
                }
            ).encode("utf-8"),
            None,
        ),
        (
            json.dumps(
                {"kind": RUN_KIND, "schema_version": 0, "run_id": "abc"}
            ).encode("utf-8"),
            None,
        ),
        (
            json.dumps(
                {"kind": RUN_KIND, "schema_version": 1, "run_id": 123}
            ).encode("utf-8"),
            None,
        ),
        (
            json.dumps(
                {
                    "kind": RUN_KIND,
                    "schema_version": RUN_SCHEMA_VERSION,
                    "run_id": "abc",
                }
            ).encode("utf-8"),
            "abc",
        ),
    ],
)
def test_read_previous_run_id_lenient_table_returns_expected(
    data: bytes, expected: str | None
) -> None:
    assert read_previous_run_id(data) == expected
