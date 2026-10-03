"""ADR-040/ADR-048/R16/R17/R55: the DECOMP declarative ``OutputPlan``
(ticket-052).

v1's ``output_compression_and_cleanup``
(``app/adapter/repository/decomp.py:744-764``) moved the files of
``out/`` to the workspace root before matching basenames. ``_v1``
reproduces that selection without moving anything: a pattern matches a
basename at the root or directly inside ``out/``. Basename patterns use
``[^/]``, never ``.``, so no recursive selector reaches any other
directory (``sintese/``, a legacy clone folder).

Name templates carry ``{ext}``, the ``caso.dat`` extension. v1's
``entradas/<dadger>`` echo is deliberately not reproduced: the dadger
is a member of both ``entradas/eco_deck.zip`` and
``entradas/deck_processado.zip``, and nothing reads the echo
(ticket-052 Decision A).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from hpc_model_utils.core.outputs import (
    Flat,
    OutputPlan,
    RawFile,
    Selector,
    ZipGroup,
)
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.models.decomp import deck

# v1's _list_operation_files, ``^X.*\.csv$`` translated to ``X[^/]*\.csv``.
_OPERATION_PATTERNS: tuple[str, ...] = (
    r"bengnl[^/]*\.csv",
    r"dec_oper[^/]*\.csv",
    r"energia_acopla[^/]*\.csv",
    r"balsub[^/]*\.csv",
    r"cei[^/]*\.csv",
    r"cmar[^/]*\.csv",
    r"contratos[^/]*\.csv",
    r"ener[^/]*\.csv",
    r"ever[^/]*\.csv",
    r"evnt[^/]*\.csv",
    r"flx[^/]*\.csv",
    r"hidrpat[^/]*\.csv",
    r"pdef[^/]*\.csv",
    r"qnat[^/]*\.csv",
    r"qtur[^/]*\.csv",
    r"term[^/]*\.csv",
    r"usina[^/]*\.csv",
    r"ute[^/]*\.csv",
    r"vert[^/]*\.csv",
    r"vutil[^/]*\.csv",
    r"oper_[^/]*\.csv",
)

_REPORT_NAMES: tuple[str, ...] = (
    "decomp.tim",
    "relato.{ext}",
    "sumario.{ext}",
    "relato2.{ext}",
    "inviab_unic.{ext}",
    "inviab.{ext}",
    "relgnl.{ext}",
    "custos.{ext}",
    "avl_cortesfpha_dec.{ext}",
    "dec_desvfpha.{ext}",
    "dec_estatfpha.{ext}",
    "energia.{ext}",
    "log_desvfpha_dec.{ext}",
    "outgnl.{ext}",
    "memcal.{ext}",
    "runstate.dat",
    "runtrace.dat",
    "eco_fpha_.{ext}",
    "dec_eco_desvioagua.csv",
    "dec_eco_discr.csv",
    "dec_eco_evap.csv",
    "dec_eco_qlat.csv",
    "dec_eco_cotajus.csv",
    "avl_turb_max.csv",
    "dec_avl_evap.csv",
    "dec_cortes_evap.csv",
    "dec_estatevap.csv",
    "fcfnwi.{ext}",
    "fcfnwn.{ext}",
    "cmdeco.{ext}",
    "indice_saida.csv",
    "mensagens.csv",
    "mensagensErro.txt",
)
_REPORT_PATTERNS: tuple[str, ...] = (
    r"osl_[^/]*",
    r"deco_[^/]*msg",
    r"eco_[^/]*\.csv",
    r"dec_fcf_cortes[^/]*",
    r"avl_desvfpha_v_q_[^/]*",
    r"avl_desvfpha_s_[^/]*",
)

_CUT_NAMES: tuple[str, ...] = ("cortdeco.{ext}", "mapcut.{ext}")

# v1's _upload_outputs raw names, plus C6's relgnl (R17).
_RAW_NAMES: tuple[str, ...] = (
    "inviab_unic.{ext}",
    "inviab.{ext}",
    "relato.{ext}",
    "sumario.{ext}",
    "decomp.tim",
    "relgnl.{ext}",
)


def _v1(pattern: str) -> str:
    return rf"(?:out/)?(?:{pattern})"


def _lit(name: str) -> str:
    return _v1(re.escape(name))


def _select(names: Iterable[str], patterns: Iterable[str] = ()) -> Selector:
    return Selector(
        patterns=(
            *(_lit(name) for name in names),
            *(_v1(pattern) for pattern in patterns),
        ),
        recursive=True,
    )


def _group(
    archive: str, names: Iterable[str], patterns: Iterable[str] = ()
) -> ZipGroup:
    return ZipGroup(archive, _select(names, patterns), Flat())


def _names(templates: Iterable[str], ext: str) -> tuple[str, ...]:
    return tuple(template.format(ext=ext) for template in templates)


def output_plan(ws: Workspace) -> OutputPlan:
    ext = deck.extension(ws)
    groups = (
        _group("operacao.zip", (), _OPERATION_PATTERNS),
        _group("relatorios.zip", _names(_REPORT_NAMES, ext), _REPORT_PATTERNS),
        _group("cortes.zip", _names(_CUT_NAMES, ext)),
    )
    raw = tuple(RawFile(_select((name,))) for name in _names(_RAW_NAMES, ext))
    return OutputPlan(deck_inputs=deck.input_files(ws), groups=groups, raw=raw)
