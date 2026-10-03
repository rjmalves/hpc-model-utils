"""ADR-016/R20/R52/R53/R94/R111/R113 tests for the DECOMP FC cut
coupling (ticket-050a).

Every dadger variant is a copy of a fixture dadger with only the named
lines rewritten, delivered into the workspace through
``decomp_workspace(extra_files=...)``; no fixture is ever written.
Every test that runs ``couple``/``apply_coupling``/``prepare`` asserts
``sha256(dadger.rv0)`` is unchanged (R20).
"""

from __future__ import annotations

import functools
import hashlib
import logging
import re
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import pytest

from hpc_model_utils.core.errors import DataError, UsageError
from hpc_model_utils.core.state import (
    ParentInfo,
    RunState,
    StateStore,
    ToolInfo,
)
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.infra.errors import UnsafeArchiveError
from hpc_model_utils.models.decomp import DecompPlugin, cuts, deck
from tests.support.decks import (
    FIXTURES,
    binary_cut_files,
    decomp_workspace,
    input_zip,
)

DadgerKind = Literal["deck", "flexibilizador"]
LineEdit = Callable[[str], list[str]]

_PARENT_URI = "s3://outputs-bucket/artifacts/parenthash02"
_START_2025 = "2025-11-01T00:00:00+00:00"
_START_2024 = "2024-11-01T00:00:00+00:00"
_FLEX_START = "2025-08-01T00:00:00+00:00"
_HEADER = "cortesh.dat"
_CUTS = "cortes-012.dat"
_FLEX_CUTS = "cortes-009.dat"
_MISMATCH_2024 = (
    "FC stage mismatch: NEWCUT cortes-012.dat, expected cortes-024.dat "
    "from the parent start 2024-11-01 and the DECOMP horizon end 2026-01-01"
)
_FC_LINE = re.compile(r"FC  (NEWV21|NEWCUT)\b")


# -- builders ------------------------------------------------------------


@functools.cache
def _fixture_dadger(kind: DadgerKind) -> str:
    if kind == "flexibilizador":
        raw = (FIXTURES / "flexibilizador" / "dadger.rv0").read_bytes()
    else:
        with zipfile.ZipFile(
            FIXTURES / "decks" / "deck_decomp.zip"
        ) as deck_zip:
            raw = deck_zip.read("dadger.rv0")
    return raw.decode("latin-1")


def _fc_variant(
    kind: DadgerKind = "deck",
    *,
    newv21: str | None = _HEADER,
    newcut: str | None = _CUTS,
) -> bytes:
    """The fixture dadger with only its two FC lines rewritten; ``None``
    drops the register."""
    paths = {"NEWV21": newv21, "NEWCUT": newcut}
    seen: list[str] = []
    lines: list[str] = []
    for line in _fixture_dadger(kind).splitlines(keepends=True):
        match = _FC_LINE.match(line)
        if match is None:
            lines.append(line)
            continue
        register = match.group(1)
        seen.append(register)
        path = paths[register]
        if path is not None:
            lines.append(f"FC  {register:<10}{path}\n")
    assert sorted(seen) == ["NEWCUT", "NEWV21"]
    return "".join(lines).encode("latin-1")


def _edited_dadger(kind: DadgerKind, edit: LineEdit) -> bytes:
    original = _fixture_dadger(kind)
    edited = "".join(
        new for line in original.splitlines(keepends=True) for new in edit(line)
    )
    assert edited != original
    return edited.encode("latin-1")


def _replace_line(prefix: str, replacement: str) -> LineEdit:
    return lambda line: [replacement] if line.startswith(prefix) else [line]


def _set_stage1_duration(field: str) -> LineEdit:
    """Rewrite ``duracao_1`` (columns 29-38) of the first DP register of
    stage 1 in the deck fixture."""
    assert len(field) == 10

    def edit(line: str) -> list[str]:
        if line.startswith("DP   1    1 "):
            return [line[:29] + field + line[39:]]
        return [line]

    return edit


def _drop_dp(line: str) -> list[str]:
    return [] if line.startswith("DP ") else [line]


def _duplicate_newv21(line: str) -> list[str]:
    return [line, line] if line.startswith("FC  NEWV21") else [line]


def _blank_first_dp_stage(line: str) -> list[str]:
    """Blank ``estagio`` (columns 4-5) of the first DP register."""
    if line.startswith("DP   1    1 "):
        return [line[:4] + "  " + line[6:]]
    return [line]


def _keep_first_dp_only() -> LineEdit:
    kept: list[str] = []

    def edit(line: str) -> list[str]:
        if not line.startswith("DP "):
            return [line]
        if kept:
            return []
        kept.append(line)
        return [line]

    return edit


