"""ADR-040/ADR-048/R16/R17/R55/R90: the NEWAVE declarative ``OutputPlan``
(ticket-048).

v1's ``output_compression_and_cleanup``
(``app/adapter/repository/newave.py:625-877``) moved the files of
``out/``, ``evaporacao/``, ``fpha/`` and ``log/`` to the workspace root
before matching basenames. ``_v1`` reproduces that selection without
moving anything: a pattern matches a basename at the root or directly
inside one of those four directories. Basename patterns use ``[^/]``,
never ``.``, so no recursive selector reaches any other directory
(``sintese/``, a legacy clone folder).
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
from hpc_model_utils.models.newave import deck

_TIM_NAME = "newave.tim"

_OPERATION_NAMES: tuple[str, ...] = ("nwlistop.dat",)
_OPERATION_PATTERNS: tuple[str, ...] = (r"[^/]*\.CSV", r"[^/]*\.out")

_REPORT_NAMES: tuple[str, ...] = (
    _TIM_NAME,
    "nwv_avl_evap.csv",
    "nwv_cortes_evap.csv",
    "nwv_eco_evap.csv",
    "evap_avl_desv.csv",
    "evap_eco.csv",
    "evap_cortes.csv",
    "boots.rel",
    "consultafcf.rel",
    "eco_fpha_.dat",
    "eco_fpha.csv",
    "fpha_eco.csv",
    "fpha_cortes.csv",
    "avl_cortesfpha_nwv.dat",
    "avl_cortesfpha_nwv.csv",
    "parpeol.dat",
    "parpvaz.dat",
    "runtrace.dat",
    "runstate.dat",
    "prociter.rel",
    "CONVERG.TMP",
    "ETAPA.TMP",
    "TAREFA.TMP",
    "mensagens.csv",
    "indice_saida.csv",
    "qprevs-medio-usina.csv",
)
_REPORT_PATTERNS: tuple[str, ...] = (
    r"alertainv[^/]*\.rel",
    r"cativo_[^/]*\.rel",
    r"avl_desvfpha[^/]*\.dat",
    r"avl_desvfpha[^/]*\.csv",
    r"newave_[^/]*\.log",
    r"nwv_[^/]*\.rel",
)

_RESOURCE_NAMES: tuple[str, ...] = ("mlt.dat",)
_RESOURCE_PATTERNS: tuple[str, ...] = (
    r"energiaas[^/]*\.dat",
    r"energiaasx[^/]*\.dat",
    r"energiaf[^/]*\.dat",
    r"energiaaf[^/]*\.dat",
    r"energiab[^/]*\.dat",
    r"energiaxf[^/]*\.dat",
    r"energias[^/]*\.dat",
    r"energiaxs[^/]*\.dat",
    r"energiap[^/]*\.dat",
    r"energiaas[^/]*\.csv",
    r"energiaasx[^/]*\.csv",
    r"energiaf[^/]*\.csv",
    r"energiaaf[^/]*\.csv",
    r"energiab[^/]*\.csv",
    r"energiax[^/]*\.csv",
    r"energiaxf[^/]*\.csv",
    r"energiaxs[^/]*\.csv",
    r"energiap[^/]*\.csv",
    r"eng[^/]*\.dat",
    r"enavazf[^/]*\.dat",
    r"enavazxf[^/]*\.dat",
    r"enavazxs[^/]*\.dat",
    r"enavazb[^/]*\.dat",
    r"enavazs[^/]*\.dat",
    r"enavazf[^/]*\.csv",
    r"enavazxf[^/]*\.csv",
    r"enavazaf[^/]*\.csv",
    r"enavazb[^/]*\.csv",
    r"enavazs[^/]*\.csv",
    r"vazaof[^/]*\.dat",
    r"vazaoaf[^/]*\.dat",
    r"vazaob[^/]*\.dat",
    r"vazaos[^/]*\.dat",
    r"vazaoas[^/]*\.dat",
    r"vazaoxs[^/]*\.dat",
    r"vazaoxf[^/]*\.dat",
    r"vazaop[^/]*\.dat",
    r"vazaof[^/]*\.csv",
    r"vazaoaf[^/]*\.csv",
    r"vazaob[^/]*\.csv",
    r"vazaos[^/]*\.csv",
    r"vazthd[^/]*\.dat",
    r"vazinat[^/]*\.dat",
    r"ventos[^/]*\.dat",
    r"vento[^/]*\.csv",
    r"eolicaf[^/]*\.dat",
    r"eolicab[^/]*\.dat",
    r"eolicas[^/]*\.dat",
    r"eolp[^/]*\.dat",
    r"eolf[^/]*\.csv",
    r"eolb[^/]*\.csv",
    r"eolp[^/]*\.csv",
    r"eols[^/]*\.csv",
)

_CUT_NAMES: tuple[str, ...] = ("arquivos-nwlistcf.dat", "nwlistcf.rel")
_CUT_PATTERNS: tuple[str, ...] = (r"cortes-[0-9]*[^/]*\.dat",)

_STATE_NAMES: tuple[str, ...] = ("cortese.dat", "estados.rel")
_STATE_PATTERNS: tuple[str, ...] = (r"cortese-[0-9]*[^/]*\.dat",)

_SIMULATION_NAMES: tuple[str, ...] = (
    "planej.dat",
    "daduhe.dat",
    "nwdant.dat",
    "saida.rel",
)

# v1's _upload_outputs: every *.dat no archive or raw entry claimed.
_RESIDUAL_PATTERN = r"[^/]+\.dat"

# v1 cleanup names that are never archived; nwlistcf.dat is also the
# one name the residual *.dat upload would otherwise pick up.
_DISCARD_PATTERNS: tuple[str, ...] = (r"svc[^/]*", r"fort\.[^/]*")
_DISCARD_NAMES: tuple[str, ...] = ("nwlistcf.dat",)


def _v1(pattern: str) -> str:
    return rf"(?:(?:out|evaporacao|fpha|log)/)?(?:{pattern})"


def _lit(name: str) -> str:
    return _v1(re.escape(name))


def _patterns(
    names: Iterable[str | None], patterns: Iterable[str] = ()
) -> tuple[str, ...]:
    return (
        *(_lit(name) for name in names if name is not None),
        *(_v1(pattern) for pattern in patterns),
    )


def _select(
    names: Iterable[str | None], patterns: Iterable[str] = ()
) -> Selector:
    return Selector(patterns=_patterns(names, patterns), recursive=True)


def _group(
    archive: str, names: Iterable[str | None], patterns: Iterable[str] = ()
) -> ZipGroup:
    return ZipGroup(archive, _select(names, patterns), Flat())


def output_plan(ws: Workspace) -> OutputPlan:
    arq = deck.arquivos(ws)
    groups = (
        _group("operacao.zip", _OPERATION_NAMES, _OPERATION_PATTERNS),
        _group(
            "relatorios.zip",
            (arq.pmo, arq.parp, arq.dados_simulacao_final, *_REPORT_NAMES),
            _REPORT_PATTERNS,
        ),
        _group("recursos.zip", _RESOURCE_NAMES, _RESOURCE_PATTERNS),
        _group(
            "cortes.zip", (arq.cortesh, arq.cortes, *_CUT_NAMES), _CUT_PATTERNS
        ),
        _group("estados.zip", _STATE_NAMES, _STATE_PATTERNS),
        _group(
            "simulacao.zip",
            (arq.forward, arq.forwardh, arq.newdesp, *_SIMULATION_NAMES),
        ),
    )
    raw = (
        *(
            RawFile(_select((name,)))
            for name in (_TIM_NAME, arq.pmo, arq.dados_simulacao_final)
            if name is not None
        ),
        RawFile(_select((), (_RESIDUAL_PATTERN,)), residual=True),
    )
    return OutputPlan(
        deck_inputs=deck.input_files(ws),
        groups=groups,
        raw=raw,
        discard=_patterns(_DISCARD_NAMES, _DISCARD_PATTERNS),
    )
