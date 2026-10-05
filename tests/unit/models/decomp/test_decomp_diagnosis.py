"""ADR-014/ADR-015/R48/R49/R50/R108/R124 tests for the
DECOMP diagnosis rule table (ticket-051).

Every relato and inviab variant comes from ``tests/support/
decomp_outputs.py`` (line/text operations on the vendored bytes); the
first section verifies each variant through idecomp itself.
"""

from __future__ import annotations

import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, cast

import pytest

from hpc_model_utils.core.diagnosis import (
    RULE_ID_PATTERN,
    EvidenceItem,
    JobReport,
    RunStatus,
    evaluate,
)
from hpc_model_utils.core.lifecycle.prepare import extract_sanitize_inputs
from hpc_model_utils.core.state import StateStore
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.models.decomp import DecompPlugin, diagnosis
from hpc_model_utils.models.decomp.diagnosis import (
    RULES,
    DecompEvidence,
    Rule,
    ViolationCount,
    _render,
)
from tests.support import decomp_outputs
from tests.support.decks import FIXTURES, decomp_workspace, input_zip
from tests.support.decomp_outputs import (
    InviabKind,
    MessageKind,
    RelatoKind,
)
from tests.support.reporter import RecordingReporter

DadgerKind = Literal["deck", "flexibilizador"]

plugin = DecompPlugin()

_REPORT = JobReport(None, 0, ())
_FLEX_DADGER = FIXTURES / "flexibilizador" / "dadger.rv0"
_OUTPUT_NAMES = (
    "relato.rv0",
    "relato2.rv0",
    "inviab_unic.rv0",
    "inviab.rv0",
    "sumario.rv0",
)


def _workspace(
    tmp_path: Path,
    *,
    relato: bytes | None = None,
    inviab_unic: bytes | None = None,
    inviab: bytes | None = None,
    dadger: DadgerKind = "deck",
    dadger_bytes: bytes | None = None,
    others: Mapping[str, bytes] | None = None,
) -> Workspace:
    extra: dict[str, bytes] = dict(others or {})
    for name, payload in (
        ("relato.rv0", relato),
        ("inviab_unic.rv0", inviab_unic),
        ("inviab.rv0", inviab),
        ("dadger.rv0", dadger_bytes),
    ):
        if payload is not None:
            extra[name] = payload
    return decomp_workspace(tmp_path, dadger=dadger, extra_files=extra).ws


def _flex_dadger_without_he() -> bytes:
    lines = _FLEX_DADGER.read_bytes().splitlines(keepends=True)
    kept = [line for line in lines if not line.startswith(b"HE ")]
    assert len(kept) < len(lines)
    return b"".join(kept)


def _assert_no_root(
    ws: Workspace, reason: str, items: tuple[EvidenceItem, ...]
) -> None:
    root = str(ws.root)
    assert root not in reason
    for item in items:
        assert root not in item.source
        assert root not in item.detail


# -- support: every variant verified through idecomp -------------------------


@dataclass(frozen=True)
class _RelatoVariant:
    kind: RelatoKind
    also: tuple[MessageKind, ...]
    messages: frozenset[MessageKind]
    has_cmo: bool


_RELATO_VARIANTS: tuple[_RelatoVariant, ...] = (
    _RelatoVariant("no_cmo", (), frozenset(), False),
    _RelatoVariant("converged", (), frozenset(), True),
    _RelatoVariant("data_error", (), frozenset({"data_error"}), True),
    _RelatoVariant("max_iterations", (), frozenset({"max_iterations"}), True),
    _RelatoVariant("negative_gap", (), frozenset({"negative_gap"}), True),
    _RelatoVariant(
        "data_error",
        ("max_iterations",),
        frozenset({"data_error", "max_iterations"}),
        True,
    ),
)


