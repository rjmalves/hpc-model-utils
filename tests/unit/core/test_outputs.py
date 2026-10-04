"""ADR-040/ADR-038/R16/R17/R55/R26: OutputPlan realization."""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import pytest

from hpc_model_utils.core.outputs import (
    Flat,
    OutputPlan,
    RawFile,
    Selector,
    Tree,
    ZipGroup,
    realize,
)
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.infra.errors import UnsafeArchiveError

# -- Selector ---------------------------------------------------------


def test_selector_matches_exact_name_returns_true() -> None:
    selector = Selector(names=("pmo.dat",))
    assert selector.matches("pmo.dat") is True
    assert selector.matches("other.dat") is False


def test_selector_matches_non_recursive_excludes_nested_path() -> None:
    selector = Selector(patterns=(r".*\.dat",))
    assert selector.matches("sub/x.dat") is False
    assert selector.matches("x.dat") is True


def test_selector_matches_recursive_includes_nested_path() -> None:
    selector = Selector(patterns=(r".*\.dat",), recursive=True)
    assert selector.matches("sub/x.dat") is True


def test_selector_matches_hpcmu_and_assets_paths_returns_false() -> None:
    selector = Selector(patterns=(r".*",), recursive=True)
    assert selector.matches(".hpcmu/outputs/x.zip") is False
    assert selector.matches("assets/model.exe") is False


def test_selector_init_invalid_pattern_raises_value_error() -> None:
    with pytest.raises(ValueError, match="invalid"):
        Selector(patterns=("(unclosed",))


# -- Construction validation -------------------------------------------


def test_zip_group_init_invalid_archive_name_raises_value_error() -> None:
    with pytest.raises(ValueError, match="archive"):
        ZipGroup(archive="../evil.zip", select=Selector())


def test_zip_group_init_non_zip_suffix_raises_value_error() -> None:
    with pytest.raises(ValueError, match="archive"):
        ZipGroup(archive="not-a-zip.txt", select=Selector())


def test_zip_group_default_layout_is_flat() -> None:
    group = ZipGroup(archive="g.zip", select=Selector())
    assert isinstance(group.layout, Flat)


def test_raw_file_init_dotdot_dest_raises_value_error() -> None:
    with pytest.raises(ValueError, match="dest"):
        RawFile(select=Selector(), dest="../escape")


def test_raw_file_init_absolute_dest_raises_value_error() -> None:
    with pytest.raises(ValueError, match="dest"):
        RawFile(select=Selector(), dest="/abs")


def test_raw_file_init_backslash_dest_raises_value_error() -> None:
    with pytest.raises(ValueError, match="dest"):
        RawFile(select=Selector(), dest="sub\\dir")


def test_tree_init_absolute_root_raises_value_error() -> None:
    with pytest.raises(ValueError, match="root"):
        Tree(root="/abs")


def test_tree_init_dotdot_root_raises_value_error() -> None:
    with pytest.raises(ValueError, match="root"):
        Tree(root="../escape")


def test_output_plan_init_invalid_discard_pattern_raises_value_error() -> None:
    with pytest.raises(ValueError, match="discard"):
        OutputPlan(deck_inputs=(), groups=(), raw=(), discard=("(bad",))


def test_output_plan_init_duplicate_group_archive_raises_value_error() -> None:
    groups = (
        ZipGroup(archive="dup.zip", select=Selector(names=("a.dat",))),
        ZipGroup(archive="dup.zip", select=Selector(names=("b.dat",))),
    )
    with pytest.raises(ValueError, match="duplicate group archive"):
        OutputPlan(deck_inputs=(), groups=groups, raw=())


def test_output_plan_init_group_archive_reserved_raises_value_error() -> None:
    group = ZipGroup(
        archive="deck_processado.zip", select=Selector(names=("a.dat",))
    )
    with pytest.raises(ValueError, match="reserved deck archive"):
        OutputPlan(deck_inputs=(), groups=(group,), raw=())


