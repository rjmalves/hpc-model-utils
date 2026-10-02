"""Integration tests for Boto3ObjectStore against LocalStack."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import mkdtemp

import boto3
import pytest
from mypy_boto3_s3 import S3Client

from hpc_model_utils.infra.errors import (
    ObjectNotFoundError,
    StorageBackendError,
)
from hpc_model_utils.infra.s3 import Boto3ObjectStore, S3Uri

pytestmark = pytest.mark.integration


@pytest.fixture
def object_store() -> Boto3ObjectStore:
    client = boto3.client("s3", endpoint_url=os.environ["AWS_ENDPOINT_URL"])
    return Boto3ObjectStore(client)


class TestExistsAny:
    def test_returns_true_when_object_under_prefix_exists(
        self,
        test_bucket: str,
        localstack_s3_client: S3Client,
        object_store: Boto3ObjectStore,
    ) -> None:
        localstack_s3_client.put_object(
            Bucket=test_bucket, Key="ingest/deck.zip", Body=b"data"
        )
        assert object_store.exists_any(S3Uri(test_bucket, "ingest/")) is True

    def test_returns_false_when_prefix_has_no_objects(
        self, test_bucket: str, object_store: Boto3ObjectStore
    ) -> None:
        assert (
            object_store.exists_any(S3Uri(test_bucket, "nonexistent/")) is False
        )


class TestGetBytes:
    def test_returns_object_content(
        self,
        test_bucket: str,
        localstack_s3_client: S3Client,
        object_store: Boto3ObjectStore,
    ) -> None:
        content = b'{"status": "SUCCESS"}'
        localstack_s3_client.put_object(
            Bucket=test_bucket, Key="config/metadata.json", Body=content
        )
        result = object_store.get_bytes(
            S3Uri(test_bucket, "config/metadata.json")
        )
        assert result == content

    def test_missing_key_raises_object_not_found(
        self, test_bucket: str, object_store: Boto3ObjectStore
    ) -> None:
        with pytest.raises(ObjectNotFoundError):
            object_store.get_bytes(S3Uri(test_bucket, "missing/key.txt"))


class TestDownload:
    def test_exact_key_downloads_only_that_object(
        self,
        test_bucket: str,
        localstack_s3_client: S3Client,
        object_store: Boto3ObjectStore,
    ) -> None:
        localstack_s3_client.put_object(
            Bucket=test_bucket, Key="ingest/deck.zip", Body=b"ingest/deck.zip"
        )
        localstack_s3_client.put_object(
            Bucket=test_bucket,
            Key="ingest/deck.zip.bak",
            Body=b"ingest/deck.zip.bak",
        )
        destination = Path(mkdtemp())
        dest_file = destination / "deck.zip"

        result = object_store.download(
            S3Uri(test_bucket, "ingest/deck.zip"), dest_file
        )

        assert result == dest_file
        assert list(destination.iterdir()) == [dest_file]
        assert dest_file.read_bytes() == b"ingest/deck.zip"


class TestDownloadPrefix:
    def test_flattens_nested_keys_into_dest_dir(
        self,
        test_bucket: str,
        localstack_s3_client: S3Client,
        object_store: Boto3ObjectStore,
    ) -> None:
        files = {
            "data/nested/file1.txt": b"content1",
            "data/file2.txt": b"content2",
        }
        for key, content in files.items():
            localstack_s3_client.put_object(
                Bucket=test_bucket, Key=key, Body=content
            )
        destination = Path(mkdtemp())

        result = object_store.download_prefix(
            S3Uri(test_bucket, "data"), destination
        )

        assert sorted(p.name for p in result) == ["file1.txt", "file2.txt"]
        assert (destination / "file1.txt").read_bytes() == b"content1"
        assert (destination / "file2.txt").read_bytes() == b"content2"

    def test_trailing_slash_rule_excludes_sibling_directory(
        self,
        test_bucket: str,
        localstack_s3_client: S3Client,
        object_store: Boto3ObjectStore,
    ) -> None:
        localstack_s3_client.put_object(
            Bucket=test_bucket, Key="versoes/newave/30/a.txt", Body=b"a"
        )
        localstack_s3_client.put_object(
            Bucket=test_bucket, Key="versoes/newave/300/x.txt", Body=b"x"
        )
        destination = Path(mkdtemp())

        result = object_store.download_prefix(
            S3Uri(test_bucket, "versoes/newave/30"), destination
        )

        assert [p.name for p in result] == ["a.txt"]

    def test_empty_prefix_raises_object_not_found(
        self, test_bucket: str, object_store: Boto3ObjectStore
    ) -> None:
        destination = Path(mkdtemp())
        with pytest.raises(ObjectNotFoundError):
            object_store.download_prefix(
                S3Uri(test_bucket, "nothing/here"), destination
            )


class TestUpload:
    def test_upload_then_get_bytes_round_trips(
        self,
        test_bucket: str,
        localstack_s3_client: S3Client,
        object_store: Boto3ObjectStore,
    ) -> None:
        local_file = Path(mkdtemp()) / "output.zip"
        content = b"compressed output data"
        local_file.write_bytes(content)

        object_store.upload(
            local_file, S3Uri(test_bucket, "outputs/result.zip")
        )

        response = localstack_s3_client.get_object(
            Bucket=test_bucket, Key="outputs/result.zip"
        )
        assert response["Body"].read() == content


class TestDelete:
    def test_delete_removes_object(
        self,
        test_bucket: str,
        localstack_s3_client: S3Client,
        object_store: Boto3ObjectStore,
    ) -> None:
        localstack_s3_client.put_object(
            Bucket=test_bucket, Key="temp/to-delete.txt", Body=b"temporary"
        )

        object_store.delete(S3Uri(test_bucket, "temp/to-delete.txt"))

        objects = localstack_s3_client.list_objects_v2(
            Bucket=test_bucket, Prefix="temp/to-delete.txt"
        )
        assert "Contents" not in objects


class TestErrorMapping:
    def test_unreachable_endpoint_raises_storage_backend_error(self) -> None:
        client = boto3.client(
            "s3",
            endpoint_url="http://127.0.0.1:9",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        store = Boto3ObjectStore(client)
        with pytest.raises(StorageBackendError):
            store.exists_any(S3Uri("some-bucket", "key"))