@pytest.mark.parametrize(
    "variant",
    _RELATO_VARIANTS,
    ids=["+".join((v.kind, *v.also)) for v in _RELATO_VARIANTS],
)
def test_relato_bytes_variant_idecomp_reads_expected_messages_and_cmo(
    tmp_path: Path, variant: _RelatoVariant
) -> None:
    from cfinterface.components.defaultblock import DefaultBlock
    from idecomp.decomp.relato import Relato

    path = tmp_path / "relato.rv0"
    path.write_bytes(decomp_outputs.relato_bytes(variant.kind, *variant.also))
    relato = cast(Relato, Relato.read(str(path)))

    lines = [
        block.data
        for block in relato.data.of_type(DefaultBlock)
        if isinstance(block.data, str)
    ]
    found = {
        kind
        for kind, message in decomp_outputs.MESSAGES.items()
        if any(message in line for line in lines)
    }
    assert found == variant.messages
    assert (relato.cmo_medio_submercado is not None) is variant.has_cmo
    convergencia = relato.convergencia
    assert convergencia is not None
    assert int(convergencia.iloc[-1]["iteracao"]) == 13


def test_relato_bytes_converged_idecomp_reads_twenty_row_cmo_table(
    tmp_path: Path,
) -> None:
    from idecomp.decomp.relato import Relato

    path = tmp_path / "relato.rv0"
    path.write_bytes(decomp_outputs.relato_bytes("converged"))
    cmo = cast(Relato, Relato.read(str(path))).cmo_medio_submercado
    assert cmo is not None
    assert cmo.shape == (20, 7)
    assert list(cmo["nome_submercado"].unique()) == ["SE", "S", "NE", "N", "FC"]


_SimFinalRow = tuple[int, int, str, float, str]


def _final_simulation_rows(
    tmp_path: Path, kind: InviabKind
) -> list[_SimFinalRow]:
    from idecomp.decomp.inviabunic import InviabUnic

    path = tmp_path / f"inviab_unic_{kind}.rv0"
    path.write_bytes(decomp_outputs.inviab_unic_bytes(kind))
    inviab = cast(InviabUnic, InviabUnic.read(str(path)))
    table = inviab.inviabilidades_simulacao_final
    assert table is not None
    return [
        (
            int(row.estagio),
            int(row.cenario),
            str(row.restricao),
            float(row.violacao),
            str(row.unidade),
        )
        for row in table.itertuples()
    ]


def test_inviab_unic_bytes_real_idecomp_reads_24_non_deficit_rows(
    tmp_path: Path,
) -> None:
    rows = _final_simulation_rows(tmp_path, "real")
    assert len(rows) == 24
    assert not any("DEFICIT" in row[2] for row in rows)


def test_inviab_unic_bytes_deficit_only_idecomp_reads_24_deficit_rows(
    tmp_path: Path,
) -> None:
    real = _final_simulation_rows(tmp_path, "real")
    derived = _final_simulation_rows(tmp_path, "deficit_only")
    assert len(derived) == 24
    assert all(row[2].startswith("DEFICIT") for row in derived)
    assert [(*row[:2], *row[3:]) for row in derived] == [
        (*row[:2], *row[3:]) for row in real
    ]


def test_inviab_unic_bytes_deficit_only_leaves_iteration_table_unchanged(
    tmp_path: Path,
) -> None:
    from idecomp.decomp.inviabunic import InviabUnic

    tables = []
    for kind in ("real", "deficit_only"):
        path = tmp_path / f"{kind}.rv0"
        path.write_bytes(decomp_outputs.inviab_unic_bytes(kind))
        inviab = cast(InviabUnic, InviabUnic.read(str(path)))
        tables.append(inviab.inviabilidades_iteracoes)
    assert tables[0] is not None
    assert tables[1] is not None
    assert tables[0].equals(tables[1])


def test_flex_dadger_without_he_idecomp_he_returns_none(tmp_path: Path) -> None:
    from idecomp.decomp.dadger import Dadger

    real = cast(Dadger, Dadger.read(str(_FLEX_DADGER)))
    path = tmp_path / "dadger.rv0"
    path.write_bytes(_flex_dadger_without_he())
    stripped = cast(Dadger, Dadger.read(str(path)))
    assert real.he() is not None
    assert stripped.he() is None


# -- AC1: the real INFEASIBLE row (R124) -------------------------------------


