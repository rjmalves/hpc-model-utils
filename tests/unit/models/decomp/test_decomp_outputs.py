"""ADR-040/ADR-048/R16/R17/R55 tests for the DECOMP ``OutputPlan``
(ticket-052), checked against the frozen v1.1.2 archive golden."""

from __future__ import annotations

import re
import zipfile
from collections.abc import Mapping
from pathlib import Path

import pytest

from hpc_model_utils.core.errors import DataError
from hpc_model_utils.core.outputs import (
    Flat,
    OutputPlan,
    RealizedOutputs,
    Selector,
    realize,
)
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.infra.errors import UnsafeArchiveError
from hpc_model_utils.models.decomp import DecompPlugin, cuts, deck
from hpc_model_utils.models.decomp.outputs import output_plan
from tests.support.decks import (
    archive_golden,
    binary_cut_files,
    golden_archive_workspace,
)

_GROUP_ARCHIVES: tuple[str, ...] = (
    "operacao.zip",
    "relatorios.zip",
    "cortes.zip",
)
_DECK_ARCHIVE = "deck_processado.zip"
_REALIZED_ARCHIVES: tuple[str, ...] = (*_GROUP_ARCHIVES, _DECK_ARCHIVE)
_RELGNL = "relgnl.rv0"
# The README's ``archives.decomp.uploads`` (C6, R17) intended difference.
_EXPECTED_RAW_DESTS = frozenset(
    {
        "decomp.tim",
        "inviab.rv0",
        "inviab_unic.rv0",
        "relato.rv0",
        "sumario.rv0",
        _RELGNL,
    }
)
_V1_PREFIX = "(?:out/)?(?:"
_BARE_DOT = re.compile(r"(?<!\\)\.")
_SINTESE_BYTES = b"sintese copy\n"


def _realize(ws: Workspace) -> RealizedOutputs:
    return realize(output_plan(ws), ws, workers=2)


def _golden_archive(name: str) -> list[str]:
    members = archive_golden("decomp")["archives"][name]
    assert isinstance(members, list)
    return [str(member) for member in members]


def _namelist(ws: Workspace, archive: str) -> list[str]:
    with zipfile.ZipFile(ws.outputs_dir / archive) as zf:
        return sorted(zf.namelist())


def _all_members(ws: Workspace) -> dict[str, list[bytes]]:
    members: dict[str, list[bytes]] = {}
    for path in sorted(ws.outputs_dir.glob("*.zip")):
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                members.setdefault(name, []).append(zf.read(name))
    return members


def _write(ws: Workspace, files: Mapping[str, bytes]) -> None:
    for rel, content in files.items():
        target = ws.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _selectors(plan: OutputPlan) -> list[Selector]:
    return [group.select for group in plan.groups] + [
        entry.select for entry in plan.raw
    ]


def _workspace_files(ws: Workspace) -> dict[str, bytes]:
    return {
        path.relative_to(ws.root).as_posix(): path.read_bytes()
        for path in ws.root.rglob("*")
        if path.is_file() and ws.hpcmu_dir not in path.parents
    }


def _to_rv3(name: str) -> str:
    if name == "rv0":
        return "rv3"
    if name.endswith(".rv0"):
        return name.removesuffix(".rv0") + ".rv3"
    return name


def _convert_to_rv3(ws: Workspace) -> None:
    """Rewrite ``caso.dat`` and the arquivos index to extension ``rv3``
    and rename every top-level ``rv0``-suffixed file to match."""
    caso_path = ws.root / "caso.dat"
    caso_text = caso_path.read_text(encoding="latin-1")
    assert caso_text.startswith("rv0\n")
    caso_path.write_text("rv3" + caso_text[3:], encoding="latin-1")
    index_text = (ws.root / "rv0").read_text(encoding="latin-1")
    assert ".rv0" in index_text
    (ws.root / "rv3").write_text(
        index_text.replace(".rv0", ".rv3"), encoding="latin-1"
    )
    (ws.root / "rv0").unlink()
    for path in sorted(ws.root.iterdir()):
        if path.is_file() and path.name.endswith(".rv0"):
            path.rename(ws.root / _to_rv3(path.name))


# -- AC1: golden archives (R16, R90 shape) ---------------------------------


@pytest.mark.parametrize("archive", _REALIZED_ARCHIVES)
def test_realize_golden_workspace_archive_equals_v1_golden(
    tmp_path: Path, archive: str
) -> None:
    ws = golden_archive_workspace(tmp_path, "decomp")

    result = _realize(ws)

    assert f".hpcmu/outputs/{archive}" in (*result.archives, result.deck)
    assert _namelist(ws, archive) == _golden_archive(archive)
    assert all("/" not in name for name in _namelist(ws, archive))


