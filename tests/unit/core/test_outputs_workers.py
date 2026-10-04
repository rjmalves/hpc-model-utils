"""ticket-048b AC3 (threads-and-scratch half), driven through ``realize``."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from hpc_model_utils.core.outputs import OutputPlan, Selector, ZipGroup, realize
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.infra import archive


def _three_groups_of_six(ws: Workspace) -> OutputPlan:
    groups = []
    for group_index in range(3):
        for member_index in range(6):
            name = f"g{group_index}_m{member_index}.dat"
            (ws.root / name).write_text(name)
        groups.append(
            ZipGroup(
                archive=f"g{group_index}.zip",
                select=Selector(patterns=(rf"g{group_index}_m[0-9]+\.dat",)),
            )
        )
    return OutputPlan(deck_inputs=(), groups=tuple(groups), raw=())


def test_realize_three_groups_workers_three_bounds_concurrency_and_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = Workspace.at(tmp_path)
    plan = _three_groups_of_six(ws)

    lock = threading.Lock()
    active = 0
    max_active = 0
    scratch_counts: list[int] = []
    real_compress = archive._compress_member

    def _tracked(path: Path, arcname: str, scratch: Path) -> object:
        nonlocal active, max_active
        with lock:
            scratch_counts.append(len(list(scratch.iterdir())))
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.05)
            return real_compress(path, arcname, scratch)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(archive, "_compress_member", _tracked)

    result = realize(plan, ws, workers=3)

    assert len(result.archives) == 3
    assert max_active == 3
    assert all(count <= 6 for count in scratch_counts)


def test_realize_deck_and_two_groups_leaves_no_scratch_or_part_files(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    (tmp_path / "dger.dat").write_text("d")
    (tmp_path / "g0_m0.dat").write_text("a")
    (tmp_path / "g1_m0.dat").write_text("b")
    plan = OutputPlan(
        deck_inputs=("dger.dat",),
        groups=(
            ZipGroup(archive="g0.zip", select=Selector(names=("g0_m0.dat",))),
            ZipGroup(archive="g1.zip", select=Selector(names=("g1_m0.dat",))),
        ),
        raw=(),
    )

    result = realize(plan, ws, workers=2)

    assert result.deck is not None
    for rel in (result.deck, *result.archives):
        assert (ws.root / rel).exists()

    entries = list(ws.outputs_dir.iterdir())
    assert not any(entry.name.endswith(".members") for entry in entries)
    assert not any(entry.name.endswith(".part") for entry in entries)