def test_diagnose_real_flexibilizador_outputs_returns_infeasible_with_no_cmo(
    tmp_path: Path,
) -> None:
    ws = _workspace(
        tmp_path,
        relato=decomp_outputs.relato_bytes("no_cmo"),
        inviab_unic=decomp_outputs.inviab_unic_bytes("real"),
        dadger="flexibilizador",
    )

    verdict = plugin.diagnose(ws, _REPORT)

    assert verdict.status is RunStatus.INFEASIBLE
    assert verdict.rule_id == "decomp.infeasible"
    assert "24 violation(s), 24 non-deficit" in verdict.reason
    assert "in inviab_unic.rv0" in verdict.reason
    assert "decomp.no_cmo" in verdict.matched
    assert verdict.matched == ("decomp.infeasible", "decomp.no_cmo")
    _assert_no_root(ws, verdict.reason, verdict.evidence)


# -- AC2: the R108 precedence table ------------------------------------------


@dataclass(frozen=True)
class _TableRow:
    relato: RelatoKind
    also: tuple[MessageKind, ...]
    inviab_unic: InviabKind | None
    status: RunStatus
    rule_id: str
    matched: tuple[str, ...]
    reason: str


_TABLE_ROWS: tuple[_TableRow, ...] = (
    _TableRow(
        "converged",
        (),
        None,
        RunStatus.SUCCESS,
        "decomp.converged",
        ("decomp.converged",),
        "converged at iteration 13",
    ),
    _TableRow(
        "no_cmo",
        (),
        None,
        RunStatus.DATA_ERROR,
        "decomp.no_cmo",
        ("decomp.no_cmo",),
        "relato.rv0 has no CUSTO MARGINAL DE OPERACAO table",
    ),
    _TableRow(
        "data_error",
        (),
        None,
        RunStatus.DATA_ERROR,
        "decomp.data_error",
        ("decomp.data_error",),
        "ERRO(S) DE ENTRADA DE DADOS",
    ),
    _TableRow(
        "data_error",
        ("max_iterations",),
        None,
        RunStatus.DATA_ERROR,
        "decomp.data_error",
        ("decomp.data_error", "decomp.max_iterations"),
        "ERRO(S) DE ENTRADA DE DADOS",
    ),
    _TableRow(
        "max_iterations",
        (),
        "real",
        RunStatus.RUNTIME_ERROR,
        "decomp.max_iterations",
        ("decomp.max_iterations", "decomp.infeasible"),
        "CONVERGENCIA NAO ALCANCADA",
    ),
    _TableRow(
        "negative_gap",
        (),
        "real",
        RunStatus.INFEASIBLE,
        "decomp.infeasible",
        ("decomp.infeasible", "decomp.negative_gap"),
        "24 violation(s), 24 non-deficit",
    ),
    _TableRow(
        "negative_gap",
        (),
        None,
        RunStatus.RUNTIME_ERROR,
        "decomp.negative_gap",
        ("decomp.negative_gap",),
        "ATENCAO: GAP NEGATIVO",
    ),
)

_TABLE_ROW_IDS = [
    "converged-none",
    "no_cmo-none",
    "data_error-none",
    "data_error+max_iterations-none",
    "max_iterations-real",
    "negative_gap-real",
    "negative_gap-none",
]


@pytest.mark.parametrize("row", _TABLE_ROWS, ids=_TABLE_ROW_IDS)
def test_diagnose_r108_table_row_returns_expected_winner_and_matched(
    tmp_path: Path, row: _TableRow
) -> None:
    ws = _workspace(
        tmp_path,
        relato=decomp_outputs.relato_bytes(row.relato, *row.also),
        inviab_unic=(
            None
            if row.inviab_unic is None
            else decomp_outputs.inviab_unic_bytes(row.inviab_unic)
        ),
    )

    verdict = plugin.diagnose(ws, _REPORT)

    assert verdict.status is row.status
    assert verdict.rule_id == row.rule_id
    assert verdict.matched == row.matched
    assert row.reason in verdict.reason
    _assert_no_root(ws, verdict.reason, verdict.evidence)


