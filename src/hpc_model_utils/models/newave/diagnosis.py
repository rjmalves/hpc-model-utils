"""ADR-014/ADR-015/R48/R49/R103/R109/R124: the NEWAVE L2/L3 diagnosis
rules (ticket-047).

NEWAVE never emits ``INFEASIBLE`` and has no negative-gap rule
(ADR-015): with sampled forward passes its upper bound is statistical.
inewave is imported lazily, inside functions, following ``deck.py``
from ticket-046.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from hpc_model_utils.core.diagnosis import (
    EvidenceItem,
    JobReport,
    LogPattern,
    RunStatus,
    Verdict,
)
from hpc_model_utils.core.errors import DataError
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.models.newave import deck

LOG_PATTERNS: tuple[LogPattern, ...] = (
    LogPattern(
        "newave.license_failure",
        r"Falha de licenciamento",
        RunStatus.LICENSE_ERROR,
        "NEWAVE licence check failed (Falha de licenciamento)",
    ),
)

_PMO_NAME = "pmo.dat"


@dataclass(frozen=True, slots=True)
class NewaveEvidence:
    tipo_execucao: int | None
    tipo_simulacao_final: int | None
    has_convergence: bool
    has_simulated_cost: bool
    iterations: int | None
    zinf: float | None
    zsup: float | None


def _unknown_execution_type(evidence: NewaveEvidence) -> bool:
    return evidence.tipo_execucao not in (0, 1)


def _consistency_run(evidence: NewaveEvidence) -> bool:
    return evidence.tipo_simulacao_final == 3


def _missing_convergence(evidence: NewaveEvidence) -> bool:
    return evidence.tipo_execucao == 1 and not evidence.has_convergence


def _final_simulation_incomplete(evidence: NewaveEvidence) -> bool:
    return (
        evidence.tipo_execucao == 1
        and evidence.tipo_simulacao_final in (1, 2)
        and not evidence.has_simulated_cost
    )


def _simulation_incomplete(evidence: NewaveEvidence) -> bool:
    return evidence.tipo_execucao == 0 and not evidence.has_simulated_cost


@dataclass(frozen=True, slots=True)
class Rule:
    rule_id: str
    status: RunStatus
    template: str
    applies: Callable[[NewaveEvidence], bool]


# ADR-014/ADR-015 L3 table: the first row whose ``applies`` predicate is
# true wins. ``newave.completed`` is the fallback -- true exactly when no
# row of ``_DIAGNOSTIC_RULES`` applies -- so it never widens
# ``diagnose``'s ``matched`` audit with a trivial always-true entry.
_DIAGNOSTIC_RULES: tuple[Rule, ...] = (
    Rule(
        "newave.unknown_execution_type",
        RunStatus.UNKNOWN,
        "unrecognized TIPO DE EXECUCAO {tipo_execucao}",
        _unknown_execution_type,
    ),
    Rule(
        "newave.consistency_run",
        RunStatus.SUCCESS,
        "consistency run (TIPO SIMUL. FINAL {tipo_simulacao_final})",
        _consistency_run,
    ),
    Rule(
        "newave.missing_convergence",
        RunStatus.DATA_ERROR,
        "full run (TIPO DE EXECUCAO {tipo_execucao}) missing the "
        "convergence table",
        _missing_convergence,
    ),
    Rule(
        "newave.final_simulation_incomplete",
        RunStatus.RUNTIME_ERROR,
        "final simulation (TIPO SIMUL. FINAL {tipo_simulacao_final}) "
        "incomplete: missing the simulated-series cost table",
        _final_simulation_incomplete,
    ),
    Rule(
        "newave.simulation_incomplete",
        RunStatus.DATA_ERROR,
        "final-simulation-only run (TIPO DE EXECUCAO {tipo_execucao}) "
        "missing the simulated-series cost table",
        _simulation_incomplete,
    ),
)


def _completed(evidence: NewaveEvidence) -> bool:
    return not any(rule.applies(evidence) for rule in _DIAGNOSTIC_RULES)


RULES: tuple[Rule, ...] = (
    *_DIAGNOSTIC_RULES,
    Rule(
        "newave.completed",
        RunStatus.SUCCESS,
        "completed at iteration {iterations}: ZINF {zinf}, ZSUP {zsup}",
        _completed,
    ),
)


def _format_field(value: int | float | None) -> str:
    if value is None:
        return "?"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _render(template: str, evidence: NewaveEvidence) -> str:
    return template.format(
        tipo_execucao=_format_field(evidence.tipo_execucao),
        tipo_simulacao_final=_format_field(evidence.tipo_simulacao_final),
        iterations=_format_field(evidence.iterations),
        zinf=_format_field(evidence.zinf),
        zsup=_format_field(evidence.zsup),
    )


def _pmo_path(ws: Workspace) -> Path:
    try:
        name = deck.arquivos(ws).pmo
    except DataError:
        name = None
    return ws.root / (name or _PMO_NAME)


def primary_evidence(ws: Workspace) -> tuple[Path, ...]:
    return (_pmo_path(ws),)


def extract_evidence(ws: Workspace) -> NewaveEvidence:
    info = deck.dger(ws)
    pmo_path = _pmo_path(ws)
    if not pmo_path.is_file():
        return NewaveEvidence(
            tipo_execucao=info.tipo_execucao,
            tipo_simulacao_final=info.tipo_simulacao_final,
            has_convergence=False,
            has_simulated_cost=False,
            iterations=None,
            zinf=None,
            zsup=None,
        )
    from inewave.newave.pmo import Pmo

    pmo = cast(Pmo, Pmo.read(str(pmo_path)))
    convergencia = pmo.convergencia
    if convergencia is not None and not convergencia.empty:
        last = convergencia.iloc[-1]
        has_convergence = True
        iterations: int | None = int(last["iteracao"])
        zinf: float | None = float(last["zinf"])
        zsup: float | None = float(last["zsup"])
    else:
        has_convergence = False
        iterations = zinf = zsup = None
    custo = pmo.custo_operacao_series_simuladas
    has_simulated_cost = custo is not None and not custo.empty
    return NewaveEvidence(
        tipo_execucao=info.tipo_execucao,
        tipo_simulacao_final=info.tipo_simulacao_final,
        has_convergence=has_convergence,
        has_simulated_cost=has_simulated_cost,
        iterations=iterations,
        zinf=zinf,
        zsup=zsup,
    )


def _evidence_items(
    ws: Workspace, job: JobReport, evidence: NewaveEvidence
) -> tuple[EvidenceItem, ...]:
    dger_basename = Path(deck.dger_name(ws)).name
    pmo_basename = _pmo_path(ws).name
    convergence_state = "present" if evidence.has_convergence else "absent"
    cost_state = "present" if evidence.has_simulated_cost else "absent"
    items = [
        EvidenceItem(
            "plugin",
            dger_basename,
            f"TIPO DE EXECUCAO {_format_field(evidence.tipo_execucao)}; "
            "TIPO SIMUL. FINAL "
            f"{_format_field(evidence.tipo_simulacao_final)}",
        ),
        EvidenceItem(
            "plugin",
            pmo_basename,
            f"convergence table: {convergence_state}; "
            f"simulated-series cost table: {cost_state}",
        ),
    ]
    if job.process_exit is not None:
        items.append(
            EvidenceItem("plugin", "model.exit", str(job.process_exit))
        )
    return tuple(items)


def diagnose(ws: Workspace, job: JobReport) -> Verdict:
    evidence = extract_evidence(ws)
    applicable = [rule for rule in RULES if rule.applies(evidence)]
    winner = applicable[0]
    return Verdict(
        status=winner.status,
        rule_id=winner.rule_id,
        reason=_render(winner.template, evidence),
        evidence=_evidence_items(ws, job, evidence),
        matched=tuple(rule.rule_id for rule in applicable),
    )
