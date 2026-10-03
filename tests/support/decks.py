"""ADR-025/R50/R80/R103: synthesize deck workspaces from vendored fixtures.

Every builder is pure with respect to `tests/fixtures/`: it reads the
vendored decks and never opens anything under that tree for writing. All
variants (stale outputs, extra files, dadger swaps) are materialized fresh
under the caller's `tmp_path`.
"""

from __future__ import annotations

import json
import shutil
import struct
import zipfile
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from hpc_model_utils.core.workspace import Workspace

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
GOLDENS = Path(__file__).resolve().parents[1] / "goldens" / "v1_1_2"

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


def archive_golden(model: Literal["newave", "decomp"]) -> dict[str, Any]:
    """The frozen ``archives.json`` entry for ``model`` (ADR-048)."""
    data = json.loads((GOLDENS / "archives.json").read_text(encoding="ascii"))
    entry = data[model]
    if not isinstance(entry, dict):
        raise TypeError(f"archives.json[{model!r}] is not an object")
    return entry


def synthetic_outputs(model: Literal["newave", "decomp"]) -> tuple[str, ...]:
    names = archive_golden(model)["synthetic_outputs"]
    if not isinstance(names, list) or not all(
        isinstance(name, str) for name in names
    ):
        raise TypeError(
            f"archives.json[{model!r}].synthetic_outputs is not a string list"
        )
    return tuple(names)


def _lowercase_top_level_files(root: Path) -> None:
    """Stand in for the CEPEL name-normalizer binaries, as the v1.1.2
    archive capture did: every top-level file with uppercase characters
    is renamed to its lowercase name, validated in full before any rename.
    """
    existing = {entry.name for entry in root.iterdir() if entry.is_file()}
    renames: dict[str, str] = {}
    for name in sorted(existing):
        target = name.lower()
        if target == name:
            continue
        if target in existing or target in renames.values():
            raise ValueError(
                f"lowercasing {name!r} collides on {target!r} in {root}"
            )
        renames[name] = target
    for name, target in renames.items():
        (root / name).rename(root / target)


def golden_archive_workspace(
    tmp_path: Path, model: Literal["newave", "decomp"]
) -> Workspace:
    """Reproduce the v1.1.2 archive-capture workspace (ADR-048) without
    importing ``tests/goldens/v1_1_2/capture.py``.

    In the capture's order: the fixture deck is copied byte for byte to
    ``eco_deck.zip``, extracted, its uppercase top-level names are
    lowercased, then every ``synthetic_outputs`` entry is written as
    ``b"x\\n"``.
    """
    root = tmp_path / "ws"
    root.mkdir(parents=True, exist_ok=True)
    ws = Workspace.at(root)
    deck_zip = _require_fixture(FIXTURES / "decks" / f"deck_{model}.zip")
    shutil.copyfile(deck_zip, ws.eco_deck_path)
    _extract_deck(deck_zip, root)
    _lowercase_top_level_files(root)
    for name in synthetic_outputs(model):
        _validate_member_name(name)
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x\n")
    ws.assets.mkdir(parents=True, exist_ok=True)
    return ws


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
