from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from hpc_model_utils.core.diagnosis import (
    SLURM_RULES,
    EvidenceItem,
    JobReport,
    LogPattern,
    RunStatus,
    Verdict,
    evaluate,
)
from hpc_model_utils.infra.slurm import JobOutcome


def _outcome(
    *,
    job_id: str = "123",
    state: str = "COMPLETED",
    exit_code: int | None = 0,
    signal: int | None = None,
    elapsed: str | None = "00:10:00",
    time_limit: str | None = "01:00:00",
    oom: bool = False,
    source: Literal["sacct", "scontrol", "none"] = "sacct",
    raw: str = "",
) -> JobOutcome:
    return JobOutcome(
        job_id=job_id,
        state=state,
        exit_code=exit_code,
        signal=signal,
        elapsed=elapsed,
        time_limit=time_limit,
        oom=oom,
        source=source,
        raw=raw,
    )


def _l3_forbidden() -> Verdict:
    raise AssertionError("L3 must not run")


def _l3_success() -> Verdict:
    return Verdict(RunStatus.SUCCESS, "newave.ok", "converged")


_oom_status, _oom_rule_id, _ = SLURM_RULES["OUT_OF_MEMORY"]
_SLURM_RULE_CASES = [
    (state, False, status, rule_id)
    for state, (status, rule_id, _template) in SLURM_RULES.items()
] + [("FAILED", True, _oom_status, _oom_rule_id)]


@pytest.mark.parametrize(
    ("state", "oom", "expected_status", "expected_rule_id"),
    _SLURM_RULE_CASES,
)
def test_evaluate_l1_every_slurm_rule_row_maps_to_expected_status(
    state: str, oom: bool, expected_status: RunStatus, expected_rule_id: str
) -> None:
    report = JobReport(
        outcome=_outcome(state=state, oom=oom),
        process_exit=None,
        log_paths=(),
    )
    diagnosis = evaluate(
        report,
        log_patterns=(),
        primary_evidence=(),
        rules=_l3_forbidden,
        job_id="123",
    )
    assert diagnosis.status is expected_status
    assert diagnosis.rule_id == expected_rule_id


@pytest.mark.parametrize("state", ["COMPLETED", "FAILED"])
def test_evaluate_l1_completed_and_failed_states_fall_through_to_l3(
    state: str,
) -> None:
    report = JobReport(
        outcome=_outcome(state=state), process_exit=None, log_paths=()
    )
    diagnosis = evaluate(
        report,
        log_patterns=(),
        primary_evidence=(),
        rules=_l3_success,
        job_id="123",
    )
    assert diagnosis.status is RunStatus.SUCCESS
    assert diagnosis.rule_id == "newave.ok"


def test_evaluate_l1_source_none_falls_through_reports_success(
    tmp_path: Path,
) -> None:
    evidence_file = tmp_path / "pmo.dat"
    evidence_file.write_text("ok", encoding="utf-8")
    report = JobReport(
        outcome=_outcome(state="UNKNOWN", source="none"),
        process_exit=None,
        log_paths=(),
    )
    diagnosis = evaluate(
        report,
        log_patterns=(),
        primary_evidence=(evidence_file,),
        rules=_l3_success,
        job_id="123",
    )
    assert diagnosis.status is RunStatus.SUCCESS
    assert any(item.source == "accounting" for item in diagnosis.evidence)


def test_evaluate_l1_timeout_precedes_l2_license_match_wins_timeout(
    tmp_path: Path,
) -> None:
    log = tmp_path / "newave.log"
    log.write_text("Falha de licenciamento!\n", encoding="utf-8")
    license_pattern = LogPattern(
        rule_id="newave.license_error",
        pattern="Falha de licenciamento!",
        status=RunStatus.LICENSE_ERROR,
        reason="licence check failed",
    )
    report = JobReport(
        outcome=_outcome(state="TIMEOUT"),
        process_exit=None,
        log_paths=(log,),
    )
    diagnosis = evaluate(
        report,
        log_patterns=(license_pattern,),
        primary_evidence=(),
        rules=_l3_forbidden,
        job_id="123",
    )
    assert diagnosis.status is RunStatus.TIMEOUT
    assert diagnosis.rule_id == "slurm.timeout"
    assert "newave.license_error" in diagnosis.matched


