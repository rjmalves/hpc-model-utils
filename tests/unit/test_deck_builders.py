"""ADR-025/R50/R80/R103: tests/support/decks.py builder coverage."""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import re
import shutil
import zipfile
from pathlib import Path
from typing import Literal

import pytest

from tests.support import decks
from tests.support.decks import (
    STALE_DECOMP,
    STALE_NEWAVE,
    DeckWorkspace,
    archive_golden,
    binary_cut_files,
    decomp_workspace,
    golden_archive_workspace,
    input_zip,
    newave_workspace,
    relato_excerpt,
)

_PROVENANCE_PATH = decks.FIXTURES / "PROVENANCE.md"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _provenance_hashes() -> dict[str, str]:
    hashes: dict[str, str] = {}
    for line in _PROVENANCE_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [
            cell.strip().strip("`") for cell in stripped.strip("|").split("|")
        ]
        if len(cells) != 5 or not _SHA256_RE.match(cells[4]):
            continue
        hashes[cells[0]] = cells[4]
    return hashes


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def test_newave_workspace_default_creates_assets_without_hpcmu(
    tmp_path: Path,
) -> None:
    result = newave_workspace(tmp_path, stale_outputs=STALE_NEWAVE)

    root = result.ws.root
    assert (root / "caso.dat").is_file()
    assert (root / "dger.dat").is_file()
    for name in STALE_NEWAVE:
        assert (root / name).is_file()
    assert (root / "assets").is_dir()
    assert not (root / ".hpcmu").exists()


def test_newave_workspace_members_excludes_stale_and_extra_files(
    tmp_path: Path,
) -> None:
    result = newave_workspace(
        tmp_path,
        stale_outputs=STALE_NEWAVE,
        extra_files={"notes/extra.txt": b"hi"},
    )

    assert "caso.dat" in result.members
    assert "pmo.dat" not in result.members
    assert "notes/extra.txt" not in result.members
    assert result.members == tuple(sorted(result.members))


def test_newave_workspace_extra_files_writes_nested_bytes(
    tmp_path: Path,
) -> None:
    result = newave_workspace(
        tmp_path, extra_files={"nested/dir/extra.bin": b"\x00payload"}
    )

    target = result.ws.root / "nested" / "dir" / "extra.bin"
    assert target.read_bytes() == b"\x00payload"


def test_newave_workspace_extra_files_absolute_name_raises_value_error(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="unsafe extra file name"):
        newave_workspace(tmp_path, extra_files={"/abs/evil.txt": b"x"})


def test_newave_workspace_extra_files_dotdot_name_raises_value_error(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="unsafe extra file name"):
        newave_workspace(tmp_path, extra_files={"../evil.txt": b"x"})


def test_newave_workspace_missing_fixture_raises_file_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing_root = tmp_path / "no-fixtures-here"
    monkeypatch.setattr(decks, "FIXTURES", missing_root)

    expected = missing_root / "decks" / "deck_newave.zip"
    with pytest.raises(FileNotFoundError, match=re.escape(str(expected))):
        newave_workspace(tmp_path / "ws-parent")


def test_newave_workspace_extra_files_default_is_not_mutable() -> None:
    assert (
        inspect.signature(newave_workspace).parameters["extra_files"].default
        is None
    )


def test_decomp_workspace_default_keeps_deck_dadger(tmp_path: Path) -> None:
    result = decomp_workspace(tmp_path)

    content = (result.ws.root / "dadger.rv0").read_bytes()
    assert b"NEWCUT    cortes-012.dat" in content


def test_decomp_workspace_flexibilizador_swaps_dadger(tmp_path: Path) -> None:
    result = decomp_workspace(tmp_path, dadger="flexibilizador")

    content = (result.ws.root / "dadger.rv0").read_bytes()
    assert b"NEWCUT    cortes-009.dat" in content


def test_decomp_workspace_stale_outputs_contain_marker_bytes(
    tmp_path: Path,
) -> None:
    result = decomp_workspace(tmp_path, stale_outputs=STALE_DECOMP)

    for name in STALE_DECOMP:
        assert (result.ws.root / name).read_bytes() == (
            b"STALE " + name.encode()
        )


def test_decomp_workspace_missing_flexibilizador_fixture_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixtures_root = tmp_path / "fixtures-missing-flex"
    (fixtures_root / "decks").mkdir(parents=True)
    shutil.copy2(
        decks.FIXTURES / "decks" / "deck_decomp.zip",
        fixtures_root / "decks" / "deck_decomp.zip",
    )
    monkeypatch.setattr(decks, "FIXTURES", fixtures_root)

    expected = fixtures_root / "flexibilizador" / "dadger.rv0"
    with pytest.raises(FileNotFoundError, match=re.escape(str(expected))):
        decomp_workspace(tmp_path / "ws-parent", dadger="flexibilizador")


def test_decomp_workspace_extra_files_default_is_not_mutable() -> None:
    assert (
        inspect.signature(decomp_workspace).parameters["extra_files"].default
        is None
    )