def test_realize_golden_workspace_writes_exactly_the_three_group_archives(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "decomp")

    result = _realize(ws)

    v1_archives = set(archive_golden("decomp")["archives"]) - {"eco_deck.zip"}
    assert v1_archives == set(_REALIZED_ARCHIVES)
    assert result.archives == tuple(
        sorted(f".hpcmu/outputs/{name}" for name in _GROUP_ARCHIVES)
    )
    assert result.deck == f".hpcmu/outputs/{_DECK_ARCHIVE}"


# -- AC2: raw set (C6) and the dadger echo (Decision A) ---------------------


def test_realize_golden_workspace_raw_dests_equal_v1_uploads_plus_relgnl(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "decomp")

    result = _realize(ws)

    v1_raw = {
        key.removeprefix("saidas/")
        for key in archive_golden("decomp")["uploads"]
        if key.startswith("saidas/") and not key.endswith((".zip", ".modelops"))
    }
    assert v1_raw == _EXPECTED_RAW_DESTS - {_RELGNL}
    assert {dest for _, dest in result.raw} == _EXPECTED_RAW_DESTS
    assert set(result.raw) == {(dest, dest) for dest in _EXPECTED_RAW_DESTS}


def test_realize_relgnl_absent_raw_dests_lose_only_relgnl(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "decomp")
    present = {dest for _, dest in _realize(ws).raw}
    (ws.root / _RELGNL).unlink()

    absent = {dest for _, dest in _realize(ws).raw}

    assert present - absent == {_RELGNL}
    assert absent == _EXPECTED_RAW_DESTS - {_RELGNL}
    assert _namelist(ws, "relatorios.zip") == sorted(
        set(_golden_archive("relatorios.zip")) - {_RELGNL}
    )


def test_realize_golden_workspace_no_realized_field_contains_dadger(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "decomp")

    result = _realize(ws)

    assert result.deck is not None
    assert "dadger" not in result.deck
    assert all("dadger" not in path for path in result.archives)
    assert all(
        "dadger" not in source and "dadger" not in dest
        for source, dest in result.raw
    )


def test_realize_golden_workspace_dadger_echo_inside_both_deck_archives(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "decomp")
    dadger = deck.dadger_name(ws)

    _realize(ws)

    v1_entradas = {
        key
        for key in archive_golden("decomp")["uploads"]
        if key.startswith("entradas/")
    }
    assert v1_entradas - {f"entradas/{dadger}"} == {
        "entradas/eco_deck.zip",
        f"entradas/{_DECK_ARCHIVE}",
    }
    assert dadger in _namelist(ws, _DECK_ARCHIVE)
    with zipfile.ZipFile(ws.eco_deck_path) as zf:
        assert dadger in zf.namelist()


def test_realize_golden_workspace_leaves_workspace_files_unchanged(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "decomp")
    before = _workspace_files(ws)

    _realize(ws)

    assert _workspace_files(ws) == before


def test_realize_fc_cut_files_present_never_archived_or_raw(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "decomp")
    header, cut = cuts.read_fc(deck.dadger(ws), deck.dadger_name(ws))
    assert header is not None
    assert cut is not None
    _write(ws, binary_cut_files((header, cut)))

    plan = output_plan(ws)
    result = realize(plan, ws, workers=2)

    assert {header, cut}.isdisjoint(plan.deck_inputs)
    assert _namelist(ws, _DECK_ARCHIVE) == _golden_archive(_DECK_ARCHIVE)
    assert {header, cut}.isdisjoint(_all_members(ws))
    assert {dest for _, dest in result.raw} == _EXPECTED_RAW_DESTS


# -- AC3: selection boundary -----------------------------------------------


def test_realize_out_file_selected_deep_and_sintese_files_never_selected(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "decomp")
    assert (ws.root / "out" / "relato_extra.rv0").is_file()
    _write(
        ws,
        {
            "out/dec_oper_x.csv": b"x\n",
            "deep/out/dec_oper_y.csv": b"y\n",
            "sintese/relato.rv0": _SINTESE_BYTES,
        },
    )

    result = _realize(ws)

    members = _all_members(ws)
    assert _namelist(ws, "operacao.zip") == sorted(
        [*_golden_archive("operacao.zip"), "dec_oper_x.csv"]
    )
    assert "dec_oper_y.csv" not in members
    assert all(_SINTESE_BYTES not in contents for contents in members.values())
    assert all(not source.startswith("sintese/") for source, _ in result.raw)
    assert "relato_extra.rv0" not in members
    assert all("relato_extra" not in "".join(pair) for pair in result.raw)
    assert {dest for _, dest in result.raw} == _EXPECTED_RAW_DESTS


