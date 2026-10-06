from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path

import boto3
import pytest
from boto3.exceptions import S3UploadFailedError
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    IncompleteReadError,
    ResponseStreamingError,
)
from botocore.response import StreamingBody
from botocore.stub import Stubber
from mypy_boto3_s3 import S3Client

from hpc_model_utils.infra.errors import (
    ObjectNotFoundError,
    StorageBackendError,
)
from hpc_model_utils.infra.s3 import Boto3ObjectStore, S3Uri


@pytest.fixture
def client() -> S3Client:
    return boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


@pytest.fixture
def stubber(client: S3Client) -> Stubber:
    stub = Stubber(client)
    stub.activate()
    return stub


def test_exists_any_nonzero_key_count_returns_true(
    client: S3Client, stubber: Stubber
) -> None:
    stubber.add_response(
        "list_objects_v2",
        {"KeyCount": 1},
        {"Bucket": "bucket", "Prefix": "ingest/", "MaxKeys": 1},
    )
    store = Boto3ObjectStore(client)
    assert store.exists_any(S3Uri("bucket", "ingest/")) is True
    stubber.assert_no_pending_responses()


def test_exists_any_zero_key_count_returns_false(
    client: S3Client, stubber: Stubber
) -> None:
    stubber.add_response(
        "list_objects_v2",
        {"KeyCount": 0},
        {"Bucket": "bucket", "Prefix": "missing/", "MaxKeys": 1},
    )
    store = Boto3ObjectStore(client)
    assert store.exists_any(S3Uri("bucket", "missing/")) is False
    stubber.assert_no_pending_responses()


def test_get_bytes_returns_object_body(
    client: S3Client, stubber: Stubber
) -> None:
    stubber.add_response(
        "get_object",
        {"Body": BytesIO(b"metadata content")},
        {"Bucket": "bucket", "Key": "config/metadata.json"},
    )
    store = Boto3ObjectStore(client)
    assert (
        store.get_bytes(S3Uri("bucket", "config/metadata.json"))
        == b"metadata content"
    )
    stubber.assert_no_pending_responses()


def test_download_exact_key_ignores_similarly_prefixed_key(
    client: S3Client, stubber: Stubber, tmp_path: Path
) -> None:
    stubber.add_response(
        "get_object",
        {"Body": BytesIO(b"deck contents")},
        {"Bucket": "bucket", "Key": "ingest/deck.zip"},
    )
    store = Boto3ObjectStore(client)
    dest = tmp_path / "out" / "deck.zip"

    result = store.download(S3Uri("bucket", "ingest/deck.zip"), dest)

    assert result == dest
    assert dest.read_bytes() == b"deck contents"
    assert list((tmp_path / "out").iterdir()) == [dest]
    stubber.assert_no_pending_responses()


def test_download_to_existing_directory_raises_value_error(
    client: S3Client, tmp_path: Path
) -> None:
    store = Boto3ObjectStore(client)
    existing_dir = tmp_path / "already-a-dir"
    existing_dir.mkdir()

    with pytest.raises(ValueError, match="invalid download destination"):
        store.download(S3Uri("bucket", "ingest/deck.zip"), existing_dir)


_LARGE_CHUNK = 8 * 1024 * 1024


class _FailingBody:
    def __init__(self, first_chunk: bytes) -> None:
        self._first_chunk: bytes | None = first_chunk

    def read(self, amt: int | None = None) -> bytes:
        if self._first_chunk is not None:
            chunk, self._first_chunk = self._first_chunk, None
            return chunk
        raise ResponseStreamingError(error="connection reset mid-stream")


def test_download_streams_large_body_without_loading_fully_in_memory(
    client: S3Client, stubber: Stubber, tmp_path: Path
) -> None:
    payload = (b"x" * _LARGE_CHUNK) * 3 + b"tail-bytes"
    stubber.add_response(
        "get_object",
        {"Body": StreamingBody(BytesIO(payload), len(payload))},
        {"Bucket": "bucket", "Key": "ingest/cortes.zip"},
    )
    store = Boto3ObjectStore(client)
    dest = tmp_path / "cortes.zip"

    result = store.download(S3Uri("bucket", "ingest/cortes.zip"), dest)

    assert result == dest
    assert dest.read_bytes() == payload
    assert list(tmp_path.iterdir()) == [dest]
    stubber.assert_no_pending_responses()


def test_download_body_error_partway_leaves_no_partial_file(
    client: S3Client, stubber: Stubber, tmp_path: Path
) -> None:
    stubber.add_response(
        "get_object",
        {"Body": _FailingBody(b"partial-bytes")},
        {"Bucket": "bucket", "Key": "ingest/cortes.zip"},
    )
    store = Boto3ObjectStore(client)
    dest = tmp_path / "cortes.zip"

    with pytest.raises(StorageBackendError):
        store.download(S3Uri("bucket", "ingest/cortes.zip"), dest)

    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []


