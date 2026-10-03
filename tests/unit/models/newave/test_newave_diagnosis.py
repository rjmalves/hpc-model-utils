"""ADR-014/ADR-015/R48/R49/R103/R109/R124 tests for the NEWAVE
diagnosis rule table (ticket-047)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

from hpc_model_utils.core.diagnosis import (
    RULE_ID_PATTERN,
    JobReport,
    RunStatus,
    evaluate,
)
from hpc_model_utils.core.errors import DataError
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.models.newave import NewavePlugin, diagnosis
from hpc_model_utils.models.newave.diagnosis import (
    RULES,
    NewaveEvidence,
    Rule,
    _render,
)
from tests.support import decks, newave_outputs

plugin = NewavePlugin()


@dataclass(frozen=True)
class _TableRow:
    tipo_execucao: int
    tipo_simulacao_final: int
    pmo_kind: Literal["complete", "no_simulated_cost", "no_convergence"]
    status: RunStatus
    rule_id: str


_TABLE_ROWS: tuple[_TableRow, ...] = (
    _TableRow(1, 1, "complete", RunStatus.SUCCESS, "newave.completed"),
    _TableRow(
        1,
        1,
        "no_simulated_cost",
        RunStatus.RUNTIME_ERROR,
        "newave.final_simulation_incomplete",
    ),
    _TableRow(1, 0, "no_simulated_cost", RunStatus.SUCCESS, "newave.completed"),
    _TableRow(1, 2, "complete", RunStatus.SUCCESS, "newave.completed"),
    _TableRow(
        1,
        1,
        "no_convergence",
        RunStatus.DATA_ERROR,
        "newave.missing_convergence",
    ),
    _TableRow(
        0,
        1,
        "no_simulated_cost",
        RunStatus.DATA_ERROR,
        "newave.simulation_incomplete",
    ),
    _TableRow(0, 1, "complete", RunStatus.SUCCESS, "newave.completed"),
    _TableRow(
        1, 3, "no_convergence", RunStatus.SUCCESS, "newave.consistency_run"
    ),
    _TableRow(
        7, 1, "complete", RunStatus.UNKNOWN, "newave.unknown_execution_type"
    ),
)

_TABLE_ROW_IDS = [
    "te1-tsf1-complete",
    "te1-tsf1-no_simulated_cost",
    "te1-tsf0-no_simulated_cost",
    "te1-tsf2-complete",
    "te1-tsf1-no_convergence",
    "te0-tsf1-no_simulated_cost",
    "te0-tsf1-complete",
    "te1-tsf3-no_convergence",
    "te7-tsf1-complete",
]


def _build_row_workspace(tmp_path: Path, row: _TableRow) -> Workspace:
    dw = decks.newave_workspace(tmp_path)
    newave_outputs.write_dger(
        dw.ws,
        tipo_execucao=row.tipo_execucao,
        tipo_simulacao_final=row.tipo_simulacao_final,
    )
    (dw.ws.root / "pmo.dat").write_bytes(newave_outputs.pmo_bytes(row.pmo_kind))
    return dw.ws


@pytest.mark.parametrize("row", _TABLE_ROWS, ids=_TABLE_ROW_IDS)
def test_diagnose_r124_table_row_returns_expected_status_and_rule_id(
    tmp_path: Path, row: _TableRow
) -> None:
    ws = _build_row_workspace(tmp_path, row)
    verdict = diagnosis.diagnose(ws, JobReport(None, 0, ()))
    assert verdict.status is row.status
    assert verdict.rule_id == row.rule_id
    assert str(ws.root) not in verdict.reason
    for item in verdict.evidence:
        assert str(ws.root) not in item.source
        assert str(ws.root) not in item.detail


def test_diagnose_full_run_complete_reason_contains_iteration_fifty(
    tmp_path: Path,
) -> None:
    ws = _build_row_workspace(tmp_path, _TABLE_ROWS[0])
    verdict = diagnosis.diagnose(ws, JobReport(None, 0, ()))
    assert "iteration 50" in verdict.reason


def test_diagnose_consistency_run_matched_also_includes_missing_convergence(
    tmp_path: Path,
) -> None:
    ws = _build_row_workspace(tmp_path, _TABLE_ROWS[7])
    verdict = diagnosis.diagnose(ws, JobReport(None, 0, ()))
    assert verdict.matched == (
        "newave.consistency_run",
        "newave.missing_convergence",
    )


def test_evaluate_license_log_missing_pmo_returns_license_error(
    tmp_path: Path,
) -> None:
    dw = decks.newave_workspace(tmp_path)
    log = tmp_path / "newave.log"
    log.write_text(newave_outputs.LICENCE_FAILURE_LOG, encoding="utf-8")
    report = JobReport(outcome=None, process_exit=1, log_paths=(log,))
    result = evaluate(
        report,
        log_patterns=plugin.log_patterns,
        primary_evidence=plugin.primary_evidence(dw.ws),
        rules=lambda: plugin.diagnose(dw.ws, report),
        job_id="1",
    )
    assert result.status is RunStatus.LICENSE_ERROR
    assert result.rule_id == "newave.license_failure"


def test_evaluate_consistency_run_missing_pmo_guard_wins_runtime_error(
    tmp_path: Path,
) -> None:
    dw = decks.newave_workspace(tmp_path)
    newave_outputs.write_dger(dw.ws, tipo_execucao=1, tipo_simulacao_final=3)
    report = JobReport(outcome=None, process_exit=0, log_paths=())
    result = evaluate(
        report,
        log_patterns=plugin.log_patterns,
        primary_evidence=plugin.primary_evidence(dw.ws),
        rules=lambda: plugin.diagnose(dw.ws, report),
        job_id="1",
    )
    assert result.status is RunStatus.RUNTIME_ERROR
    assert result.rule_id == "core.missing_output"


def test_evaluate_missing_dger_present_pmo_returns_unknown_diagnosis_exception(
    tmp_path: Path,
) -> None:
    dw = decks.newave_workspace(tmp_path)
    (dw.ws.root / "dger.dat").unlink()
    (dw.ws.root / "pmo.dat").write_bytes(newave_outputs.pmo_bytes("complete"))
    report = JobReport(outcome=None, process_exit=0, log_paths=())
    result = evaluate(
        report,
        log_patterns=plugin.log_patterns,
        primary_evidence=plugin.primary_evidence(dw.ws),
        rules=lambda: plugin.diagnose(dw.ws, report),
        job_id="1",
    )
    assert result.status is RunStatus.UNKNOWN
    assert result.rule_id == "core.diagnosis_exception"
    assert "dger.dat" in result.reason
    assert str(dw.ws.root) not in result.reason


def test_write_dger_missing_dger_dat_raises_data_error(tmp_path: Path) -> None:
    dw = decks.newave_workspace(tmp_path)
    (dw.ws.root / "dger.dat").unlink()
    with pytest.raises(DataError, match="dger.dat"):
        newave_outputs.write_dger(
            dw.ws, tipo_execucao=1, tipo_simulacao_final=1
        )


_ALL_NONE_EVIDENCE = NewaveEvidence(
    tipo_execucao=None,
    tipo_simulacao_final=None,
    has_convergence=False,
    has_simulated_cost=False,
    iterations=None,
    zinf=None,
    zsup=None,
)

_FULLY_POPULATED_EVIDENCE = NewaveEvidence(
    tipo_execucao=1,
    tipo_simulacao_final=1,
    has_convergence=True,
    has_simulated_cost=True,
    iterations=50,
    zinf=346338.26,
    zsup=115807.38,
)


@pytest.mark.parametrize("rule", RULES, ids=[rule.rule_id for rule in RULES])
def test_rules_template_renders_for_none_and_populated_evidence_without_raising(
    rule: Rule,
) -> None:
    _render(rule.template, _ALL_NONE_EVIDENCE)
    _render(rule.template, _FULLY_POPULATED_EVIDENCE)


def test_rules_rule_id_fullmatches_pattern_with_newave_prefix() -> None:
    for rule in RULES:
        assert RULE_ID_PATTERN.fullmatch(rule.rule_id)
        assert rule.rule_id.startswith("newave.")


def test_rules_no_row_status_is_infeasible() -> None:
    assert all(rule.status is not RunStatus.INFEASIBLE for rule in RULES)


def test_rules_no_row_rule_id_mentions_gap() -> None:
    assert all("gap" not in rule.rule_id for rule in RULES)


def test_primary_evidence_empty_workspace_falls_back_to_pmo_dat(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    result = plugin.primary_evidence(ws)
    assert result == (ws.root / "pmo.dat",)


def test_primary_evidence_fixture_deck_reads_name_from_arquivos_dat(
    tmp_path: Path,
) -> None:
    dw = decks.newave_workspace(tmp_path)
    result = plugin.primary_evidence(dw.ws)
    assert result == (dw.ws.root / "pmo.dat",)


def test_primary_evidence_custom_arquivos_pmo_name_returns_renamed_path(
    tmp_path: Path,
) -> None:
    dw = decks.newave_workspace(tmp_path)
    arq_path = dw.ws.root / "arquivos.dat"
    text = arq_path.read_text(encoding="latin-1")
    arq_path.write_text(
        text.replace(
            "RELATORIO DE CONVERGENCIA   : pmo.dat",
            "RELATORIO DE CONVERGENCIA   : pmo_x.dat",
        ),
        encoding="latin-1",
    )
    result = plugin.primary_evidence(dw.ws)
    assert result == (dw.ws.root / "pmo_x.dat",)


def test_diagnose_job_process_exit_present_adds_model_exit_evidence_item(
    tmp_path: Path,
) -> None:
    ws = _build_row_workspace(tmp_path, _TABLE_ROWS[0])
    verdict = diagnosis.diagnose(ws, JobReport(None, 137, ()))
    assert any(
        item.layer == "plugin"
        and item.source == "model.exit"
        and item.detail == "137"
        for item in verdict.evidence
    )


def test_diagnose_job_process_exit_none_omits_model_exit_evidence_item(
    tmp_path: Path,
) -> None:
    ws = _build_row_workspace(tmp_path, _TABLE_ROWS[0])
    verdict = diagnosis.diagnose(ws, JobReport(None, None, ()))
    assert all(item.source != "model.exit" for item in verdict.evidence)
