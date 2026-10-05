"""ADR-014/ADR-015/R48/R49/R50/R108/R124: the DECOMP L2/L3
diagnosis rules (ticket-051).

R108 keeps v1's precedence (``generate_execution_status``): data error,
max iterations, infeasible, negative gap, no CMO -- but max iterations
and a negative gap are now ``RUNTIME_ERROR``, never flexibilized. DECOMP
is the only model that emits ``INFEASIBLE`` and the only one with a
negative-gap rule (ADR-015).

idecomp parses a missing path as content (E12), so every relato and
inviab read is guarded by ``is_file()``; a missing inviab file means no
violations, which is safe only because the ``core.missing_output`` guard
requires the relato first. idecomp (and cfinterface, for v1's
``DefaultBlock`` message scan) is imported lazily, from concrete
submodules, following ``deck.py``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
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
from hpc_model_utils.models.decomp import deck

LOG_PATTERNS: tuple[LogPattern, ...] = (
    LogPattern(
        "decomp.license_failure",
        r"Falha de licenciamento",
        RunStatus.LICENSE_ERROR,
        "DECOMP licence check failed (Falha de licenciamento)",
    ),
)

_CASO_NAME = "caso.dat"
_RELATO = "relato"
_INVIAB_STEMS: tuple[str, ...] = ("inviab_unic", "inviab")
_OUTPUT_STEMS: tuple[str, ...] = (
    _RELATO,
    "relato2",
    *_INVIAB_STEMS,
    "sumario",
)

# v1's relato messages, matched as substrings of any DefaultBlock line.
_DATA_ERROR_MESSAGE = "ERRO(S) DE ENTRADA DE DADOS"
_MAX_ITERATIONS_MESSAGE = "CONVERGENCIA NAO ALCANCADA EM"
_NEGATIVE_GAP_MESSAGE = "ATENCAO: GAP NEGATIVO"
_DEFICIT = "DEFICIT"


@dataclass(frozen=True, slots=True)
class ViolationCount:
    file: str
    total: int
    non_deficit: int


@dataclass(frozen=True, slots=True)
class DecompEvidence:
    ext: str
    present: Mapping[str, bool]
    data_error: bool
    max_iterations: bool
    negative_gap: bool
    has_cmo: bool
    violations: tuple[ViolationCount, ...]
    he_present: bool
    iterations: int | None
    zinf: float | None
    zsup: float | None
    gap: float | None


def _data_error(evidence: DecompEvidence) -> bool:
    return evidence.data_error


def _max_iterations(evidence: DecompEvidence) -> bool:
    return evidence.max_iterations


def _infeasible(evidence: DecompEvidence) -> bool:
    # v1's premise, unchanged: a non-deficit violation is infeasible;
    # deficits alone count only when dadger has an HE register.
    non_deficit = any(v.non_deficit > 0 for v in evidence.violations)
    deficit_only = any(
        v.total > 0 and v.non_deficit == 0 for v in evidence.violations
    )
    return non_deficit or (deficit_only and evidence.he_present)


def _negative_gap(evidence: DecompEvidence) -> bool:
    return evidence.negative_gap


def _no_cmo(evidence: DecompEvidence) -> bool:
    return not evidence.has_cmo


@dataclass(frozen=True, slots=True)
class Rule:
    rule_id: str
    status: RunStatus
    template: str
    applies: Callable[[DecompEvidence], bool]


# ADR-014/ADR-015/R108 L3 table, in v1's order: the first row whose
# ``applies`` predicate is true wins. ``decomp.converged`` is the
# fallback -- true exactly when no row of ``_DIAGNOSTIC_RULES`` applies
# -- so it never widens ``diagnose``'s ``matched`` audit with a trivial
# always-true entry.
_DIAGNOSTIC_RULES: tuple[Rule, ...] = (
    Rule(
        "decomp.data_error",
        RunStatus.DATA_ERROR,
        "relato.{ext} reports input data errors (ERRO(S) DE ENTRADA DE DADOS)",
        _data_error,
    ),
    Rule(
        "decomp.max_iterations",
        RunStatus.RUNTIME_ERROR,
        "convergence not reached (CONVERGENCIA NAO ALCANCADA) at "
        "iteration {iterations}: ZINF {zinf}, ZSUP {zsup}, gap {gap}%",
        _max_iterations,
    ),
    Rule(
        "decomp.infeasible",
        RunStatus.INFEASIBLE,
        "final simulation has {violation_total} violation(s), "
        "{violation_non_deficit} non-deficit, in {violation_files}",
        _infeasible,
    ),
    Rule(
        "decomp.negative_gap",
        RunStatus.RUNTIME_ERROR,
        "negative gap (ATENCAO: GAP NEGATIVO) at iteration "
        "{iterations}: ZINF {zinf}, ZSUP {zsup}, gap {gap}%",
        _negative_gap,
    ),
    Rule(
        "decomp.no_cmo",
        RunStatus.DATA_ERROR,
        "relato.{ext} has no CUSTO MARGINAL DE OPERACAO table",
        _no_cmo,
    ),
)


def _converged(evidence: DecompEvidence) -> bool:
    return not any(rule.applies(evidence) for rule in _DIAGNOSTIC_RULES)


RULES: tuple[Rule, ...] = (
    *_DIAGNOSTIC_RULES,
    Rule(
        "decomp.converged",
        RunStatus.SUCCESS,
        "converged at iteration {iterations}: ZINF {zinf}, ZSUP {zsup}, "
        "gap {gap}%",
        _converged,
    ),
)


def _format_field(value: int | float | None) -> str:
    if value is None:
        return "?"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _format_gap(value: float | None) -> str:
    # The relato prints the gap with 7 decimals (e.g. 0.0005151); two
    # would round every converged gap to 0.00.
    return "?" if value is None else f"{value:.7g}"


def _render(template: str, evidence: DecompEvidence) -> str:
    counted = [v for v in evidence.violations if v.total > 0]
    return template.format(
        ext=evidence.ext,
        iterations=_format_field(evidence.iterations),
        zinf=_format_field(evidence.zinf),
        zsup=_format_field(evidence.zsup),
        gap=_format_gap(evidence.gap),
        violation_total=sum(v.total for v in evidence.violations),
        violation_non_deficit=sum(v.non_deficit for v in evidence.violations),
        violation_files=", ".join(v.file for v in counted) or "?",
    )


def primary_evidence(ws: Workspace) -> tuple[Path, ...]:
    """Never raises: finalize calls it outside the L3 wrapper."""
    try:
        ext = deck.extension(ws)
    except DataError:
        return (ws.root / _CASO_NAME,)
    return (ws.root / f"{_RELATO}.{ext}",)


@dataclass(frozen=True, slots=True)
class _RelatoSummary:
    data_error: bool = False
    max_iterations: bool = False
    negative_gap: bool = False
    has_cmo: bool = False
    iterations: int | None = None
    zinf: float | None = None
    zsup: float | None = None
    gap: float | None = None


def _finite(value: float) -> float | None:
    number = float(value)
    return number if math.isfinite(number) else None


def _summarize_relato(path: Path) -> _RelatoSummary:
    if not path.is_file():
        return _RelatoSummary()
    from cfinterface.components.defaultblock import DefaultBlock
    from idecomp.decomp.relato import Relato

    relato = cast(Relato, Relato.read(str(path)))
    lines = [
        block.data
        for block in relato.data.of_type(DefaultBlock)
        if isinstance(block.data, str)
    ]

    def mentions(message: str) -> bool:
        return any(message in line for line in lines)

    convergencia = relato.convergencia
    if convergencia is not None and not convergencia.empty:
        last = convergencia.iloc[-1]
        iterations: int | None = int(last["iteracao"])
        zinf = _finite(last["zinf"])
        zsup = _finite(last["zsup"])
        gap = _finite(last["gap_percentual"])
    else:
        iterations = None
        zinf = zsup = gap = None
    return _RelatoSummary(
        data_error=mentions(_DATA_ERROR_MESSAGE),
        max_iterations=mentions(_MAX_ITERATIONS_MESSAGE),
        negative_gap=mentions(_NEGATIVE_GAP_MESSAGE),
        has_cmo=relato.cmo_medio_submercado is not None,
        iterations=iterations,
        zinf=zinf,
        zsup=zsup,
        gap=gap,
    )


def _count_violations(path: Path) -> ViolationCount | None:
    if not path.is_file():
        return None
    from idecomp.decomp.inviabunic import InviabUnic

    inviab = cast(InviabUnic, InviabUnic.read(str(path)))
    table = inviab.inviabilidades_simulacao_final
    if table is None or table.empty:
        return ViolationCount(path.name, 0, 0)
    messages = [str(message) for message in table["restricao"].tolist()]
    return ViolationCount(
        path.name,
        len(messages),
        sum(_DEFICIT not in message for message in messages),
    )


def extract_evidence(ws: Workspace) -> DecompEvidence:
    ext = deck.extension(ws)
    names = {stem: f"{stem}.{ext}" for stem in _OUTPUT_STEMS}
    present = {name: (ws.root / name).is_file() for name in names.values()}
    relato = _summarize_relato(ws.root / names[_RELATO])
    violations = tuple(
        count
        for stem in _INVIAB_STEMS
        if (count := _count_violations(ws.root / names[stem])) is not None
    )
    return DecompEvidence(
        ext=ext,
        present=present,
        data_error=relato.data_error,
        max_iterations=relato.max_iterations,
        negative_gap=relato.negative_gap,
        has_cmo=relato.has_cmo,
        violations=violations,
        he_present=deck.dadger(ws).he() is not None,
        iterations=relato.iterations,
        zinf=relato.zinf,
        zsup=relato.zsup,
        gap=relato.gap,
    )


def _evidence_items(
    job: JobReport, evidence: DecompEvidence
) -> tuple[EvidenceItem, ...]:
    items: list[EvidenceItem] = []
    items.extend(
        EvidenceItem("plugin", name, "present" if found else "absent")
        for name, found in evidence.present.items()
    )
    items.extend(
        EvidenceItem(
            "plugin",
            count.file,
            f"final simulation: {count.total} violation(s), "
            f"{count.non_deficit} non-deficit",
        )
        for count in evidence.violations
    )
    if job.process_exit is not None:
        items.append(
            EvidenceItem("plugin", "model.exit", str(job.process_exit))
        )
    return tuple(items)


def diagnose(ws: Workspace, job: JobReport) -> Verdict:
    evidence = extract_evidence(ws)
    applicable = [rule for rule in RULES if rule.applies(evidence)]
    winner = applicable[0]
    reason = _render(winner.template, evidence)
    return Verdict(
        status=winner.status,
        rule_id=winner.rule_id,
        reason=reason,
        evidence=_evidence_items(job, evidence),
        matched=tuple(rule.rule_id for rule in applicable),
    )
