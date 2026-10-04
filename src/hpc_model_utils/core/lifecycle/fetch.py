"""ADR-043/ADR-028/ADR-011/ADR-009/R10/R17/R21/R94/R136: the plugin-
agnostic fetch lifecycle steps (v1's ``check_and_fetch_executables``
and ``check_and_fetch_inputs``, ported once instead of twice per
model, R26: the lifecycle owns S3).

ADR-043 (supersedes ADR-041): every parent artifact -- the gating
``metadata.modelops`` and each of ``plugin.parent_artifacts`` -- is
read from the parent URI's *own* bucket (R94), never the input
bucket. A parent's ``model_name`` must equal ``plugin.parent_model``
by exact string compare, and its ``status`` must equal ``"SUCCESS"``
by exact string compare too, never ``RunStatus.parse`` -- a stray
trailing space must fail closed rather than coerce to ``UNKNOWN``.
Every parent rejection is a ``DataError`` (D7, R14): it means the
``parentPath`` input was bad.

``fetch_inputs`` never deletes the input object until every download
and parent check has succeeded; v1 deleted first and lost the input
on a rejected parent. ``fetch_executables`` downloads by prefix (the
versoes layout) and chmods ``0o755``, never v1's ``0o777``;
``fetch_inputs`` downloads the input deck by its *exact* key, never a
prefix -- v1's prefix match grabbed ``deck.zip.bak`` for a
``deck.zip`` request.
"""

from __future__ import annotations

import json
import re
import socket
import time
from dataclasses import replace
from pathlib import Path

from hpc_model_utils.core.diagnosis import utc_now_iso
from hpc_model_utils.core.errors import DataError, UsageError
from hpc_model_utils.core.lifecycle.run import StatusReporter
from hpc_model_utils.core.plugin import ModelPlugin
from hpc_model_utils.core.state import (
    InputsInfo,
    ModelInfo,
    ParentInfo,
    RunState,
    StateStore,
    StepRecord,
    metadata_items,
    write_projections,
)
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.infra.errors import ObjectNotFoundError
from hpc_model_utils.infra.s3 import ObjectStore, S3Uri

_VERSOES_PATTERN = re.compile(
    r"s3://(?P<bucket>[a-z0-9][a-z0-9.-]{1,61}[a-z0-9])/versoes/"
    r"(?P<model>[A-Za-z0-9][A-Za-z0-9._-]{0,63})/"
    r"(?P<version>[A-Za-z0-9][A-Za-z0-9._-]{0,63})/?"
)
_WHITESPACE_PATTERN = re.compile(r"\s")
_EMPTY_PARENT_TOKENS = frozenset({"", "''", '""'})


def normalize_parent_path(raw: str) -> str | None:
    """C9: ``None`` for an empty or quoted-empty ``parentPath`` -- the
    quoted-heredoc idiom of R128 can deliver the literal two-character
    value ``''`` or ``\"\"`` rather than a truly empty string."""
    stripped = raw.strip()
    if stripped in _EMPTY_PARENT_TOKENS:
        return None
    return stripped


def _emit(
    reporter: StatusReporter,
    state: RunState,
    plugin: ModelPlugin,
    keys: tuple[str, ...],
) -> None:
    for key, value in metadata_items(
        state, always_write_parent_path=plugin.always_write_parent_path
    ):
        if key in keys:
            reporter.metadata(key, value)


def _parse_versoes_uri(uri: str, plugin: ModelPlugin) -> tuple[str, str, str]:
    match = _VERSOES_PATTERN.fullmatch(uri)
    if match is None:
        raise UsageError(f"invalid versoes uri: {uri!r}")
    bucket = match.group("bucket")
    model = match.group("model")
    version = match.group("version")
    if ".." in model or ".." in version:
        raise UsageError(f"invalid versoes uri: {uri!r}")
    if model.lower() != plugin.name:
        raise UsageError(
            f"versoes model {model!r} does not match plugin "
            f"{plugin.name!r}: {uri!r}"
        )
    return bucket, model, version


def fetch_executables(
    ws: Workspace,
    plugin: ModelPlugin,
    store: ObjectStore,
    reporter: StatusReporter,
    state_store: StateStore,
    uri: str,
) -> RunState:
    bucket, model, version = _parse_versoes_uri(uri, plugin)

    started_at = utc_now_iso()
    entry_monotonic = time.monotonic()

    state = state_store.load_or_create(plugin.name)

    try:
        paths = store.download_prefix(
            S3Uri(bucket, f"versoes/{model}/{version}/"), ws.assets
        )
    except ObjectNotFoundError as err:
        raise DataError(
            f"no executables found under versoes prefix: {uri}"
        ) from err

    seen: set[Path] = set()
    for path in paths:
        if path in seen:
            raise DataError(
                "duplicate executable basename under versoes prefix: "
                f"{path.name!r}"
            )
        seen.add(path)
    for path in paths:
        path.chmod(0o755)

    plugin.check_executables(ws)

    step = StepRecord(
        command="check_and_fetch_executables",
        host=socket.gethostname(),
        started_at=started_at,
        finished_at=utc_now_iso(),
        duration_seconds=time.monotonic() - entry_monotonic,
        outcome="ok",
    )
    new_state = replace(
        state,
        model=ModelInfo(plugin.model_name, version),
        steps=state.steps + (step,),
    )
    state_store.save(new_state)
    write_projections(
        ws,
        new_state,
        always_write_parent_path=plugin.always_write_parent_path,
    )
    _emit(reporter, new_state, plugin, ("model_name", "model_version"))
    return new_state


