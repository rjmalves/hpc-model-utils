from __future__ import annotations

import re

import pytest

from hpc_model_utils.core.diagnosis import (
    RULE_ID_PATTERN,
    Diagnosis,
    EvidenceItem,
    RunStatus,
    utc_now_iso,
)

_TOKEN_PATTERN = re.compile(r"^[A-Z]+(_[A-Z]+)*$")


def test_run_status_members_count_is_nine() -> None:
    assert len(list(RunStatus)) == 9


def test_run_status_members_value_equals_name() -> None:
    for member in RunStatus:
        assert member.value == member.name


def test_run_status_non_success_tokens_exclude_success() -> None:
    for member in RunStatus:
        if member is RunStatus.SUCCESS:
            continue
        assert "SUCCESS" not in member.value
        assert _TOKEN_PATTERN.match(member.value)


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("SUCCESS", RunStatus.SUCCESS),
        ("SUCCESS\n", RunStatus.SUCCESS),
        ("  TIMEOUT  ", RunStatus.TIMEOUT),
        ("bogus", RunStatus.UNKNOWN),
        ("", RunStatus.UNKNOWN),
        ("success", RunStatus.UNKNOWN),
    ],
)
def test_run_status_parse_various_inputs_returns_expected(
    token: str, expected: RunStatus
) -> None:
    assert RunStatus.parse(token) is expected


def _diagnosis(
    *,
    status: RunStatus = RunStatus.DATA_ERROR,
    rule_id: str = "slurm.oom",
    reason: str = "killed by oom",
    evidence: tuple[EvidenceItem, ...] = (),
    matched: tuple[str, ...] = (),
    job_id: str | None = None,
    at: str = "",
) -> Diagnosis:
    return Diagnosis(
        status=status,
        rule_id=rule_id,
        reason=reason,
        evidence=evidence,
        matched=matched,
        job_id=job_id,
        at=at,
    )


@pytest.mark.parametrize(
    ("rule_id", "should_raise"),
    [
        ("slurm.oom", False),
        ("core.timeout", False),
        ("newave.nonconvergence", False),
        ("decomp.guard_x", False),
        ("cobre.plugin_fail", False),
        ("bad-id", True),
        ("SLURM.oom", True),
        ("slurm.", True),
        ("slurm", True),
        ("unknown.rule", True),
        ("core.crash\n", True),
    ],
)
def test_diagnosis_post_init_rule_id_validity_matches_expectation(
    rule_id: str, should_raise: bool
) -> None:
    if should_raise:
        with pytest.raises(ValueError, match="rule_id"):
            _diagnosis(rule_id=rule_id)
    else:
        assert _diagnosis(rule_id=rule_id).rule_id == rule_id


def test_diagnosis_post_init_bad_rule_id_raises_value_error() -> None:
    with pytest.raises(ValueError, match="rule_id"):
        Diagnosis(status=RunStatus.UNKNOWN, rule_id="bad-id", reason="x")


@pytest.mark.parametrize(
    ("matched", "should_raise"),
    [
        (("slurm.oom",), False),
        (("slurm.oom", "core.timeout"), False),
        (("bad-id",), True),
        (("slurm.oom", "bad-id"), True),
        (("slurm.oom\n",), True),
    ],
)
def test_diagnosis_post_init_matched_entries_validity_matches_expectation(
    matched: tuple[str, ...], should_raise: bool
) -> None:
    if should_raise:
        with pytest.raises(ValueError, match="matched entry"):
            _diagnosis(matched=matched)
    else:
        assert _diagnosis(matched=matched).matched == matched


def test_diagnosis_post_init_25_evidence_items_caps_at_20() -> None:
    evidence = tuple(
        EvidenceItem(layer="slurm", source=f"s{i}", detail="d")
        for i in range(25)
    )
    diagnosis = _diagnosis(evidence=evidence)
    assert len(diagnosis.evidence) == 20


def test_evidence_item_post_init_600_char_detail_truncates_to_500() -> None:
    item = EvidenceItem(layer="log", source="x", detail="a" * 600)
    assert len(item.detail) == 500


