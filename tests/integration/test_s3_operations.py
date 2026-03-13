"""Integration tests for core S3 utility functions."""

from pathlib import Path
from tempfile import mkdtemp

import pytest
from mypy_boto3_s3 import S3Client

from app.utils.s3 import (
    check_items_in_bucket,
    delete_bucket_items,
    download_bucket_items,
    get_bucket_items,
    upload_file_to_bucket,
)

pytestmark = pytest.mark.integration


class TestCheckItemsInBucket:
    """Test check_items_in_bucket() against LocalStack."""

    def test_finds_existing_object(
        self, test_bucket: str, localstack_s3_client: S3Client
    ):
        """Given object exists, should return its key."""
        key = "test-data/file.txt"
        localstack_s3_client.put_object(
            Bucket=test_bucket, Key=key, Body=b"test content"
        )

        result = check_items_in_bucket(
            bucket_name=test_bucket,
            remote_prefix="test-data/",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )

        assert key in result

    def test_returns_empty_for_nonexistent_prefix(self, test_bucket: str):
        """Given prefix doesn't exist, should return empty list."""
        result = check_items_in_bucket(
            bucket_name=test_bucket,
            remote_prefix="nonexistent/",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        assert result == []

    def test_finds_multiple_objects_with_prefix(
        self, test_bucket: str, localstack_s3_client: S3Client
    ):
        """Given multiple objects with prefix, should return all keys."""
        keys = [
            "models/newave/deck.zip",
            "models/newave/metadata.json",
            "models/decomp/deck.zip",
        ]
        for key in keys:
            localstack_s3_client.put_object(
                Bucket=test_bucket, Key=key, Body=b"data"
            )

        result = check_items_in_bucket(
            bucket_name=test_bucket,
            remote_prefix="models/newave/",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )

        assert len(result) == 2
        assert "models/newave/deck.zip" in result
        assert "models/newave/metadata.json" in result
        assert "models/decomp/deck.zip" not in result

    def test_handles_empty_bucket(self, test_bucket: str):
        """Given empty bucket, should return empty list."""
        result = check_items_in_bucket(
            bucket_name=test_bucket,
            remote_prefix="",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        assert result == []


class TestDownloadBucketItems:
    """Test download_bucket_items() against LocalStack."""

    def test_downloads_single_file(
        self, test_bucket: str, localstack_s3_client: S3Client
    ):
        """Given single file in S3, should download to local filesystem."""
        key = "inputs/deck.zip"
        content = b"fake zip content"
        localstack_s3_client.put_object(
            Bucket=test_bucket, Key=key, Body=content
        )
        destination = mkdtemp()

        result = download_bucket_items(
            bucket_name=test_bucket,
            prefixes=[key],
            destination=destination,
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )

        assert len(result) == 1
        downloaded_path = Path(result[0])
        assert downloaded_path.exists()
        assert downloaded_path.name == "deck.zip"
        assert downloaded_path.read_bytes() == content

    def test_downloads_multiple_files(
        self, test_bucket: str, localstack_s3_client: S3Client
    ):
        """Given multiple files, should download all."""
        files = {
            "data/file1.txt": b"content1",
            "data/file2.txt": b"content2",
            "data/file3.txt": b"content3",
        }
        for key, content in files.items():
            localstack_s3_client.put_object(
                Bucket=test_bucket, Key=key, Body=content
            )
        destination = mkdtemp()

        result = download_bucket_items(
            bucket_name=test_bucket,
            prefixes=list(files.keys()),
            destination=destination,
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )

        assert len(result) == 3
        for filepath in result:
            path = Path(filepath)
            assert path.exists()
            expected_content = files[f"data/{path.name}"]
            assert path.read_bytes() == expected_content

    def test_downloads_binary_file(
        self, test_bucket: str, localstack_s3_client: S3Client
    ):
        """Given binary file, should preserve content integrity."""
        key = "data/binary.dat"
        content = bytes(range(256))
        localstack_s3_client.put_object(
            Bucket=test_bucket, Key=key, Body=content
        )
        destination = mkdtemp()

        result = download_bucket_items(
            bucket_name=test_bucket,
            prefixes=[key],
            destination=destination,
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )

        assert Path(result[0]).read_bytes() == content


class TestUploadFileToBucket:
    """Test upload_file_to_bucket() against LocalStack."""

    def test_uploads_file_successfully(
        self, test_bucket: str, localstack_s3_client: S3Client
    ):
        """Given local file, should upload to S3."""
        temp_dir = Path(mkdtemp())
        local_file = temp_dir / "output.zip"
        content = b"compressed output data"
        local_file.write_bytes(content)
        remote_key = "outputs/result.zip"

        upload_file_to_bucket(
            local_filepath=str(local_file),
            destination_bucket=test_bucket,
            remote_filepath=remote_key,
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )

        response = localstack_s3_client.get_object(
            Bucket=test_bucket, Key=remote_key
        )
        assert response["Body"].read() == content

    @pytest.mark.xfail(
        reason="LocalStack 3.0 bug: empty file upload fails with NoneType error",
        raises=Exception,
    )
    def test_uploads_empty_file(
        self, test_bucket: str, localstack_s3_client: S3Client
    ):
        """Given empty file, should upload successfully."""
        temp_dir = Path(mkdtemp())
        local_file = temp_dir / "empty.txt"
        local_file.write_bytes(b"")
        remote_key = "data/empty.txt"

        upload_file_to_bucket(
            local_filepath=str(local_file),
            destination_bucket=test_bucket,
            remote_filepath=remote_key,
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )

        response = localstack_s3_client.get_object(
            Bucket=test_bucket, Key=remote_key
        )
        assert response["Body"].read() == b""


class TestGetBucketItems:
    """Test get_bucket_items() against LocalStack."""

    def test_gets_file_content_as_string(
        self, test_bucket: str, localstack_s3_client: S3Client
    ):
        """Given text file in S3, should return content as string."""
        key = "config/metadata.json"
        content = '{"status": "SUCCESS", "version": "1.0"}'
        localstack_s3_client.put_object(
            Bucket=test_bucket, Key=key, Body=content.encode()
        )

        result = get_bucket_items(
            bucket_name=test_bucket,
            prefixes=[key],
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )

        assert key in result
        assert result[key] == content

    def test_gets_multiple_files(
        self, test_bucket: str, localstack_s3_client: S3Client
    ):
        """Given multiple files, should return all contents."""
        files = {
            "file1.txt": "content1",
            "file2.txt": "content2",
        }
        for key, content in files.items():
            localstack_s3_client.put_object(
                Bucket=test_bucket, Key=key, Body=content.encode()
            )

        result = get_bucket_items(
            bucket_name=test_bucket,
            prefixes=list(files.keys()),
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )

        assert len(result) == 2
        for key, expected_content in files.items():
            assert result[key] == expected_content


class TestDeleteBucketItems:
    """Test delete_bucket_items() against LocalStack."""

    def test_deletes_existing_object(
        self, test_bucket: str, localstack_s3_client: S3Client
    ):
        """Given object exists, should delete it."""
        key = "temp/to-delete.txt"
        localstack_s3_client.put_object(
            Bucket=test_bucket, Key=key, Body=b"temporary"
        )

        result = delete_bucket_items(
            bucket_name=test_bucket,
            prefixes=[key],
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )

        assert key in result

        objects = localstack_s3_client.list_objects_v2(
            Bucket=test_bucket, Prefix=key
        )
        assert "Contents" not in objects

    def test_deletes_multiple_objects(
        self, test_bucket: str, localstack_s3_client: S3Client
    ):
        """Given multiple objects, should delete all."""
        keys = ["temp/file1.txt", "temp/file2.txt", "temp/file3.txt"]
        for key in keys:
            localstack_s3_client.put_object(
                Bucket=test_bucket, Key=key, Body=b"data"
            )

        result = delete_bucket_items(
            bucket_name=test_bucket,
            prefixes=keys,
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )

        assert len(result) == 3
        for key in keys:
            assert key in result
            objects = localstack_s3_client.list_objects_v2(
                Bucket=test_bucket, Prefix=key
            )
            assert "Contents" not in objects
