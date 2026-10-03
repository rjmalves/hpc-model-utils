"""ADR-016/R20/R52/R53/R94/R111/R113: the DECOMP FC cut coupling
(ticket-050a).

A chained DECOMP run couples its horizon's end state ``x_T`` to the
parent NEWAVE future-cost function through the two files the dadger
``FC`` registers name: ``NEWV21`` (the cut header) and ``NEWCUT`` (the
cuts ``theta >= alpha^k + (beta^k)^T x_T`` of the stage whose cost-to-go
that end state needs). FC is the single source of truth, and it is
read-only (R20/D1): nothing here writes dadger, so an FC path that
cannot be satisfied as written is a ``DataError``, never a rewrite.
Which members are taken from the parent ``cortes.zip`` (staged at
``ws.parent_dir`` from the parent's own bucket, R94) and which deck cut
files survive both follow from FC alone. The calendar stage formula
(R111) only ever produces a warning here; ticket-079 promotes it.

Nothing is persisted: ``stage_warning`` recomputes from inputs that are
immutable after ``prepare`` (dadger FC/DT/DP and the recorded parent).

idecomp is imported lazily, from concrete submodules only (see
``deck.py``).
"""

from __future__ import annotations

import logging
import math
import os
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Literal

from hpc_model_utils.core.errors import DataError, UsageError
from hpc_model_utils.core.state import ParentInfo, StateStore
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.infra.archive import extract, validated_members
from hpc_model_utils.models.decomp import deck

if TYPE_CHECKING:
    from idecomp.decomp.dadger import Dadger

logger = logging.getLogger(__name__)

_HEADER = "NEWV21"
_CUTS = "NEWCUT"
_PARENT_ARCHIVE = "cortes.zip"
_IN_PARENT = f"the parent's {_PARENT_ARCHIVE}"
_IN_DECK = "the deck"
_STAGING_DIR = "fc"
_CUT_FILE_PATTERN = re.compile(r"cortes(h)?(-\d+)?\.dat", re.IGNORECASE)
_STAGE_CUT_PATTERN = re.compile(r"cortes-(\d+)\.dat")
_DRIVE_LETTER_PATTERN = re.compile(r"[A-Za-z]:.*")
_SEPARATOR_PATTERN = re.compile(r"[\\/]")


@dataclass(frozen=True, slots=True)
class CutCoupling:
    """The coupled FC pair, as workspace-relative POSIX paths."""

    header: str
    cuts: str
    source: Literal["deck", "parent"]
    expected_cuts: str | None


@dataclass(frozen=True, slots=True)
class _Stage:
    expected: str
    parent_start: date
    horizon_end: date


def _basename(path: str) -> str:
    return _SEPARATOR_PATTERN.split(path)[-1]


def _fc_path(dadger: Dadger, name: str, register: str) -> str | None:
    from idecomp.decomp.modelos.dadger import FC

    found = dadger.fc(tipo=register)
    if found is None:
        return None
    if isinstance(found, FC):
        return (found.caminho or "").strip()
    raise DataError(f"{name}: more than one FC {register} register")


def read_fc(dadger: Dadger, name: str) -> tuple[str | None, str | None]:
    """The stripped ``(NEWV21, NEWCUT)`` paths, ``None`` for an absent
    register. ``name`` is the dadger file name, for messages only."""
    return _fc_path(dadger, name, _HEADER), _fc_path(dadger, name, _CUTS)


def _path_violation(ws: Workspace, path: str) -> str | None:
    pure = PurePosixPath(path)
    if not pure.parts:
        return "empty path"
    if "\\" in path:
        return "backslash in path"
    if pure.is_absolute():
        return "absolute path"
    if _DRIVE_LETTER_PATTERN.fullmatch(pure.parts[0]):
        return "drive-letter path"
    if ".." in pure.parts:
        return "'..' segment"
    root = ws.root.resolve()
    resolved = (root / pure).resolve()
    if not resolved.is_relative_to(root):
        return "resolves outside the workspace"
    first = resolved.relative_to(root).parts[:1]
    if first and (
        first[0] == ws.hpcmu_dir.name or first[0].lower() == ws.assets.name
    ):
        return f"under the reserved {first[0]}/ directory"
    return None


