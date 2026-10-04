"""ADR-003/ADR-011/ADR-009/R10/R93/R96 tests for NEWAVE offline-run
ingestion (ticket-049)."""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from hpc_model_utils.core.diagnosis import Diagnosis, RunStatus
from hpc_model_utils.core.errors import DataError, UsageError
from hpc_model_utils.core.lifecycle.fetch import fetch_executables
from hpc_model_utils.core.lifecycle.ingest import ingest_offline_run
from hpc_model_utils.core.state import (
    ModelInfo,
    RunState,
    StateStore,
    render_metadata,
)
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.infra.s3 import S3Uri
from hpc_model_utils.models.newave import NewavePlugin, deck, offline
from tests.support.decks import FIXTURES, binary_cut_files, input_zip
from tests.support.fake_plugin import FakePlugin
from tests.support.fake_slurm.cli import write_executable_stub
from tests.support.object_store import RecordingObjectStore
from tests.support.reporter import RecordingReporter

_PROJECTIONS_PATH = (
    Path(__file__).resolve().parents[3]
    / "goldens"
    / "v1_1_2"
    / "projections.json"
)
_STUDY_NAME = "PMO - NOVEMBRO - 2025 - Niveis para 01/11 - NW Versao 30.0.4"

_VERSIONS_BUCKET = "versions-bucket"
_INPUTS_BUCKET = "inputs-bucket"
_VERSOES_URI = f"s3://{_VERSIONS_BUCKET}/versoes/newave/30.0.4/"
_INPUTS_URI = f"s3://{_INPUTS_BUCKET}/ingest/offline/inputs.zip"
_OUTPUTS_URI = f"s3://{_INPUTS_BUCKET}/ingest/offline/outputs.zip"
_CORTES_URI = f"s3://{_INPUTS_BUCKET}/ingest/offline/cortes.zip"
_CUT_NAMES = ("cortes-001.dat", "cortes-002.dat")
_PMO_CONTENT = b"PMO OUTPUT\n"


def _load_golden_metadata(sequence: str) -> str:
    data = json.loads(_PROJECTIONS_PATH.read_text(encoding="utf-8"))
    return str(data["sequences"][sequence]["metadata.modelops"])


def _seed_offline_archives(
    store: RecordingObjectStore, tmp_path: Path
) -> dict[str, bytes]:
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    write_executable_stub(stub_dir, "newave", "exit 0")
    write_executable_stub(stub_dir, "ConverteNomesArquivos", "exit 0")
    store.seed(
        S3Uri(_VERSIONS_BUCKET, "versoes/newave/30.0.4/newave"),
        (stub_dir / "newave").read_bytes(),
    )
    store.seed(
        S3Uri(_VERSIONS_BUCKET, "versoes/newave/30.0.4/ConverteNomesArquivos"),
        (stub_dir / "ConverteNomesArquivos").read_bytes(),
    )

    archives_dir = tmp_path / "archives"
    archives_dir.mkdir()
    inputs_zip = archives_dir / "inputs.zip"
    inputs_zip.write_bytes(
        (FIXTURES / "decks" / "deck_newave.zip").read_bytes()
    )
    store.seed(S3Uri.parse(_INPUTS_URI), inputs_zip.read_bytes())

    outputs_zip = input_zip(
        {"pmo.dat": _PMO_CONTENT}, archives_dir / "outputs.zip"
    )
    store.seed(S3Uri.parse(_OUTPUTS_URI), outputs_zip.read_bytes())

    cuts = binary_cut_files(_CUT_NAMES)
    cortes_zip = input_zip(cuts, archives_dir / "cortes.zip")
    store.seed(S3Uri.parse(_CORTES_URI), cortes_zip.read_bytes())

    return cuts


def _run_offline_sequence(
    tmp_path: Path, *, with_prior_fetch: bool
) -> tuple[Workspace, RunState, RecordingReporter, dict[str, bytes]]:
    root = tmp_path / "ws"
    root.mkdir()
    ws = Workspace.at(root)
    store = RecordingObjectStore()
    cuts = _seed_offline_archives(store, tmp_path)

    plugin = NewavePlugin()
    state_store = StateStore(ws)
    reporter = RecordingReporter()

    if with_prior_fetch:
        fetch_executables(
            ws, plugin, store, reporter, state_store, _VERSOES_URI
        )
        reporter.metadata_calls.clear()

    state = ingest_offline_run(
        ws,
        plugin,
        store,
        reporter,
        state_store,
        (_INPUTS_URI, _OUTPUTS_URI, _CORTES_URI),
    )
    return ws, state, reporter, cuts


# -- AC1: golden `newave_offline` metadata ---------------------------------


def test_ingest_offline_run_golden_sequence_matches_metadata(
    tmp_path: Path,
) -> None:
    _, state, _, _ = _run_offline_sequence(tmp_path, with_prior_fetch=True)

    final_state = replace(
        state,
        diagnosis=Diagnosis(
            status=RunStatus.SUCCESS, rule_id="newave.manual", reason="ok"
        ),
        reported_job_id="1005",
    )
    rendered = render_metadata(
        final_state,
        always_write_parent_path=NewavePlugin().always_write_parent_path,
    )
    assert rendered == _load_golden_metadata("newave_offline")
    assert final_state.inputs is None
    assert final_state.parent is None


# -- AC2: workspace shape ----------------------------------------------------


