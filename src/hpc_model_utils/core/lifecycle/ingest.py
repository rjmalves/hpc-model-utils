"""ADR-003/ADR-011/ADR-009/R10/R93/R96: the plugin-agnostic offline-run
ingestion lifecycle step (v1's ``NEWAVE.ingest_offline_run``,
``app/adapter/repository/newave.py:1018-1125``, ported once, R26).

A plugin supports offline ingestion by overriding
``ModelPlugin.ingest_offline`` (ADR-003); the check happens before any
S3 call. The three archives are downloaded by exact key only -- never
``download_prefix`` -- into ``ws.hpcmu_dir / "offline"``, so an
arbitrary upstream key name never collides with a workspace basename
and never lands under the outputs root. ``RunState.inputs`` and
``RunState.parent`` are left untouched: an offline run has neither
(R10), and NEWAVE's ``metadata_items`` only writes ``parent_path``
when ``inputs`` is set.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from hpc_model_utils.core.diagnosis import utc_now_iso
from hpc_model_utils.core.errors import DataError, UsageError
from hpc_model_utils.core.lifecycle.run import StatusReporter
from hpc_model_utils.core.plugin import ModelPlugin
from hpc_model_utils.core.state import (
    ExecutionSource,
    ModelInfo,
    RunState,
    StateStore,
    StepRecord,
    metadata_items,
    write_projections,
)
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.infra.errors import ObjectNotFoundError
from hpc_model_utils.infra.s3 import ObjectStore, S3Uri

_STAGED_NAMES: tuple[str, ...] = ("inputs.zip", "outputs.zip", "cortes.zip")
_METADATA_KEYS: tuple[str, ...] = (
    "study_starting_date",
    "study_name",
    "execution_source",
)


def _validate_uri(uri: str) -> S3Uri:
    try:
        parsed = S3Uri.parse(uri)
    except ValueError as exc:
        raise UsageError(f"invalid offline archive uri {uri!r}: {exc}") from exc
    if not parsed.key or parsed.key.endswith("/"):
        raise UsageError(
            f"invalid offline archive uri {uri!r}: must be an exact key"
        )
    return parsed


def ingest_offline_run(
    ws: Workspace,
    plugin: ModelPlugin,
    store: ObjectStore,
    reporter: StatusReporter,
    state_store: StateStore,
    uris: Sequence[str],
) -> RunState:
    if len(uris) != 3:
        raise UsageError(
            f"ingest_offline_run requires exactly 3 archive uris, got "
            f"{len(uris)}"
        )
    parsed_uris = tuple(_validate_uri(uri) for uri in uris)

    if type(plugin).ingest_offline is ModelPlugin.ingest_offline:
        raise UsageError(f"{plugin.name} does not support offline ingestion")

    started_at = utc_now_iso()
    entry_monotonic = time.monotonic()

    state = state_store.load_or_create(plugin.name)
    model_was_none = state.model is None

    staging_dir = ws.hpcmu_dir / "offline"
    archives: list[Path] = []
    for uri, parsed, name in zip(uris, parsed_uris, _STAGED_NAMES, strict=True):
        dest = staging_dir / name
        try:
            store.download(parsed, dest)
        except ObjectNotFoundError as err:
            raise DataError(f"offline archive not found: {uri}") from err
        archives.append(dest)

    plugin.ingest_offline(ws, tuple(archives))
    study = plugin.study_info(ws)

    step = StepRecord(
        command="ingest_offline_run",
        host=socket.gethostname(),
        started_at=started_at,
        finished_at=utc_now_iso(),
        duration_seconds=time.monotonic() - entry_monotonic,
        outcome="ok",
    )
    new_state = replace(
        state,
        model=state.model or ModelInfo(plugin.model_name, None),
        study=study,
        execution_source=ExecutionSource.OFFLINE,
        steps=state.steps + (step,),
    )
    state_store.save(new_state)
    write_projections(
        ws,
        new_state,
        always_write_parent_path=plugin.always_write_parent_path,
    )

    keys = ("model_name", *_METADATA_KEYS) if model_was_none else _METADATA_KEYS
    for key, value in metadata_items(
        new_state, always_write_parent_path=plugin.always_write_parent_path
    ):
        if key in keys:
            reporter.metadata(key, value)

    return new_state
