"""ticket-055c: the drop-in replay suite (ADR-047, R10, R11, R86, R95,
R96, R101, R133) -- the only proof that bumping ``utilsAppVersion`` in
the existing prd workflows is enough (R86). Each sequence runs the
real C1 commands through the legacy clone's console script, as a real
subprocess, against the fake SLURM scheduler and a file-backed object
store; nothing here shares an in-memory store with the subprocess
(ticket-053's ``cli.workflow.object_store`` seam is patched only
inside the generated script).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from hpc_model_utils.infra.errors import ObjectNotFoundError
from hpc_model_utils.infra.s3 import S3Uri
from tests.support.command_lines import load_command_lines, render
from tests.support.decks import (
    FIXTURES,
    archive_golden,
    binary_cut_files,
    input_zip,
)
from tests.support.fake_models import seed_versions
from tests.support.fake_slurm import FakeSlurm
from tests.support.hooks import Hook, parse_hooks
from tests.support.legacy_layout import build_legacy_clone, install_sintetizador
from tests.support.newave_outputs import pmo_bytes
from tests.support.object_store import (
    DirectoryObjectStore,
    RecordingObjectStore,
)

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "contract"
    / "fixtures"
    / "prd_command_lines.txt"
)
_COMMAND_LINES = load_command_lines(_FIXTURE)

_QUEUE = "q-a"
_CORES = "4"
_MAX_CORES_PER_NODE = "2"
_MAX_JOB_TIME_HOURS = "1"
_SUBPROCESS_TIMEOUT = 120.0

_STATUS_HOOK_METHODS = frozenset(
    {"SetSuccess", "SetModelError", "SetDataError", "SetRuntimeError"}
)


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _replay(
    cli_bin: Path,
    labels: Sequence[str],
    values: Mapping[str, str],
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> list[subprocess.CompletedProcess[str]]:
    """Runs each ``label`` through ``cli_bin`` in order, failing fast
    with that command's own stdout/stderr on a non-zero exit."""
    results: list[subprocess.CompletedProcess[str]] = []
    for label in labels:
        argv = [str(cli_bin), *render(_COMMAND_LINES[label], values)]
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=dict(env),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_SUBPROCESS_TIMEOUT,
        )
        assert result.returncode == 0, (
            f"{label} ({' '.join(argv)}) exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        results.append(result)
    return results


def _run_sequence(
    cli_bin: Path,
    labels: Sequence[str],
    values: Mapping[str, str],
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> dict[str, subprocess.CompletedProcess[str]]:
    results = _replay(cli_bin, labels, values, cwd=cwd, env=env)
    return dict(zip(labels, results, strict=True))


def _replay_env(fake_slurm: FakeSlurm) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{fake_slurm.bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["HPCMU_POLL_INTERVAL"] = "0.1"
    env["HPCMU_SETTLE_WINDOW"] = "0.2"
    env["HPCMU_OUTCOME_BACKOFF"] = "0.1"
    return env


def _artifacts_uri(tag: str) -> str:
    return f"s3://outputs-bucket/artifacts/{tag * 64}"


def _uploaded_keys(store_root: Path, prefix: S3Uri) -> set[str]:
    base = store_root / prefix.bucket / prefix.key
    if not base.is_dir():
        return set()
    return {
        p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()
    }


def _seed_deck(store: DirectoryObjectStore, model: str) -> str:
    uri = f"s3://inputs-bucket/ingest/deck_{model}.zip"
    store.seed(
        S3Uri.parse(uri),
        (FIXTURES / "decks" / f"deck_{model}.zip").read_bytes(),
    )
    return uri


def _base_values(
    *,
    model: str,
    fake_slurm: FakeSlurm,
    versions_uri: str,
    input_uri: str,
    artifacts_uri: str,
) -> dict[str, str]:
    bin_dir = str(fake_slurm.bin_dir)
    return {
        "model": model,
        "versions_uri": versions_uri,
        "input_uri": input_uri,
        "parent_path": "",
        "execution_name": f"replay-{model.lower()}",
        "queue": _QUEUE,
        "cores": _CORES,
        "max_cores_per_node": _MAX_CORES_PER_NODE,
        "max_job_time_hours": _MAX_JOB_TIME_HOURS,
        "mpich_path": bin_dir,
        "slurm_path": bin_dir,
        "artifacts_uri": artifacts_uri,
    }


def _annotation(hooks: list[Hook]) -> str:
    return next(h for h in hooks if h.method == "SetAnnotation").args[0]


# ---------------------------------------------------------------------------
# Unit test (Testing Requirements): store semantics, both backends.
# ---------------------------------------------------------------------------


@pytest.fixture(params=["recording", "directory"])
def _object_store(
    request: pytest.FixtureRequest, tmp_path: Path
) -> RecordingObjectStore:
    if request.param == "recording":
        return RecordingObjectStore()
    return DirectoryObjectStore(tmp_path / "objects")


def test_store_semantics_exact_key_prefix_boundary_flatten_and_missing(
    _object_store: RecordingObjectStore, tmp_path: Path
) -> None:
    store = _object_store
    store.seed(S3Uri("bucket", "docs/readme.txt"), b"exact-readme")
    store.seed(S3Uri("bucket", "a/b/x.txt"), b"under-a-b")
    store.seed(S3Uri("bucket", "a/b/sub/deep.txt"), b"nested-under-a-b")
    store.seed(S3Uri("bucket", "a/bprefix/y.txt"), b"sibling-not-under-a-b")

    # exact-key download: the literal key only, never a prefix match.
    assert (
        store.get_bytes(S3Uri("bucket", "docs/readme.txt")) == b"exact-readme"
    )

    # the trailing-slash prefix boundary: "a/b" must not also match the
    # sibling "a/bprefix/" folder; flattened download_prefix: a nested
    # member lands by basename only.
    downloaded = store.download_prefix(
        S3Uri("bucket", "a/b"), tmp_path / "flat"
    )
    assert sorted(p.name for p in downloaded) == ["deep.txt", "x.txt"]
    assert {p.read_bytes() for p in downloaded} == {
        b"under-a-b",
        b"nested-under-a-b",
    }

    with pytest.raises(ObjectNotFoundError):
        store.get_bytes(S3Uri("bucket", "does/not/exist.txt"))
    with pytest.raises(ObjectNotFoundError):
        store.download_prefix(
            S3Uri("bucket", "nothing/here"), tmp_path / "empty"
        )


# ---------------------------------------------------------------------------
# AC1 (NEWAVE PEM) + AC2 (legacy layout wiring) + AC3 (DECOMP PEM chained).
# ---------------------------------------------------------------------------


def test_newave_pem_and_decomp_chaining(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    store_root = tmp_path / "store"
    store = DirectoryObjectStore(store_root)
    env = _replay_env(fake_slurm)

    # -- NEWAVE PEM (AC1) -------------------------------------------------
    newave_dir = tmp_path / "run-newave"
    newave_dir.mkdir()
    newave_cli = build_legacy_clone(
        newave_dir, store_root=store_root, toolchain_bin=fake_slurm.bin_dir
    )
    newave_record = tmp_path / "sintetizador-newave.jsonl"
    install_sintetizador(newave_dir, "newave", record=newave_record)

    newave_versions_uri = seed_versions(store, "newave", "30.0.4")
    newave_input_uri = _seed_deck(store, "newave")
    newave_artifacts_uri = _artifacts_uri("a")
    newave_values = _base_values(
        model="NEWAVE",
        fake_slurm=fake_slurm,
        versions_uri=newave_versions_uri,
        input_uri=newave_input_uri,
        artifacts_uri=newave_artifacts_uri,
    )

    newave_results = _run_sequence(
        newave_cli,
        (
            "fetch_executables",
            "fetch_inputs_empty_parent",
            "extract_sanitize",
            "preprocess",
            "run_full",
            "result_upload",
        ),
        newave_values,
        cwd=newave_dir,
        env=env,
    )

    submitted_lines = re.findall(
        r"Submitted batch job (\d+)", newave_results["run_full"].stdout
    )
    assert len(submitted_lines) == 2

    newave_hooks = parse_hooks(newave_results["result_upload"].stdout)
    relevant = [
        h
        for h in newave_hooks
        if h.method in (_STATUS_HOOK_METHODS | {"SetExecutionArtifactsPath"})
    ]
    assert [h.method for h in relevant] == [
        "SetExecutionArtifactsPath",
        "SetSuccess",
    ]
    assert "[newave.completed]" in _annotation(newave_hooks)

    newave_prefix = S3Uri.parse(newave_artifacts_uri)
    newave_keys = _uploaded_keys(store_root, newave_prefix)
    expected_newave_keys = {
        "entradas/eco_deck.zip",
        "entradas/deck_processado.zip",
        "saidas/pmo.dat",
        "saidas/cortes.zip",
        "saidas/recursos.zip",
        "saidas/simulacao.zip",
        "saidas/status.modelops",
        "saidas/run.json",
        "saidas/metadata.modelops",
        "sintese/newave_stub.parquet",
    }
    assert expected_newave_keys <= newave_keys
    assert any(
        re.fullmatch(r"saidas/logs/model-\d+\.out", k) for k in newave_keys
    )
    assert any(
        re.fullmatch(r"saidas/logs/finalize-\d+\.out", k) for k in newave_keys
    )
    assert not any(
        "hpc-model-utils" in key or "sintetizador-" in key or "decoy" in key
        for key in newave_keys
    )

    # -- AC2: legacy layout wiring ----------------------------------------
    model_job, finalize_job = fake_slurm.submitted()
    finalize_script = Path(finalize_job["script"]).read_text(encoding="utf-8")
    assert str(newave_cli) in finalize_script

    record_lines = newave_record.read_text(encoding="utf-8").splitlines()
    assert len(record_lines) == 1
    sintetizador_argv = json.loads(record_lines[0])
    assert sintetizador_argv[-3:-1] == ["completa", "--processadores"]
    assert int(sintetizador_argv[-1]) >= 1

    # -- AC3: DECOMP PEM chained on the replayed NEWAVE artifacts ---------
    decomp_dir = tmp_path / "run-decomp"
    decomp_dir.mkdir()
    decomp_cli = build_legacy_clone(
        decomp_dir, store_root=store_root, toolchain_bin=fake_slurm.bin_dir
    )
    decomp_record = tmp_path / "sintetizador-decomp.jsonl"
    install_sintetizador(decomp_dir, "decomp", record=decomp_record)

    decomp_versions_uri = seed_versions(store, "decomp", "32.0")
    decomp_input_uri = _seed_deck(store, "decomp")
    decomp_artifacts_uri = _artifacts_uri("b")
    decomp_values = _base_values(
        model="DECOMP",
        fake_slurm=fake_slurm,
        versions_uri=decomp_versions_uri,
        input_uri=decomp_input_uri,
        artifacts_uri=decomp_artifacts_uri,
    )
    decomp_values["parent_path"] = newave_artifacts_uri

    _run_sequence(
        decomp_cli,
        ("fetch_executables", "fetch_inputs", "extract_sanitize", "preprocess"),
        decomp_values,
        cwd=decomp_dir,
        env=env,
    )
    assert (decomp_dir / "cortes-012.dat").is_file()

    decomp_results = _run_sequence(
        decomp_cli,
        ("run_full", "result_upload"),
        decomp_values,
        cwd=decomp_dir,
        env=env,
    )

    decomp_hooks = parse_hooks(decomp_results["result_upload"].stdout)
    decomp_annotation = _annotation(decomp_hooks)
    assert "[decomp.converged]" in decomp_annotation
    assert "stage mismatch" not in decomp_annotation.lower()

    decomp_prefix = S3Uri.parse(decomp_artifacts_uri)
    decomp_keys = _uploaded_keys(store_root, decomp_prefix)
    assert {
        "saidas/relato.rv0",
        "saidas/relgnl.rv0",
        "entradas/deck_processado.zip",
    } <= decomp_keys

    deck_processado = (
        store_root
        / decomp_prefix.bucket
        / decomp_prefix.key
        / "entradas"
        / "deck_processado.zip"
    )
    with zipfile.ZipFile(deck_processado) as archive:
        assert "caso.dat" in archive.namelist()


# ---------------------------------------------------------------------------
# AC4: Upload NEWAVE offline.
# ---------------------------------------------------------------------------


def test_upload_newave_offline(fake_slurm: FakeSlurm, tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    store = DirectoryObjectStore(store_root)
    env = _replay_env(fake_slurm)

    run_dir = tmp_path / "run-newave"
    run_dir.mkdir()
    cli_bin = build_legacy_clone(
        run_dir, store_root=store_root, toolchain_bin=fake_slurm.bin_dir
    )
    record = tmp_path / "sintetizador-newave.jsonl"
    install_sintetizador(run_dir, "newave", record=record)

    versions_uri = seed_versions(store, "newave", "30.0.4")

    inputs_uri = "s3://inputs-bucket/offline/inputs.zip"
    outputs_uri = "s3://inputs-bucket/offline/outputs.zip"
    cuts_uri = "s3://inputs-bucket/offline/cortes.zip"

    store.seed(
        S3Uri.parse(inputs_uri),
        (FIXTURES / "decks" / "deck_newave.zip").read_bytes(),
    )

    synthetic = archive_golden("newave")["synthetic_outputs"]
    outputs_files = {name: b"x\n" for name in synthetic if name != "pmo.dat"}
    outputs_files["pmo.dat"] = pmo_bytes("complete")
    outputs_zip = tmp_path / "outputs.zip"
    input_zip(outputs_files, outputs_zip)
    store.seed(S3Uri.parse(outputs_uri), outputs_zip.read_bytes())

    cuts_zip = tmp_path / "cuts.zip"
    input_zip(binary_cut_files(["cortes-012.dat", "cortesh.dat"]), cuts_zip)
    store.seed(S3Uri.parse(cuts_uri), cuts_zip.read_bytes())

    artifacts_uri = _artifacts_uri("d")
    values = _base_values(
        model="NEWAVE",
        fake_slurm=fake_slurm,
        versions_uri=versions_uri,
        input_uri="",
        artifacts_uri=artifacts_uri,
    )
    values["inputs_uri"] = inputs_uri
    values["outputs_uri"] = outputs_uri
    values["cuts_uri"] = cuts_uri

    results = _run_sequence(
        cli_bin,
        ("fetch_executables", "ingest_offline", "run_minimal", "result_upload"),
        values,
        cwd=run_dir,
        env=env,
    )

    submitted_lines = re.findall(
        r"Submitted batch job (\d+)", results["run_minimal"].stdout
    )
    assert len(submitted_lines) == 1

    hooks = parse_hooks(results["result_upload"].stdout)
    status_hooks = [h for h in hooks if h.method in _STATUS_HOOK_METHODS]
    assert status_hooks == [Hook("SetSuccess", ())]
    annotation = _annotation(hooks)
    assert annotation.endswith(
        " (imported offline run, not executed on the cluster)"
    )

    prefix = S3Uri.parse(artifacts_uri)
    metadata_path = (
        store_root / prefix.bucket / prefix.key / "saidas" / "metadata.modelops"
    )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["execution_source"] == "OFFLINE"
    assert "parent_path" not in metadata


# ---------------------------------------------------------------------------
# AC5: missing sintetizador is loud, not fatal.
# ---------------------------------------------------------------------------


def test_missing_sintetizador_is_loud_not_fatal(
    fake_slurm: FakeSlurm, tmp_path: Path
) -> None:
    store_root = tmp_path / "store"
    store = DirectoryObjectStore(store_root)
    env = _replay_env(fake_slurm)

    run_dir = tmp_path / "run-newave"
    run_dir.mkdir()
    cli_bin = build_legacy_clone(
        run_dir, store_root=store_root, toolchain_bin=fake_slurm.bin_dir
    )
    # deliberately no install_sintetizador() call.

    versions_uri = seed_versions(store, "newave", "30.0.4")
    input_uri = _seed_deck(store, "newave")
    artifacts_uri = _artifacts_uri("c")
    values = _base_values(
        model="NEWAVE",
        fake_slurm=fake_slurm,
        versions_uri=versions_uri,
        input_uri=input_uri,
        artifacts_uri=artifacts_uri,
    )

    results = _run_sequence(
        cli_bin,
        (
            "fetch_executables",
            "fetch_inputs_empty_parent",
            "extract_sanitize",
            "preprocess",
            "run_full",
            "result_upload",
        ),
        values,
        cwd=run_dir,
        env=env,
    )

    hooks = parse_hooks(results["result_upload"].stdout)
    status_hooks = [h for h in hooks if h.method in _STATUS_HOOK_METHODS]
    assert status_hooks == [Hook("SetRuntimeError", ())]
    annotation = _annotation(hooks)
    assert "core.synthesis_missing" in annotation
    assert "sintetizador binary not found" in annotation
