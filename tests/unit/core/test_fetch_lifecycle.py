"""ADR-043/ADR-028/ADR-011/ADR-009/R10/R17/R21/R94/R136 tests for the
``fetch_executables``/``fetch_inputs`` lifecycle steps (ticket-044)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hpc_model_utils.core.errors import DataError, UsageError
from hpc_model_utils.core.lifecycle.fetch import (
    fetch_executables,
    fetch_inputs,
    normalize_parent_path,
    read_parent,
)
from hpc_model_utils.core.state import ParentInfo, StateStore
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.infra.s3 import S3Uri
from tests.support.fake_plugin import FakePlugin, ParentPlugin
from tests.support.object_store import RecordingObjectStore
from tests.support.reporter import RecordingReporter

_VERSIONS_BUCKET = "versions-bucket"
_INPUTS_BUCKET = "inputs-bucket"
_OUTPUTS_BUCKET = "outputs-bucket"
_BASE_VERSOES_URI = f"s3://{_VERSIONS_BUCKET}/versoes/fake/1.2/"
_PARENT_URI = f"s3://{_OUTPUTS_BUCKET}/artifacts/abc"
_INPUT_URI = f"s3://{_INPUTS_BUCKET}/entradas/deck.zip"


class _AlwaysWriteParentPlugin(ParentPlugin):
    name = "fakeparentalways"
    always_write_parent_path = True


class _CountingPlugin(FakePlugin):
    def __init__(self) -> None:
        super().__init__()
        self.check_executables_calls = 0

    def check_executables(self, ws: Workspace) -> None:
        self.check_executables_calls += 1


def _ws(tmp_path: Path) -> Workspace:
    workspace = Workspace.at(tmp_path)
    workspace.ensure_layout()
    return workspace


def _parent_metadata(
    *, model_name: str = "NEWAVE", status: str = "SUCCESS"
) -> bytes:
    return json.dumps(
        {
            "model_name": model_name,
            "status": status,
            "study_starting_date": "2025-10-01T00:00:00+00:00",
        }
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# fetch_executables
# ---------------------------------------------------------------------------


def test_fetch_executables_happy_path_downloads_chmods_and_commits_state(
    tmp_path: Path,
) -> None:
    ws = _ws(tmp_path)
    store = RecordingObjectStore()
    store.seed(S3Uri(_VERSIONS_BUCKET, "versoes/fake/1.2/fake-model"), b"bin-a")
    store.seed(S3Uri(_VERSIONS_BUCKET, "versoes/fake/1.2/helper"), b"bin-b")
    reporter = RecordingReporter()

    state = fetch_executables(
        ws, FakePlugin(), store, reporter, StateStore(ws), _BASE_VERSOES_URI
    )

    for name in ("fake-model", "helper"):
        assert (ws.assets / name).stat().st_mode & 0o777 == 0o755

    assert state.model is not None
    assert state.model.name == "FAKE"
    assert state.model.version == "1.2"
    loaded = StateStore(ws).load()
    assert loaded is not None
    assert loaded.model == state.model
    assert reporter.metadata_calls == [
        ("model_name", "FAKE"),
        ("model_version", "1.2"),
    ]


def test_fetch_executables_duplicate_basename_raises_data_error(
    tmp_path: Path,
) -> None:
    ws = _ws(tmp_path)
    store = RecordingObjectStore()
    store.seed(S3Uri(_VERSIONS_BUCKET, "versoes/fake/1.2/a/x"), b"bin-a")
    store.seed(S3Uri(_VERSIONS_BUCKET, "versoes/fake/1.2/b/x"), b"bin-b")
    reporter = RecordingReporter()

    with pytest.raises(DataError, match="'x'"):
        fetch_executables(
            ws,
            FakePlugin(),
            store,
            reporter,
            StateStore(ws),
            _BASE_VERSOES_URI,
        )


def test_fetch_executables_empty_prefix_raises_data_error(
    tmp_path: Path,
) -> None:
    ws = _ws(tmp_path)
    store = RecordingObjectStore()
    reporter = RecordingReporter()

    with pytest.raises(DataError, match="no executables found"):
        fetch_executables(
            ws,
            FakePlugin(),
            store,
            reporter,
            StateStore(ws),
            _BASE_VERSOES_URI,
        )


def test_fetch_executables_model_mismatch_raises_usage_error(
    tmp_path: Path,
) -> None:
    ws = _ws(tmp_path)
    store = RecordingObjectStore()
    reporter = RecordingReporter()

    with pytest.raises(UsageError, match="versoes"):
        fetch_executables(
            ws,
            FakePlugin(),
            store,
            reporter,
            StateStore(ws),
            f"s3://{_VERSIONS_BUCKET}/versoes/other/1.2/",
        )


def test_fetch_executables_dotdot_token_raises_usage_error(
    tmp_path: Path,
) -> None:
    ws = _ws(tmp_path)
    store = RecordingObjectStore()
    reporter = RecordingReporter()

    with pytest.raises(UsageError, match="invalid versoes uri"):
        fetch_executables(
            ws,
            FakePlugin(),
            store,
            reporter,
            StateStore(ws),
            f"s3://{_VERSIONS_BUCKET}/versoes/fake/1..2/",
        )


@pytest.mark.parametrize(
    "uri",
    [
        f"{_BASE_VERSOES_URI}\n",
        f"{_BASE_VERSOES_URI}\r\n",
        f"\n{_BASE_VERSOES_URI}",
    ],
)
def test_fetch_executables_trailing_newline_variants_raise_usage_error(
    tmp_path: Path, uri: str
) -> None:
    ws = _ws(tmp_path)
    store = RecordingObjectStore()
    reporter = RecordingReporter()

    with pytest.raises(UsageError, match="invalid versoes uri"):
        fetch_executables(
            ws, FakePlugin(), store, reporter, StateStore(ws), uri
        )


def test_fetch_executables_calls_check_executables_once(
    tmp_path: Path,
) -> None:
    ws = _ws(tmp_path)
    store = RecordingObjectStore()
    store.seed(S3Uri(_VERSIONS_BUCKET, "versoes/fake/1.2/fake-model"), b"bin-a")
    reporter = RecordingReporter()
    plugin = _CountingPlugin()

    fetch_executables(
        ws, plugin, store, reporter, StateStore(ws), _BASE_VERSOES_URI
    )

    assert plugin.check_executables_calls == 1


# ---------------------------------------------------------------------------
# fetch_inputs
# ---------------------------------------------------------------------------


def test_fetch_inputs_exact_key_download_ignores_similarly_named_object(
    tmp_path: Path,
) -> None:
    ws = _ws(tmp_path)
    store = RecordingObjectStore()
    store.seed(S3Uri(_INPUTS_BUCKET, "entradas/deck.zip"), b"good-bytes")
    store.seed(S3Uri(_INPUTS_BUCKET, "entradas/deck.zip.bak"), b"bak-bytes")
    reporter = RecordingReporter()

    fetch_inputs(
        ws,
        FakePlugin(),
        store,
        reporter,
        StateStore(ws),
        _INPUT_URI,
        parent_path="",
        delete=False,
    )

    assert ws.eco_deck_path.read_bytes() == b"good-bytes"
    assert [uri for op, uri in store.ops if op == "download"] == [_INPUT_URI]


def test_fetch_inputs_missing_object_raises_data_error(
    tmp_path: Path,
) -> None:
    ws = _ws(tmp_path)
    store = RecordingObjectStore()
    reporter = RecordingReporter()

    with pytest.raises(DataError, match="entradas/deck.zip"):
        fetch_inputs(
            ws,
            FakePlugin(),
            store,
            reporter,
            StateStore(ws),
            _INPUT_URI,
            parent_path="",
            delete=False,
        )


def test_fetch_inputs_delete_true_deletes_input_last(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    store = RecordingObjectStore()
    store.seed(S3Uri(_INPUTS_BUCKET, "entradas/deck.zip"), b"deck-bytes")
    reporter = RecordingReporter()

    fetch_inputs(
        ws,
        FakePlugin(),
        store,
        reporter,
        StateStore(ws),
        _INPUT_URI,
        parent_path="",
        delete=True,
    )

    assert store.ops[-1] == ("delete", _INPUT_URI)


def test_fetch_inputs_parent_from_own_bucket_downloads_cross_bucket_and_deletes_input(
    tmp_path: Path,
) -> None:
    ws = _ws(tmp_path)
    store = RecordingObjectStore()
    store.seed(S3Uri(_INPUTS_BUCKET, "entradas/deck.zip"), b"deck-bytes")
    store.seed(
        S3Uri(_OUTPUTS_BUCKET, "artifacts/abc/saidas/metadata.modelops"),
        _parent_metadata(),
    )
    store.seed(
        S3Uri(_OUTPUTS_BUCKET, "artifacts/abc/saidas/cortes.zip"),
        b"cortes-bytes",
    )
    reporter = RecordingReporter()

    state = fetch_inputs(
        ws,
        ParentPlugin(),
        store,
        reporter,
        StateStore(ws),
        _INPUT_URI,
        parent_path=_PARENT_URI,
        delete=True,
    )

    assert ws.eco_deck_path.is_file()
    assert (ws.parent_dir / "cortes.zip").read_bytes() == b"cortes-bytes"
    assert state.parent is not None
    assert state.parent.path == _PARENT_URI

    parent_downloads = [
        uri
        for op, uri in store.ops
        if op == "download" and uri.startswith(f"s3://{_OUTPUTS_BUCKET}/")
    ]
    assert parent_downloads == [
        f"s3://{_OUTPUTS_BUCKET}/artifacts/abc/saidas/cortes.zip"
    ]
    assert store.ops[-1] == ("delete", _INPUT_URI)


def test_fetch_inputs_parent_artifact_missing_raises_data_error_and_skips_delete(
    tmp_path: Path,
) -> None:
    ws = _ws(tmp_path)
    store = RecordingObjectStore()
    store.seed(S3Uri(_INPUTS_BUCKET, "entradas/deck.zip"), b"deck-bytes")
    store.seed(
        S3Uri(_OUTPUTS_BUCKET, "artifacts/abc/saidas/metadata.modelops"),
        _parent_metadata(),
    )
    # cortes.zip seeded only in the input bucket, never the parent's own
    # bucket (R94: a DECOMP/NEWAVE parent artifact never falls back to
    # the input bucket).
    store.seed(
        S3Uri(_INPUTS_BUCKET, "artifacts/abc/saidas/cortes.zip"), b"wrong"
    )
    reporter = RecordingReporter()

    with pytest.raises(DataError, match="cortes.zip"):
        fetch_inputs(
            ws,
            ParentPlugin(),
            store,
            reporter,
            StateStore(ws),
            _INPUT_URI,
            parent_path=_PARENT_URI,
            delete=True,
        )

    assert "delete" not in [op for op, _ in store.ops]


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        pytest.param(
            _parent_metadata(model_name="DECOMP"),
            "NEWAVE.*DECOMP",
            id="model_name_mismatch",
        ),
        pytest.param(
            _parent_metadata(status="RUNTIME_ERROR"),
            "RUNTIME_ERROR",
            id="status_not_success",
        ),
        pytest.param(
            _parent_metadata(status="SUCCESS "),
            "SUCCESS ",
            id="status_trailing_space",
        ),
        pytest.param(
            json.dumps({"model_name": "NEWAVE", "status": "SUCCESS"}).encode(
                "utf-8"
            ),
            "required string field",
            id="missing_starting_date",
        ),
        pytest.param(b"[]", "JSON object", id="non_object_json"),
        pytest.param(None, "not found", id="missing_metadata_object"),
    ],
)
def test_fetch_inputs_parent_gate_variants_raise_data_error(
    tmp_path: Path, payload: bytes | None, match: str
) -> None:
    ws = _ws(tmp_path)
    store = RecordingObjectStore()
    store.seed(S3Uri(_INPUTS_BUCKET, "entradas/deck.zip"), b"deck-bytes")
    if payload is not None:
        store.seed(
            S3Uri(_OUTPUTS_BUCKET, "artifacts/abc/saidas/metadata.modelops"),
            payload,
        )
    reporter = RecordingReporter()

    with pytest.raises(DataError, match=match):
        fetch_inputs(
            ws,
            ParentPlugin(),
            store,
            reporter,
            StateStore(ws),
            _INPUT_URI,
            parent_path=_PARENT_URI,
            delete=True,
        )

    assert "delete" not in [op for op, _ in store.ops]
    assert not ws.parent_dir.exists()


@pytest.mark.parametrize("raw_parent_path", ["", "''", '""', "  "])
def test_fetch_inputs_empty_parent_normalization_succeeds_with_no_parent(
    tmp_path: Path, raw_parent_path: str
) -> None:
    ws = _ws(tmp_path)
    store = RecordingObjectStore()
    store.seed(S3Uri(_INPUTS_BUCKET, "entradas/deck.zip"), b"deck-bytes")
    reporter = RecordingReporter()

    state = fetch_inputs(
        ws,
        FakePlugin(),
        store,
        reporter,
        StateStore(ws),
        _INPUT_URI,
        parent_path=raw_parent_path,
        delete=False,
    )

    assert state.inputs is not None
    assert state.inputs.parent_path == ""
    assert state.parent is None


def test_fetch_inputs_parent_given_to_unsupported_plugin_raises_usage_error_before_any_op(
    tmp_path: Path,
) -> None:
    ws = _ws(tmp_path)
    store = RecordingObjectStore()
    reporter = RecordingReporter()

    with pytest.raises(UsageError, match="does not support parent runs"):
        fetch_inputs(
            ws,
            FakePlugin(),
            store,
            reporter,
            StateStore(ws),
            _INPUT_URI,
            parent_path="s3://b-1/artifacts/x",
            delete=False,
        )

    assert store.ops == []


def test_fetch_inputs_metadata_keys_respect_always_write_parent_path_true(
    tmp_path: Path,
) -> None:
    ws = _ws(tmp_path)
    store = RecordingObjectStore()
    store.seed(S3Uri(_INPUTS_BUCKET, "entradas/deck.zip"), b"deck-bytes")
    reporter = RecordingReporter()

    fetch_inputs(
        ws,
        _AlwaysWriteParentPlugin(),
        store,
        reporter,
        StateStore(ws),
        _INPUT_URI,
        parent_path="",
        delete=False,
    )

    assert reporter.metadata_calls == [("parent_path", "")]


def test_fetch_inputs_metadata_keys_respect_always_write_parent_path_false(
    tmp_path: Path,
) -> None:
    ws = _ws(tmp_path)
    store = RecordingObjectStore()
    store.seed(S3Uri(_INPUTS_BUCKET, "entradas/deck.zip"), b"deck-bytes")
    reporter = RecordingReporter()

    fetch_inputs(
        ws,
        ParentPlugin(),
        store,
        reporter,
        StateStore(ws),
        _INPUT_URI,
        parent_path="",
        delete=False,
    )

    assert reporter.metadata_calls == []


# ---------------------------------------------------------------------------
# read_parent
# ---------------------------------------------------------------------------


def test_read_parent_returns_parent_info_with_starting_date_verbatim() -> None:
    store = RecordingObjectStore()
    store.seed(
        S3Uri(_OUTPUTS_BUCKET, "artifacts/abc/saidas/metadata.modelops"),
        _parent_metadata(),
    )

    info = read_parent(store, ParentPlugin(), _PARENT_URI)

    assert info == ParentInfo(
        path=_PARENT_URI,
        model_name="NEWAVE",
        starting_date="2025-10-01T00:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# normalize_parent_path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["", "''", '""', "  "])
def test_normalize_parent_path_quoted_empty_tokens_return_none(
    raw: str,
) -> None:
    assert normalize_parent_path(raw) is None


def test_normalize_parent_path_non_empty_value_returns_stripped_string() -> (
    None
):
    assert normalize_parent_path("  s3://b/x  ") == "s3://b/x"
