"""ADR-040/ADR-048/R16/R17/R55/R90 tests for the NEWAVE ``OutputPlan``
(ticket-048), checked against the frozen v1.1.2 archive golden."""

from __future__ import annotations

import re
import zipfile
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
from hpc_model_utils.models.newave import NewavePlugin
from hpc_model_utils.models.newave.outputs import output_plan
from tests.support.decks import archive_golden, golden_archive_workspace

_GROUP_ARCHIVES: tuple[str, ...] = (
    "operacao.zip",
    "relatorios.zip",
    "recursos.zip",
    "cortes.zip",
    "estados.zip",
    "simulacao.zip",
)
_DECK_ARCHIVE = "deck_processado.zip"
# The README's ``archives.newave.archives.deck_processado.zip`` and
# ``archives.newave.uploads.residual_inputs`` intended differences.
_DECK_ONLY_INPUTS = frozenset(
    {"bid.dat", "elnino.dat", "ensoaux.dat", "itaipu.dat"}
)
_EXPECTED_RAW_DESTS = frozenset(
    {"newave.tim", "pmo.dat", "simfinal.dat", "cdefvar.dat", "extra_output.dat"}
)
_V1_PREFIX = "(?:(?:out|evaporacao|fpha|log)/)?(?:"
_BARE_DOT = re.compile(r"(?<!\\)\.")


def _realize(ws: Workspace) -> RealizedOutputs:
    return realize(output_plan(ws), ws, workers=2)


def _namelist(ws: Workspace, archive: str) -> list[str]:
    with zipfile.ZipFile(ws.outputs_dir / archive) as zf:
        return sorted(zf.namelist())


def _all_members(ws: Workspace) -> set[str]:
    members: set[str] = set()
    for path in ws.outputs_dir.glob("*.zip"):
        members.update(_namelist(ws, path.name))
    return members


def _write(ws: Workspace, *rel_paths: str) -> None:
    for rel in rel_paths:
        target = ws.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x\n")


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


# -- AC1: golden archives (R90) -------------------------------------------


@pytest.mark.parametrize("archive", _GROUP_ARCHIVES)
def test_realize_golden_workspace_group_archive_equals_v1_golden(
    tmp_path: Path, archive: str
) -> None:
    ws = golden_archive_workspace(tmp_path, "newave")

    result = _realize(ws)

    expected = archive_golden("newave")["archives"][archive]
    assert f".hpcmu/outputs/{archive}" in result.archives
    assert _namelist(ws, archive) == expected
    assert all("/" not in name for name in _namelist(ws, archive))


def test_realize_golden_workspace_writes_exactly_the_six_group_archives(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "newave")

    result = _realize(ws)

    assert result.archives == tuple(
        sorted(f".hpcmu/outputs/{name}" for name in _GROUP_ARCHIVES)
    )
    assert result.deck == f".hpcmu/outputs/{_DECK_ARCHIVE}"


# -- AC2: deck and raw sets ------------------------------------------------


def test_realize_golden_workspace_deck_archive_adds_four_deck_inputs(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "newave")

    _realize(ws)

    golden = archive_golden("newave")["archives"][_DECK_ARCHIVE]
    assert _DECK_ONLY_INPUTS.isdisjoint(golden)
    assert _namelist(ws, _DECK_ARCHIVE) == sorted([*golden, *_DECK_ONLY_INPUTS])
    assert all("/" not in name for name in _namelist(ws, _DECK_ARCHIVE))


def test_realize_golden_workspace_raw_dests_equal_v1_uploads_minus_inputs(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "newave")

    result = _realize(ws)

    v1_raw = {
        key.removeprefix("saidas/")
        for key in archive_golden("newave")["uploads"]
        if key.startswith("saidas/") and not key.endswith((".zip", ".modelops"))
    }
    assert v1_raw - _DECK_ONLY_INPUTS == _EXPECTED_RAW_DESTS
    assert {dest for _, dest in result.raw} == _EXPECTED_RAW_DESTS
    assert set(result.raw) == {(dest, dest) for dest in _EXPECTED_RAW_DESTS}


@pytest.mark.parametrize(
    "name", ["fort.10", "svc0001", "format.tmp", "mensag.tmp"]
)
def test_realize_golden_workspace_cleanup_only_name_never_selected(
    tmp_path: Path, name: str
) -> None:
    ws = golden_archive_workspace(tmp_path, "newave")
    assert (ws.root / name).is_file()

    result = _realize(ws)

    assert name not in _all_members(ws)
    assert all(name not in pair for pair in result.raw)


@pytest.mark.parametrize(
    "rel", ["nwlistcf.dat", "out/nwlistcf.dat", "svc0001.dat", "fort.dat"]
)
def test_realize_discarded_dat_name_never_uploaded_or_archived(
    tmp_path: Path, rel: str
) -> None:
    ws = golden_archive_workspace(tmp_path, "newave")
    _write(ws, rel)

    result = _realize(ws)

    assert Path(rel).name not in _all_members(ws)
    assert all(rel != source for source, _ in result.raw)
    assert {dest for _, dest in result.raw} == _EXPECTED_RAW_DESTS


def test_realize_golden_workspace_leaves_workspace_files_unchanged(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "newave")
    before = _workspace_files(ws)

    _realize(ws)

    assert _workspace_files(ws) == before


# -- AC3: selection boundary -----------------------------------------------


