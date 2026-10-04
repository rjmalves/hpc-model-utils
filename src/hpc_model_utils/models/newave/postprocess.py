"""ADR-053/ADR-003/R12/R42/R137: NEWAVE nwlistcf/nwlistop postprocessing
(ticket-048a).

Ports v1's ``_run_nwlistcf``/``_run_nwlistop`` control-file generators
and invocation sequence (``app/adapter/repository/newave.py:416-555``).
All four invocations (nwlistcf options 1/2, nwlistop options 2/4) are
attempted regardless of earlier failures; every failure is collected
into one ``PostprocessError`` rather than short-circuiting the rest,
and no known-benign message is tolerated (Requirement 6).
"""

from __future__ import annotations

import logging
import os

from hpc_model_utils.core.errors import DataError
from hpc_model_utils.core.plugin import PostprocessError
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.infra.errors import ShellCommandError
from hpc_model_utils.infra.shell import run as shell_run
from hpc_model_utils.models.newave import deck

logger = logging.getLogger(__name__)


def _nwlistcf_arquivos_dat(month: int) -> str:
    return "".join(
        (
            "ARQUIVO DE DADOS GERAIS     : nwlistcf.dat\n",
            f"ARQUIVO DE CORTES DE BENDERS: cortes-{month:03d}.dat\n",
            "ARQUIVO DE CABECALHO CORTES : cortesh.dat\n",
            "ARQUIVO P/DESPACHO HIDROTERM: newdesp.dat\n",
            f"ARQUIVO DE ESTADOS CORTES   : cortese-{month:03d}.dat\n",
            "ARQUIVO DE ENERGIAS FORWARD : energiaf.dat\n",
            "ARQUIVO DE RESTRICOES SAR   : rsar.dat\n",
            "ARQUIVO DE CABECALHO SAR    : rsarh.dat\n",
            "ARQUIVO DE INDICE SAR       : rsari.dat\n",
            "ARQUIVO LISTAGEM CORTES     : nwlistcf.rel\n",
            "ARQUIVO LISTAGEM ESTADOS FCF: estados.rel\n",
            "ARQUIVO LISTAGEM SAR        : rsar.rel\n",
            "ARQUIVO DE ENERGIAS X FORW  : energiaxf.dat\n",
            "ARQUIVO DE VAZAO FORWARD    : vazaof.dat\n",
            "ARQUIVO DE VAZAO X FORWARD  : vazaoxf.dat\n",
        )
    )


def _nwlistcf_dat(month: int, option: int) -> str:
    return "".join(
        (
            " INI FIM FC (FC = 1: IMPRIME TODOS CORTES, FC = 0: IMPRIME APENAS CORTES VALIDOS NA ULTIMA ITERACAO)\n",
            " XXX XXX X\n",
            f"  {month:02d}  {month:02d} 1\n",
            " OPCOES DE IMPRESSAO : 01 - CORTES FCF  02 - ESTADOS FCF  03 - RESTRICAO SAR\n",
            " XX XX XX (SE 99 CONSIDERA TODAS)\n",
            f" {option:02d}\n",
        )
    )


