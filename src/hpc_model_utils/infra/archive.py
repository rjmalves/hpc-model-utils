"""infra.archive: stdlib-only safe zip adapter.

ADR-028 and the security brief require every member to be validated
before any extraction happens, so an unsafe archive fails loudly instead
of being half-extracted. ADR-040 and ADR-038 require streamed members
(never read whole into memory), Zip64 always on, flat decks with no
basename collisions, and tree archives whose members are POSIX paths
relative to a declared root that can never escape it.

Partial-archive policy: ``write_flat`` and ``write_tree`` validate every
member first -- for ``write_flat`` that includes the whole basename
collision table, for ``write_tree`` the root-escape check -- before
opening any zip file, so a validation failure never creates a file on
disk. Once validation has passed, the archive is built at a sibling
temporary path, ``archive.with_name("." + archive.name + ".part")``,
and only ``os.replace``'d onto ``archive`` after every member has been
written successfully; on any exception the partial file is unlinked.
``archive`` is therefore always either absent/unchanged or a complete,
valid zip, never a half-written one.
"""

from __future__ import annotations

import os
import re
import zipfile
from collections.abc import Collection, Sequence
from pathlib import Path, PurePosixPath

from hpc_model_utils.infra.errors import UnsafeArchiveError

_HPCMU_DIR = ".hpcmu"
_STORED_SUFFIXES = frozenset({".parquet", ".zip", ".gz", ".bz2", ".xz", ".7z"})
_DRIVE_LETTER_RE = re.compile(r"[A-Za-z]:.*")


def _normalized_parts(name: str) -> tuple[str, ...]:
    pure = PurePosixPath(name.replace("\\", "/"))
    if pure.is_absolute():
        raise UnsafeArchiveError(f"absolute member path: {name!r}")
    parts = pure.parts
    if not parts:
        raise UnsafeArchiveError(f"empty member name: {name!r}")
    if _DRIVE_LETTER_RE.fullmatch(parts[0]):
        raise UnsafeArchiveError(f"drive-like member path: {name!r}")
    if ".." in parts:
        raise UnsafeArchiveError(f"member path escapes archive root: {name!r}")
    if parts[0] == _HPCMU_DIR:
        raise UnsafeArchiveError(
            f"member path under reserved {_HPCMU_DIR}/: {name!r}"
        )
    return parts


def validated_members(archive: Path) -> list[str]:
    try:
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
    except zipfile.BadZipFile as err:
        raise UnsafeArchiveError(f"not a zip archive: {archive.name}") from err
    names: list[str] = []
    for info in infos:
        if info.is_dir():
            continue
        _normalized_parts(info.filename)
        names.append(info.filename)
    return names


def extract(
    archive: Path, dest: Path, *, members: Collection[str] | None = None
) -> list[str]:
    valid = validated_members(archive)
    selected = valid if members is None else [n for n in valid if n in members]
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        for name in selected:
            zf.extract(name, dest)
    return sorted(selected)


def common_root(names: Sequence[str]) -> str | None:
    if not names:
        return None
    roots: set[str] = set()
    for name in names:
        parts = PurePosixPath(name).parts
        if len(parts) < 2:
            return None
        roots.add(parts[0])
    if len(roots) != 1:
        return None
    return roots.pop()


def compression_for(path: Path) -> int:
    if path.suffix.lower() in _STORED_SUFFIXES:
        return zipfile.ZIP_STORED
    return zipfile.ZIP_DEFLATED


def _write_zip(archive: Path, entries: Sequence[tuple[Path, str]]) -> None:
    tmp = archive.with_name("." + archive.name + ".part")
    try:
        with zipfile.ZipFile(
            tmp, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
        ) as zf:
            for path, arcname in entries:
                zf.write(path, arcname, compress_type=compression_for(path))
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, archive)


def write_flat(archive: Path, files: Sequence[Path]) -> list[str]:
    seen: dict[str, Path] = {}
    for path in files:
        name = path.name
        if name in seen:
            raise UnsafeArchiveError(
                f"duplicate basename {name!r}: {seen[name]} and {path}"
            )
        seen[name] = path
    _write_zip(archive, [(path, name) for name, path in seen.items()])
    return sorted(seen)


def write_tree(archive: Path, root: Path, files: Sequence[Path]) -> list[str]:
    root_resolved = root.resolve()
    entries: list[tuple[Path, str]] = []
    for path in files:
        resolved = path.resolve()
        try:
            arcname = resolved.relative_to(root_resolved).as_posix()
        except ValueError as err:
            raise UnsafeArchiveError(
                f"path escapes root {root}: {path}"
            ) from err
        entries.append((path, arcname))
    _write_zip(archive, entries)
    return sorted(arcname for _, arcname in entries)
