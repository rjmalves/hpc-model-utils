"""R124: vendored-``pmo.dat`` derivations and ``dger.dat`` rewriting for
the NEWAVE diagnosis tests (ticket-047), reused by ticket-054,
ticket-055a and ticket-055c.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.models.newave import deck

PMO_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "outputs"
    / "newave"
    / "pmo.dat"
)

# inewave's BlocoConvergenciaPMO/BlocoCustoOperacaoPMO BEGIN_PATTERN values.
_CONVERGENCE_HEADER = b"    ITER               LIM.INF.        "
_COST_HEADER = b"PARCELA           V.ESPERADO"

LICENCE_FAILURE_LOG = (
    "NEWAVE Build 29\nFalha de licenciamento!\nEncerrando a execucao.\n"
)


def pmo_bytes(
    kind: Literal["complete", "no_simulated_cost", "no_convergence"],
) -> bytes:
    raw = PMO_FIXTURE.read_bytes()
    if kind == "complete":
        return raw
    lines = raw.split(b"\n")
    if kind == "no_simulated_cost":
        index = next(i for i, line in enumerate(lines) if _COST_HEADER in line)
        return b"\n".join(lines[:index])
    return b"\n".join(line for line in lines if _CONVERGENCE_HEADER not in line)


def write_dger(
    ws: Workspace, *, tipo_execucao: int, tipo_simulacao_final: int
) -> None:
    info = deck.dger(ws)
    info.tipo_execucao = tipo_execucao
    info.tipo_simulacao_final = tipo_simulacao_final
    info.write(str(ws.root / deck.dger_name(ws)))