def check_fc_path(ws: Workspace, name: str, register: str, path: str) -> str:
    """Invariant (c): every lexical check runs before the one
    ``resolve()``, and the message names only the basename, never the
    path as written (it may be absolute) or ``ws.root``. Returns the
    normalized workspace-relative POSIX path."""
    reason = _path_violation(ws, path)
    if reason is None:
        return PurePosixPath(path).as_posix()
    base = _basename(path)
    subject = f"FC {register} {base}" if base else f"FC {register}"
    raise DataError(
        f"{name}: {subject} path must be workspace-relative and stay "
        f"inside the workspace ({reason}); dadger is never rewritten"
    )


def _prune(ws: Workspace, keep: frozenset[str]) -> None:
    """Invariants (b)/(g), R52: top-level ``cortes(h)?(-\\d+)?.dat``
    files only, never through a symlink; ``cortdeco.*``/``mapcut.*``
    never match."""
    deleted: list[str] = []
    for entry in sorted(ws.root.iterdir()):
        if entry.name in keep or not _CUT_FILE_PATTERN.fullmatch(entry.name):
            continue
        if entry.is_symlink() or not entry.is_file():
            continue
        entry.unlink()
        deleted.append(entry.name)
    logger.info("pruned NEWAVE cut files not named by FC: %s", deleted)


def _require_in_deck(ws: Workspace, name: str, header: str, cuts: str) -> None:
    missing = [
        f"FC {register} {path}"
        for register, path in ((_HEADER, header), (_CUTS, cuts))
        if not (ws.root / path).is_file()
    ]
    if missing:
        raise DataError(
            f"{name}: {' and '.join(missing)} not found in {_IN_DECK}"
        )


def _take_from_parent(
    ws: Workspace, name: str, archive: Path, header: str, cuts: str
) -> None:
    """Invariants (d)-(f): every member is located, and the pair
    checked, before anything is extracted."""
    members = validated_members(archive)
    found: list[tuple[str, str, str]] = []
    missing: list[str] = []
    for register, path in ((_HEADER, header), (_CUTS, cuts)):
        base = PurePosixPath(path).name
        candidates = sorted(m for m in members if PurePosixPath(m).name == base)
        if len(candidates) > 1:
            raise DataError(
                f"{name}: FC {register} {base} is ambiguous in {_IN_PARENT} "
                f"(members {', '.join(candidates)})"
            )
        if candidates:
            found.append((register, path, candidates[0]))
        else:
            missing.append(f"FC {register} {base}")
    if found and missing:
        present = f"FC {found[0][0]} {PurePosixPath(found[0][1]).name}"
        raise DataError(
            f"{name}: mixed-origin FC pair: {present} is in {_IN_PARENT} but "
            f"{missing[0]} is not; the header indexes only its own run's "
            "cut records"
        )
    if missing:
        raise DataError(
            f"{name}: {' and '.join(missing)} not found in {_IN_PARENT}"
        )

    staging = ws.parent_dir / _STAGING_DIR
    staging.mkdir(parents=True, exist_ok=True)
    try:
        for register, path, member in found:
            extract(archive, staging, members={member})
            target = ws.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging.joinpath(*PurePosixPath(member).parts), target)
            logger.info(
                "FC %s %s taken from %s member %s",
                register,
                path,
                _IN_PARENT,
                member,
            )
    finally:
        shutil.rmtree(staging)