def test_diagnose_converged_row_reason_renders_last_convergence_row(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path, relato=decomp_outputs.relato_bytes("converged"))

    verdict = plugin.diagnose(ws, _REPORT)

    assert verdict.reason == (
        "converged at iteration 13: ZINF 596969161.90, ZSUP 596972236.70, "
        "gap 0.0005151%"
    )


# -- AC3: the deficit premise (v1's, unchanged) ------------------------------


def test_diagnose_deficit_only_violations_with_he_dadger_returns_infeasible(
    tmp_path: Path,
) -> None:
    ws = _workspace(
        tmp_path,
        relato=decomp_outputs.relato_bytes("converged"),
        inviab_unic=decomp_outputs.inviab_unic_bytes("deficit_only"),
        dadger="flexibilizador",
    )

    verdict = plugin.diagnose(ws, _REPORT)

    assert verdict.status is RunStatus.INFEASIBLE
    assert verdict.rule_id == "decomp.infeasible"
    assert "24 violation(s), 0 non-deficit" in verdict.reason


def test_diagnose_deficit_only_violations_without_he_dadger_returns_converged(
    tmp_path: Path,
) -> None:
    ws = _workspace(
        tmp_path,
        relato=decomp_outputs.relato_bytes("converged"),
        inviab_unic=decomp_outputs.inviab_unic_bytes("deficit_only"),
        dadger_bytes=_flex_dadger_without_he(),
    )

    verdict = plugin.diagnose(ws, _REPORT)

    assert verdict.status is RunStatus.SUCCESS
    assert verdict.rule_id == "decomp.converged"
    assert verdict.matched == ("decomp.converged",)
    assert (
        EvidenceItem(
            "plugin",
            "inviab_unic.rv0",
            "final simulation: 24 violation(s), 0 non-deficit",
        )
        in verdict.evidence
    )


def test_diagnose_inviab_alone_non_deficit_violations_returns_infeasible(
    tmp_path: Path,
) -> None:
    ws = _workspace(
        tmp_path,
        relato=decomp_outputs.relato_bytes("converged"),
        inviab=decomp_outputs.inviab_unic_bytes("real"),
    )

    verdict = plugin.diagnose(ws, _REPORT)

    assert verdict.status is RunStatus.INFEASIBLE
    assert verdict.rule_id == "decomp.infeasible"
    assert verdict.reason == (
        "final simulation has 24 violation(s), 24 non-deficit, in inviab.rv0"
    )
    assert not (ws.root / "inviab_unic.rv0").exists()


def test_extract_evidence_missing_relato_and_inviab_never_calls_idecomp_readers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from idecomp.decomp.inviabunic import InviabUnic
    from idecomp.decomp.relato import Relato

    ws = _workspace(tmp_path)

    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("idecomp reader called on a missing output")

    monkeypatch.setattr(Relato, "read", refuse)
    monkeypatch.setattr(InviabUnic, "read", refuse)

    evidence = diagnosis.extract_evidence(ws)

    assert evidence.violations == ()
    assert evidence.has_cmo is False
    assert evidence.iterations is None
    assert dict(evidence.present) == dict.fromkeys(_OUTPUT_NAMES, False)


# -- AC4: layers and the E12 regression --------------------------------------


def _deck_members() -> dict[str, bytes]:
    with zipfile.ZipFile(FIXTURES / "decks" / "deck_decomp.zip") as deck_zip:
        return {
            name: deck_zip.read(name)
            for name in deck_zip.namelist()
            if not name.endswith("/")
        }


