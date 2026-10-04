"""ADR-042/ADR-043/R12/R16/R17/R51/R82/R90/R94/R103: the consumer-
expectation contract suite (ticket-055a).

Pins how three read-only consumer repositories read what v2 writes:
ranqueamento-prospectivo-utils, a v1 child of a v2 parent (this repo's
own frozen ``app/adapter/repository/{newave,decomp}.py``), and
encadeador-pem. Every consumer read is replicated here, module-private
and pure, each with a comment citing the consumer file and line range
it mirrors. The suite adds no production code (Requirement 4): a
failing expectation is a v2 defect or a consumer behaviour to
escalate, never a reason to relax a replica.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, Literal

import pytest
from click.testing import CliRunner, Result

from hpc_model_utils.cli import workflow
from hpc_model_utils.cli.root import AppContext, cli
from hpc_model_utils.core.diagnosis import Diagnosis, RunStatus, utc_now_iso
from hpc_model_utils.core.errors import ExitCode, classify
from hpc_model_utils.core.state import (
    RunState,
    StateStore,
    current_tool,
    write_projections,
)
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.infra.errors import ObjectNotFoundError
from hpc_model_utils.infra.s3 import ObjectStore, S3Uri
from hpc_model_utils.models import PLUGINS
from hpc_model_utils.platform.modelops import Reporter
from tests.support.decks import (
    FIXTURES,
    GOLDENS,
    DeckWorkspace,
    archive_golden,
    newave_workspace,
)
from tests.support.newave_outputs import pmo_bytes, write_dger
from tests.support.object_store import RecordingObjectStore
from tests.support.published_run import (
    PREFIX,
    ModelName,
    PublishedRun,
    publish_golden_run,
)

_PROJECTIONS = GOLDENS / "projections.json"
_MODELS: Final[tuple[ModelName, ...]] = ("newave", "decomp")
_PmoKind = Literal["complete", "no_simulated_cost"]


class _ListChannel:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write_line(self, line: str) -> None:
        self.lines.append(line)


def _app_context() -> AppContext:
    return AppContext(
        reporter=Reporter(_ListChannel(), enabled=True), channels=None
    )


# ---------------------------------------------------------------------------
# Requirement 1: consumer replicas (module-private, pure).
# ---------------------------------------------------------------------------

# ranqueamento-prospectivo-utils app/models/runstatus.py:4-10
_RANQUEAMENTO_STATUSES: Final[frozenset[str]] = frozenset(
    {
        "SUCCESS",
        "INFEASIBLE",
        "DATA_ERROR",
        "RUNTIME_ERROR",
        "COMMUNICATION_ERROR",
        "UNKNOWN",
    }
)


# ranqueamento-prospectivo-utils app/models/runstatus.py:12-17
def _ranqueamento_factory(text: str) -> str:
    return text if text in _RANQUEAMENTO_STATUSES else "UNKNOWN"


# ranqueamento-prospectivo-utils
# app/adapter/repository/ranqueamento.py:257-277
def _ranqueamento_rollup(statuses: Sequence[str]) -> str:
    if all(status == "SUCCESS" for status in statuses):
        return "SUCCESS"
    if any(status == "DATA_ERROR" for status in statuses):
        return "DATA_ERROR"
    return "RUNTIME_ERROR"


# hpc-model-utils app/adapter/repository/newave.py:210-266 (NEWAVE child)
# and app/adapter/repository/decomp.py:232-285 (DECOMP child): v1's
# parent gate, frozen since v1.1.2. Both children require NEWAVE as the
# parent model.
def _v1_parent_gate(
    store: ObjectStore, prefix: str, *, child: ModelName
) -> str:
    expected_model = PLUGINS[child].parent_model
    assert expected_model is not None, f"{child} has no parent model"
    parent = S3Uri.parse(prefix)
    raw = store.get_bytes(parent.join("saidas", "metadata.modelops"))
    data = json.loads(raw.decode("utf-8"))
    missing = [
        key
        for key in ("model_name", "status", "study_starting_date")
        if key not in data
    ]
    assert not missing, f"Parent metadata is incomplete [{data}]"
    model_name = data["model_name"]
    assert model_name == expected_model, (
        f"Parent model is not {expected_model} (got {model_name!r})"
    )
    status = data["status"]
    assert status == "SUCCESS", (
        f"Parent execution status was not SUCCESS (got {status!r})"
    )
    starting_date = data["study_starting_date"]
    assert isinstance(starting_date, str)
    return starting_date


# encadeador-pem packages/encadeador/app/services/s3_unitofwork.py:148-156
def _encadeador_ext(deck_zip_bytes: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(deck_zip_bytes)) as archive:
        return archive.read("caso.dat").decode().strip()


# ---------------------------------------------------------------------------
# Inline checks of the replicas against literal inputs.
# ---------------------------------------------------------------------------


def test_ranqueamento_factory_given_timeout_token_returns_unknown() -> None:
    assert _ranqueamento_factory("TIMEOUT") == "UNKNOWN"


def test_ranqueamento_factory_given_success_token_returns_success() -> None:
    assert _ranqueamento_factory("SUCCESS") == "SUCCESS"


def test_ranqueamento_rollup_given_all_success_returns_success() -> None:
    assert _ranqueamento_rollup(["SUCCESS", "SUCCESS"]) == "SUCCESS"


def test_ranqueamento_rollup_given_any_data_error_returns_data_error() -> None:
    assert _ranqueamento_rollup(["SUCCESS", "DATA_ERROR"]) == "DATA_ERROR"


def test_ranqueamento_rollup_given_neither_all_success_nor_any_data_error_returns_runtime_error() -> (
    None
):
    assert _ranqueamento_rollup(["SUCCESS", "UNKNOWN"]) == "RUNTIME_ERROR"


def test_encadeador_ext_given_literal_caso_dat_zip_returns_stripped_extension() -> (
    None
):
    zip_bytes = _zip_bytes({"caso.dat": b"rv0\n"})

    assert _encadeador_ext(zip_bytes) == "rv0"


# ---------------------------------------------------------------------------
# Shared test-only fixture helpers (not consumer replicas).
# ---------------------------------------------------------------------------


def _zip_bytes(members: Mapping[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _archive_members(name: str) -> list[str]:
    """The v1.1.2 ``archives.json[newave].archives.<name>`` member list
    (ADR-048): the parent run is always a NEWAVE run."""
    members = archive_golden("newave")["archives"][name]
    if not isinstance(members, list) or not all(
        isinstance(member, str) for member in members
    ):
        raise TypeError(
            f"archives.json[newave].archives.{name} is not a string list"
        )
    return members


def _build_archive(names: Sequence[str]) -> bytes:
    return _zip_bytes({name: b"x\n" for name in names})


def _golden_metadata(sequence: str) -> bytes:
    data = json.loads(_PROJECTIONS.read_text(encoding="utf-8"))
    text = data["sequences"][sequence]["metadata.modelops"]
    if not isinstance(text, str):
        raise TypeError(
            f"projections.json[{sequence}].metadata.modelops is not a string"
        )
    return text.encode("ascii")


def _override_metadata(sequence: str, **overrides: str) -> bytes:
    data = json.loads(_golden_metadata(sequence).decode("ascii"))
    data.update(overrides)
    return json.dumps(data).encode("ascii")


# Requirement 2: the v1 parent fixture.
def _seed_v1_parent(
    store: RecordingObjectStore,
    uri: str,
    *,
    metadata: bytes | None,
    artifacts: tuple[str, ...],
) -> None:
    parent = S3Uri.parse(uri)
    if metadata is not None:
        store.seed(parent.join("saidas", "metadata.modelops"), metadata)
    for name in artifacts:
        store.seed(
            parent.join("saidas", name), _build_archive(_archive_members(name))
        )


# ---------------------------------------------------------------------------
# AC1: ranqueamento reads (R12, R51, R90).
# ---------------------------------------------------------------------------


def _generate_newave_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pmo_kind: _PmoKind,
) -> DeckWorkspace:
    deck = newave_workspace(tmp_path)
    if pmo_kind == "no_simulated_cost":
        write_dger(deck.ws, tipo_execucao=1, tipo_simulacao_final=1)
    (deck.ws.root / "pmo.dat").write_bytes(pmo_bytes(pmo_kind))
    monkeypatch.chdir(deck.ws.root)

    result = CliRunner().invoke(
        cli,
        ["generate_execution_status", "newave", "--job-id", "9"],
        obj=_app_context(),
    )
    assert result.exit_code == 0, result.output
    return deck


@pytest.mark.parametrize(
    ("pmo_kind", "expected"),
    [("complete", "SUCCESS"), ("no_simulated_cost", "RUNTIME_ERROR")],
)
def test_ranqueamento_factory_given_toolbox_diagnosed_status_matches_expected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pmo_kind: _PmoKind,
    expected: str,
) -> None:
    deck = _generate_newave_status(tmp_path, monkeypatch, pmo_kind)

    written = deck.ws.legacy_status_path.read_text(encoding="ascii").strip()
    assert _ranqueamento_factory(written) == expected


@pytest.mark.parametrize(
    ("pmo_kind", "expect_zero"),
    [("complete", True), ("no_simulated_cost", False)],
)
def test_ranqueamento_job_grep_q_success_given_toolbox_diagnosed_status_matches_expected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pmo_kind: _PmoKind,
    expect_zero: bool,
) -> None:
    deck = _generate_newave_status(tmp_path, monkeypatch, pmo_kind)
    assert shutil.which("grep") is not None

    result = subprocess.run(
        ["grep", "-q", "SUCCESS", "status.modelops"], cwd=deck.ws.root
    )

    assert (result.returncode == 0) is expect_zero


def _write_status(ws: Workspace, status: RunStatus) -> None:
    diagnosis = Diagnosis(
        status=status, rule_id="core.ok", reason="x", at=utc_now_iso()
    )
    state = replace(RunState.new("newave", current_tool()), diagnosis=diagnosis)
    write_projections(ws, state, always_write_parent_path=False)


@pytest.mark.parametrize("status", list(RunStatus))
def test_ranqueamento_factory_given_every_runstatus_member_matches_known_set_or_unknown(
    tmp_path: Path, status: RunStatus
) -> None:
    ws = Workspace.at(tmp_path)
    _write_status(ws, status)
    written = ws.legacy_status_path.read_text(encoding="ascii")

    expected = (
        status.value if status.value in _RANQUEAMENTO_STATUSES else "UNKNOWN"
    )
    assert _ranqueamento_factory(written) == expected


@pytest.mark.parametrize("status", list(RunStatus))
def test_ranqueamento_rollup_given_every_runstatus_member_paired_with_success_is_success_only_for_success(
    tmp_path: Path, status: RunStatus
) -> None:
    ws = Workspace.at(tmp_path)
    _write_status(ws, status)
    written = ws.legacy_status_path.read_text(encoding="ascii")

    rollup = _ranqueamento_rollup([_ranqueamento_factory(written), "SUCCESS"])
    assert (rollup == "SUCCESS") is (status is RunStatus.SUCCESS)


@pytest.mark.parametrize("status", list(RunStatus))
def test_ranqueamento_job_grep_q_success_given_every_runstatus_member_exits_zero_only_for_success(
    tmp_path: Path, status: RunStatus
) -> None:
    ws = Workspace.at(tmp_path)
    _write_status(ws, status)
    assert shutil.which("grep") is not None

    result = subprocess.run(
        ["grep", "-q", "SUCCESS", str(ws.legacy_status_path)]
    )

    assert (result.returncode == 0) is (status is RunStatus.SUCCESS)


def test_ranqueamento_job_unzip_cortes_zip_extracts_cortesh_dat(
    tmp_path: Path,
) -> None:
    run = publish_golden_run(tmp_path, "newave")
    cortes_zip = tmp_path / "cortes.zip"
    cortes_zip.write_bytes(
        run.store.get_bytes(run.prefix.join("saidas", "cortes.zip"))
    )
    dest = tmp_path / "dest"
    dest.mkdir()
    assert shutil.which("unzip") is not None

    result = subprocess.run(
        [
            "unzip",
            str(cortes_zip),
            "-d",
            str(dest),
            "cortes-*.dat",
            "cortesh.dat",
        ]
    )

    assert result.returncode == 0
    assert (dest / "cortesh.dat").is_file()


def test_ranqueamento_job_unzip_cortes_zip_extracts_at_least_one_cortes_numbered_file(
    tmp_path: Path,
) -> None:
    run = publish_golden_run(tmp_path, "newave")
    cortes_zip = tmp_path / "cortes.zip"
    cortes_zip.write_bytes(
        run.store.get_bytes(run.prefix.join("saidas", "cortes.zip"))
    )
    dest = tmp_path / "dest"
    dest.mkdir()
    assert shutil.which("unzip") is not None
    subprocess.run(
        [
            "unzip",
            str(cortes_zip),
            "-d",
            str(dest),
            "cortes-*.dat",
            "cortesh.dat",
        ],
        check=True,
    )

    assert list(dest.glob("cortes-*.dat"))


def test_ranqueamento_job_unzip_simulacao_zip_extracts_newdesp_dat(
    tmp_path: Path,
) -> None:
    run = publish_golden_run(tmp_path, "newave")
    simulacao_zip = tmp_path / "simulacao.zip"
    simulacao_zip.write_bytes(
        run.store.get_bytes(run.prefix.join("saidas", "simulacao.zip"))
    )
    dest = tmp_path / "dest"
    dest.mkdir()
    assert shutil.which("unzip") is not None

    result = subprocess.run(
        ["unzip", str(simulacao_zip), "-d", str(dest), "newdesp.dat"]
    )

    assert result.returncode == 0
    assert (dest / "newdesp.dat").is_file()


def test_ranqueamento_job_unzip_extraction_leaves_no_subdirectory_under_dest(
    tmp_path: Path,
) -> None:
    run = publish_golden_run(tmp_path, "newave")
    dest = tmp_path / "dest"
    dest.mkdir()
    cortes_zip = tmp_path / "cortes.zip"
    cortes_zip.write_bytes(
        run.store.get_bytes(run.prefix.join("saidas", "cortes.zip"))
    )
    simulacao_zip = tmp_path / "simulacao.zip"
    simulacao_zip.write_bytes(
        run.store.get_bytes(run.prefix.join("saidas", "simulacao.zip"))
    )
    assert shutil.which("unzip") is not None
    subprocess.run(
        [
            "unzip",
            str(cortes_zip),
            "-d",
            str(dest),
            "cortes-*.dat",
            "cortesh.dat",
        ],
        check=True,
    )
    subprocess.run(
        ["unzip", str(simulacao_zip), "-d", str(dest), "newdesp.dat"],
        check=True,
    )

    assert all(entry.is_file() for entry in dest.iterdir())


# ---------------------------------------------------------------------------
# AC2: v1 parent, v2 child (ADR-043, R94).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _FetchRun:
    ws: Workspace
    store: RecordingObjectStore
    input_uri: str
    parent_uri: str
    artifact_bytes: dict[str, bytes]
    result: Result


def _run_v1_parent_v2_child_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, child: ModelName
) -> _FetchRun:
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    store = RecordingObjectStore()
    deck_bytes = (FIXTURES / "decks" / f"deck_{child}.zip").read_bytes()
    input_uri = f"s3://inputs-bucket/ingest/deck_{child}.zip"
    store.seed(S3Uri.parse(input_uri), deck_bytes)
    parent_uri = "s3://outputs-bucket/artifacts/parenthash01"
    artifacts = PLUGINS[child].parent_artifacts
    _seed_v1_parent(
        store,
        parent_uri,
        metadata=_golden_metadata("newave_no_parent"),
        artifacts=artifacts,
    )
    artifact_bytes = {
        name: _build_archive(_archive_members(name)) for name in artifacts
    }
    monkeypatch.chdir(ws_root)
    monkeypatch.setattr(workflow, "object_store", lambda: store)

    result = CliRunner().invoke(
        cli,
        [
            "check_and_fetch_inputs",
            child,
            input_uri,
            "--parent-path",
            parent_uri,
            "--delete",
        ],
        obj=_app_context(),
    )
    return _FetchRun(
        Workspace.at(ws_root),
        store,
        input_uri,
        parent_uri,
        artifact_bytes,
        result,
    )


@pytest.mark.parametrize("child", _MODELS)
def test_check_and_fetch_inputs_v1_parent_v2_child_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, child: ModelName
) -> None:
    run = _run_v1_parent_v2_child_fetch(tmp_path, monkeypatch, child)

    assert run.result.exit_code == 0, run.result.output


@pytest.mark.parametrize("child", _MODELS)
def test_check_and_fetch_inputs_v1_parent_v2_child_downloads_every_parent_artifact_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, child: ModelName
) -> None:
    run = _run_v1_parent_v2_child_fetch(tmp_path, monkeypatch, child)
    assert run.result.exit_code == 0, run.result.output

    for name, data in run.artifact_bytes.items():
        assert (run.ws.parent_dir / name).read_bytes() == data


@pytest.mark.parametrize("child", _MODELS)
def test_check_and_fetch_inputs_v1_parent_v2_child_records_parent_starting_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, child: ModelName
) -> None:
    run = _run_v1_parent_v2_child_fetch(tmp_path, monkeypatch, child)
    assert run.result.exit_code == 0, run.result.output

    state = StateStore(run.ws).load()
    assert state is not None
    assert state.parent is not None
    assert state.parent.starting_date == "2025-11-01T00:00:00+00:00"


@pytest.mark.parametrize("child", _MODELS)
def test_check_and_fetch_inputs_v1_parent_v2_child_records_parent_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, child: ModelName
) -> None:
    run = _run_v1_parent_v2_child_fetch(tmp_path, monkeypatch, child)
    assert run.result.exit_code == 0, run.result.output

    state = StateStore(run.ws).load()
    assert state is not None
    assert state.parent is not None
    assert state.parent.path == run.parent_uri


@pytest.mark.parametrize("child", _MODELS)
def test_check_and_fetch_inputs_v1_parent_v2_child_deletes_input_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, child: ModelName
) -> None:
    run = _run_v1_parent_v2_child_fetch(tmp_path, monkeypatch, child)
    assert run.result.exit_code == 0, run.result.output

    with pytest.raises(ObjectNotFoundError, match=run.input_uri):
        run.store.get_bytes(S3Uri.parse(run.input_uri))


# ---------------------------------------------------------------------------
# AC3: v2 parent, v1 and v2 children.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("child", _MODELS)
def test_v1_parent_gate_given_v2_success_parent_returns_study_starting_date(
    tmp_path: Path, child: ModelName
) -> None:
    run = publish_golden_run(tmp_path, "newave")

    starting_date = _v1_parent_gate(run.store, PREFIX, child=child)

    assert starting_date == "2025-11-01T00:00:00+00:00"


def _run_decomp_fetch_against_published_newave_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run: PublishedRun
) -> Result:
    deck_bytes = (FIXTURES / "decks" / "deck_decomp.zip").read_bytes()
    input_uri = "s3://inputs-bucket/ingest/deck_decomp.zip"
    run.store.seed(S3Uri.parse(input_uri), deck_bytes)
    ws_root = tmp_path / "decomp_ws"
    ws_root.mkdir()
    monkeypatch.chdir(ws_root)
    monkeypatch.setattr(workflow, "object_store", lambda: run.store)
    return CliRunner().invoke(
        cli,
        [
            "check_and_fetch_inputs",
            "decomp",
            input_uri,
            "--parent-path",
            PREFIX,
            "--delete",
        ],
        obj=_app_context(),
    )


def test_check_and_fetch_inputs_decomp_child_against_v2_success_parent_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = publish_golden_run(tmp_path, "newave")

    result = _run_decomp_fetch_against_published_newave_parent(
        tmp_path, monkeypatch, run
    )

    assert result.exit_code == 0, result.output


def test_v1_parent_gate_given_v2_runtime_error_parent_raises_assertion_error_naming_status(
    tmp_path: Path,
) -> None:
    run = publish_golden_run(tmp_path, "newave", status=RunStatus.RUNTIME_ERROR)

    with pytest.raises(AssertionError, match="status"):
        _v1_parent_gate(run.store, PREFIX, child="decomp")


def test_check_and_fetch_inputs_decomp_child_against_v2_runtime_error_parent_raises_data_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = publish_golden_run(tmp_path, "newave", status=RunStatus.RUNTIME_ERROR)

    result = _run_decomp_fetch_against_published_newave_parent(
        tmp_path, monkeypatch, run
    )

    assert result.exception is not None
    assert classify(result.exception).status is RunStatus.DATA_ERROR


# ---------------------------------------------------------------------------
# AC4: parent refusals (ADR-042, ADR-043).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ParentRefusal:
    id: str
    metadata: bytes | None
    expected_substrings: tuple[str, ...]


_PARENT_REFUSALS: Final[tuple[_ParentRefusal, ...]] = (
    _ParentRefusal(
        "model_mismatch",
        _override_metadata("newave_no_parent", model_name="DECOMP"),
        ("NEWAVE", "DECOMP"),
    ),
    _ParentRefusal("missing_marker", None, ("metadata.modelops",)),
    _ParentRefusal(
        "bad_status",
        _override_metadata("newave_no_parent", status="RUNTIME_ERROR"),
        ("RUNTIME_ERROR",),
    ),
)
_PARENT_REFUSAL_IDS: Final[list[str]] = [case.id for case in _PARENT_REFUSALS]


def _run_parent_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: _ParentRefusal
) -> _FetchRun:
    store = RecordingObjectStore()
    deck_bytes = (FIXTURES / "decks" / "deck_decomp.zip").read_bytes()
    input_uri = "s3://inputs-bucket/ingest/deck_decomp.zip"
    store.seed(S3Uri.parse(input_uri), deck_bytes)
    parent_uri = "s3://outputs-bucket/artifacts/parenthash01"
    _seed_v1_parent(
        store, parent_uri, metadata=case.metadata, artifacts=("cortes.zip",)
    )
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    monkeypatch.chdir(ws_root)
    monkeypatch.setattr(workflow, "object_store", lambda: store)

    result = CliRunner().invoke(
        cli,
        [
            "check_and_fetch_inputs",
            "decomp",
            input_uri,
            "--parent-path",
            parent_uri,
            "--delete",
        ],
        obj=_app_context(),
    )
    return _FetchRun(
        Workspace.at(ws_root), store, input_uri, parent_uri, {}, result
    )


@pytest.mark.parametrize("case", _PARENT_REFUSALS, ids=_PARENT_REFUSAL_IDS)
def test_check_and_fetch_inputs_parent_refusal_classify_status_is_data_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: _ParentRefusal
) -> None:
    run = _run_parent_refusal(tmp_path, monkeypatch, case)
    assert run.result.exception is not None

    assert classify(run.result.exception).status is RunStatus.DATA_ERROR


@pytest.mark.parametrize("case", _PARENT_REFUSALS, ids=_PARENT_REFUSAL_IDS)
def test_check_and_fetch_inputs_parent_refusal_classify_exit_code_is_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: _ParentRefusal
) -> None:
    run = _run_parent_refusal(tmp_path, monkeypatch, case)
    assert run.result.exception is not None

    assert classify(run.result.exception).exit_code == ExitCode.DATA


@pytest.mark.parametrize("case", _PARENT_REFUSALS, ids=_PARENT_REFUSAL_IDS)
def test_check_and_fetch_inputs_parent_refusal_message_contains_expected_substrings_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: _ParentRefusal
) -> None:
    run = _run_parent_refusal(tmp_path, monkeypatch, case)
    assert run.result.exception is not None

    message = classify(run.result.exception).message
    positions = [
        message.index(substring) for substring in case.expected_substrings
    ]
    assert positions == sorted(positions)


@pytest.mark.parametrize("case", _PARENT_REFUSALS, ids=_PARENT_REFUSAL_IDS)
def test_check_and_fetch_inputs_parent_refusal_leaves_parent_dir_without_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: _ParentRefusal
) -> None:
    run = _run_parent_refusal(tmp_path, monkeypatch, case)

    assert not run.ws.parent_dir.exists() or not any(
        run.ws.parent_dir.iterdir()
    )


@pytest.mark.parametrize("case", _PARENT_REFUSALS, ids=_PARENT_REFUSAL_IDS)
def test_check_and_fetch_inputs_parent_refusal_leaves_input_object_undeleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: _ParentRefusal
) -> None:
    run = _run_parent_refusal(tmp_path, monkeypatch, case)

    assert run.store.get_bytes(S3Uri.parse(run.input_uri))


# ---------------------------------------------------------------------------
# AC5: encadeador reads (R16, R17).
# ---------------------------------------------------------------------------


def test_encadeador_ext_given_published_decomp_deck_processado_zip_returns_rv0(
    tmp_path: Path,
) -> None:
    run = publish_golden_run(tmp_path, "decomp")
    deck_bytes = run.store.get_bytes(
        run.prefix.join("entradas", "deck_processado.zip")
    )

    assert _encadeador_ext(deck_bytes) == "rv0"


def test_encadeador_decomp_source_outputs_relato_exists_for_published_run(
    tmp_path: Path,
) -> None:
    run = publish_golden_run(tmp_path, "decomp")

    assert run.store.get_bytes(run.prefix.join("saidas", "relato.rv0"))


def test_encadeador_decomp_source_outputs_relgnl_exists_for_published_run(
    tmp_path: Path,
) -> None:
    run = publish_golden_run(tmp_path, "decomp")

    assert run.store.get_bytes(run.prefix.join("saidas", "relgnl.rv0"))


def test_encadeador_extracted_decomp_deck_processado_yields_caso_dat_at_root(
    tmp_path: Path,
) -> None:
    run = publish_golden_run(tmp_path, "decomp")
    deck_bytes = run.store.get_bytes(
        run.prefix.join("entradas", "deck_processado.zip")
    )
    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()
    with zipfile.ZipFile(io.BytesIO(deck_bytes)) as archive:
        archive.extractall(extract_dir)

    assert (extract_dir / "caso.dat").is_file()


def test_encadeador_extracted_newave_deck_processado_yields_caso_dat_and_arquivos_dat_at_root(
    tmp_path: Path,
) -> None:
    run = publish_golden_run(tmp_path, "newave")
    deck_bytes = run.store.get_bytes(
        run.prefix.join("entradas", "deck_processado.zip")
    )
    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()
    with zipfile.ZipFile(io.BytesIO(deck_bytes)) as archive:
        archive.extractall(extract_dir)

    assert (extract_dir / "caso.dat").is_file()
    assert (extract_dir / "arquivos.dat").is_file()


def test_encadeador_newave_source_outputs_pmo_exists_for_published_run(
    tmp_path: Path,
) -> None:
    run = publish_golden_run(tmp_path, "newave")

    assert run.store.get_bytes(run.prefix.join("saidas", "pmo.dat"))