def _nwlistop_dat(option: int, initial: int, final: int) -> str:
    return "".join(
        (
            f" {option}\n",
            "FORWARD  (ARQ. DE DADOS)    : forward.dat\n",
            "FORWARDH (ARQ. CABECALHOS)  : forwarh.dat\n",
            "NEWDESP  (REL. CONFIGS)     : newdesp.dat\n",
            "-----------------------------------------\n",
            " XXX XXX    PERIODOS INICIAL E FINAL\n",
            f" {initial:03d} {final:03d}\n",
            " 1-CMO           2-DEFICITS         3-ENA CONTROL.   4-EARM FINAL       5-ENA FIO BRUTA 6-EVAPORACAO    7-VERTIMENTO\n",
            " 8-VAZAO MIN.    9-GER.HIDR.CONT   10-GER. TERMICA  11-INTERCAMBIOS    12-MERC.LIQ.    13-VALOR AGUA   14-VOLUME MORTO\n",
            "15-EXCESSO      16-GHMAX           17-OUTROS USOS   18-BENEF.INT/AGR   19-F.CORR.EC    20-GHTOTAL      21-ENA BRUTA\n",
            "22-ACOPLAMENTO  23-INVASAO CG      24-PENAL.INV.CG. 25-ACIONAMENTO MAR 26-COPER        27-CTERM        28-CDEFICIT\n",
            "29-GER.FIO LIQ. 30-PERDA FIO       31-ENA FIO LIQ.  32- BENEF. GNL     33-VIOL.GHMIN   34-PERDAS       37-GEE             38-SOMA AFL.PAS.\n",
            " XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX (SE 99 CONSIDERA TODAS)\n",
            " 99\n",
            "-----------------------------------------------------------------------------------------------------------------------\n",
            " 1-VOL.ARMAZ       2-GER.HID         3-VOL.TURB.     4-VOL. VERT.      5-VIOL.GHMIN    6-ENCH.MORTO   7-FOLGA DEPMIN.\n",
            " 8-DESV. AGUA      9-DESV. POS.      10-DESVIO NEG.  11-FOLGA FPGHA   12-VAZAO AFL.  13-VAZAO INCREM. 14-VARM PCT.\n",
            " XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX XX (SE 99 CONSIDERA TODAS)\n",
            " 99\n",
            " XXX XXX XXX XXX XXX XXX XXX XXX XXX XXX XXX XXX XXX XXX XXX XXX  (SE 999 CONSIDERA TODAS AS USINAS)\n",
            " 999\n",
        )
    )


def _invoke(
    ws: Workspace, program: str, option: int, timeout: float
) -> str | None:
    try:
        result = shell_run(
            [str(ws.assets / program)],
            cwd=ws.root,
            timeout=timeout,
            on_line=logger.info,
        )
    except ShellCommandError as exc:
        cause = exc.__cause__
        strerror = cause.strerror if isinstance(cause, OSError) else None
        return (
            f"{program} could not be executed: {strerror or 'not executable'}"
        )
    if result.timed_out:
        return f"{program} option {option} timed out after {timeout:g}s"
    if result.returncode != 0:
        return f"{program} option {option} exited {result.returncode}"
    return None


def _run_nwlistcf(
    ws: Workspace, index: str, start_month: int | None, timeout: float
) -> list[str]:
    if start_month is None:
        return ["nwlistcf: dger.dat has no study start month"]
    month = start_month + 1
    root = ws.root
    bkp = root / "arquivos.dat.bkp"
    arquivos_dat = root / "arquivos.dat"
    os.replace(root / index, bkp)
    failures: list[str] = []
    try:
        arquivos_dat.write_text(_nwlistcf_arquivos_dat(month), encoding="utf-8")
        for option in (1, 2):
            (root / "nwlistcf.dat").write_text(
                _nwlistcf_dat(month, option), encoding="utf-8"
            )
            failure = _invoke(ws, "nwlistcf", option, timeout)
            if failure is not None:
                failures.append(failure)
    finally:
        if arquivos_dat.exists():
            os.replace(arquivos_dat, root / "arquivos-nwlistcf.dat")
        os.replace(bkp, root / index)
    return failures


def _run_nwlistop(
    ws: Workspace,
    study_years: int | None,
    start_month: int | None,
    pre_years: int | None,
    post_years: int | None,
    timeout: float,
) -> list[str]:
    if (
        study_years is None
        or start_month is None
        or pre_years is None
        or post_years is None
    ):
        return ["nwlistop: dger.dat lacks the study horizon fields"]
    initial = pre_years * 12 + 1
    final = study_years * 12 + post_years * 12 - (start_month - 1)
    failures: list[str] = []
    for option in (2, 4):
        (ws.root / "nwlistop.dat").write_text(
            _nwlistop_dat(option, initial, final), encoding="utf-8"
        )
        failure = _invoke(ws, "nwlistop", option, timeout)
        if failure is not None:
            failures.append(failure)
    return failures


def run_postprocess(ws: Workspace, *, timeout: float = 3600.0) -> None:
    try:
        index = deck.index_name(ws)
        info = deck.dger(ws)
    except DataError as exc:
        raise PostprocessError(f"deck unreadable: {exc.message}") from exc

    failures = _run_nwlistcf(ws, index, info.mes_inicio_estudo, timeout)
    failures += _run_nwlistop(
        ws,
        info.num_anos_estudo,
        info.mes_inicio_estudo,
        info.num_anos_pre_estudo,
        info.num_anos_pos_sim_final,
        timeout,
    )
    if failures:
        raise PostprocessError("; ".join(failures))
