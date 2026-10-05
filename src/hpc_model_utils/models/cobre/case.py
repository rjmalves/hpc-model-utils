"""ADR-003/ADR-017/R73/R74: the cobre case readers (ticket-068).

A cobre case is a directory (``config.json``, ``stages.json``,
``system/*.json``, ...) delivered as ``eco_deck.zip``, either flat or
under one top folder (R74). Every reader here works from the zip's
member list, so the case root is always derived the same way, and every
absent or malformed file is an explicit ``DataError`` -- never an empty
default. Messages carry zip-relative names (``caso/stages.json``), never
an absolute path.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hpc_model_utils.core.errors import DataError
from hpc_model_utils.core.state import StudyInfo
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.infra.archive import common_root, validated_members
from hpc_model_utils.infra.errors import UnsafeArchiveError

OUTPUT_PATTERNS: tuple[str, ...] = (r"(?:[^/]+/)?output/.+",)

_START_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


@dataclass(frozen=True, slots=True)
class Phases:
    training: bool
    simulation: bool


def deck_members(ws: Workspace) -> tuple[str, ...]:
    if not ws.eco_deck_path.is_file():
        raise DataError("eco_deck.zip missing: the cobre case is read from it")
    try:
        return tuple(validated_members(ws.eco_deck_path))
    except UnsafeArchiveError as err:
        raise DataError(str(err)) from err
    except OSError as err:
        raise DataError("eco_deck.zip cannot be read") from err


def top_folder(ws: Workspace) -> str | None:
    return common_root(deck_members(ws))


def case_root(ws: Workspace) -> Path:
    top = top_folder(ws)
    return ws.root if top is None else ws.root / top


def case_prefix(ws: Workspace) -> str:
    top = top_folder(ws)
    return "" if top is None else f"{top}/"


def _read_object(ws: Workspace, name: str) -> Mapping[str, object]:
    label = f"{case_prefix(ws)}{name}"
    path = case_root(ws) / name
    if not path.is_file():
        raise DataError(f"missing {label}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as err:
        raise DataError(f"{label}: cannot be read as UTF-8 text") from err
    except json.JSONDecodeError as err:
        raise DataError(f"{label}: invalid JSON: {err}") from err
    if not isinstance(data, dict):
        raise DataError(f"{label}: not a JSON object")
    return data


def read_config(ws: Workspace) -> Mapping[str, object]:
    return _read_object(ws, "config.json")


def _enabled(
    config: Mapping[str, object], ws: Workspace, section: str, default: bool
) -> bool:
    label = f"{case_prefix(ws)}config.json"
    if section not in config:
        return default
    body = config[section]
    if not isinstance(body, dict):
        raise DataError(f"{label}: {section} is not an object")
    value = body.get("enabled", default)
    if not isinstance(value, bool):
        raise DataError(f"{label}: {section}.enabled is not a boolean")
    return value


def phases(ws: Workspace) -> Phases:
    config = read_config(ws)
    return Phases(
        training=_enabled(config, ws, "training", True),
        simulation=_enabled(config, ws, "simulation", False),
    )


def _start_date(entry: object, label: str, index: int) -> datetime:
    value = entry.get("start_date") if isinstance(entry, dict) else None
    if not isinstance(value, str) or not _START_DATE.fullmatch(value):
        raise DataError(
            f"{label}: stages[{index}].start_date is not a YYYY-MM-DD date"
        )
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as err:
        raise DataError(
            f"{label}: stages[{index}].start_date is not a valid date"
        ) from err


def study_info(ws: Workspace) -> StudyInfo:
    label = f"{case_prefix(ws)}stages.json"
    stages = _read_object(ws, "stages.json").get("stages")
    if not isinstance(stages, list) or not stages:
        raise DataError(f"{label}: stages is not a non-empty list")
    earliest = min(
        _start_date(entry, label, index) for index, entry in enumerate(stages)
    )
    return StudyInfo(
        name=top_folder(ws) or "", starting_date=earliest.isoformat()
    )


def input_files(ws: Workspace) -> tuple[str, ...]:
    outputs = tuple(re.compile(p, re.IGNORECASE) for p in OUTPUT_PATTERNS)
    names = sorted(
        {
            member
            for member in deck_members(ws)
            if not any(pattern.fullmatch(member) for pattern in outputs)
        }
    )
    if not names:
        raise DataError("the cobre case zip has no input members")
    return tuple(names)
