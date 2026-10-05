"""ADR-038/ADR-040/R78/R116 tests for the cobre ``OutputPlan``
(ticket-071)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from hpc_model_utils.core.errors import DataError
from hpc_model_utils.core.outputs import RealizedOutputs, Tree, realize
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.models.cobre import CobrePlugin, case
from hpc_model_utils.models.cobre.outputs import output_plan
from tests.support.cobre_case import (
    case_members,
    cobre_workspace,
    write_outputs,
)

_OUTPUTS = ".hpcmu/outputs"
_ARCHIVES = (
    f"{_OUTPUTS}/policy.zip",
    f"{_OUTPUTS}/simulation.zip",
    f"{_OUTPUTS}/training.zip",
)


def _realize(ws: Workspace) -> RealizedOutputs:
    return realize(output_plan(ws), ws, workers=1)


def _infos(ws: Workspace, archive: str) -> list[zipfile.ZipInfo]:
    with zipfile.ZipFile(ws.outputs_dir / archive) as zf:
        return zf.infolist()


def _names(ws: Workspace, archive: str) -> list[str]:
    return sorted(info.filename for info in _infos(ws, archive))


# -- plan declaration ---------------------------------------------------


def test_plan_top_folder_declares_phase_trees_raw_metadata_and_deck_tree(
    tmp_path: Path,
) -> None:
    ws = cobre_workspace(tmp_path).ws

    plan = output_plan(ws)

    assert [group.archive for group in plan.groups] == [
        "training.zip",
        "policy.zip",
        "simulation.zip",
    ]
    assert all(
        group.layout == Tree("caso_cobre/output") for group in plan.groups
    )
    assert [entry.dest for entry in plan.raw] == ["training", "simulation"]
    assert plan.deck_layout == Tree("caso_cobre")
    assert plan.deck_inputs == case.input_files(ws)
    assert plan.discard == ()


def test_plan_flat_zip_uses_the_output_tree_and_an_empty_deck_root(
    tmp_path: Path,
) -> None:
    ws = cobre_workspace(tmp_path, top=None).ws

    plan = output_plan(ws)

    assert all(group.layout == Tree("output") for group in plan.groups)
    assert plan.deck_layout == Tree("")
    assert plan.deck_inputs == case.input_files(ws)


def test_plan_called_twice_returns_equal_plans(tmp_path: Path) -> None:
    ws = cobre_workspace(tmp_path).ws

    assert output_plan(ws) == output_plan(ws)


def test_plan_missing_eco_deck_raises_data_error(tmp_path: Path) -> None:
    with pytest.raises(DataError, match=r"eco_deck\.zip missing"):
        output_plan(Workspace.at(tmp_path))


def test_plan_plugin_outputs_delegates_to_output_plan(tmp_path: Path) -> None:
    ws = cobre_workspace(tmp_path).ws

    assert CobrePlugin().outputs(ws) == output_plan(ws)


# -- realized archives --------------------------------------------------


def test_realize_all_phases_writes_three_archives_and_the_deck(
    tmp_path: Path,
) -> None:
    ws = cobre_workspace(tmp_path).ws
    write_outputs(case.case_root(ws))

    realized = _realize(ws)

    assert realized.archives == _ARCHIVES
    assert realized.deck == f"{_OUTPUTS}/deck_processado.zip"
    assert sorted(path.name for path in ws.outputs_dir.iterdir()) == [
        "deck_processado.zip",
        "policy.zip",
        "simulation.zip",
        "training.zip",
    ]


def test_realize_phase_archives_hold_the_phase_paths_relative_to_output(
    tmp_path: Path,
) -> None:
    ws = cobre_workspace(tmp_path).ws
    written = write_outputs(case.case_root(ws))

    _realize(ws)

    for phase in ("training", "policy", "simulation"):
        assert _names(ws, f"{phase}.zip") == [
            name for name in written if name.startswith(f"{phase}/")
        ]
    assert "simulation/costs/scenario_id=0000/part-0000.parquet" in _names(
        ws, "simulation.zip"
    )


def test_realize_parquet_members_are_stored_and_json_members_deflated(
    tmp_path: Path,
) -> None:
    ws = cobre_workspace(tmp_path).ws
    write_outputs(case.case_root(ws))

    _realize(ws)

    infos = [
        info
        for phase in ("training", "policy", "simulation")
        for info in _infos(ws, f"{phase}.zip")
    ]
    parquet = [i for i in infos if i.filename.endswith(".parquet")]
    jsons = [i for i in infos if i.filename.endswith(".json")]
    assert parquet
    assert jsons
    assert {i.compress_type for i in parquet} == {zipfile.ZIP_STORED}
    assert {i.compress_type for i in jsons} == {zipfile.ZIP_DEFLATED}


def test_realize_deck_archive_is_the_case_tree_without_top_or_output(
    tmp_path: Path,
) -> None:
    ws = cobre_workspace(tmp_path).ws
    write_outputs(case.case_root(ws))

    _realize(ws)

    names = _names(ws, "deck_processado.zip")
    assert "config.json" in names
    assert "system/hydros.json" in names
    assert names == sorted(case_members())
    assert not any(n.startswith(("caso_cobre/", "output/")) for n in names)


def test_realize_raw_pairs_are_the_training_and_simulation_metadata(
    tmp_path: Path,
) -> None:
    ws = cobre_workspace(tmp_path).ws
    write_outputs(case.case_root(ws))

    realized = _realize(ws)

    assert realized.raw == (
        (
            "caso_cobre/output/simulation/metadata.json",
            "simulation/metadata.json",
        ),
        (
            "caso_cobre/output/training/metadata.json",
            "training/metadata.json",
        ),
    )


def test_realize_never_writes_a_cortes_archive(tmp_path: Path) -> None:
    ws = cobre_workspace(tmp_path).ws
    write_outputs(case.case_root(ws))

    realized = _realize(ws)

    assert all(Path(path).name != "cortes.zip" for path in realized.archives)
    assert not (ws.outputs_dir / "cortes.zip").exists()


def test_realize_flat_zip_names_members_without_a_prefix(
    tmp_path: Path,
) -> None:
    ws = cobre_workspace(tmp_path, top=None).ws
    written = write_outputs(case.case_root(ws))

    realized = _realize(ws)

    assert realized.archives == _ARCHIVES
    assert _names(ws, "training.zip") == [
        name for name in written if name.startswith("training/")
    ]
    assert _names(ws, "deck_processado.zip") == sorted(case_members())
    assert realized.raw == (
        ("output/simulation/metadata.json", "simulation/metadata.json"),
        ("output/training/metadata.json", "training/metadata.json"),
    )


def test_realize_top_folder_with_regex_metacharacters_is_matched_literally(
    tmp_path: Path,
) -> None:
    ws = cobre_workspace(tmp_path, top="caso.+(v2)").ws
    written = write_outputs(case.case_root(ws))

    realized = _realize(ws)

    assert realized.archives == _ARCHIVES
    assert _names(ws, "simulation.zip") == [
        name for name in written if name.startswith("simulation/")
    ]
    assert len(realized.raw) == 2


def test_realize_opt_in_exports_are_never_archived(tmp_path: Path) -> None:
    ws = cobre_workspace(tmp_path).ws
    output = case.case_root(ws) / "output"
    write_outputs(case.case_root(ws))
    for relative in (
        "stochastic/noise.parquet",
        "hydro_models/fpha_deviation_points.parquet",
    ):
        (output / relative).parent.mkdir(parents=True, exist_ok=True)
        (output / relative).write_bytes(b"opt-in export")

    realized = _realize(ws)

    assert realized.archives == _ARCHIVES
    members = [
        name
        for archive in ("training", "policy", "simulation")
        for name in _names(ws, f"{archive}.zip")
    ]
    assert not any(
        n.startswith(("stochastic/", "hydro_models/")) for n in members
    )
    assert len(realized.raw) == 2


# -- disabled phase -----------------------------------------------------


def test_disabled_simulation_writes_no_simulation_archive_or_raw_pair(
    tmp_path: Path,
) -> None:
    ws = cobre_workspace(tmp_path).ws
    write_outputs(case.case_root(ws), simulation=None)

    realized = _realize(ws)

    assert realized.archives == (
        f"{_OUTPUTS}/policy.zip",
        f"{_OUTPUTS}/training.zip",
    )
    assert not (ws.outputs_dir / "simulation.zip").exists()
    assert realized.raw == (
        (
            "caso_cobre/output/training/metadata.json",
            "training/metadata.json",
        ),
    )


def test_disabled_training_writes_no_training_archive_or_raw_pair(
    tmp_path: Path,
) -> None:
    ws = cobre_workspace(tmp_path).ws
    write_outputs(case.case_root(ws), training=None)

    realized = _realize(ws)

    assert realized.archives == (
        f"{_OUTPUTS}/policy.zip",
        f"{_OUTPUTS}/simulation.zip",
    )
    assert not (ws.outputs_dir / "training.zip").exists()
    assert realized.raw == (
        (
            "caso_cobre/output/simulation/metadata.json",
            "simulation/metadata.json",
        ),
    )
