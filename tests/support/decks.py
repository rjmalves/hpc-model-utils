"""ADR-025/R50/R80/R103: synthesize deck workspaces from vendored fixtures.

Every builder is pure with respect to `tests/fixtures/`: it reads the
vendored decks and never opens anything under that tree for writing. All
variants (stale outputs, extra files, dadger swaps) are materialized fresh
under the caller's `tmp_path`.
"""

from __future__ import annotations

import struct
import zipfile
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from hpc_model_utils.core.workspace import Workspace

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

STALE_NEWAVE: tuple[str, ...] = (
    "pmo.dat",
    "parp.dat",
    "newave.tim",
    "metadata.modelops",
    "status.modelops",
)
STALE_DECOMP: tuple[str, ...] = (
    "relato.rv0",
    "inviab_unic.rv0",
    "inviab.rv0",
    "sumario.rv0",
    "metadata.modelops",
    "status.modelops",
)

_RELATO_PATTERNS: dict[str, str] = {
    "max_iterations": "CONVERGENCIA NAO ALCANCADA EM",
    "negative_gap": "ATENCAO: GAP NEGATIVO",
    "data_error": "ERRO(S) DE ENTRADA DE DADOS",
}


@dataclass(frozen=True)
class DeckWorkspace:
    ws: Workspace
    members: tuple[str, ...]


def _require_fixture(path: Path) -> Path:
    if not path.is_file():
        raise FileNotFoundError(f"missing fixture: {path}")
    return path


def _extract_deck(zip_path: Path, root: Path) -> tuple[str, ...]:
    _require_fixture(zip_path)
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(root)
        return tuple(
            sorted(
                name for name in archive.namelist() if not name.endswith("/")
            )
        )


def _validate_member_name(name: str) -> None:
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe extra file name: {name}")


def _write_stale_outputs(root: Path, stale_outputs: Sequence[str]) -> None:
    for name in stale_outputs:
        (root / name).write_bytes(b"STALE " + name.encode())


def _write_extra_files(
    root: Path, extra_files: Mapping[str, bytes] | None
) -> None:
    for name, content in (extra_files or {}).items():
        _validate_member_name(name)
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def newave_workspace(
    tmp_path: Path,
    *,
    stale_outputs: Sequence[str] = (),
    extra_files: Mapping[str, bytes] | None = None,
) -> DeckWorkspace:
    root = tmp_path / "ws"
    members = _extract_deck(FIXTURES / "decks" / "deck_newave.zip", root)
    _write_stale_outputs(root, stale_outputs)
    _write_extra_files(root, extra_files)
    (root / "assets").mkdir(parents=True, exist_ok=True)
    return DeckWorkspace(Workspace.at(root), members)


def decomp_workspace(
    tmp_path: Path,
    *,
    dadger: Literal["deck", "flexibilizador"] = "deck",
    stale_outputs: Sequence[str] = (),
    extra_files: Mapping[str, bytes] | None = None,
) -> DeckWorkspace:
    root = tmp_path / "ws"
    members = _extract_deck(FIXTURES / "decks" / "deck_decomp.zip", root)
    if dadger == "flexibilizador":
        flex_path = _require_fixture(FIXTURES / "flexibilizador" / "dadger.rv0")
        (root / "dadger.rv0").write_bytes(flex_path.read_bytes())
    _write_stale_outputs(root, stale_outputs)
    _write_extra_files(root, extra_files)
    (root / "assets").mkdir(parents=True, exist_ok=True)
    return DeckWorkspace(Workspace.at(root), members)


def input_zip(
    files: Mapping[str, bytes], dest: Path, *, root: str | None = None
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w") as archive:
        for name, content in files.items():
            member_name = name if root is None else f"{root}/{name}"
            archive.writestr(zipfile.ZipInfo(member_name), content)
    return dest


def binary_cut_files(names: Sequence[str]) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    for name in names:
        seed = zlib.crc32(name.encode("utf-8"))
        values = tuple(float((seed >> (4 * i)) & 0xF) for i in range(8))
        payloads[name] = struct.pack("<8d", *values)
    return payloads


def relato_excerpt(
    kind: Literal["converged", "max_iterations", "negative_gap", "data_error"],
) -> bytes:
    """Encoded as Latin-1, matching the legacy DECOMP relato output encoding."""
    if kind == "converged":
        text = "RELATORIO DE EXECUCAO DO DECOMP\nCONVERGENCIA ALCANCADA\n"
    else:
        text = f"RELATORIO DE EXECUCAO DO DECOMP\n{_RELATO_PATTERNS[kind]}\n"
    return text.encode("latin-1")