def test_ingest_offline_run_workspace_shape_matches_expected(
    tmp_path: Path,
) -> None:
    ws, _, _, cuts = _run_offline_sequence(tmp_path, with_prior_fetch=True)

    assert not (ws.hpcmu_dir / "offline").exists()

    assert (ws.root / "pmo.dat").is_file()
    assert (ws.root / "pmo.dat").read_bytes() == _PMO_CONTENT
    for name, payload in cuts.items():
        assert (ws.root / name).read_bytes() == payload

    existing_input_files = sorted(
        name for name in deck.input_files(ws) if (ws.root / name).is_file()
    )
    echoed_dir = tmp_path / "echoed"
    echoed_dir.mkdir()
    with zipfile.ZipFile(ws.eco_deck_path) as zf:
        assert sorted(zf.namelist()) == existing_input_files
        zf.extract("caso.dat", echoed_dir)

    echoed_ws = Workspace.at(echoed_dir)
    assert deck.caso(echoed_ws).gerenciador_processos == ""

    assert deck.caso(ws).gerenciador_processos == f"{ws.assets}/"
    assert deck.dger(ws).nome_caso == _STUDY_NAME


# -- AC3: hooks ---------------------------------------------------------------


def test_ingest_offline_run_with_prior_fetch_emits_three_metadata_keys(
    tmp_path: Path,
) -> None:
    _, _, reporter, _ = _run_offline_sequence(tmp_path, with_prior_fetch=True)

    assert [key for key, _ in reporter.metadata_calls] == [
        "study_starting_date",
        "study_name",
        "execution_source",
    ]


def test_ingest_offline_run_without_prior_fetch_emits_model_name_too(
    tmp_path: Path,
) -> None:
    _, state, reporter, _ = _run_offline_sequence(
        tmp_path, with_prior_fetch=False
    )

    assert [key for key, _ in reporter.metadata_calls] == [
        "model_name",
        "study_starting_date",
        "study_name",
        "execution_source",
    ]
    assert state.model == ModelInfo("NEWAVE", None)


# -- AC4: refusals ------------------------------------------------------------


def _new_ingest_fixtures(
    tmp_path: Path,
) -> tuple[Workspace, RecordingObjectStore, RecordingReporter, StateStore]:
    root = tmp_path / "ws"
    root.mkdir()
    ws = Workspace.at(root)
    store = RecordingObjectStore()
    reporter = RecordingReporter()
    state_store = StateStore(ws)
    return ws, store, reporter, state_store


def test_ingest_offline_run_unsupported_plugin_raises_usage_error_before_download(
    tmp_path: Path,
) -> None:
    ws, store, reporter, state_store = _new_ingest_fixtures(tmp_path)

    with pytest.raises(UsageError, match="does not support offline ingestion"):
        ingest_offline_run(
            ws,
            FakePlugin(),
            store,
            reporter,
            state_store,
            (_INPUTS_URI, _OUTPUTS_URI, _CORTES_URI),
        )

    assert not any(op == "download" for op, _ in store.ops)


def test_ingest_offline_run_missing_outputs_object_raises_data_error_naming_uri(
    tmp_path: Path,
) -> None:
    ws, store, reporter, state_store = _new_ingest_fixtures(tmp_path)
    store.seed(
        S3Uri.parse(_INPUTS_URI),
        (FIXTURES / "decks" / "deck_newave.zip").read_bytes(),
    )

    with pytest.raises(DataError, match=re.escape(_OUTPUTS_URI)):
        ingest_offline_run(
            ws,
            NewavePlugin(),
            store,
            reporter,
            state_store,
            (_INPUTS_URI, _OUTPUTS_URI, _CORTES_URI),
        )


def test_ingest_offline_run_two_uris_raises_usage_error(
    tmp_path: Path,
) -> None:
    ws, store, reporter, state_store = _new_ingest_fixtures(tmp_path)

    with pytest.raises(UsageError, match="requires exactly 3"):
        ingest_offline_run(
            ws,
            NewavePlugin(),
            store,
            reporter,
            state_store,
            (_INPUTS_URI, _OUTPUTS_URI),
        )


def test_ingest_offline_run_empty_key_uri_raises_usage_error(
    tmp_path: Path,
) -> None:
    ws, store, reporter, state_store = _new_ingest_fixtures(tmp_path)

    with pytest.raises(UsageError, match="must be an exact key"):
        ingest_offline_run(
            ws,
            NewavePlugin(),
            store,
            reporter,
            state_store,
            (_INPUTS_URI, "s3://b-1/", _CORTES_URI),
        )


# -- extraction-order overwrite (Testing Requirements) -----------------------


def test_ingest_offline_same_member_in_inputs_and_outputs_outputs_wins(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "assets").mkdir()
    ws = Workspace.at(root)

    archives_dir = tmp_path / "archives"
    archives_dir.mkdir()
    inputs_zip = archives_dir / "inputs.zip"
    inputs_zip.write_bytes(
        (FIXTURES / "decks" / "deck_newave.zip").read_bytes()
    )
    outputs_zip = input_zip(
        {"hidr.dat": b"OVERRIDDEN"}, archives_dir / "outputs.zip"
    )
    cortes_zip = input_zip({}, archives_dir / "cortes.zip")

    offline.ingest_offline(
        NewavePlugin(), ws, (inputs_zip, outputs_zip, cortes_zip)
    )

    assert (ws.root / "hidr.dat").read_bytes() == b"OVERRIDDEN"