def _parent(start: str) -> ParentInfo:
    return ParentInfo(_PARENT_URI, "NEWAVE", start)


def _save_state(ws: Workspace, parent: ParentInfo | None) -> None:
    ws.ensure_layout()
    state = RunState.new("decomp", ToolInfo("hpc-model-utils", "test"))
    StateStore(ws).save(replace(state, parent=parent))


def _workspace(
    tmp_path: Path,
    *,
    kind: DadgerKind = "deck",
    dadger: bytes | None = None,
    deck_files: Mapping[str, bytes] | None = None,
    parent_start: str | None = None,
    parent_files: Mapping[str, bytes] | None = None,
) -> Workspace:
    extra = dict(deck_files or {})
    if dadger is not None:
        extra["dadger.rv0"] = dadger
    ws = decomp_workspace(tmp_path, dadger=kind, extra_files=extra).ws
    if parent_start is not None:
        _save_state(ws, _parent(parent_start))
    if parent_files is not None:
        input_zip(parent_files, ws.parent_dir / "cortes.zip")
    return ws


def _parent_payloads(*names: str) -> dict[str, bytes]:
    return binary_cut_files(names)


def _deck_payloads(*names: str) -> dict[str, bytes]:
    """Deck copies, byte-distinct from the parent's ``binary_cut_files``."""
    return {
        name: b"deck:" + payload
        for name, payload in binary_cut_files(names).items()
    }


def _sha(ws: Workspace) -> str:
    return hashlib.sha256((ws.root / "dadger.rv0").read_bytes()).hexdigest()


def _staging(ws: Workspace) -> Path:
    return ws.parent_dir / "fc"


def _archive(ws: Workspace) -> Path:
    return ws.parent_dir / "cortes.zip"


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]


def _couplings(caplog: pytest.LogCaptureFixture) -> list[cuts.CutCoupling]:
    return [
        arg
        for record in caplog.records
        if isinstance(record.args, tuple)
        for arg in record.args
        if isinstance(arg, cuts.CutCoupling)
    ]


def _assert_files(ws: Workspace, expected: Mapping[str, bytes]) -> None:
    for name, payload in expected.items():
        assert (ws.root / name).read_bytes() == payload, name


# -- ADR-016 invariant table: one row per invariant (a)-(g) ----------------


@dataclass(frozen=True)
class _InvariantRow:
    invariant: str
    newv21: str | None = _HEADER
    newcut: str | None = _CUTS
    deck: tuple[str, ...] = ()
    parent: tuple[str, ...] | None = None
    error: str | None = None
    result: cuts.CutCoupling | None = None
    from_deck: tuple[str, ...] = ()
    from_parent: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    warning: str | None = None


_INVARIANT_ROWS: tuple[_InvariantRow, ...] = (
    _InvariantRow(
        "a-both-or-neither",
        newcut=None,
        deck=(_HEADER,),
        error="both FC NEWV21 and FC NEWCUT must be present, or neither",
        from_deck=(_HEADER,),
    ),
    _InvariantRow(
        "b-neither-present",
        newv21=None,
        newcut=None,
        deck=(_HEADER, "cortes-003.dat", "cortdeco.rv0"),
        parent=(_HEADER, _CUTS),
        from_deck=("cortdeco.rv0",),
        deleted=(_HEADER, "cortes-003.dat"),
        warning="unused",
    ),
    _InvariantRow(
        "c-escaping-path",
        newv21="../../cortesh.dat",
        deck=(_HEADER, _CUTS),
        error="workspace-relative",
        from_deck=(_HEADER, _CUTS),
    ),
    _InvariantRow(
        "d-parent-overwrites-deck",
        deck=(_HEADER, _CUTS),
        parent=(_HEADER, _CUTS),
        result=cuts.CutCoupling(_HEADER, _CUTS, "parent", _CUTS),
        from_parent=(_HEADER, _CUTS),
    ),
    _InvariantRow(
        "e-mixed-origin",
        deck=(_HEADER, _CUTS),
        parent=(_HEADER,),
        error="mixed-origin FC pair",
        from_deck=(_HEADER, _CUTS),
    ),
    _InvariantRow(
        "f-missing-in-deck",
        deck=(_HEADER,),
        error=r"FC NEWCUT cortes-012\.dat not found in the deck",
        from_deck=(_HEADER,),
    ),
    _InvariantRow(
        "g-prune-unreferenced",
        deck=(
            _HEADER,
            _CUTS,
            "cortes-011.dat",
            "cortes.dat",
            "CORTES-010.DAT",
            "cortdeco.rv0",
            "mapcut.rv0",
            "cortes-pos.dat",
            "cortes-012.dat.bak",
            "sub/cortes-011.dat",
        ),
        result=cuts.CutCoupling(_HEADER, _CUTS, "deck", None),
        from_deck=(
            _HEADER,
            _CUTS,
            "cortdeco.rv0",
            "mapcut.rv0",
            "cortes-pos.dat",
            "cortes-012.dat.bak",
            "sub/cortes-011.dat",
        ),
        deleted=("cortes-011.dat", "cortes.dat", "CORTES-010.DAT"),
    ),
)


