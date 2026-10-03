"""R20/D1: fail-closed idecomp deck readers for DECOMP (ticket-050).

idecomp's ``SectionFile.read``/``RegisterFile.read`` do not raise on a
missing path: they silently parse the path string itself as file
content. Every reader here therefore checks ``Path.is_file()`` before
ever calling into idecomp, and idecomp itself is imported lazily,
inside each function, so importing this module -- and therefore
``hpc_model_utils.models`` -- never imports idecomp (AC1).

``dadger`` is read-only here (R20/D1 forbid the CLI from ever writing
it); ``prepare``'s FC cut coupling is ticket-050a's concern.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from hpc_model_utils.core.errors import DataError
from hpc_model_utils.core.state import StudyInfo
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.models.indices import read_index_libraries

if TYPE_CHECKING:
    # Imported from each concrete submodule, never from ``idecomp.decomp``
    # itself: that package re-exports every class through a PEP 562
    # ``__getattr__`` typed ``-> Any``, which would make every reader
    # below silently untyped under ``mypy --strict``.
    from idecomp.decomp.arquivos import Arquivos
    from idecomp.decomp.caso import Caso
    from idecomp.decomp.dadger import Dadger

_CASO_NAME = "caso.dat"

_ARQUIVOS_ATTRS: tuple[str, ...] = (
    "dadger",
    "dadgnl",
    "vazoes",
    "mlt",
    "hidr",
    "perdas",
)


def caso(ws: Workspace) -> Caso:
    path = ws.root / _CASO_NAME
    if not path.is_file():
        raise DataError(f"missing {_CASO_NAME}")
    from idecomp.decomp.caso import Caso

    return cast(Caso, Caso.read(str(path)))


def extension(ws: Workspace) -> str:
    name = (caso(ws).arquivos or "").strip()
    if not name:
        raise DataError(f"{_CASO_NAME}: empty extension")
    return name


def arquivos(ws: Workspace) -> Arquivos:
    name = extension(ws)
    path = ws.root / name
    if not path.is_file():
        raise DataError(f"missing {name}")
    from idecomp.decomp.arquivos import Arquivos

    return cast(Arquivos, Arquivos.read(str(path)))


def dadger_name(ws: Workspace) -> str:
    name = arquivos(ws).dadger
    if not name:
        raise DataError(f"{extension(ws)}: missing dadger entry")
    return name


def dadger(ws: Workspace) -> Dadger:
    name = dadger_name(ws)
    path = ws.root / name
    if not path.is_file():
        raise DataError(f"missing {name}")
    from idecomp.decomp.dadger import Dadger

    return cast(Dadger, Dadger.read(str(path)))


def study_info(ws: Workspace) -> StudyInfo:
    info = dadger(ws)
    te = info.te
    if te is None:
        raise DataError(f"{dadger_name(ws)}: missing TE register")
    dt = info.dt
    if dt is None:
        raise DataError(f"{dadger_name(ws)}: missing DT register")
    year, month, day = dt.ano, dt.mes, dt.dia
    if not year or not month or not day:
        raise DataError(f"{dadger_name(ws)}: incomplete DT date")
    starting_date = datetime(year, month, day, tzinfo=UTC).isoformat()
    return StudyInfo(name=te.titulo or "", starting_date=starting_date)


def input_files(ws: Workspace) -> tuple[str, ...]:
    arq = arquivos(ws)
    info = dadger(ws)
    names: list[str | None] = [_CASO_NAME, extension(ws)]
    names.extend(getattr(arq, attr) for attr in _ARQUIVOS_ATTRS)

    fa, fj, vt = info.fa, info.fj, info.vt
    if fa is not None:
        names.append(fa.arquivo)
    if fj is not None:
        names.append(fj.arquivo)
    if vt is not None:
        names.append(vt.arquivo)

    if fa is not None and fa.arquivo is not None:
        fa_path = ws.root / fa.arquivo
        if not fa_path.is_file():
            raise DataError(f"missing {fa.arquivo}")
        names.extend(read_index_libraries(fa_path))

    return tuple(dict.fromkeys(n for n in names if n is not None))