def read_parent(
    store: ObjectStore, plugin: ModelPlugin, parent_uri: str
) -> ParentInfo:
    """The ADR-043 reader: GET ``<parent_uri>/saidas/metadata.modelops``
    from the parent URI's own bucket and gate on it. ``run.json`` is
    never read."""
    parent_s3 = S3Uri.parse(parent_uri)
    try:
        raw = store.get_bytes(parent_s3.join("saidas", "metadata.modelops"))
    except ObjectNotFoundError as err:
        raise DataError(
            f"parent metadata.modelops not found: {parent_uri}"
        ) from err
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as err:
        raise DataError(
            f"parent metadata.modelops is not valid UTF-8: {parent_uri}"
        ) from err
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError as err:
        raise DataError(
            f"parent metadata.modelops is not valid JSON: {parent_uri}"
        ) from err
    if not isinstance(parsed, dict):
        raise DataError(
            f"parent metadata.modelops is not a JSON object: {parent_uri}"
        )
    model_name = parsed.get("model_name")
    status = parsed.get("status")
    starting_date = parsed.get("study_starting_date")
    if (
        not isinstance(model_name, str)
        or not isinstance(status, str)
        or not isinstance(starting_date, str)
    ):
        raise DataError(
            "parent metadata.modelops missing a required string field "
            f"(model_name/status/study_starting_date): {parent_uri}"
        )
    if status != "SUCCESS":
        raise DataError(
            f"parent run status is not SUCCESS: {status!r} ({parent_uri})"
        )
    if model_name != plugin.parent_model:
        raise DataError(
            "parent model_name mismatch: expected "
            f"{plugin.parent_model!r}, got {model_name!r} ({parent_uri})"
        )
    return ParentInfo(
        path=parent_uri, model_name=model_name, starting_date=starting_date
    )


def _validate_input_uri(uri: str) -> S3Uri:
    try:
        parsed = S3Uri.parse(uri)
    except ValueError as exc:
        raise UsageError(f"invalid input uri {uri!r}: {exc}") from exc
    if not parsed.key or parsed.key.endswith("/"):
        raise UsageError(f"invalid input uri {uri!r}: must be an exact key")
    return parsed


def _validate_parent_uri(parent: str) -> S3Uri:
    try:
        parsed = S3Uri.parse(parent)
    except ValueError as exc:
        raise UsageError(f"invalid parent uri {parent!r}: {exc}") from exc
    if not parsed.key:
        raise UsageError(f"invalid parent uri {parent!r}: empty key")
    if ".." in parsed.key.split("/"):
        raise UsageError(f"invalid parent uri {parent!r}: contains ..")
    if _WHITESPACE_PATTERN.search(parent):
        raise UsageError(f"invalid parent uri {parent!r}: contains whitespace")
    return parsed


def fetch_inputs(
    ws: Workspace,
    plugin: ModelPlugin,
    store: ObjectStore,
    reporter: StatusReporter,
    state_store: StateStore,
    uri: str,
    *,
    parent_path: str,
    delete: bool,
) -> RunState:
    input_s3 = _validate_input_uri(uri)
    parent = normalize_parent_path(parent_path)
    parent_s3: S3Uri | None = None
    if parent is not None:
        parent_s3 = _validate_parent_uri(parent)
        if plugin.parent_model is None:
            raise UsageError(f"{plugin.name} does not support parent runs")

    started_at = utc_now_iso()
    entry_monotonic = time.monotonic()

    state = state_store.load_or_create(plugin.name)

    try:
        store.download(input_s3, ws.eco_deck_path)
    except ObjectNotFoundError as err:
        raise DataError(f"input object not found: {uri}") from err

    parent_info: ParentInfo | None = None
    if parent_s3 is not None:
        assert parent is not None
        parent_info = read_parent(store, plugin, parent)
        for name in plugin.parent_artifacts:
            try:
                store.download(
                    parent_s3.join("saidas", name), ws.parent_dir / name
                )
            except ObjectNotFoundError as err:
                raise DataError(f"parent artifact not found: {name}") from err

    step = StepRecord(
        command="check_and_fetch_inputs",
        host=socket.gethostname(),
        started_at=started_at,
        finished_at=utc_now_iso(),
        duration_seconds=time.monotonic() - entry_monotonic,
        outcome="ok",
    )
    new_state = replace(
        state,
        inputs=InputsInfo(source=uri, parent_path=parent or ""),
        parent=parent_info,
        steps=state.steps + (step,),
    )
    state_store.save(new_state)
    write_projections(
        ws,
        new_state,
        always_write_parent_path=plugin.always_write_parent_path,
    )
    _emit(reporter, new_state, plugin, ("parent_path", "parent_starting_date"))

    if delete:
        store.delete(input_s3)

    return new_state