def test_evaluate_crashed_run_with_stale_deck_outputs_returns_missing_output(
    tmp_path: Path,
) -> None:
    stale = {
        "relato.rv0": decomp_outputs.relato_bytes("converged"),
        "inviab_unic.rv0": decomp_outputs.inviab_unic_bytes("real"),
    }
    control = _workspace(tmp_path / "control", others=stale)
    assert plugin.diagnose(control, _REPORT).status is RunStatus.INFEASIBLE

    root = tmp_path / "run"
    root.mkdir()
    ws = Workspace.at(root)
    input_zip({**_deck_members(), **stale}, ws.eco_deck_path)
    extract_sanitize_inputs(ws, plugin, StateStore(ws), RecordingReporter())
    assert not (ws.root / "relato.rv0").exists()
    assert not (ws.root / "inviab_unic.rv0").exists()

    report = JobReport(outcome=None, process_exit=139, log_paths=())
    result = evaluate(
        report,
        log_patterns=plugin.log_patterns,
        primary_evidence=plugin.primary_evidence(ws),
        rules=lambda: plugin.diagnose(ws, report),
        job_id="1",
    )

    assert result.status is RunStatus.RUNTIME_ERROR
    assert result.rule_id == "core.missing_output"
    assert result.reason == "missing output file(s): relato.rv0"
    assert "decomp.infeasible" not in result.matched


