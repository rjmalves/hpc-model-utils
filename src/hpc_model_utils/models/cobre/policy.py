"""ADR-098/ADR-099/ADR-100/ADR-101/ADR-102: the cobre parent-run
checks (ticket-002, ticket-003).

A cobre child reuses a parent's policy only when both ran the same
novomodelo release; a patched build such as ``v0.19.1-fixinflows``
counts as its release ``v0.19.1``. The release-tag rule is a verbatim
copy of the one in ``deploy/modelops/apply.py``, because ``src/``
cannot depend on ``deploy/``. No drift test pins the two together, by
operator decision: the literal case table in ``test_cobre_policy.py``
pins this copy's behaviour instead.

``restore`` puts a parent's ``policy.zip`` into ``<case>/output/policy``,
where novomodelo reads it, and runs every check before it writes
anything: the archive is safe, every entry sits under ``policy/``, the
manifest and at least one cut are present, ``policy.path`` is the
default, and the parent's start date is this case's. The first failure
wins. ``policy.path`` is only ever the default in a parent run, because
the policy is restored to that one place; a custom path fails closed.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from hpc_model_utils.core.errors import DataError, UsageError
from hpc_model_utils.core.state import ParentInfo
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.infra.archive import (
    extract,
    validated_directories,
    validated_members,
)
from hpc_model_utils.models.cobre import case

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
_MODEL_VERSION_RE = re.compile(
    rf"(?P<release>{_TAG_RE.pattern})(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
_DEFAULT_POLICY_PATHS = frozenset({"./policy", "policy"})


def _release_tag(version: str) -> str | None:
    match = _MODEL_VERSION_RE.fullmatch(version)
    return None if match is None else match["release"]


def check_versions(
    parent: ParentInfo,
    parent_version: str | None,
    child_version: str | None,
) -> None:
    """Require the parent and this run to share one cobre release tag.

    The child's version is checked first, so a missing child version is a
    ``UsageError`` (executables not fetched) even when the parent's
    version is also bad; every other failure is a ``DataError``.
    """
    if child_version is None:
        raise UsageError(
            "cobre model version unknown: run check_and_fetch_executables first"
        )
    child_tag = _release_tag(child_version)
    if child_tag is None:
        raise DataError(
            f"cobre model version {child_version!r} is not a release tag "
            "(vX.Y.Z) or a patched build of one"
        )
    if parent_version is None:
        raise DataError(
            f"parent metadata.modelops has no model_version ({parent.path})"
        )
    parent_tag = _release_tag(parent_version)
    if parent_tag is None:
        raise DataError(
            f"parent model_version {parent_version!r} is not a release tag "
            f"(vX.Y.Z) or a patched build of one ({parent.path})"
        )
    if parent_tag != child_tag:
        raise DataError(
            f"parent model_version {parent_version!r} (release {parent_tag}) "
            f"does not match this run's {child_version!r} "
            f"(release {child_tag}) ({parent.path})"
        )


def _check_entries(files: Sequence[str], directories: Sequence[str]) -> None:
    for entry in sorted([*files, *directories]):
        if not entry.startswith("policy/"):
            raise DataError(
                f"parent policy.zip has an entry outside policy/: {entry!r}"
            )
    if "policy/manifest.bin" not in files:
        raise DataError("parent policy.zip has no policy/manifest.bin")
    if not any(name.startswith("policy/cuts/") for name in files):
        raise DataError("parent policy.zip has no file under policy/cuts/")


def _check_policy_path(ws: Workspace, prefix: str) -> None:
    section = case.read_config(ws).get("policy")
    if not isinstance(section, dict):
        return
    path = section.get("path")
    if isinstance(path, str) and path not in _DEFAULT_POLICY_PATHS:
        raise DataError(
            f"{prefix}config.json: policy.path must be absent, ./policy or "
            "policy in a parent run (the parent policy is restored only to "
            f"{prefix}output/policy)"
        )


def _check_start_date(
    ws: Workspace, prefix: str, parent_starting_date: str
) -> None:
    case_date = case.study_info(ws).starting_date
    try:
        same = datetime.fromisoformat(
            parent_starting_date
        ) == datetime.fromisoformat(case_date)
    except ValueError:
        same = False
    if not same:
        raise DataError(
            f"parent study_starting_date {parent_starting_date!r} does not "
            f"match this case's start date {case_date!r} "
            f"({prefix}stages.json)"
        )


def _count(files: Sequence[str], folder: str) -> int:
    return sum(1 for name in files if name.startswith(f"policy/{folder}/"))


def restore(ws: Workspace, archive: Path, parent_starting_date: str) -> None:
    """Restore the parent's ``policy.zip`` into ``<case>/output/policy``.

    Every check runs before the first write, so a rejected parent leaves
    ``<case>/output`` untouched. ``UnsafeArchiveError`` from the archive
    and the ``DataError`` of a case file read both propagate unchanged;
    each is a DATA_ERROR. An existing file at a member's path is
    overwritten, which makes a retry idempotent.
    """
    files = validated_members(archive)
    directories = validated_directories(archive)
    _check_entries(files, directories)
    prefix = case.case_prefix(ws)
    _check_policy_path(ws, prefix)
    _check_start_date(ws, prefix, parent_starting_date)
    output = case.case_root(ws) / "output"
    extract(archive, output, members=set(files))
    (output / "policy" / "basis").mkdir(parents=True, exist_ok=True)
    logger.info(
        "parent policy restored into %s: manifest.bin, %d cuts, %d basis, "
        "%d states",
        f"{prefix}output/policy",
        _count(files, "cuts"),
        _count(files, "basis"),
        _count(files, "states"),
    )
