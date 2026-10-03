"""ADR-040/ADR-038/R16/R17/R55/R26: the declarative OutputPlan and its
realization against a workspace.

``.hpcmu/`` and ``assets/`` are reserved and never matched by any
selector, at any depth. Only regular, non-symlink files are ever
archived or raw-listed: the workspace walk never follows a symlinked
directory, and a symlinked file is skipped, so a planted symlink can
never pull a file from outside the workspace into an uploaded archive.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.infra import archive

logger = logging.getLogger(__name__)

_DECK_ARCHIVE_NAME = "deck_processado.zip"
_ARCHIVE_PATTERN = re.compile(r"[A-Za-z0-9._-]+\.zip")
_EXCLUDED_ROOTS = frozenset({".hpcmu", "assets"})


def _is_excluded(rel_path: str) -> bool:
    return rel_path.split("/", 1)[0] in _EXCLUDED_ROOTS


def _validate_relative(value: str, *, field_name: str) -> None:
    if "\\" in value:
        raise ValueError(f"{field_name} must not contain '\\\\': {value!r}")
    if value == "":
        return
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError(
            f"{field_name} must be relative and free of '..': {value!r}"
        )


def _compile_check(pattern: str, *, field_name: str) -> None:
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(
            f"invalid {field_name} pattern {pattern!r}: {exc}"
        ) from exc


@dataclass(frozen=True, slots=True)
class Selector:
    names: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()
    recursive: bool = False

    def __post_init__(self) -> None:
        for pattern in self.patterns:
            _compile_check(pattern, field_name="patterns")

    def matches(self, rel_path: str) -> bool:
        if _is_excluded(rel_path):
            return False
        if not self.recursive and "/" in rel_path:
            return False
        if rel_path in self.names:
            return True
        return any(re.fullmatch(pattern, rel_path) for pattern in self.patterns)


@dataclass(frozen=True, slots=True)
class Flat:
    pass


@dataclass(frozen=True, slots=True)
class Tree:
    root: str

    def __post_init__(self) -> None:
        _validate_relative(self.root, field_name="root")


Layout = Flat | Tree


@dataclass(frozen=True, slots=True)
class ZipGroup:
    archive: str
    select: Selector
    layout: Layout = field(default_factory=Flat)

    def __post_init__(self) -> None:
        if not _ARCHIVE_PATTERN.fullmatch(self.archive):
            raise ValueError(
                f"archive must match {_ARCHIVE_PATTERN.pattern}: "
                f"{self.archive!r}"
            )


@dataclass(frozen=True, slots=True)
class RawFile:
    select: Selector
    dest: str = ""
    residual: bool = False

    def __post_init__(self) -> None:
        _validate_relative(self.dest, field_name="dest")


@dataclass(frozen=True, slots=True)
class OutputPlan:
    deck_inputs: tuple[str, ...]
    groups: tuple[ZipGroup, ...]
    raw: tuple[RawFile, ...]
    discard: tuple[str, ...] = ()
    deck_layout: Layout = field(default_factory=Flat)

    def __post_init__(self) -> None:
        for pattern in self.discard:
            _compile_check(pattern, field_name="discard")
        seen: set[str] = set()
        for group in self.groups:
            if group.archive == _DECK_ARCHIVE_NAME:
                raise ValueError(
                    f"group archive {group.archive!r} collides with "
                    "the reserved deck archive name"
                )
            if group.archive in seen:
                raise ValueError(
                    f"duplicate group archive name: {group.archive!r}"
                )
            seen.add(group.archive)

    def is_discarded(self, rel_path: str) -> bool:
        return any(re.fullmatch(pattern, rel_path) for pattern in self.discard)


@dataclass(frozen=True, slots=True)
class RealizedOutputs:
    deck: str | None
    archives: tuple[str, ...]
    raw: tuple[tuple[str, str], ...]


def _snapshot(root: Path) -> list[str]:
    paths: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        rel_dir = current.relative_to(root)
        if rel_dir == Path("."):
            dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_ROOTS]
        for filename in filenames:
            full = current / filename
            if full.is_symlink() or not full.is_file():
                continue
            paths.append((rel_dir / filename).as_posix())
    return sorted(paths)


def _resolve_deck_inputs(
    ws: Workspace, deck_inputs: Sequence[str]
) -> list[Path]:
    root = ws.root.resolve()
    sources: list[Path] = []
    skipped: list[str] = []
    for name in deck_inputs:
        pure = PurePosixPath(name)
        if pure.is_absolute() or ".." in pure.parts:
            skipped.append(name)
            continue
        path = ws.root / pure
        if path.is_symlink() or not path.is_file():
            skipped.append(name)
            continue
        real = path.resolve()
        if real != root and root not in real.parents:
            skipped.append(name)
            continue
        sources.append(path)
    if skipped:
        logger.debug("skipping missing or unsafe deck inputs: %s", skipped)
    return sources


def _raw_destination(entry: RawFile, rel_path: str) -> str:
    # PurePosixPath drops a trailing "/" on entry.dest, so "sub" and
    # "sub/" normalize to the same destination and collide correctly
    # in the duplicate check below.
    basename = PurePosixPath(rel_path).name
    return (PurePosixPath(entry.dest) / basename).as_posix()


def _write_archive(
    path: Path,
    ws: Workspace,
    layout: Layout,
    rels: Sequence[str],
    *,
    workers: int,
) -> None:
    files = [ws.root / rel for rel in rels]
    if isinstance(layout, Tree):
        archive.write_tree(path, ws.root / layout.root, files, workers=workers)
    else:
        archive.write_flat(path, files, workers=workers)


def _clear_declared_outputs(plan: OutputPlan, ws: Workspace) -> None:
    declared = {_DECK_ARCHIVE_NAME, *(group.archive for group in plan.groups)}
    for name in declared:
        (ws.outputs_dir / name).unlink(missing_ok=True)


def realize(
    plan: OutputPlan, ws: Workspace, *, workers: int
) -> RealizedOutputs:
    """Realize an ``OutputPlan`` against a workspace.

    Resolution precedence:
      1. ``plan.deck_inputs`` are resolved first (missing, symlinked or
         escaping entries are skipped silently) and excluded from every
         group's candidate pool, matching v1's ``input_files``
         exclusion.
      2. Groups are resolved next, against the snapshot minus the deck
         inputs and minus every ``discard`` match.
      3. Non-residual raw entries (``residual=False``) are resolved in
         declaration order, against the snapshot minus every
         ``discard`` match. They do **not** exclude deck inputs or
         group members -- a raw entry may deliberately re-list a file a
         group also archives.
      4. Residual raw entries (``residual=True``) are resolved last, in
         declaration order, against the snapshot minus everything
         already claimed: deck inputs, every group member, every
         non-residual raw match, and every earlier residual raw match.
      ``discard`` excludes a file from every group and every raw entry,
      residual or not, but never from the deck, whose inputs are
      explicit.

    A single snapshot of regular, non-symlink workspace files is taken
    before anything is written. Any existing file under
    ``ws.outputs_dir`` whose name this plan declares (the deck archive
    or a group's archive) is removed before writing, so a re-run never
    leaves a stale archive for a group that no longer matches. Archives
    are written one at a time -- the deck first, then the groups in
    plan order -- each with up to ``workers`` threads compressing its
    members in parallel (ADR-040, parallelism clause amended by
    ticket-048b). An empty group writes nothing.
    """
    ws.ensure_layout()
    snapshot = _snapshot(ws.root)

    deck_sources = _resolve_deck_inputs(ws, plan.deck_inputs)
    deck_rel = {ws.relative(path) for path in deck_sources}
    claimed: set[str] = set(deck_rel)

    group_jobs: list[tuple[ZipGroup, list[str]]] = []
    for group in plan.groups:
        matches = sorted(
            rel
            for rel in snapshot
            if rel not in deck_rel
            and not plan.is_discarded(rel)
            and group.select.matches(rel)
        )
        group_jobs.append((group, matches))
        claimed.update(matches)

    raw_pairs: set[tuple[str, str]] = set()
    dest_sources: dict[str, str] = {}

    def _resolve_raw(entry: RawFile, pool: Sequence[str]) -> list[str]:
        matches = sorted(
            rel
            for rel in pool
            if not plan.is_discarded(rel) and entry.select.matches(rel)
        )
        for rel in matches:
            dest = _raw_destination(entry, rel)
            prior = dest_sources.get(dest)
            if prior is not None and prior != rel:
                raise ValueError(
                    f"raw destination collision {dest!r}: {prior} and {rel}"
                )
            dest_sources[dest] = rel
            raw_pairs.add((rel, dest))
        return matches

    for entry in plan.raw:
        if not entry.residual:
            claimed.update(_resolve_raw(entry, snapshot))
    for entry in plan.raw:
        if entry.residual:
            pool = [rel for rel in snapshot if rel not in claimed]
            claimed.update(_resolve_raw(entry, pool))

    _clear_declared_outputs(plan, ws)

    deck_rel_path: str | None = None
    if deck_sources:
        deck_archive_path = ws.outputs_dir / _DECK_ARCHIVE_NAME
        _write_archive(
            deck_archive_path,
            ws,
            plan.deck_layout,
            sorted(deck_rel),
            workers=workers,
        )
        deck_rel_path = ws.relative(deck_archive_path)

    jobs = [
        (ws.outputs_dir / group.archive, group.layout, members)
        for group, members in group_jobs
        if members
    ]
    for path, layout, members in jobs:
        _write_archive(path, ws, layout, members, workers=workers)

    return RealizedOutputs(
        deck=deck_rel_path,
        archives=tuple(sorted(ws.relative(path) for path, _, _ in jobs)),
        raw=tuple(sorted(raw_pairs)),
    )