def test_evaluate_licence_failure_log_without_relato_returns_license_error(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    log = tmp_path / "decomp.log"
    log.write_text(decomp_outputs.LICENCE_FAILURE_LOG, encoding="utf-8")
    report = JobReport(outcome=None, process_exit=1, log_paths=(log,))

    result = evaluate(
        report,
        log_patterns=plugin.log_patterns,
        primary_evidence=plugin.primary_evidence(ws),
        rules=lambda: plugin.diagnose(ws, report),
        job_id="1",
    )

    assert result.status is RunStatus.LICENSE_ERROR
    assert result.rule_id == "decomp.license_failure"


def test_evaluate_present_relato_missing_dadger_returns_unknown_diagnosis_exception(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path, relato=decomp_outputs.relato_bytes("converged"))
    (ws.root / "dadger.rv0").unlink()
    report = JobReport(outcome=None, process_exit=0, log_paths=())

    result = evaluate(
        report,
        log_patterns=plugin.log_patterns,
        primary_evidence=plugin.primary_evidence(ws),
        rules=lambda: plugin.diagnose(ws, report),
        job_id="1",
    )

    assert result.status is RunStatus.UNKNOWN
    assert result.rule_id == "core.diagnosis_exception"
    assert "dadger.rv0" in result.reason
    assert str(ws.root) not in result.reason


# -- AC6: hygiene ---------------------------------------------------------------


_ALL_NONE_EVIDENCE = DecompEvidence(
    ext="rv0",
    present={},
    data_error=False,
    max_iterations=False,
    negative_gap=False,
    has_cmo=False,
    violations=(),
    he_present=False,
    iterations=None,
    zinf=None,
    zsup=None,
    gap=None,
)

_FULLY_POPULATED_EVIDENCE = DecompEvidence(
    ext="rv0",
    present=dict.fromkeys(_OUTPUT_NAMES, True),
    data_error=True,
    max_iterations=True,
    negative_gap=True,
    has_cmo=True,
    violations=(
        ViolationCount("inviab_unic.rv0", 24, 24),
        ViolationCount("inviab.rv0", 3, 0),
    ),
    he_present=True,
    iterations=13,
    zinf=596969161.9,
    zsup=596972236.7,
    gap=0.0005151,
)


@pytest.mark.parametrize("rule", RULES, ids=[rule.rule_id for rule in RULES])
def test_rules_template_renders_for_none_and_populated_evidence_without_raising(
    rule: Rule,
) -> None:
    for evidence in (_ALL_NONE_EVIDENCE, _FULLY_POPULATED_EVIDENCE):
        rendered = _render(rule.template, evidence)
        assert "{" not in rendered
        assert "}" not in rendered


def test_render_all_none_evidence_renders_question_marks() -> None:
    converged = RULES[-1]
    assert _render(converged.template, _ALL_NONE_EVIDENCE) == (
        "converged at iteration ?: ZINF ?, ZSUP ?, gap ?%"
    )


def test_render_infeasible_template_sums_violations_and_lists_files() -> None:
    infeasible = RULES[2]
    assert _render(infeasible.template, _FULLY_POPULATED_EVIDENCE) == (
        "final simulation has 27 violation(s), 24 non-deficit, in "
        "inviab_unic.rv0, inviab.rv0"
    )


def test_rules_rule_id_fullmatches_pattern_with_decomp_prefix() -> None:
    for rule in RULES:
        assert RULE_ID_PATTERN.fullmatch(rule.rule_id)
        assert rule.rule_id.startswith("decomp.")


def test_rules_order_and_statuses_match_r108_precedence() -> None:
    assert [(rule.rule_id, rule.status) for rule in RULES] == [
        ("decomp.data_error", RunStatus.DATA_ERROR),
        ("decomp.max_iterations", RunStatus.RUNTIME_ERROR),
        ("decomp.infeasible", RunStatus.INFEASIBLE),
        ("decomp.negative_gap", RunStatus.RUNTIME_ERROR),
        ("decomp.no_cmo", RunStatus.DATA_ERROR),
        ("decomp.converged", RunStatus.SUCCESS),
    ]


def test_rules_exactly_one_row_is_infeasible_decomp_infeasible() -> None:
    infeasible = [
        rule.rule_id for rule in RULES if rule.status is RunStatus.INFEASIBLE
    ]
    assert infeasible == ["decomp.infeasible"]


def test_rules_converged_fallback_applies_only_without_diagnostic_hit() -> None:
    converged = RULES[-1]
    assert converged.applies(replace(_ALL_NONE_EVIDENCE, has_cmo=True))
    assert not converged.applies(_ALL_NONE_EVIDENCE)
    assert not converged.applies(_FULLY_POPULATED_EVIDENCE)


def test_primary_evidence_empty_workspace_returns_caso_dat_without_raising(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    assert plugin.primary_evidence(ws) == (ws.root / "caso.dat",)


def test_primary_evidence_empty_caso_dat_returns_caso_dat_without_raising(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    (ws.root / "caso.dat").write_text("\n\n", encoding="utf-8")
    assert plugin.primary_evidence(ws) == (ws.root / "caso.dat",)


def test_primary_evidence_fixture_deck_returns_relato_path(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    assert plugin.primary_evidence(ws) == (ws.root / "relato.rv0",)


# -- evidence items -------------------------------------------------------------


def test_diagnose_all_outputs_present_lists_each_file_and_violation_file(
    tmp_path: Path,
) -> None:
    ws = _workspace(
        tmp_path,
        relato=decomp_outputs.relato_bytes("converged"),
        inviab_unic=decomp_outputs.inviab_unic_bytes("real"),
        inviab=decomp_outputs.inviab_unic_bytes("deficit_only"),
        others={"relato2.rv0": b"relato2\n", "sumario.rv0": b"sumario\n"},
    )

    verdict = plugin.diagnose(ws, JobReport(None, None, ()))

    assert verdict.evidence == (
        *(EvidenceItem("plugin", name, "present") for name in _OUTPUT_NAMES),
        EvidenceItem(
            "plugin",
            "inviab_unic.rv0",
            "final simulation: 24 violation(s), 24 non-deficit",
        ),
        EvidenceItem(
            "plugin",
            "inviab.rv0",
            "final simulation: 24 violation(s), 0 non-deficit",
        ),
    )
    assert verdict.reason == (
        "final simulation has 48 violation(s), 24 non-deficit, in "
        "inviab_unic.rv0, inviab.rv0"
    )


def test_diagnose_only_relato_present_lists_other_outputs_absent(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path, relato=decomp_outputs.relato_bytes("converged"))

    verdict = plugin.diagnose(ws, JobReport(None, None, ()))

    assert verdict.evidence == (
        EvidenceItem("plugin", "relato.rv0", "present"),
        *(EvidenceItem("plugin", name, "absent") for name in _OUTPUT_NAMES[1:]),
    )


def test_diagnose_job_process_exit_present_adds_model_exit_evidence_item(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path, relato=decomp_outputs.relato_bytes("converged"))

    verdict = plugin.diagnose(ws, JobReport(None, 137, ()))

    assert verdict.evidence[-1] == EvidenceItem("plugin", "model.exit", "137")


def test_diagnose_job_process_exit_none_omits_model_exit_evidence_item(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path, relato=decomp_outputs.relato_bytes("converged"))

    verdict = plugin.diagnose(ws, JobReport(None, None, ()))

    assert all(item.source != "model.exit" for item in verdict.evidence)