@pytest.mark.parametrize(
    "row", _INVARIANT_ROWS, ids=[row.invariant for row in _INVARIANT_ROWS]
)
def test_couple_invariant_row_holds(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, row: _InvariantRow
) -> None:
    deck_files = _deck_payloads(*row.deck)
    parent_files = None if row.parent is None else _parent_payloads(*row.parent)
    ws = _workspace(
        tmp_path,
        dadger=_fc_variant(newv21=row.newv21, newcut=row.newcut),
        deck_files=deck_files,
        parent_files=parent_files,
    )
    parent = None if row.parent is None else _parent(_START_2025)
    archive = None if row.parent is None else _archive(ws)
    before = _sha(ws)

    with caplog.at_level(logging.INFO):
        if row.error is not None:
            with pytest.raises(DataError, match=row.error):
                cuts.couple(ws, parent=parent, parent_archive=archive)
        else:
            result = cuts.couple(ws, parent=parent, parent_archive=archive)
            assert result == row.result

    assert _sha(ws) == before
    _assert_files(ws, {name: deck_files[name] for name in row.from_deck})
    if row.from_parent:
        assert parent_files is not None
        _assert_files(
            ws, {name: parent_files[name] for name in row.from_parent}
        )
    for name in row.deleted:
        assert not (ws.root / name).exists(), name
    warnings = _warnings(caplog)
    if row.warning is None:
        assert warnings == []
    else:
        assert len(warnings) == 1
        assert row.warning in warnings[0]
    assert not _staging(ws).exists()


# -- AC1: chained success and pruning --------------------------------------


