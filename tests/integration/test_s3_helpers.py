"""Integration tests for high-level S3 helper functions."""

from logging import Logger
from pathlib import Path
from tempfile import mkdtemp
from unittest.mock import MagicMock

import pytest
from mypy_boto3_s3 import S3Client

from app.utils.s3 import (
    check_and_delete_bucket_item,
    check_and_download_bucket_items,
    check_and_get_bucket_item,
)

pytestmark = pytest.mark.integration


class TestCheckAndDownloadBucketItems:
    """Test check_and_download_bucket_items() end-to-end."""

    def test_downloads_existing_files(
        self, test_bucket: str, localstack_s3_client: S3Client
    ):
        """Given files exist, should check and download."""
        keys = ["data/file1.txt", "data/file2.txt"]
        for key in keys:
            localstack_s3_client.put_object(
                Bucket=test_bucket, Key=key, Body=b"content"
            )
        destination = mkdtemp()
        logger = MagicMock(spec=Logger)

        result = check_and_download_bucket_items(
            bucket=test_bucket,
            destination=destination,
            remote_filepaths="data/",
            logger=logger,
        )

        assert len(result) == 2
        for filepath in result:
            assert Path(filepath).exists()
        logger.info.assert_called()

    def test_raises_error_if_files_not_found(self, test_bucket: str):
        """Given files don't exist, should raise FileNotFoundError."""
        destination = mkdtemp()
        logger = MagicMock(spec=Logger)

        with pytest.raises(FileNotFoundError, match="not found"):
            check_and_download_bucket_items(
                bucket=test_bucket,
                destination=destination,
                remote_filepaths="nonexistent/",
                logger=logger,
            )

        logger.error.assert_called()


class TestCheckAndGetBucketItem:
    """Test check_and_get_bucket_item() end-to-end."""

    def test_gets_existing_file_content(
        self, test_bucket: str, localstack_s3_client: S3Client
    ):
        """Given file exists, should check and get content."""
        key = "config/settings.json"
        content = '{"key": "value"}'
        localstack_s3_client.put_object(
            Bucket=test_bucket, Key=key, Body=content.encode()
        )
        logger = MagicMock(spec=Logger)

        result = check_and_get_bucket_item(
            bucket=test_bucket,
            remote_filepath=key,
            logger=logger,
        )

        assert result == content
        logger.info.assert_called()

    def test_raises_error_if_file_not_found(self, test_bucket: str):
        """Given file doesn't exist, should raise FileNotFoundError."""
        logger = MagicMock(spec=Logger)

        with pytest.raises(FileNotFoundError, match="not found"):
            check_and_get_bucket_item(
                bucket=test_bucket,
                remote_filepath="nonexistent.txt",
                logger=logger,
            )

        logger.error.assert_called()


class TestCheckAndDeleteBucketItem:
    """Test check_and_delete_bucket_item() end-to-end."""

    def test_deletes_existing_file(
        self, test_bucket: str, localstack_s3_client: S3Client
    ):
        """Given file exists, should check and delete."""
        key = "temp/to-delete.txt"
        localstack_s3_client.put_object(
            Bucket=test_bucket, Key=key, Body=b"temporary"
        )
        logger = MagicMock(spec=Logger)

        result = check_and_delete_bucket_item(
            bucket=test_bucket,
            filename="to-delete.txt",
            remote_filepath=key,
            logger=logger,
        )

        assert result == key
        logger.info.assert_called()

        objects = localstack_s3_client.list_objects_v2(
            Bucket=test_bucket, Prefix=key
        )
        assert "Contents" not in objects

    def test_raises_error_if_file_not_found(self, test_bucket: str):
        """Given file doesn't exist, should raise FileNotFoundError."""
        logger = MagicMock(spec=Logger)

        with pytest.raises(FileNotFoundError, match="not found"):
            check_and_delete_bucket_item(
                bucket=test_bucket,
                filename="nonexistent.txt",
                remote_filepath="nonexistent.txt",
                logger=logger,
            )

        logger.error.assert_called()