def _horizon_hours(dadger: Dadger) -> float | None:
    """The sum of the durations of the first DP register of each stage.

    ``Dadger.dp(df=True)`` is not used: idecomp raises ``ValueError``
    while expanding a register with a blank duration, which is exactly
    the case that must degrade to a warning here.
    """
    from idecomp.decomp.modelos.dadger import DP

    found = dadger.dp()
    if isinstance(found, DP):
        registers = [found]
    elif isinstance(found, list):
        registers = [reg for reg in found if isinstance(reg, DP)]
    else:
        registers = []
    first: dict[int, DP] = {}
    for reg in registers:
        stage = reg.estagio
        if stage is None:
            logger.warning("FC stage check skipped: a DP register has no stage")
            return None
        first.setdefault(stage, reg)
    if not first:
        logger.warning("FC stage check skipped: no DP registers")
        return None
    total = 0.0
    for stage, reg in first.items():
        durations = reg.duracao or []
        blocks = reg.numero_patamares
        if (
            not blocks
            or len(durations) != blocks
            or not all(math.isfinite(hours) for hours in durations)
        ):
            logger.warning(
                "FC stage check skipped: DP stage %d has a missing duration "
                "(%d values for %s load blocks)",
                stage,
                len(durations),
                blocks,
            )
            return None
        total += sum(durations)
    return total


def _stage(dadger: Dadger, parent_starting_date: str) -> _Stage | None:
    dt = dadger.dt
    if dt is None:
        logger.warning("FC stage check skipped: missing DT register")
        return None
    year, month, day = dt.ano, dt.mes, dt.dia
    if not year or not month or not day:
        logger.warning("FC stage check skipped: incomplete DT date")
        return None
    try:
        start = datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        logger.warning(
            "FC stage check skipped: DT %04d-%02d-%02d is not a date",
            year,
            month,
            day,
        )
        return None
    hours = _horizon_hours(dadger)
    if hours is None:
        return None
    try:
        parent_start = datetime.fromisoformat(parent_starting_date)
    except ValueError:
        logger.warning(
            "FC stage check skipped: parent starting date %r is not ISO 8601",
            parent_starting_date,
        )
        return None
    try:
        end = start + timedelta(hours=hours)
    except OverflowError:
        logger.warning(
            "FC stage check skipped: DECOMP horizon of %.1f h overflows",
            hours,
        )
        return None
    months = (
        (end.year - parent_start.year) * 12 + end.month - parent_start.month
    )
    return _Stage(
        f"cortes-{parent_start.month + months - 1:03d}.dat",
        parent_start.date(),
        end.date(),
    )


def expected_cut_file(dadger: Dadger, parent_starting_date: str) -> str | None:
    """R111: NEWAVE's calendar-month counter of the stage whose
    cost-to-go the DECOMP horizon end needs.

    ``end = DT + sum(durations of the first DP register of each stage)``
    and ``NNN = m0_parent + months(end, start_parent) - 1``. Returns
    ``None`` after one WARNING when DT, a duration or the date cannot
    be used.
    """
    stage = _stage(dadger, parent_starting_date)
    return None if stage is None else stage.expected


def _stage_check(
    dadger: Dadger, cuts: str, parent_starting_date: str
) -> tuple[str, str | None] | None:
    """``(expected, mismatch text or None)``, or ``None`` when the check
    does not apply (not a ``cortes-NNN.dat`` NEWCUT) or is incomputable."""
    actual = PurePosixPath(cuts).name
    if not _STAGE_CUT_PATTERN.fullmatch(actual):
        return None
    stage = _stage(dadger, parent_starting_date)
    if stage is None:
        return None
    if actual == stage.expected:
        return stage.expected, None
    return stage.expected, (
        f"FC stage mismatch: NEWCUT {actual}, expected {stage.expected} "
        f"from the parent start {stage.parent_start.isoformat()} and the "
        f"DECOMP horizon end {stage.horizon_end.isoformat()}"
    )