def test_evaluate_l1_timeout_still_reports_guard_missing_output_evidence(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "pmo.dat"
    report = JobReport(
        outcome=_outcome(state="TIMEOUT"), process_exit=None, log_paths=()
    )
    diagnosis = evaluate(
        report,
        log_patterns=(),
        primary_evidence=(missing,),
        rules=_l3_forbidden,
        job_id="123",
    )
    assert diagnosis.status is RunStatus.TIMEOUT
    assert any(item.layer == "guard" for item in diagnosis.evidence)


def test_evaluate_l2_license_match_precedes_guard_wins_license_error(
    tmp_path: Path,
) -> None:
    log = tmp_path / "newave.log"
    log.write_text("Falha de licenciamento!\n", encoding="utf-8")
    missing = tmp_path / "pmo.dat"
    license_pattern = LogPattern(
        rule_id="newave.license_error",
        pattern="Falha de licenciamento!",
        status=RunStatus.LICENSE_ERROR,
        reason="licence check failed",
    )
    report = JobReport(
        outcome=_outcome(state="COMPLETED"),
        process_exit=None,
        log_paths=(log,),
    )
    diagnosis = evaluate(
        report,
        log_patterns=(license_pattern,),
        primary_evidence=(missing,),
        rules=_l3_forbidden,
        job_id="123",
    )
    assert diagnosis.status is RunStatus.LICENSE_ERROR
    assert "newave.license_error" in diagnosis.matched


def test_evaluate_guard_missing_output_precedes_l3_wins_runtime_error(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "pmo.dat"
    calls: list[None] = []

    def _success_tracked() -> Verdict:
        calls.append(None)
        return Verdict(RunStatus.SUCCESS, "newave.ok", "converged")

    report = JobReport(
        outcome=_outcome(state="COMPLETED"), process_exit=None, log_paths=()
    )
    diagnosis = evaluate(
        report,
        log_patterns=(),
        primary_evidence=(missing,),
        rules=_success_tracked,
        job_id="123",
    )
    assert diagnosis.status is RunStatus.RUNTIME_ERROR
    assert diagnosis.rule_id == "core.missing_output"
    assert "pmo.dat" in diagnosis.reason
    assert calls == []


def test_evaluate_outcome_none_finalize_only_starts_at_l2(
    tmp_path: Path,
) -> None:
    log = tmp_path / "finalize.log"
    log.write_text("Falha de licenciamento!\n", encoding="utf-8")
    license_pattern = LogPattern(
        rule_id="newave.license_error",
        pattern="Falha de licenciamento!",
        status=RunStatus.LICENSE_ERROR,
        reason="licence check failed",
    )
    report = JobReport(outcome=None, process_exit=0, log_paths=(log,))
    diagnosis = evaluate(
        report,
        log_patterns=(license_pattern,),
        primary_evidence=(),
        rules=_l3_forbidden,
        job_id=None,
    )
    assert diagnosis.status is RunStatus.LICENSE_ERROR
    assert diagnosis.job_id is None


def test_evaluate_l3_exception_becomes_unknown_with_exception_detail(
    tmp_path: Path,
) -> None:
    evidence_file = tmp_path / "pmo.dat"
    evidence_file.write_text("ok", encoding="utf-8")

    def _raise() -> Verdict:
        raise ValueError("bad table")

    report = JobReport(
        outcome=_outcome(state="COMPLETED"), process_exit=None, log_paths=()
    )
    diagnosis = evaluate(
        report,
        log_patterns=(),
        primary_evidence=(evidence_file,),
        rules=_raise,
        job_id="123",
    )
    assert diagnosis.status is RunStatus.UNKNOWN
    assert diagnosis.rule_id == "core.diagnosis_exception"
    assert any("bad table" in item.detail for item in diagnosis.evidence)


def test_evaluate_l2_unreadable_log_skipped_adds_evidence(
    tmp_path: Path,
) -> None:
    missing_log = tmp_path / "absent.log"
    report = JobReport(
        outcome=_outcome(state="COMPLETED"),
        process_exit=None,
        log_paths=(missing_log,),
    )
    diagnosis = evaluate(
        report,
        log_patterns=(),
        primary_evidence=(),
        rules=_l3_success,
        job_id="123",
    )
    assert diagnosis.status is RunStatus.SUCCESS
    log_evidence = [item for item in diagnosis.evidence if item.layer == "log"]
    assert len(log_evidence) == 1
    assert isinstance(log_evidence[0], EvidenceItem)
    assert log_evidence[0].source == missing_log.name
    assert str(tmp_path) not in log_evidence[0].source
    assert str(tmp_path) not in log_evidence[0].detail


def test_evaluate_evidence_cap_passthrough_caps_at_twenty(
    tmp_path: Path,
) -> None:
    missing_logs = tuple(tmp_path / f"absent{i}.log" for i in range(25))
    report = JobReport(
        outcome=_outcome(state="COMPLETED"),
        process_exit=None,
        log_paths=missing_logs,
    )
    diagnosis = evaluate(
        report,
        log_patterns=(),
        primary_evidence=(),
        rules=_l3_success,
        job_id="123",
    )
    assert len(diagnosis.evidence) == 20


def test_log_pattern_init_invalid_rule_id_raises_value_error() -> None:
    with pytest.raises(ValueError, match="rule_id"):
        LogPattern(
            rule_id="bad-id",
            pattern="x",
            status=RunStatus.UNKNOWN,
            reason="y",
        )


def test_log_pattern_search_semantics_matches_substring_not_fullmatch(
    tmp_path: Path,
) -> None:
    log = tmp_path / "x.log"
    log.write_text("prefix Falha de licenciamento! suffix\n", encoding="utf-8")
    pattern = LogPattern(
        rule_id="newave.license_error",
        pattern="Falha de licenciamento!",
        status=RunStatus.LICENSE_ERROR,
        reason="licence check failed",
    )
    report = JobReport(
        outcome=_outcome(state="COMPLETED"),
        process_exit=None,
        log_paths=(log,),
    )
    diagnosis = evaluate(
        report,
        log_patterns=(pattern,),
        primary_evidence=(),
        rules=_l3_success,
        job_id="123",
    )
    assert diagnosis.status is RunStatus.LICENSE_ERROR


def test_evaluate_l1_timeout_reason_renders_none_time_limit_as_question_mark() -> (  # noqa: E501
    None
):
    report = JobReport(
        outcome=_outcome(state="TIMEOUT", time_limit=None),
        process_exit=None,
        log_paths=(),
    )
    diagnosis = evaluate(
        report,
        log_patterns=(),
        primary_evidence=(),
        rules=_l3_forbidden,
        job_id="123",
    )
    assert "?" in diagnosis.reason


def test_evaluate_l2_matched_records_every_pattern_in_first_match_order(
    tmp_path: Path,
) -> None:
    log = tmp_path / "x.log"
    log.write_text("first BBB marker\nsecond AAA marker\n", encoding="utf-8")
    pattern_a = LogPattern(
        rule_id="newave.pattern_a",
        pattern="AAA",
        status=RunStatus.DATA_ERROR,
        reason="a",
    )
    pattern_b = LogPattern(
        rule_id="newave.pattern_b",
        pattern="BBB",
        status=RunStatus.DATA_ERROR,
        reason="b",
    )
    report = JobReport(
        outcome=_outcome(state="COMPLETED"),
        process_exit=None,
        log_paths=(log,),
    )
    diagnosis = evaluate(
        report,
        log_patterns=(pattern_a, pattern_b),
        primary_evidence=(),
        rules=_l3_forbidden,
        job_id="123",
    )
    assert diagnosis.matched == ("newave.pattern_b", "newave.pattern_a")
    assert diagnosis.rule_id == "newave.pattern_b"


def test_evaluate_l2_bounded_readline_handles_oversized_line_without_loading_fully(  # noqa: E501
    tmp_path: Path,
) -> None:
    log = tmp_path / "huge.log"
    huge_line = "Falha de licenciamento!" + ("x" * (2 * (1 << 20)))
    log.write_text(huge_line, encoding="utf-8")
    pattern = LogPattern(
        rule_id="newave.license_error",
        pattern="Falha de licenciamento!",
        status=RunStatus.LICENSE_ERROR,
        reason="licence check failed",
    )
    report = JobReport(
        outcome=_outcome(state="COMPLETED"),
        process_exit=None,
        log_paths=(log,),
    )
    diagnosis = evaluate(
        report,
        log_patterns=(pattern,),
        primary_evidence=(),
        rules=_l3_forbidden,
        job_id="123",
    )
    assert diagnosis.status is RunStatus.LICENSE_ERROR


def test_verdict_defaults_evidence_and_matched_to_empty_tuple() -> None:
    verdict = Verdict(RunStatus.SUCCESS, "newave.ok", "converged")
    assert verdict.evidence == ()
    assert verdict.matched == ()


def test_job_report_outcome_none_is_allowed() -> None:
    report = JobReport(outcome=None, process_exit=None, log_paths=())
    assert report.outcome is None


def test_evaluate_determinism_at_field_is_utc_offset_timestamp() -> None:
    report = JobReport(
        outcome=_outcome(state="COMPLETED"), process_exit=None, log_paths=()
    )
    diagnosis = evaluate(
        report,
        log_patterns=(),
        primary_evidence=(),
        rules=_l3_success,
        job_id="123",
    )
    assert diagnosis.at.endswith("+00:00")
