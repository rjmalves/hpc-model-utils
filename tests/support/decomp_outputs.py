"""R124: vendored-relato and inviab derivations for the DECOMP
diagnosis tests (ticket-051), reused by ticket-055c.

Every variant is a line or text operation on the vendored bytes of
``tests/fixtures/flexibilizador/`` (never written): no public
converged-with-CMO, max-iterations or negative-gap relato exists, so
those are synthetic derivations of the real one, as R124 allows.
"""

from __future__ import annotations

from typing import Literal

from tests.support.decks import FIXTURES

RELATO_FIXTURE = FIXTURES / "flexibilizador" / "relato.rv0"
INVIAB_UNIC_FIXTURE = FIXTURES / "flexibilizador" / "inviab_unic.rv0"

RelatoKind = Literal[
    "no_cmo", "converged", "data_error", "max_iterations", "negative_gap"
]
MessageKind = Literal["data_error", "max_iterations", "negative_gap"]
InviabKind = Literal["real", "deficit_only"]

# v1's three relato message lines (app/adapter/repository/decomp.py).
MESSAGES: dict[MessageKind, str] = {
    "data_error": "ERRO(S) DE ENTRADA DE DADOS",
    "max_iterations": "CONVERGENCIA NAO ALCANCADA EM  50 ITERACOES",
    "negative_gap": "ATENCAO: GAP NEGATIVO",
}

# The DECOMP licence-failure text is unverified (ticket-051 Requirement
# 1); this is the substring both CEPEL models are expected to share.
LICENCE_FAILURE_LOG = "Falha de licenciamento!\nEncerrando a execucao.\n"

# Copied from the legacy tests/mocks/decomp.py::MOCK_RELATO (deleted by
# ticket-057): the CMO block idecomp's BlocoCMORelato parses, which
# begins with its BEGIN_PATTERN "   CUSTO MARGINAL DE OPERACAO" and ends
# at the closing "X------X" rule. Trailing padding is dropped and the
# last line gains its newline, so further lines can follow it.
CMO_BLOCK = (
    "   CUSTO MARGINAL DE OPERACAO  ($/MWh)\n"
    "   X------X----------X----------X----------X----------X----------X\n"
    "     Ssis  Sem_01     Sem_02     Sem_03     Sem_04     Sem_05\n"
    "   X------X----------X----------X----------X----------X----------X\n"
    "    Pat_1    287.91     291.06     291.99     289.35     287.67\n"
    "    Pat_2    287.27     289.42     290.16     287.72     283.62\n"
    "    Pat_3    281.48     283.41     283.32     280.07     278.99\n"
    "    Med_SE   284.12     286.88     287.26     284.37     282.32\n"
    "    Pat_1    287.91     291.06     291.99     289.35     287.67\n"
    "    Pat_2    287.27     289.42     290.16     287.72     283.62\n"
    "    Pat_3    281.48     283.41     283.32     280.07     278.99\n"
    "    Med_S    284.12     286.88     287.26     284.37     282.32\n"
    "    Pat_1    287.91     291.06     291.99     289.35     287.67\n"
    "    Pat_2    287.27     289.42     290.16     287.72     283.62\n"
    "    Pat_3    281.48     283.41     283.32     280.07     278.99\n"
    "    Med_NE   284.12     286.88     287.26     284.37     282.32\n"
    "    Pat_1    287.91     291.06     291.99     289.35     287.67\n"
    "    Pat_2    287.27     289.42     290.16     287.72     283.62\n"
    "    Pat_3    281.48     283.41     283.32     280.07     278.99\n"
    "    Med_N    284.12     286.88     287.26     284.37     282.32\n"
    "    Pat_1    287.91     291.06     291.99     289.35     287.67\n"
    "    Pat_2    287.27     289.42     290.16     287.72     283.62\n"
    "    Pat_3    281.48     283.41     283.32     280.07     278.99\n"
    "    Med_FC   284.12     286.88     287.26     284.37     282.32\n"
    "   X------X----------X----------X----------X----------X----------X\n"
)

# idecomp's BlocoInviabilidadesSimFinal: the BEGIN_PATTERN, the header
# lines it skips (the begin line included), and its ``restricao``
# LiteralField(76, 22) columns. A row shorter than 5 non-blank
# characters ends the table.
_SIM_FINAL_BEGIN = b"SIMULACAO FINAL:"
_SIM_FINAL_HEADER_LINES = 4
_RESTRICAO_START = 22
_RESTRICAO_WIDTH = 76
_DEFICIT_PREFIX = b"DEFICIT "


def relato_bytes(kind: RelatoKind, *also: MessageKind) -> bytes:
    """``no_cmo`` is the vendored relato as-is; ``converged`` appends
    :data:`CMO_BLOCK`; each message kind is ``converged`` plus its
    :data:`MESSAGES` line. ``also`` appends further message lines, in
    order, to any kind."""
    raw = RELATO_FIXTURE.read_bytes()
    if not raw.endswith(b"\n"):
        raise ValueError(f"{RELATO_FIXTURE.name} must end with a newline")
    if kind == "no_cmo":
        return _with_messages(raw, also)
    converged = raw + CMO_BLOCK.encode("ascii")
    if kind == "converged":
        return _with_messages(converged, also)
    return _with_messages(converged, (kind, *also))


def _with_messages(text: bytes, messages: tuple[MessageKind, ...]) -> bytes:
    return text + b"".join(
        f"{MESSAGES[message]}\n".encode("ascii") for message in messages
    )


def inviab_unic_bytes(kind: InviabKind) -> bytes:
    """``real`` is the vendored ``inviab_unic``; ``deficit_only``
    rewrites the ``restricao`` field of every final-simulation row to a
    same-width (76-column) value starting ``DEFICIT``, leaving the
    iteration table and every other column untouched."""
    raw = INVIAB_UNIC_FIXTURE.read_bytes()
    if kind == "real":
        return raw
    lines = raw.splitlines(keepends=True)
    begin = next(
        index for index, line in enumerate(lines) if _SIM_FINAL_BEGIN in line
    )
    end = _RESTRICAO_START + _RESTRICAO_WIDTH
    for index in range(begin + _SIM_FINAL_HEADER_LINES, len(lines)):
        line = lines[index]
        if len(line.strip()) < 5:
            break
        field = line[_RESTRICAO_START:end]
        indent = field[: len(field) - len(field.lstrip())]
        value = indent + _DEFICIT_PREFIX + field.strip()
        if len(value) > _RESTRICAO_WIDTH:
            raise ValueError(f"row {index + 1}: DEFICIT value overflows")
        lines[index] = (
            line[:_RESTRICAO_START] + value.ljust(_RESTRICAO_WIDTH) + line[end:]
        )
    return b"".join(lines)