def test_input_zip_root_nests_members_under_top_level_folder(
    tmp_path: Path,
) -> None:
    dest = input_zip({"config.json": b"{}"}, tmp_path / "case.zip", root="case")

    with zipfile.ZipFile(dest) as archive:
        assert archive.namelist() == ["case/config.json"]


def test_input_zip_keeps_hostile_member_names_verbatim(
    tmp_path: Path,
) -> None:
    dest = input_zip({"../evil": b"a", "/abs": b"b"}, tmp_path / "hostile.zip")

    with zipfile.ZipFile(dest) as archive:
        assert set(archive.namelist()) == {"../evil", "/abs"}


def test_input_zip_creates_missing_parent_and_returns_dest(
    tmp_path: Path,
) -> None:
    dest = tmp_path / "nested" / "case.zip"
    result = input_zip({"a.txt": b"a"}, dest)

    assert result == dest
    assert dest.is_file()


def test_binary_cut_files_each_payload_contains_nul_byte() -> None:
    payloads = binary_cut_files(["cortes-001.dat", "cortes-002.dat"])

    for payload in payloads.values():
        assert b"\x00" in payload


def test_binary_cut_files_is_deterministic_across_calls() -> None:
    names = ["cortes-001.dat", "cortes-002.dat"]
    assert binary_cut_files(names) == binary_cut_files(names)


def test_relato_excerpt_converged_has_no_failure_pattern() -> None:
    excerpt = relato_excerpt("converged")

    assert b"CONVERGENCIA NAO ALCANCADA EM" not in excerpt
    assert b"ATENCAO: GAP NEGATIVO" not in excerpt
    assert b"ERRO(S) DE ENTRADA DE DADOS" not in excerpt


def test_relato_excerpt_max_iterations_contains_pattern() -> None:
    assert b"CONVERGENCIA NAO ALCANCADA EM" in relato_excerpt("max_iterations")


def test_relato_excerpt_negative_gap_contains_pattern() -> None:
    assert b"ATENCAO: GAP NEGATIVO" in relato_excerpt("negative_gap")


def test_relato_excerpt_data_error_contains_pattern() -> None:
    assert b"ERRO(S) DE ENTRADA DE DADOS" in relato_excerpt("data_error")


@pytest.mark.parametrize("model", ["newave", "decomp"])
def test_golden_archive_workspace_each_model_writes_outputs_and_eco_deck_path(
    tmp_path: Path, model: Literal["newave", "decomp"]
) -> None:
    ws = golden_archive_workspace(tmp_path, model)

    synthetic = archive_golden(model)["synthetic_outputs"]
    assert synthetic
    for name in synthetic:
        assert (ws.root / name).read_bytes() == b"x\n"
    deck_zip = decks.FIXTURES / "decks" / f"deck_{model}.zip"
    assert ws.eco_deck_path.read_bytes() == deck_zip.read_bytes()
    assert ws.assets.is_dir()
    assert not ws.hpcmu_dir.exists()


def test_golden_archive_workspace_newave_lowercases_deck_names(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "newave")

    with zipfile.ZipFile(decks.FIXTURES / "decks" / "deck_newave.zip") as zf:
        bid_bytes = zf.read("BID.DAT")
        deck_names = set(zf.namelist())
    names = {entry.name for entry in ws.root.iterdir() if entry.is_file()}
    assert "BID.DAT" not in names
    assert (ws.root / "bid.dat").read_bytes() == bid_bytes
    assert names >= {name.lower() for name in deck_names}
    uppercase = {name for name in deck_names if name != name.lower()}
    assert uppercase
    assert uppercase.isdisjoint(names)


def test_golden_archive_workspace_lowercase_collision_raises_value_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixtures_root = tmp_path / "fixtures-collision"
    input_zip(
        {"BID.DAT": b"upper", "bid.dat": b"lower"},
        fixtures_root / "decks" / "deck_newave.zip",
    )
    monkeypatch.setattr(decks, "FIXTURES", fixtures_root)

    with pytest.raises(ValueError, match="lowercasing 'BID.DAT' collides"):
        golden_archive_workspace(tmp_path / "ws-parent", "newave")


def test_deck_workspace_is_frozen(tmp_path: Path) -> None:
    result = newave_workspace(tmp_path)

    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(result, "members", ())

    assert isinstance(result, DeckWorkspace)


def test_fixtures_unchanged_after_builders_run_matches_provenance(
    tmp_path: Path,
) -> None:
    newave_workspace(
        tmp_path / "a",
        stale_outputs=STALE_NEWAVE,
        extra_files={"extra.txt": b"x"},
    )
    decomp_workspace(
        tmp_path / "b", dadger="flexibilizador", stale_outputs=STALE_DECOMP
    )
    input_zip({"f.txt": b"x"}, tmp_path / "zips" / "input.zip", root="case")
    golden_archive_workspace(tmp_path / "c", "newave")
    golden_archive_workspace(tmp_path / "d", "decomp")

    repo_root = _PROVENANCE_PATH.parents[2]
    hashes = _provenance_hashes()
    assert len(hashes) == 7
    for rel_path, expected in hashes.items():
        assert _sha256(repo_root / rel_path) == expected