def test_realize_files_outside_v1_dirs_never_selected_out_dat_uploaded_raw(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "newave")
    outside = (
        "deep/x/cmarg.out",
        "sintese/a.dat",
        "hpc-model-utils/venv/bin/b.dat",
    )
    _write(ws, *outside, "out/c.dat")

    result = _realize(ws)

    members = _all_members(ws)
    sources = {source for source, _ in result.raw}
    dests = {dest for _, dest in result.raw}
    for rel in outside:
        assert Path(rel).name not in members
        assert Path(rel).name not in dests
        assert rel not in sources
    assert ("out/c.dat", "c.dat") in result.raw
    assert dests == _EXPECTED_RAW_DESTS | {"c.dat"}


def test_realize_pmo_at_root_and_in_out_raises_raw_destination_collision(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "newave")
    _write(ws, "out/pmo.dat")
    plan = output_plan(ws)

    with pytest.raises(
        ValueError, match=re.escape("raw destination collision 'pmo.dat'")
    ):
        realize(plan, ws, workers=2)

    assert list(ws.outputs_dir.iterdir()) == []


def test_realize_same_basename_root_and_out_raises_unsafe_archive_error(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "newave")
    _write(ws, "out/cmarg001.out")

    with pytest.raises(
        UnsafeArchiveError, match=re.escape("duplicate basename 'cmarg001.out'")
    ):
        _realize(ws)

    assert not (ws.outputs_dir / "operacao.zip").exists()


# -- Deck-derived names, determinism, errors, delegation ---------------------


def test_output_plan_renamed_pmo_in_arquivos_follows_new_name(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "newave")
    arq_path = ws.root / "arquivos.dat"
    text = arq_path.read_text(encoding="latin-1")
    old_line = "RELATORIO DE CONVERGENCIA   : pmo.dat"
    assert old_line in text
    arq_path.write_text(
        text.replace(old_line, "RELATORIO DE CONVERGENCIA   : pmo_x.dat"),
        encoding="latin-1",
    )
    (ws.root / "pmo.dat").rename(ws.root / "pmo_x.dat")

    result = _realize(ws)

    relatorios = _namelist(ws, "relatorios.zip")
    assert "pmo_x.dat" in relatorios
    assert "pmo.dat" not in relatorios
    assert ("pmo_x.dat", "pmo_x.dat") in result.raw
    assert {dest for _, dest in result.raw} == (
        _EXPECTED_RAW_DESTS - {"pmo.dat"} | {"pmo_x.dat"}
    )


def test_output_plan_called_twice_returns_equal_plans(tmp_path: Path) -> None:
    ws = golden_archive_workspace(tmp_path, "newave")

    assert output_plan(ws) == output_plan(ws)


def test_output_plan_missing_caso_raises_data_error(tmp_path: Path) -> None:
    ws = Workspace.at(tmp_path)

    with pytest.raises(DataError, match="missing caso.dat"):
        output_plan(ws)


def test_newaveplugin_outputs_golden_workspace_equals_output_plan(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "newave")

    assert NewavePlugin().outputs(ws) == output_plan(ws)


# -- Pattern construction ----------------------------------------------------


def test_output_plan_every_selector_recursive_patterns_only(
    tmp_path: Path,
) -> None:
    plan = output_plan(golden_archive_workspace(tmp_path, "newave"))

    assert [group.archive for group in plan.groups] == list(_GROUP_ARCHIVES)
    assert all(isinstance(group.layout, Flat) for group in plan.groups)
    for selector in _selectors(plan):
        assert selector.recursive is True
        assert selector.names == ()
        assert selector.patterns
    assert [entry.residual for entry in plan.raw] == [False, False, False, True]
    assert all(entry.dest == "" for entry in plan.raw)


def test_output_plan_every_pattern_v1_wrapped_without_bare_dot(
    tmp_path: Path,
) -> None:
    plan = output_plan(golden_archive_workspace(tmp_path, "newave"))

    patterns = [p for s in _selectors(plan) for p in s.patterns]
    patterns += plan.discard
    assert len(plan.discard) == 3
    for pattern in patterns:
        assert pattern.startswith(_V1_PREFIX)
        assert pattern.endswith(")")
        inner = pattern.removeprefix(_V1_PREFIX)[:-1]
        assert _BARE_DOT.search(inner) is None, pattern


def test_output_plan_recursos_group_has_mlt_plus_53_v1_regexes(
    tmp_path: Path,
) -> None:
    plan = output_plan(golden_archive_workspace(tmp_path, "newave"))

    recursos = next(g for g in plan.groups if g.archive == "recursos.zip")
    assert len(recursos.select.patterns) == 54
    assert len(set(recursos.select.patterns)) == 54


def test_output_plan_patterns_matching_root_name_reject_other_directories(
    tmp_path: Path,
) -> None:
    ws = golden_archive_workspace(tmp_path, "newave")
    plan = output_plan(ws)
    names = {path.name for path in ws.root.rglob("*") if path.is_file()}

    for selector in _selectors(plan):
        for name in names:
            if not selector.matches(name):
                continue
            for parent in ("out", "evaporacao", "fpha", "log"):
                assert selector.matches(f"{parent}/{name}")
            for parent in ("sintese", "out/deep", "deep/out", "outx"):
                assert not selector.matches(f"{parent}/{name}")