def test_diagnosis_annotation_600_char_reason_truncates_to_500() -> None:
    diagnosis = _diagnosis(reason="a" * 600)
    annotation = diagnosis.annotation()
    assert len(annotation) == 500


def test_diagnosis_annotation_formats_status_reason_rule_id() -> None:
    diagnosis = _diagnosis(
        status=RunStatus.RUNTIME_ERROR, rule_id="core.crash", reason="boom"
    )
    assert diagnosis.annotation() == "RUNTIME_ERROR: boom [core.crash]"


def test_diagnosis_to_dict_holds_only_json_safe_types() -> None:
    diagnosis = _diagnosis(
        evidence=(EvidenceItem(layer="guard", source="g1", detail="d1"),),
        matched=("slurm.oom",),
        job_id="123",
        at=utc_now_iso(),
    )
    data = diagnosis.to_dict()
    assert isinstance(data["status"], str)
    assert isinstance(data["rule_id"], str)
    assert isinstance(data["reason"], str)
    assert isinstance(data["evidence"], list)
    assert isinstance(data["evidence"][0], dict)
    assert isinstance(data["matched"], list)
    assert isinstance(data["job_id"], str)
    assert isinstance(data["at"], str)


def test_diagnosis_to_dict_from_dict_round_trips_exactly() -> None:
    original = _diagnosis(
        status=RunStatus.INFEASIBLE,
        rule_id="newave.infeasible",
        reason="no feasible solution",
        evidence=(EvidenceItem(layer="plugin", source="p1", detail="d1"),),
        matched=("newave.infeasible",),
        job_id="456",
        at="2026-01-01T00:00:00+00:00",
    )
    restored = Diagnosis.from_dict(original.to_dict())
    assert restored == original


def _valid_from_dict_payload() -> dict[str, object]:
    return {
        "status": "SUCCESS",
        "rule_id": "slurm.oom",
        "reason": "ok",
        "evidence": [],
        "matched": [],
        "job_id": None,
        "at": "",
    }


def test_diagnosis_from_dict_unknown_persisted_token_decodes_to_unknown() -> (
    None
):
    data = _valid_from_dict_payload()
    data["status"] = "SOME_FUTURE_TOKEN"
    data["reason"] = "killed"

    restored = Diagnosis.from_dict(data)
    assert restored.status is RunStatus.UNKNOWN


@pytest.mark.parametrize(
    "missing_key",
    ["status", "rule_id", "reason", "evidence", "matched", "job_id", "at"],
)
def test_diagnosis_from_dict_missing_field_raises_key_error(
    missing_key: str,
) -> None:
    data = _valid_from_dict_payload()
    del data[missing_key]
    with pytest.raises(KeyError, match=re.escape(missing_key)):
        Diagnosis.from_dict(data)


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("status", 1),
        ("rule_id", None),
        ("reason", 1.5),
        ("evidence", {}),
        ("matched", "slurm.oom"),
        ("job_id", 1),
        ("at", 1),
    ],
)
def test_diagnosis_from_dict_ill_typed_field_raises_type_error(
    field_name: str, bad_value: object
) -> None:
    data = _valid_from_dict_payload()
    data[field_name] = bad_value
    with pytest.raises(TypeError, match=re.escape(field_name)):
        Diagnosis.from_dict(data)


def test_diagnosis_from_dict_invalid_evidence_layer_raises_value_error() -> (
    None
):
    data = _valid_from_dict_payload()
    data["evidence"] = [{"layer": "unknown", "source": "s", "detail": "d"}]

    with pytest.raises(ValueError, match="layer"):
        Diagnosis.from_dict(data)


def test_rule_id_pattern_matches_all_namespaces() -> None:
    for namespace in ("slurm", "core", "newave", "decomp", "cobre"):
        assert RULE_ID_PATTERN.match(f"{namespace}.ok")


def test_utc_now_iso_returns_seconds_precision_utc_offset() -> None:
    value = utc_now_iso()
    assert value.endswith("+00:00")
    assert "." not in value