def test_download_os_replace_failure_leaves_no_partial_file(
    client: S3Client,
    stubber: Stubber,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stubber.add_response(
        "get_object",
        {"Body": BytesIO(b"deck contents")},
        {"Bucket": "bucket", "Key": "ingest/deck.zip"},
    )

    def _raise_replace(src: object, dst: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", _raise_replace)
    store = Boto3ObjectStore(client)
    dest = tmp_path / "deck.zip"

    with pytest.raises(OSError, match="simulated replace failure"):
        store.download(S3Uri("bucket", "ingest/deck.zip"), dest)

    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []


def test_download_prefix_paginates_flattens_and_sorts(
    client: S3Client, stubber: Stubber, tmp_path: Path
) -> None:
    stubber.add_response(
        "list_objects_v2",
        {
            "KeyCount": 1,
            "IsTruncated": True,
            "NextContinuationToken": "token-1",
            "Contents": [{"Key": "versoes/newave/30/b.txt"}],
        },
        {"Bucket": "bucket", "Prefix": "versoes/newave/30"},
    )
    stubber.add_response(
        "list_objects_v2",
        {
            "KeyCount": 1,
            "IsTruncated": False,
            "Contents": [{"Key": "versoes/newave/30/a.txt"}],
        },
        {
            "Bucket": "bucket",
            "Prefix": "versoes/newave/30",
            "ContinuationToken": "token-1",
        },
    )
    stubber.add_response(
        "get_object",
        {"Body": BytesIO(b"b content")},
        {"Bucket": "bucket", "Key": "versoes/newave/30/b.txt"},
    )
    stubber.add_response(
        "get_object",
        {"Body": BytesIO(b"a content")},
        {"Bucket": "bucket", "Key": "versoes/newave/30/a.txt"},
    )
    store = Boto3ObjectStore(client)

    result = store.download_prefix(
        S3Uri("bucket", "versoes/newave/30"), tmp_path
    )

    assert result == [tmp_path / "a.txt", tmp_path / "b.txt"]
    assert (tmp_path / "a.txt").read_bytes() == b"a content"
    assert (tmp_path / "b.txt").read_bytes() == b"b content"
    stubber.assert_no_pending_responses()


def test_download_prefix_trailing_slash_rule_excludes_sibling_directory(
    client: S3Client, stubber: Stubber, tmp_path: Path
) -> None:
    stubber.add_response(
        "list_objects_v2",
        {
            "KeyCount": 2,
            "IsTruncated": False,
            "Contents": [
                {"Key": "versoes/newave/30/a.txt"},
                {"Key": "versoes/newave/300/x.txt"},
            ],
        },
        {"Bucket": "bucket", "Prefix": "versoes/newave/30"},
    )
    stubber.add_response(
        "get_object",
        {"Body": BytesIO(b"a content")},
        {"Bucket": "bucket", "Key": "versoes/newave/30/a.txt"},
    )
    store = Boto3ObjectStore(client)

    result = store.download_prefix(
        S3Uri("bucket", "versoes/newave/30"), tmp_path
    )

    assert result == [tmp_path / "a.txt"]
    stubber.assert_no_pending_responses()


def test_download_prefix_skips_marker_keys_downloads_only_files(
    client: S3Client, stubber: Stubber, tmp_path: Path
) -> None:
    stubber.add_response(
        "list_objects_v2",
        {
            "KeyCount": 4,
            "IsTruncated": False,
            "Contents": [
                {"Key": "prefix/"},
                {"Key": "prefix/sub/"},
                {"Key": "prefix/a.txt"},
                {"Key": "prefix/b.txt"},
            ],
        },
        {"Bucket": "bucket", "Prefix": "prefix"},
    )
    stubber.add_response(
        "get_object",
        {"Body": BytesIO(b"a content")},
        {"Bucket": "bucket", "Key": "prefix/a.txt"},
    )
    stubber.add_response(
        "get_object",
        {"Body": BytesIO(b"b content")},
        {"Bucket": "bucket", "Key": "prefix/b.txt"},
    )
    store = Boto3ObjectStore(client)

    result = store.download_prefix(S3Uri("bucket", "prefix"), tmp_path)

    expected = [tmp_path / "a.txt", tmp_path / "b.txt"]
    assert result == expected
    assert sorted(tmp_path.iterdir()) == expected
    stubber.assert_no_pending_responses()


def test_download_prefix_only_marker_key_raises_object_not_found(
    client: S3Client, stubber: Stubber, tmp_path: Path
) -> None:
    stubber.add_response(
        "list_objects_v2",
        {
            "KeyCount": 1,
            "IsTruncated": False,
            "Contents": [{"Key": "prefix/"}],
        },
        {"Bucket": "bucket", "Prefix": "prefix"},
    )
    store = Boto3ObjectStore(client)

    with pytest.raises(ObjectNotFoundError):
        store.download_prefix(S3Uri("bucket", "prefix"), tmp_path)
    stubber.assert_no_pending_responses()


def test_download_prefix_empty_result_raises_object_not_found(
    client: S3Client, stubber: Stubber, tmp_path: Path
) -> None:
    stubber.add_response(
        "list_objects_v2",
        {"KeyCount": 0, "IsTruncated": False},
        {"Bucket": "bucket", "Prefix": "nothing/here"},
    )
    store = Boto3ObjectStore(client)

    with pytest.raises(ObjectNotFoundError):
        store.download_prefix(S3Uri("bucket", "nothing/here"), tmp_path)
    stubber.assert_no_pending_responses()


def test_upload_calls_put_object(
    client: S3Client, stubber: Stubber, tmp_path: Path
) -> None:
    src = tmp_path / "output.zip"
    src.write_bytes(b"compressed output")
    stubber.add_response("put_object", {})
    store = Boto3ObjectStore(client)

    store.upload(src, S3Uri("bucket", "outputs/result.zip"))

    stubber.assert_no_pending_responses()


def test_delete_calls_delete_object(client: S3Client, stubber: Stubber) -> None:
    stubber.add_response(
        "delete_object",
        {},
        {"Bucket": "bucket", "Key": "temp/to-delete.txt"},
    )
    store = Boto3ObjectStore(client)

    store.delete(S3Uri("bucket", "temp/to-delete.txt"))

    stubber.assert_no_pending_responses()


@pytest.mark.parametrize(
    "error_code", ["404", "NoSuchKey", "NotFound", "NoSuchBucket"]
)
def test_call_client_error_not_found_code_maps_to_object_not_found(
    client: S3Client, stubber: Stubber, error_code: str
) -> None:
    stubber.add_client_error(
        "get_object", service_error_code=error_code, http_status_code=404
    )
    store = Boto3ObjectStore(client)

    with pytest.raises(ObjectNotFoundError):
        store.get_bytes(S3Uri("bucket", "key"))


def test_call_client_error_other_code_maps_to_storage_backend_error(
    client: S3Client, stubber: Stubber
) -> None:
    stubber.add_client_error(
        "get_object", service_error_code="AccessDenied", http_status_code=403
    )
    store = Boto3ObjectStore(client)

    with pytest.raises(StorageBackendError):
        store.get_bytes(S3Uri("bucket", "key"))


def test_call_botocore_error_maps_to_storage_backend_error(
    client: S3Client,
) -> None:
    store = Boto3ObjectStore(client)

    def _raise() -> bytes:
        raise BotoCoreError()

    with pytest.raises(StorageBackendError):
        store._call("op", S3Uri("bucket", "key"), _raise)


def test_upload_transfer_failure_raises_storage_backend_error(
    client: S3Client, stubber: Stubber, tmp_path: Path
) -> None:
    src = tmp_path / "output.zip"
    src.write_bytes(b"compressed output")
    stubber.add_client_error(
        "put_object", service_error_code="AccessDenied", http_status_code=403
    )
    store = Boto3ObjectStore(client)

    with pytest.raises(
        StorageBackendError,
        match=(
            r"^upload s3://bucket/outputs/result\.zip: "
            r"Failed to upload .*output\.zip to bucket/outputs/result\.zip: "
            r"An error occurred \(AccessDenied\)"
        ),
    ) as excinfo:
        store.upload(src, S3Uri("bucket", "outputs/result.zip"))

    assert isinstance(excinfo.value.__cause__, S3UploadFailedError)


def test_get_bytes_body_read_error_raises_storage_backend_error(
    client: S3Client, stubber: Stubber
) -> None:
    stubber.add_response(
        "get_object",
        {"Body": StreamingBody(BytesIO(b"partial"), 100)},
        {"Bucket": "bucket", "Key": "config/metadata.json"},
    )
    store = Boto3ObjectStore(client)

    with pytest.raises(
        StorageBackendError,
        match=(
            r"^get_bytes s3://bucket/config/metadata\.json: "
            r"7 read, but total bytes expected is 100\.$"
        ),
    ) as excinfo:
        store.get_bytes(S3Uri("bucket", "config/metadata.json"))

    assert isinstance(excinfo.value.__cause__, IncompleteReadError)


def test_exists_any_unreachable_endpoint_raises_storage_backend_error() -> None:
    client = boto3.client(
        "s3",
        region_name="us-east-1",
        endpoint_url="http://127.0.0.1:9",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
        config=Config(
            retries={"max_attempts": 1}, connect_timeout=1, read_timeout=1
        ),
    )
    store = Boto3ObjectStore(client)

    with pytest.raises(StorageBackendError):
        store.exists_any(S3Uri("bucket", "key"))


def test_init_default_client_passes_no_credential_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    real_client = boto3.client

    def _fake_client(service_name: str, **kwargs: object) -> S3Client:
        captured["service_name"] = service_name
        captured.update(kwargs)
        return real_client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )

    monkeypatch.setattr(boto3, "client", _fake_client)

    Boto3ObjectStore()

    assert captured["service_name"] == "s3"
    assert "aws_access_key_id" not in captured
    assert "aws_secret_access_key" not in captured