def couple(
    ws: Workspace, *, parent: ParentInfo | None, parent_archive: Path | None
) -> CutCoupling | None:
    """ADR-016: apply invariants (a)-(g) in order, then the R111 stage
    check. dadger is never written (R20). Outcomes:

    ==============================================  =====================
    Condition                                       Outcome
    ==============================================  =====================
    (a) exactly one of NEWV21/NEWCUT present        ``DataError``
    (b) neither present                             prune all, ``None``;
                                                    WARNING with a parent
    (c) empty, ``\\``, absolute, drive, ``..``,      ``DataError``
        escaping or reserved-directory FC path
    (d) basename shared by >1 parent members        ``DataError``
    (d) both in the parent archive                  ``source="parent"``
    (e) only one in the parent archive              ``DataError`` (mixed)
    (f) neither in the parent archive, or (no       ``DataError``
        parent) not a regular file in the deck
    unsafe parent archive member                    ``UnsafeArchiveError``
    (g) every other top-level cut file              deleted
    stage mismatch / incomputable                   WARNING only
    ==============================================  =====================

    ``parent`` and ``parent_archive`` are given together or not at all.
    """
    if (parent is None) != (parent_archive is None):
        raise ValueError("parent and parent_archive must be given together")
    name = deck.dadger_name(ws)
    dadger = deck.dadger(ws)
    header_fc, cuts_fc = read_fc(dadger, name)

    if header_fc is None or cuts_fc is None:
        if header_fc is not None or cuts_fc is not None:
            only = _HEADER if header_fc is not None else _CUTS
            raise DataError(
                f"{name}: both FC NEWV21 and FC NEWCUT must be present, or "
                f"neither (only FC {only} found)"
            )
        _prune(ws, frozenset())
        if parent is not None:
            logger.warning(
                "%s has no FC NEWV21/NEWCUT registers: the parent's cuts in "
                "%s are unused",
                name,
                _PARENT_ARCHIVE,
            )
        return None

    header = check_fc_path(ws, name, _HEADER, header_fc)
    cuts = check_fc_path(ws, name, _CUTS, cuts_fc)
    source: Literal["deck", "parent"]
    if parent_archive is None:
        _require_in_deck(ws, name, header, cuts)
        source = "deck"
    else:
        _take_from_parent(ws, name, parent_archive, header, cuts)
        source = "parent"
    _prune(
        ws,
        frozenset(
            p for p in (header, cuts) if len(PurePosixPath(p).parts) == 1
        ),
    )

    expected: str | None = None
    if parent is not None:
        check = _stage_check(dadger, cuts, parent.starting_date)
        if check is not None:
            expected, mismatch = check
            if mismatch is not None:
                logger.warning("%s", mismatch)

    coupling = CutCoupling(header, cuts, source, expected)
    logger.info("FC cut coupling: %s", coupling)
    return coupling


def _recorded_parent(ws: Workspace) -> ParentInfo | None:
    if not ws.has_state:
        return None
    state = StateStore(ws).load()
    return None if state is None else state.parent


def apply_coupling(ws: Workspace) -> CutCoupling | None:
    """``couple`` with the parent recorded in ``RunState``, if any, and
    the ``cortes.zip`` that ``check_and_fetch_inputs`` staged for it."""
    parent = _recorded_parent(ws)
    archive: Path | None = None
    if parent is not None:
        archive = ws.parent_dir / _PARENT_ARCHIVE
        if not archive.is_file():
            raise UsageError(
                "parent cortes.zip missing; run check_and_fetch_inputs first"
            )
    return couple(ws, parent=parent, parent_archive=archive)


def stage_warning(ws: Workspace) -> str | None:
    """The R111 mismatch text ``couple`` logged, recomputed: ``None``
    without state, parent or FC pair, for a non-``cortes-NNN.dat``
    NEWCUT, an incomputable expectation, or a match."""
    parent = _recorded_parent(ws)
    if parent is None:
        return None
    name = deck.dadger_name(ws)
    dadger = deck.dadger(ws)
    header_fc, cuts_fc = read_fc(dadger, name)
    if header_fc is None or cuts_fc is None:
        return None
    check = _stage_check(dadger, cuts_fc, parent.starting_date)
    return None if check is None else check[1]
