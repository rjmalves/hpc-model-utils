"""R19/R21/O14/C8: fail-closed inewave deck readers and the two deck
writers (ticket-046).

inewave's ``SectionFile.read`` does not raise on a missing path: it
silently parses the path string itself as file content. Every reader
here therefore checks ``Path.is_file()`` before ever calling into
inewave, and inewave itself is imported lazily, inside each function,
so importing this module -- and therefore ``hpc_model_utils.models``
-- never imports inewave (AC1).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from hpc_model_utils.core.errors import DataError
from hpc_model_utils.core.state import StudyInfo
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.models.indices import read_index_libraries

if TYPE_CHECKING:
    # Imported from each concrete submodule, never from ``inewave.newave``
    # itself: that package re-exports every class through a PEP 562
    # ``__getattr__`` typed ``-> Any``, which would make every reader
    # below silently untyped under ``mypy --strict``.
    from inewave.newave.arquivos import Arquivos
    from inewave.newave.caso import Caso
    from inewave.newave.dger import Dger

_CASO_NAME = "caso.dat"
_INDICES_NAME = "indices.csv"

_FIXED_INPUT_NAMES: tuple[str, ...] = (
    "hidr.dat",
    "postos.dat",
    "vazoes.dat",
    "selcor.dat",
    "dbgcortes.dat",
)

# R16/the ticket's Decision (Option B): bid/itaipu/elnino/ensoaux join
# the structural arquivos.dat attributes so deck_processado.zip stays
# runnable; cdefvar is deliberately absent (not referenced by
# arquivos.dat, so no Arquivos attribute maps to it).
_ARQUIVOS_ATTRS: tuple[str, ...] = (
    "adterm",
    "agrint",
    "c_adic",
    "cvar",
    "sar",
    "clast",
    "confhd",
    "conft",
    "curva",
    "dger",
    "dsvagua",
    "vazpast",
    "exph",
    "expt",
    "ghmin",
    "gtminpat",
    "perda",
    "manutt",
    "modif",
    "patamar",
    "penalid",
    "shist",
    "sistema",
    "term",
    "tecno",
    "re",
    "ree",
    "clasgas",
    "abertura",
    "gee",
    "cortesh_pos_estudo",
    "cortes_pos_estudo",
    "volume_referencia_sazonal",
    "eliminacao_cortes",
    "bid",
    "itaipu",
    "elnino",
    "ensoaux",
)


def caso(ws: Workspace) -> Caso:
    path = ws.root / _CASO_NAME
    if not path.is_file():
        raise DataError(f"missing {_CASO_NAME}")
    from inewave.newave.caso import Caso

    return cast(Caso, Caso.read(str(path)))


def index_name(ws: Workspace) -> str:
    name = (caso(ws).arquivos or "").strip()
    if not name:
        raise DataError(f"{_CASO_NAME}: empty index name")
    return name


def arquivos(ws: Workspace) -> Arquivos:
    name = index_name(ws)
    path = ws.root / name
    if not path.is_file():
        raise DataError(f"missing {name}")
    from inewave.newave.arquivos import Arquivos

    return cast(Arquivos, Arquivos.read(str(path)))


def dger_name(ws: Workspace) -> str:
    name = arquivos(ws).dger
    if not name:
        raise DataError(f"{index_name(ws)}: missing dger entry")
    return name


def dger(ws: Workspace) -> Dger:
    name = dger_name(ws)
    path = ws.root / name
    if not path.is_file():
        raise DataError(f"missing {name}")
    from inewave.newave.dger import Dger

    return cast(Dger, Dger.read(str(path)))


def study_info(ws: Workspace) -> StudyInfo:
    info = dger(ws)
    year = info.ano_inicio_estudo
    month = info.mes_inicio_estudo
    if not year or not month:
        raise DataError(f"{dger_name(ws)}: missing study year or month")
    starting_date = datetime(year, month, 1, tzinfo=UTC).isoformat()
    return StudyInfo(name=info.nome_caso or "", starting_date=starting_date)


def set_process_manager(ws: Workspace) -> None:
    record = caso(ws)
    record.gerenciador_processos = f"{ws.assets}/"
    record.write(str(ws.root / _CASO_NAME))


def set_title(ws: Workspace, name: str) -> None:
    target = dger_name(ws)
    info = dger(ws)
    info.nome_caso = name[:80]
    info.write(str(ws.root / target))


def input_files(ws: Workspace) -> tuple[str, ...]:
    arq = arquivos(ws)
    names: list[str | None] = [_CASO_NAME, index_name(ws)]
    names.extend(getattr(arq, attr) for attr in _ARQUIVOS_ATTRS)
    names.extend(_FIXED_INPUT_NAMES)
    indices_path = ws.root / _INDICES_NAME
    if indices_path.is_file():
        names.append(_INDICES_NAME)
        names.extend(read_index_libraries(indices_path))
    return tuple(dict.fromkeys(name for name in names if name is not None))