def test_realize_relato_at_root_and_in_out_raises_raw_destination_collision(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "decomp")
    _write(ws, {"out/relato.rv0": b"x\n"})
    plan = output_plan(ws)

    with pytest.raises(
        ValueError, match=re.escape("raw destination collision 'relato.rv0'")
    ):
        realize(plan, ws, workers=2)

    assert list(ws.outputs_dir.iterdir()) == []


def test_realize_same_basename_root_and_out_raises_unsafe_archive_error(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "decomp")
    _write(ws, {"out/cmar001.csv": b"x\n"})

    with pytest.raises(
        UnsafeArchiveError, match=re.escape("duplicate basename 'cmar001.csv'")
    ):
        _realize(ws)

    assert not (ws.outputs_dir / "operacao.zip").exists()


# -- Extension-derived names, determinism, errors, delegation ----------------


def test_output_plan_rv3_extension_names_follow_extension(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "decomp")
    _convert_to_rv3(ws)
    stray_rv0 = ("relato.rv0", "cortdeco.rv0", _RELGNL)
    _write(ws, dict.fromkeys(stray_rv0, b"x\n"))
    assert deck.extension(ws) == "rv3"

    result = _realize(ws)

    for archive in _REALIZED_ARCHIVES:
        assert _namelist(ws, archive) == sorted(
            _to_rv3(name) for name in _golden_archive(archive)
        )
    expected_raw = {_to_rv3(dest) for dest in _EXPECTED_RAW_DESTS}
    assert set(result.raw) == {(dest, dest) for dest in expected_raw}
    assert set(stray_rv0).isdisjoint(_all_members(ws))


def test_output_plan_called_twice_returns_equal_plans(tmp_path: Path) -> None:
    ws = golden_archive_workspace(tmp_path, "decomp")

    assert output_plan(ws) == output_plan(ws)


def test_output_plan_missing_caso_raises_data_error(tmp_path: Path) -> None:
    ws = Workspace.at(tmp_path)

    with pytest.raises(DataError, match="missing caso.dat"):
        output_plan(ws)


def test_output_plan_empty_extension_raises_data_error(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "decomp")
    (ws.root / "caso.dat").write_text("\n\n", encoding="latin-1")

    with pytest.raises(DataError, match=re.escape("caso.dat: empty extension")):
        output_plan(ws)


def test_decompplugin_outputs_golden_workspace_equals_output_plan(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "decomp")

    assert DecompPlugin().outputs(ws) == output_plan(ws)


# -- Pattern construction ----------------------------------------------------


def test_output_plan_every_selector_recursive_patterns_only(
    tmp_path: Path,
) -> None:
    plan = output_plan(golden_archive_workspace(tmp_path, "decomp"))

    assert [group.archive for group in plan.groups] == list(_GROUP_ARCHIVES)
    assert all(isinstance(group.layout, Flat) for group in plan.groups)
    assert isinstance(plan.deck_layout, Flat)
    for selector in _selectors(plan):
        assert selector.recursive is True
        assert selector.names == ()
        assert selector.patterns
    assert [entry.residual for entry in plan.raw] == [False] * 6
    assert all(entry.dest == "" for entry in plan.raw)
    assert plan.discard == ()


def test_output_plan_every_pattern_v1_wrapped_without_bare_dot(
    tmp_path: Path,
) -> None:
    plan = output_plan(golden_archive_workspace(tmp_path, "decomp"))

    patterns = [p for s in _selectors(plan) for p in s.patterns]
    for pattern in patterns:
        assert pattern.startswith(_V1_PREFIX)
        assert pattern.endswith(")")
        inner = pattern.removeprefix(_V1_PREFIX)[:-1]
        assert _BARE_DOT.search(inner) is None, pattern


def test_output_plan_selector_sizes_match_v1_lists(tmp_path: Path) -> None:
    plan = output_plan(golden_archive_workspace(tmp_path, "decomp"))

    sizes = {g.archive: len(g.select.patterns) for g in plan.groups}
    assert sizes == {"operacao.zip": 21, "relatorios.zip": 39, "cortes.zip": 2}
    for group in plan.groups:
        patterns = group.select.patterns
        assert len(set(patterns)) == len(patterns)
    assert [len(entry.select.patterns) for entry in plan.raw] == [1] * 6


def test_output_plan_patterns_matching_root_name_reject_other_directories(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "decomp")
    plan = output_plan(ws)
    names = {path.name for path in ws.root.rglob("*") if path.is_file()}

    matched = 0
    for selector in _selectors(plan):
        for name in names:
            if not selector.matches(name):
                continue
            matched += 1
            assert selector.matches(f"out/{name}")
            for parent in ("sintese", "out/deep", "deep/out", "outx"):
                assert not selector.matches(f"{parent}/{name}")
    assert matched > 0