# -- Groups: flat and tree layouts -------------------------------------


def test_realize_flat_group_writes_matching_members_flat(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "x.dat").write_text("x")
    (tmp_path / "b" / "y.dat").write_text("y")
    group = ZipGroup(
        archive="flat.zip",
        select=Selector(patterns=(r"[ab]/[a-z]\.dat",), recursive=True),
    )
    plan = OutputPlan(deck_inputs=(), groups=(group,), raw=())

    result = realize(plan, ws, workers=1)

    assert result.archives == (".hpcmu/outputs/flat.zip",)
    with zipfile.ZipFile(ws.outputs_dir / "flat.zip") as zf:
        assert sorted(zf.namelist()) == ["x.dat", "y.dat"]


def test_realize_tree_group_writes_relative_members_and_stores_parquet(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    out_root = tmp_path / "output"
    nested = out_root / "sim" / "scenario=1"
    nested.mkdir(parents=True)
    (nested / "data.parquet").write_bytes(b"parquet-bytes")
    group = ZipGroup(
        archive="tree.zip",
        select=Selector(patterns=(r"output/.*\.parquet",), recursive=True),
        layout=Tree(root="output"),
    )
    plan = OutputPlan(deck_inputs=(), groups=(group,), raw=())

    realize(plan, ws, workers=1)

    with zipfile.ZipFile(ws.outputs_dir / "tree.zip") as zf:
        info = zf.getinfo("sim/scenario=1/data.parquet")
        assert info.compress_type == zipfile.ZIP_STORED


def test_realize_group_with_no_matches_absent_from_archives_and_dir(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    (tmp_path / "keep.dat").write_text("k")
    group = ZipGroup(
        archive="empty.zip", select=Selector(names=("missing.dat",))
    )
    plan = OutputPlan(deck_inputs=(), groups=(group,), raw=())

    result = realize(plan, ws, workers=1)

    assert result.archives == ()
    assert not (ws.outputs_dir / "empty.zip").exists()


def test_realize_flat_collision_propagates_unsafe_archive_error(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "dup.dat").write_text("a")
    (tmp_path / "b" / "dup.dat").write_text("b")
    group = ZipGroup(
        archive="dup.zip",
        select=Selector(patterns=(r"[ab]/dup\.dat",), recursive=True),
    )
    plan = OutputPlan(deck_inputs=(), groups=(group,), raw=())

    with pytest.raises(UnsafeArchiveError, match="dup.dat"):
        realize(plan, ws, workers=1)


# -- Deck ----------------------------------------------------------------


def test_realize_deck_flat_layout_writes_deck_archive(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    (tmp_path / "dger.dat").write_text("d")
    (tmp_path / "sim1.dat").write_text("s")
    plan = OutputPlan(deck_inputs=("dger.dat", "sim1.dat"), groups=(), raw=())

    result = realize(plan, ws, workers=1)

    assert result.deck == ".hpcmu/outputs/deck_processado.zip"
    with zipfile.ZipFile(ws.outputs_dir / "deck_processado.zip") as zf:
        assert sorted(zf.namelist()) == ["dger.dat", "sim1.dat"]


def test_realize_deck_tree_layout_writes_deck_archive_relative_root(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "dger.dat").write_text("d")
    plan = OutputPlan(
        deck_inputs=("case/dger.dat",),
        groups=(),
        raw=(),
        deck_layout=Tree(root="case"),
    )

    realize(plan, ws, workers=1)

    with zipfile.ZipFile(ws.outputs_dir / "deck_processado.zip") as zf:
        assert zf.namelist() == ["dger.dat"]


def test_realize_deck_is_none_when_deck_inputs_empty(tmp_path: Path) -> None:
    ws = Workspace.at(tmp_path)
    plan = OutputPlan(deck_inputs=(), groups=(), raw=())

    result = realize(plan, ws, workers=1)

    assert result.deck is None
    assert not (ws.outputs_dir / "deck_processado.zip").exists()


def test_realize_deck_input_missing_skipped_and_logged_at_debug(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = Workspace.at(tmp_path)
    plan = OutputPlan(deck_inputs=("missing.dat",), groups=(), raw=())

    with caplog.at_level(logging.DEBUG, logger="hpc_model_utils.core.outputs"):
        result = realize(plan, ws, workers=1)

    assert result.deck is None
    assert "missing.dat" in caplog.text


# -- Amendment 1: symlinks and deck-input safety ------------------------


def test_realize_symlinked_file_never_archived_or_raw_listed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside.dat"
    outside.write_text("secret")
    (root / "link.dat").symlink_to(outside)
    (root / "keep.dat").write_text("k")
    ws = Workspace.at(root)
    plan = OutputPlan(
        deck_inputs=(),
        groups=(),
        raw=(RawFile(select=Selector(patterns=(r".*\.dat",)), residual=True),),
    )

    result = realize(plan, ws, workers=1)

    assert result.raw == (("keep.dat", "keep.dat"),)


def test_realize_symlinked_dir_escaping_workspace_never_followed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.dat").write_text("s")
    (root / "linked_dir").symlink_to(outside_dir, target_is_directory=True)
    (root / "keep.dat").write_text("k")
    ws = Workspace.at(root)
    plan = OutputPlan(
        deck_inputs=(),
        groups=(),
        raw=(
            RawFile(
                select=Selector(patterns=(r".*",), recursive=True),
                residual=True,
            ),
        ),
    )

    result = realize(plan, ws, workers=1)

    assert result.raw == (("keep.dat", "keep.dat"),)


def test_realize_deck_input_symlink_skipped_and_logged_at_debug(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside.dat"
    outside.write_text("secret")
    (root / "dger.dat").symlink_to(outside)
    ws = Workspace.at(root)
    plan = OutputPlan(deck_inputs=("dger.dat",), groups=(), raw=())

    with caplog.at_level(logging.DEBUG, logger="hpc_model_utils.core.outputs"):
        result = realize(plan, ws, workers=1)

    assert result.deck is None
    assert "dger.dat" in caplog.text


def test_realize_deck_input_resolving_outside_root_skipped(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "dger.dat").write_text("d")
    (root / "linked").symlink_to(outside_dir, target_is_directory=True)
    ws = Workspace.at(root)
    plan = OutputPlan(deck_inputs=("linked/dger.dat",), groups=(), raw=())

    result = realize(plan, ws, workers=1)

    assert result.deck is None


def test_realize_deck_input_with_dotdot_skipped(tmp_path: Path) -> None:
    ws = Workspace.at(tmp_path)
    plan = OutputPlan(deck_inputs=("../escape.dat",), groups=(), raw=())

    result = realize(plan, ws, workers=1)

    assert result.deck is None


# -- Amendment 2: discard excludes groups and every raw entry -----------


def test_realize_discard_excludes_from_groups_and_raw_entries(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    (tmp_path / "cortes-001.dat").write_text("c")
    (tmp_path / "secret.dat").write_text("s")
    (tmp_path / "pmo.dat").write_text("p")
    group = ZipGroup(
        archive="cortes.zip",
        select=Selector(patterns=(r"cortes-[0-9]+\.dat", r"secret\.dat")),
    )
    plan = OutputPlan(
        deck_inputs=(),
        groups=(group,),
        raw=(
            RawFile(select=Selector(names=("secret.dat",))),
            RawFile(select=Selector(patterns=(r".*\.dat",)), residual=True),
        ),
        discard=(r"secret\.dat",),
    )

    result = realize(plan, ws, workers=1)

    with zipfile.ZipFile(ws.outputs_dir / "cortes.zip") as zf:
        assert zf.namelist() == ["cortes-001.dat"]
    assert result.raw == (("pmo.dat", "pmo.dat"),)


def test_realize_assets_path_never_archived_or_raw_listed(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "model.exe").write_text("bin")
    (tmp_path / "keep.txt").write_text("k")
    plan = OutputPlan(
        deck_inputs=(),
        groups=(),
        raw=(
            RawFile(
                select=Selector(patterns=(r".*",), recursive=True),
                residual=True,
            ),
        ),
    )

    result = realize(plan, ws, workers=1)

    assert result.raw == (("keep.txt", "keep.txt"),)


def test_realize_hpcmu_path_never_archived_or_raw_listed(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    ws.ensure_layout()
    (ws.logs_dir / "model-1.out").write_text("log")
    (tmp_path / "keep.txt").write_text("k")
    group = ZipGroup(
        archive="all.zip",
        select=Selector(patterns=(r".*",), recursive=True),
    )
    plan = OutputPlan(
        deck_inputs=(),
        groups=(group,),
        raw=(
            RawFile(
                select=Selector(patterns=(r".*",), recursive=True),
                residual=True,
            ),
        ),
    )

    result = realize(plan, ws, workers=1)

    with zipfile.ZipFile(ws.outputs_dir / "all.zip") as zf:
        assert all(not n.startswith(".hpcmu/") for n in zf.namelist())
    assert all(not src.startswith(".hpcmu/") for src, _ in result.raw)


# -- Amendment 3: no stale outputs survive a re-run ----------------------


def test_realize_removes_stale_group_archive_on_rerun(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    source = tmp_path / "cortes-001.dat"
    source.write_text("c")
    group = ZipGroup(
        archive="cortes.zip",
        select=Selector(patterns=(r"cortes-[0-9]+\.dat",)),
    )
    plan = OutputPlan(deck_inputs=(), groups=(group,), raw=())

    realize(plan, ws, workers=1)
    assert (ws.outputs_dir / "cortes.zip").exists()

    source.unlink()
    result = realize(plan, ws, workers=1)

    assert result.archives == ()
    assert not (ws.outputs_dir / "cortes.zip").exists()


def test_realize_does_not_delete_undeclared_file_in_outputs_dir(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    ws.ensure_layout()
    sentinel = ws.outputs_dir / "unrelated.txt"
    sentinel.write_text("keep me")
    plan = OutputPlan(deck_inputs=(), groups=(), raw=())

    realize(plan, ws, workers=1)

    assert sentinel.exists()


def test_realize_never_deletes_workspace_source_files(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    (tmp_path / "pmo.dat").write_text("p")
    (tmp_path / "cortes-001.dat").write_text("c")
    group = ZipGroup(
        archive="cortes.zip",
        select=Selector(patterns=(r"cortes-[0-9]+\.dat",)),
    )
    plan = OutputPlan(
        deck_inputs=(),
        groups=(group,),
        raw=(RawFile(select=Selector(patterns=(r".*\.dat",)), residual=True),),
    )

    realize(plan, ws, workers=1)

    assert (tmp_path / "pmo.dat").exists()
    assert (tmp_path / "cortes-001.dat").exists()


# -- Raw: destinations, collisions, precedence --------------------------


def test_realize_raw_destination_collision_raises_value_error(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "same.dat").write_text("a")
    (tmp_path / "b" / "same.dat").write_text("b")
    plan = OutputPlan(
        deck_inputs=(),
        groups=(),
        raw=(
            RawFile(
                select=Selector(patterns=(r"[ab]/same\.dat",), recursive=True)
            ),
        ),
    )

    with pytest.raises(ValueError, match="same.dat"):
        realize(plan, ws, workers=1)


def test_realize_raw_destination_trailing_slash_collides_with_bare_dest(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "same.dat").write_text("a")
    (tmp_path / "b" / "same.dat").write_text("b")
    plan = OutputPlan(
        deck_inputs=(),
        groups=(),
        raw=(
            RawFile(
                select=Selector(names=("a/same.dat",), recursive=True),
                dest="sub",
            ),
            RawFile(
                select=Selector(names=("b/same.dat",), recursive=True),
                dest="sub/",
            ),
        ),
    )

    with pytest.raises(ValueError, match="sub/same.dat"):
        realize(plan, ws, workers=1)


def test_realize_recursive_raw_destination_uses_basename_only(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    nested = tmp_path / "sub" / "dir"
    nested.mkdir(parents=True)
    (nested / "deep.dat").write_text("d")
    plan = OutputPlan(
        deck_inputs=(),
        groups=(),
        raw=(
            RawFile(
                select=Selector(patterns=(r".*\.dat",), recursive=True),
                dest="saida",
            ),
        ),
    )

    result = realize(plan, ws, workers=1)

    assert result.raw == (("sub/dir/deep.dat", "saida/deep.dat"),)


def test_realize_nonresidual_raw_can_relist_a_group_member(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    (tmp_path / "pmo.dat").write_text("p")
    group = ZipGroup(archive="g.zip", select=Selector(names=("pmo.dat",)))
    plan = OutputPlan(
        deck_inputs=(),
        groups=(group,),
        raw=(RawFile(select=Selector(names=("pmo.dat",))),),
    )

    result = realize(plan, ws, workers=1)

    assert result.archives == (".hpcmu/outputs/g.zip",)
    assert result.raw == (("pmo.dat", "pmo.dat"),)


def test_realize_ac2_scenario_matches_spec_exactly(tmp_path: Path) -> None:
    ws = Workspace.at(tmp_path)
    for name, content in (
        ("pmo.dat", "p"),
        ("cortes-001.dat", "c1"),
        ("cortesh.dat", "ch"),
        ("extra.dat", "e"),
        ("dger.dat", "d"),
    ):
        (tmp_path / name).write_text(content)
    group = ZipGroup(
        archive="cortes.zip",
        select=Selector(patterns=(r"cortes-[0-9]+\.dat", r"cortesh\.dat")),
    )
    plan = OutputPlan(
        deck_inputs=("dger.dat",),
        groups=(group,),
        raw=(
            RawFile(select=Selector(names=("pmo.dat",))),
            RawFile(select=Selector(patterns=(r".*\.dat",)), residual=True),
        ),
    )

    result = realize(plan, ws, workers=2)

    with zipfile.ZipFile(ws.outputs_dir / "cortes.zip") as zf:
        assert sorted(zf.namelist()) == ["cortes-001.dat", "cortesh.dat"]
    assert result.raw == (("extra.dat", "extra.dat"), ("pmo.dat", "pmo.dat"))


# -- Determinism (AC4) ----------------------------------------------------


def test_realize_deterministic_member_lists_and_raw_pairs_across_runs(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    groups = []
    for index in range(6):
        name = f"group{index}"
        (tmp_path / f"{name}_a.dat").write_text("a")
        (tmp_path / f"{name}_b.dat").write_text("b")
        groups.append(
            ZipGroup(
                archive=f"{name}.zip",
                select=Selector(patterns=(rf"{name}_[ab]\.dat",)),
            )
        )
    (tmp_path / "extra.dat").write_text("e")
    plan = OutputPlan(
        deck_inputs=(),
        groups=tuple(groups),
        raw=(RawFile(select=Selector(patterns=(r".*\.dat",)), residual=True),),
    )

    result1 = realize(plan, ws, workers=4)
    members1 = {
        group.archive: sorted(
            zipfile.ZipFile(ws.outputs_dir / group.archive).namelist()
        )
        for group in groups
    }

    result2 = realize(plan, ws, workers=4)
    members2 = {
        group.archive: sorted(
            zipfile.ZipFile(ws.outputs_dir / group.archive).namelist()
        )
        for group in groups
    }

    assert result1 == result2
    assert members1 == members2
