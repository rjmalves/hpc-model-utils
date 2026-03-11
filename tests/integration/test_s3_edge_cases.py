"""Integration tests for S3 edge cases and error conditions."""

import pytest
from mypy_boto3_s3 import S3Client

from app.utils.s3 import check_items_in_bucket

pytestmark = pytest.mark.integration


class TestSpecialCharacters:
    """Test handling of special characters in keys."""

    def test_handles_spaces_in_key(
        self, test_bucket: str, localstack_s3_client: S3Client
    ):
        """Given key with spaces, should handle correctly."""
        key = "data/file with spaces.txt"
        localstack_s3_client.put_object(
            Bucket=test_bucket, Key=key, Body=b"content"
        )

        result = check_items_in_bucket(
            bucket_name=test_bucket,
            remote_prefix="data/",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )

        assert key in result

    def test_handles_unicode_in_key(
        self, test_bucket: str, localstack_s3_client: S3Client
    ):
        """Given key with unicode, should handle correctly."""
        key = "data/arquivo-português.txt"
        localstack_s3_client.put_object(
            Bucket=test_bucket, Key=key, Body="conteúdo".encode()
        )

        result = check_items_in_bucket(
            bucket_name=test_bucket,
            remote_prefix="data/",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )

        assert key in result


class TestPagination:
    """Test pagination with many objects."""

    @pytest.mark.slow
    def test_lists_more_than_1000_objects(
        self, test_bucket: str, localstack_s3_client: S3Client
    ):
        """Given >1000 objects, should paginate correctly."""
        for i in range(1100):
            localstack_s3_client.put_object(
                Bucket=test_bucket,
                Key=f"data/file{i:04d}.txt",
                Body=b"content",
            )

        result = check_items_in_bucket(
            bucket_name=test_bucket,
            remote_prefix="data/",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )

        assert len(result) == 1100


class TestEmptyAndEdgeCases:
    """Test empty inputs and edge cases."""

    def test_empty_prefix_lists_all(
        self, test_bucket: str, localstack_s3_client: S3Client
    ):
        """Given a prefix, should list all objects under it."""
        prefix = "empty-prefix-test/"
        keys = [f"{prefix}file1.txt", f"{prefix}dir/file2.txt"]
        for key in keys:
            localstack_s3_client.put_object(
                Bucket=test_bucket, Key=key, Body=b"content"
            )

        result = check_items_in_bucket(
            bucket_name=test_bucket,
            remote_prefix=prefix,
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )

        assert len(result) == 2

    def test_very_long_key_name(
        self, test_bucket: str, localstack_s3_client: S3Client
    ):
        """Given very long key (near 1024 char limit), should handle."""
        long_key = "data/" + "a" * 1000 + ".txt"
        localstack_s3_client.put_object(
            Bucket=test_bucket, Key=long_key, Body=b"content"
        )

        result = check_items_in_bucket(
            bucket_name=test_bucket,
            remote_prefix="data/",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )

        assert long_key in result