def test_decompplugin_prepare_chained_deck_takes_parent_cuts_and_prunes_others(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    deck_files = {
        **_deck_payloads(_HEADER, _CUTS, "cortes-011.dat", "cortes.dat"),
        "cortdeco.rv0": b"DECOMP cut output\n",
    }
    parent_files = _parent_payloads(_HEADER, _CUTS)
    assert parent_files[_HEADER] != deck_files[_HEADER]
    assert parent_files[_CUTS] != deck_files[_CUTS]
    ws = _workspace(
        tmp_path,
        deck_files=deck_files,
        parent_start=_START_2025,
        parent_files=parent_files,
    )
    before = _sha(ws)

    with caplog.at_level(logging.INFO):
        DecompPlugin().prepare(ws, "x")

    _assert_files(ws, parent_files)
    assert not (ws.root / "cortes-011.dat").exists()
    assert not (ws.root / "cortes.dat").exists()
    _assert_files(ws, {"cortdeco.rv0": deck_files["cortdeco.rv0"]})
    assert _couplings(caplog) == [
        cuts.CutCoupling(_HEADER, _CUTS, "parent", "cortes-012.dat")
    ]
    assert _warnings(caplog) == []
    assert not _staging(ws).exists()
    assert _sha(ws) == before


def test_apply_coupling_recorded_parent_returns_parent_coupling(
    tmp_path: Path,
) -> None:
    ws = _workspace(
        tmp_path,
        deck_files=_deck_payloads(_HEADER, _CUTS),
        parent_start=_START_2025,
        parent_files=_parent_payloads(_HEADER, _CUTS),
    )
    before = _sha(ws)

    result = cuts.apply_coupling(ws)

    assert result == cuts.CutCoupling(_HEADER, _CUTS, "parent", _CUTS)
    assert _sha(ws) == before


# -- AC2: invariant violations through prepare -----------------------------


@dataclass(frozen=True)
class _ViolationRow:
    case: str
    newv21: str | None
    newcut: str | None
    deck: tuple[str, ...]
    parent: tuple[str, ...] | None
    match: str


_VIOLATION_ROWS: tuple[_ViolationRow, ...] = (
    _ViolationRow(
        "only-newv21",
        _HEADER,
        None,
        (_HEADER,),
        None,
        "both FC NEWV21 and FC NEWCUT",
    ),
    _ViolationRow(
        "only-newcut",
        None,
        _CUTS,
        (_CUTS,),
        None,
        "both FC NEWV21 and FC NEWCUT",
    ),
    _ViolationRow(
        "dotdot-escape",
        "../../cortesh.dat",
        _CUTS,
        (_HEADER, _CUTS),
        None,
        "workspace-relative",
    ),
    _ViolationRow(
        "absolute",
        "/abs/cortesh.dat",
        _CUTS,
        (_HEADER, _CUTS),
        None,
        "workspace-relative",
    ),
    _ViolationRow(
        "mixed-origin",
        _HEADER,
        _CUTS,
        (_HEADER, _CUTS),
        (_HEADER,),
        "mixed",
    ),
    _ViolationRow(
        "parent-holds-neither",
        _HEADER,
        _CUTS,
        (_HEADER, _CUTS),
        ("cortes-011.dat",),
        r"cortesh\.dat.*the parent's cortes\.zip",
    ),
    _ViolationRow(
        "deck-missing-cuts",
        _HEADER,
        _CUTS,
        (_HEADER,),
        None,
        r"cortes-012\.dat.*the deck",
    ),
)


@pytest.mark.parametrize(
    "row", _VIOLATION_ROWS, ids=[row.case for row in _VIOLATION_ROWS]
)
def test_decompplugin_prepare_fc_violation_raises_data_error_dadger_unchanged(
    tmp_path: Path, row: _ViolationRow
) -> None:
    deck_files = _deck_payloads(*row.deck)
    ws = _workspace(
        tmp_path,
        dadger=_fc_variant(newv21=row.newv21, newcut=row.newcut),
        deck_files=deck_files,
        parent_start=None if row.parent is None else _START_2025,
        parent_files=None
        if row.parent is None
        else _parent_payloads(*row.parent),
    )
    before = _sha(ws)

    with pytest.raises(DataError, match=row.match) as excinfo:
        DecompPlugin().prepare(ws, "x")

    message = str(excinfo.value)
    assert str(tmp_path) not in message
    assert "/abs" not in message
    assert _sha(ws) == before
    _assert_files(ws, deck_files)
    assert not _staging(ws).exists()


# -- AC3: neither register ---------------------------------------------------


def test_decompplugin_prepare_no_fc_registers_deletes_cuts_and_warns_unused(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = _workspace(
        tmp_path,
        dadger=_fc_variant(newv21=None, newcut=None),
        deck_files=_deck_payloads(_HEADER, "cortes-003.dat"),
        parent_start=_START_2025,
        parent_files=_parent_payloads(_HEADER, _CUTS),
    )
    before = _sha(ws)

    with caplog.at_level(logging.INFO):
        DecompPlugin().prepare(ws, "x")

    assert not (ws.root / _HEADER).exists()
    assert not (ws.root / "cortes-003.dat").exists()
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "unused" in warnings[0]
    assert _couplings(caplog) == []
    assert cuts.stage_warning(ws) is None
    assert _sha(ws) == before


def test_couple_no_fc_registers_without_parent_prunes_and_does_not_warn(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = _workspace(
        tmp_path,
        dadger=_fc_variant(newv21=None, newcut=None),
        deck_files=_deck_payloads(_HEADER, _CUTS),
    )
    before = _sha(ws)

    with caplog.at_level(logging.INFO):
        result = cuts.couple(ws, parent=None, parent_archive=None)

    assert result is None
    assert not (ws.root / _HEADER).exists()
    assert not (ws.root / _CUTS).exists()
    assert _warnings(caplog) == []
    assert _sha(ws) == before


# -- AC4: stage formula (R111) ---------------------------------------------


@pytest.mark.parametrize(
    ("kind", "parent_start", "expected"),
    [
        ("deck", _START_2025, "cortes-012.dat"),
        ("deck", _START_2024, "cortes-024.dat"),
        ("flexibilizador", _FLEX_START, "cortes-009.dat"),
        ("flexibilizador", "2025-01-01T00:00:00+00:00", "cortes-009.dat"),
    ],
    ids=["deck-2025", "deck-2024", "flexibilizador-2025-08", "flex-2025-01"],
)
def test_expected_cut_file_fixture_dadger_returns_stage_file(
    tmp_path: Path, kind: DadgerKind, parent_start: str, expected: str
) -> None:
    ws = _workspace(tmp_path, kind=kind)
    assert cuts.expected_cut_file(deck.dadger(ws), parent_start) == expected


@pytest.mark.parametrize(
    ("kind", "parent_start"),
    [("deck", _START_2025), ("flexibilizador", _FLEX_START)],
    ids=["deck", "flexibilizador"],
)
def test_expected_cut_file_fixture_2025_parent_equals_fc_newcut(
    tmp_path: Path, kind: DadgerKind, parent_start: str
) -> None:
    ws = _workspace(tmp_path, kind=kind)
    dadger = deck.dadger(ws)
    _, newcut = cuts.read_fc(dadger, "dadger.rv0")
    assert cuts.expected_cut_file(dadger, parent_start) == newcut


def test_decompplugin_prepare_2024_parent_warns_stage_mismatch_and_returns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    parent_files = _parent_payloads(_HEADER, _CUTS)
    ws = _workspace(
        tmp_path, parent_start=_START_2024, parent_files=parent_files
    )
    before = _sha(ws)

    with caplog.at_level(logging.INFO):
        DecompPlugin().prepare(ws, "x")

    assert _warnings(caplog) == [_MISMATCH_2024]
    assert _couplings(caplog) == [
        cuts.CutCoupling(_HEADER, _CUTS, "parent", "cortes-024.dat")
    ]
    _assert_files(ws, parent_files)
    assert cuts.stage_warning(ws) == _MISMATCH_2024
    assert _sha(ws) == before


def test_couple_flexibilizador_dadger_2025_parent_records_matching_stage(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = _workspace(
        tmp_path,
        kind="flexibilizador",
        parent_files=_parent_payloads(_HEADER, _FLEX_CUTS),
    )
    before = _sha(ws)

    with caplog.at_level(logging.INFO):
        result = cuts.couple(
            ws, parent=_parent(_FLEX_START), parent_archive=_archive(ws)
        )

    assert result == cuts.CutCoupling(_HEADER, _FLEX_CUTS, "parent", _FLEX_CUTS)
    assert _warnings(caplog) == []
    assert _sha(ws) == before


@dataclass(frozen=True)
class _UnusableRow:
    case: str
    edit: LineEdit | None
    parent_start: str
    warning: str


_UNUSABLE_ROWS: tuple[_UnusableRow, ...] = (
    _UnusableRow(
        "dp-stage-missing-duration",
        _set_stage1_duration(" " * 10),
        _START_2025,
        "DP stage 1 has a missing duration",
    ),
    _UnusableRow(
        "dp-stage-nan-duration",
        _set_stage1_duration("       nan"),
        _START_2025,
        "DP stage 1 has a missing duration",
    ),
    _UnusableRow(
        "parent-date-not-iso",
        None,
        "01/11/2025",
        "is not ISO 8601",
    ),
    _UnusableRow(
        "dt-incomplete",
        _replace_line("DT ", "DT  01   11\n"),
        _START_2025,
        "incomplete DT date",
    ),
    _UnusableRow(
        "dt-missing",
        _replace_line("DT ", ""),
        _START_2025,
        "missing DT register",
    ),
    _UnusableRow(
        "dt-not-a-date",
        _replace_line("DT ", "DT  31    2   2025\n"),
        _START_2025,
        "is not a date",
    ),
    _UnusableRow(
        "no-dp-registers",
        _drop_dp,
        _START_2025,
        "no DP registers",
    ),
    _UnusableRow(
        "horizon-overflow",
        _set_stage1_duration("9999999999"),
        _START_2025,
        "overflows",
    ),
    _UnusableRow(
        "dp-register-without-stage",
        _blank_first_dp_stage,
        _START_2025,
        "a DP register has no stage",
    ),
)


@pytest.mark.parametrize(
    "row", _UNUSABLE_ROWS, ids=[row.case for row in _UNUSABLE_ROWS]
)
def test_expected_cut_file_unusable_input_returns_none_and_warns_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, row: _UnusableRow
) -> None:
    dadger = None if row.edit is None else _edited_dadger("deck", row.edit)
    ws = _workspace(tmp_path, dadger=dadger)

    with caplog.at_level(logging.INFO):
        result = cuts.expected_cut_file(deck.dadger(ws), row.parent_start)

    assert result is None
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert row.warning in warnings[0]


def test_expected_cut_file_single_dp_register_returns_one_stage_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # One 168 h stage from DT 2025-11-01 ends 2025-11-08: NNN = 11 + 0 - 1.
    ws = _workspace(
        tmp_path, dadger=_edited_dadger("deck", _keep_first_dp_only())
    )

    with caplog.at_level(logging.INFO):
        result = cuts.expected_cut_file(deck.dadger(ws), _START_2025)

    assert result == "cortes-010.dat"
    assert _warnings(caplog) == []


def test_couple_incomputable_stage_couples_with_expected_none(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = _workspace(tmp_path, parent_files=_parent_payloads(_HEADER, _CUTS))
    before = _sha(ws)

    with caplog.at_level(logging.INFO):
        result = cuts.couple(
            ws, parent=_parent("not-a-date"), parent_archive=_archive(ws)
        )

    assert result == cuts.CutCoupling(_HEADER, _CUTS, "parent", None)
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "is not ISO 8601" in warnings[0]
    assert _sha(ws) == before


def test_couple_non_stage_newcut_skips_stage_check(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = _workspace(
        tmp_path,
        dadger=_fc_variant(newcut="cortes.dat"),
        parent_files=_parent_payloads(_HEADER, "cortes.dat"),
    )
    before = _sha(ws)

    with caplog.at_level(logging.INFO):
        result = cuts.couple(
            ws, parent=_parent(_START_2024), parent_archive=_archive(ws)
        )

    assert result == cuts.CutCoupling(_HEADER, "cortes.dat", "parent", None)
    assert _warnings(caplog) == []
    assert _sha(ws) == before


# -- stage_warning ------------------------------------------------------------


@dataclass(frozen=True)
class _StageWarningRow:
    case: str
    state: Literal["none", "no-parent", "parent"]
    parent_start: str
    dadger: Callable[[], bytes] | None
    expected: str | None


_STAGE_WARNING_ROWS: tuple[_StageWarningRow, ...] = (
    _StageWarningRow("no-state", "none", _START_2024, None, None),
    _StageWarningRow("no-parent", "no-parent", _START_2024, None, None),
    _StageWarningRow(
        "no-fc",
        "parent",
        _START_2024,
        lambda: _fc_variant(newv21=None, newcut=None),
        None,
    ),
    _StageWarningRow(
        "non-stage-newcut",
        "parent",
        _START_2024,
        lambda: _fc_variant(newcut="cortes.dat"),
        None,
    ),
    _StageWarningRow("incomputable", "parent", "garbage", None, None),
    _StageWarningRow("match", "parent", _START_2025, None, None),
    _StageWarningRow("mismatch", "parent", _START_2024, None, _MISMATCH_2024),
)


@pytest.mark.parametrize(
    "row", _STAGE_WARNING_ROWS, ids=[row.case for row in _STAGE_WARNING_ROWS]
)
def test_stage_warning_recomputed_from_inputs_returns_expected(
    tmp_path: Path, row: _StageWarningRow
) -> None:
    ws = _workspace(
        tmp_path, dadger=None if row.dadger is None else row.dadger()
    )
    if row.state != "none":
        parent = None if row.state == "no-parent" else _parent(row.parent_start)
        _save_state(ws, parent)
    before = _sha(ws)

    assert cuts.stage_warning(ws) == row.expected
    assert _sha(ws) == before


# -- AC5: no-parent deck coupling ----------------------------------------------


@pytest.mark.parametrize(
    "with_state", [False, True], ids=["no-state", "state-without-parent"]
)
def test_decompplugin_prepare_no_parent_keeps_fc_files_and_prunes_others(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, with_state: bool
) -> None:
    deck_files = {
        **_deck_payloads(_HEADER, _CUTS, "cortes-011.dat", "cortes.dat"),
        "cortdeco.rv0": b"DECOMP cut output\n",
    }
    ws = _workspace(tmp_path, deck_files=deck_files)
    if with_state:
        _save_state(ws, None)
    before = _sha(ws)

    with caplog.at_level(logging.INFO):
        DecompPlugin().prepare(ws, "x")

    _assert_files(
        ws,
        {name: deck_files[name] for name in (_HEADER, _CUTS, "cortdeco.rv0")},
    )
    assert not (ws.root / "cortes-011.dat").exists()
    assert not (ws.root / "cortes.dat").exists()
    assert _couplings(caplog) == [
        cuts.CutCoupling(_HEADER, _CUTS, "deck", None)
    ]
    assert cuts.stage_warning(ws) is None
    assert _sha(ws) == before


# -- parent archive extraction ------------------------------------------------


def test_couple_subdirectory_fc_path_extracts_flat_parent_member_into_it(
    tmp_path: Path,
) -> None:
    deck_files = _deck_payloads(_HEADER, _CUTS)
    parent_files = _parent_payloads(_HEADER, _CUTS)
    ws = _workspace(
        tmp_path,
        dadger=_fc_variant(newv21="cuts/cortesh.dat"),
        deck_files=deck_files,
        parent_files=parent_files,
    )
    before = _sha(ws)

    result = cuts.couple(
        ws, parent=_parent(_START_2025), parent_archive=_archive(ws)
    )

    assert result == cuts.CutCoupling(
        "cuts/cortesh.dat", _CUTS, "parent", _CUTS
    )
    _assert_files(
        ws,
        {"cuts/cortesh.dat": parent_files[_HEADER], _CUTS: parent_files[_CUTS]},
    )
    assert not (ws.root / _HEADER).exists()
    assert not _staging(ws).exists()
    assert _sha(ws) == before


def test_couple_nested_parent_members_are_located_by_basename(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    parent_files = _parent_payloads(_HEADER, _CUTS)
    input_zip(parent_files, _archive(ws), root="run")
    before = _sha(ws)

    result = cuts.couple(
        ws, parent=_parent(_START_2025), parent_archive=_archive(ws)
    )

    assert result == cuts.CutCoupling(_HEADER, _CUTS, "parent", _CUTS)
    _assert_files(ws, parent_files)
    assert not (ws.root / "run").exists()
    assert not _staging(ws).exists()
    assert _sha(ws) == before


def test_couple_parent_archive_duplicate_basename_raises_data_error(
    tmp_path: Path,
) -> None:
    deck_files = _deck_payloads(_HEADER, _CUTS)
    payloads = _parent_payloads(_HEADER, _CUTS)
    ws = _workspace(
        tmp_path,
        deck_files=deck_files,
        parent_files={
            "a/cortesh.dat": payloads[_HEADER],
            "b/cortesh.dat": payloads[_HEADER],
            _CUTS: payloads[_CUTS],
        },
    )
    before = _sha(ws)

    with pytest.raises(
        DataError,
        match=r"FC NEWV21 cortesh\.dat is ambiguous in the parent's "
        r"cortes\.zip \(members a/cortesh\.dat, b/cortesh\.dat\)",
    ):
        cuts.couple(
            ws, parent=_parent(_START_2025), parent_archive=_archive(ws)
        )

    _assert_files(ws, deck_files)
    assert not _staging(ws).exists()
    assert _sha(ws) == before


def test_couple_unsafe_parent_member_raises_unsafe_archive_error(
    tmp_path: Path,
) -> None:
    deck_files = _deck_payloads(_HEADER, _CUTS)
    ws = _workspace(
        tmp_path,
        deck_files=deck_files,
        parent_files={
            **_parent_payloads(_HEADER, _CUTS),
            "../evil.dat": b"evil",
        },
    )
    before = _sha(ws)

    with pytest.raises(UnsafeArchiveError, match="escapes archive root"):
        cuts.couple(
            ws, parent=_parent(_START_2025), parent_archive=_archive(ws)
        )

    _assert_files(ws, deck_files)
    assert not (ws.parent_dir / "evil.dat").exists()
    assert not (ws.root.parent / "evil.dat").exists()
    assert not _staging(ws).exists()
    assert _sha(ws) == before


def test_couple_prune_skips_top_level_symlinks_and_directories(
    tmp_path: Path,
) -> None:
    deck_files = _deck_payloads(_HEADER, _CUTS)
    ws = _workspace(
        tmp_path,
        deck_files={**deck_files, "cortes-013.dat/placeholder": b""},
    )
    target = tmp_path / "elsewhere.dat"
    target.write_bytes(b"not a deck file")
    (ws.root / "cortes-011.dat").symlink_to(target)
    before = _sha(ws)

    result = cuts.couple(ws, parent=None, parent_archive=None)

    assert result == cuts.CutCoupling(_HEADER, _CUTS, "deck", None)
    assert (ws.root / "cortes-011.dat").is_symlink()
    assert target.read_bytes() == b"not a deck file"
    assert (ws.root / "cortes-013.dat" / "placeholder").is_file()
    _assert_files(ws, deck_files)
    assert _sha(ws) == before


def test_couple_leftover_staging_dir_is_removed_after_success(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path, parent_files=_parent_payloads(_HEADER, _CUTS))
    (_staging(ws) / "stale").mkdir(parents=True)
    (_staging(ws) / "stale" / "cortesh.dat").write_bytes(b"stale")
    before = _sha(ws)

    cuts.couple(ws, parent=_parent(_START_2025), parent_archive=_archive(ws))

    assert not _staging(ws).exists()
    assert _sha(ws) == before


def test_couple_replace_onto_directory_raises_and_removes_staging_dir(
    tmp_path: Path,
) -> None:
    ws = _workspace(
        tmp_path,
        dadger=_fc_variant(newv21="cuts/cortesh.dat"),
        deck_files={"cuts/cortesh.dat/placeholder": b""},
        parent_files=_parent_payloads(_HEADER, _CUTS),
    )
    before = _sha(ws)

    with pytest.raises(IsADirectoryError, match="Is a directory"):
        cuts.couple(
            ws, parent=_parent(_START_2025), parent_archive=_archive(ws)
        )

    assert not _staging(ws).exists()
    assert _sha(ws) == before


@pytest.mark.parametrize(
    "with_parent", [True, False], ids=["parent-only", "archive-only"]
)
def test_couple_parent_and_archive_mismatch_raises_value_error(
    tmp_path: Path, with_parent: bool
) -> None:
    ws = _workspace(tmp_path, deck_files=_deck_payloads(_HEADER, _CUTS))
    before = _sha(ws)

    with pytest.raises(ValueError, match="must be given together"):
        cuts.couple(
            ws,
            parent=_parent(_START_2025) if with_parent else None,
            parent_archive=None if with_parent else _archive(ws),
        )

    assert _sha(ws) == before


def test_apply_coupling_recorded_parent_without_archive_raises_usage_error(
    tmp_path: Path,
) -> None:
    deck_files = _deck_payloads(_HEADER, _CUTS)
    ws = _workspace(tmp_path, deck_files=deck_files, parent_start=_START_2025)
    before = _sha(ws)

    with pytest.raises(
        UsageError,
        match="parent cortes.zip missing; run check_and_fetch_inputs first",
    ):
        cuts.apply_coupling(ws)

    _assert_files(ws, deck_files)
    assert _sha(ws) == before


# -- read_fc ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("deck", (_HEADER, _CUTS)), ("flexibilizador", (_HEADER, _FLEX_CUTS))],
    ids=["deck", "flexibilizador"],
)
def test_read_fc_fixture_dadger_returns_both_paths(
    tmp_path: Path, kind: DadgerKind, expected: tuple[str, str]
) -> None:
    ws = _workspace(tmp_path, kind=kind)
    assert cuts.read_fc(deck.dadger(ws), "dadger.rv0") == expected


def test_read_fc_dropped_register_returns_none(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, dadger=_fc_variant(newcut=None))
    assert cuts.read_fc(deck.dadger(ws), "dadger.rv0") == (_HEADER, None)


def test_read_fc_blank_path_returns_empty_string(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, dadger=_fc_variant(newv21=""))
    assert cuts.read_fc(deck.dadger(ws), "dadger.rv0") == ("", _CUTS)


def test_read_fc_duplicate_register_raises_data_error(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, dadger=_edited_dadger("deck", _duplicate_newv21))
    with pytest.raises(
        DataError, match="dadger.rv0: more than one FC NEWV21 register"
    ):
        cuts.read_fc(deck.dadger(ws), "dadger.rv0")


# -- check_fc_path (invariant (c)) ------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("cortesh.dat", "cortesh.dat"),
        ("./cortesh.dat", "cortesh.dat"),
        ("cuts/cortesh.dat", "cuts/cortesh.dat"),
        ("cuts//cortesh.dat", "cuts/cortesh.dat"),
    ],
)
def test_check_fc_path_valid_path_returns_normalized_posix_path(
    tmp_path: Path, path: str, expected: str
) -> None:
    ws = Workspace.at(tmp_path)
    assert cuts.check_fc_path(ws, "dadger.rv0", "NEWV21", path) == expected


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("", "empty path"),
        (".", "empty path"),
        ("cuts\\cortesh.dat", "backslash in path"),
        ("C:\\abs\\cortesh.dat", "backslash in path"),
        ("/abs/cortesh.dat", "absolute path"),
        ("C:cortesh.dat", "drive-letter path"),
        ("c:/abs/cortesh.dat", "drive-letter path"),
        ("../../cortesh.dat", "'..' segment"),
        ("cuts/../cortesh.dat", "'..' segment"),
        (".hpcmu/cortesh.dat", "under the reserved .hpcmu/ directory"),
        ("assets/cortesh.dat", "under the reserved assets/ directory"),
        ("ASSETS/cortesh.dat", "under the reserved ASSETS/ directory"),
    ],
)
def test_check_fc_path_invalid_path_raises_data_error(
    tmp_path: Path, path: str, reason: str
) -> None:
    ws = Workspace.at(tmp_path)
    with pytest.raises(
        DataError,
        match=rf"workspace-relative .*\({re.escape(reason)}\)",
    ) as excinfo:
        cuts.check_fc_path(ws, "dadger.rv0", "NEWV21", path)
    message = str(excinfo.value)
    assert str(tmp_path) not in message
    assert "/abs" not in message
    assert "\\abs" not in message


def test_check_fc_path_symlink_escaping_workspace_raises_data_error(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    ws = Workspace.at(root)

    with pytest.raises(
        DataError,
        match=r"FC NEWCUT cortes-012\.dat path must be workspace-relative "
        r".*\(resolves outside the workspace\)",
    ) as excinfo:
        cuts.check_fc_path(ws, "dadger.rv0", "NEWCUT", "link/cortes-012.dat")
    assert str(tmp_path) not in str(excinfo.value)


def test_check_fc_path_symlink_into_reserved_dir_raises_data_error(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    ws.ensure_layout()
    (ws.root / "state-link").symlink_to(ws.hpcmu_dir, target_is_directory=True)

    with pytest.raises(
        DataError, match=r"\(under the reserved \.hpcmu/ directory\)"
    ):
        cuts.check_fc_path(ws, "dadger.rv0", "NEWV21", "state-link/state.json")
