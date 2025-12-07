"""Pytest fixtures for integration tests with LocalStack."""

import os
from typing import Generator

import boto3
import pytest
from mypy_boto3_s3 import S3Client


@pytest.fixture(scope="session", autouse=True)
def configure_localstack_environment() -> None:
    """Configure environment for LocalStack if not already set."""
    if not os.getenv("AWS_ENDPOINT_URL"):
        os.environ["AWS_ENDPOINT_URL"] = "http://localhost:4566"
    if not os.getenv("AWS_ACCESS_KEY_ID"):
        os.environ["AWS_ACCESS_KEY_ID"] = "test"
    if not os.getenv("AWS_SECRET_ACCESS_KEY"):
        os.environ["AWS_SECRET_ACCESS_KEY"] = "test"
    if not os.getenv("AWS_DEFAULT_REGION"):
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture(scope="session")
def localstack_s3_client() -> S3Client:
    """Create S3 client configured for LocalStack."""
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
        region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
    )


@pytest.fixture
def test_bucket(localstack_s3_client: S3Client) -> Generator[str, None, None]:
    """
    Create test bucket for integration tests.
    
    Yields bucket name, then cleans up after test.
    """
    bucket_name = "test-hpc-model-utils"
    
    try:
        localstack_s3_client.create_bucket(Bucket=bucket_name)
    except localstack_s3_client.exceptions.BucketAlreadyExists:
        pass
    except localstack_s3_client.exceptions.BucketAlreadyOwnedByYou:
        pass
    
    yield bucket_name
    
    try:
        objects = localstack_s3_client.list_objects_v2(Bucket=bucket_name)
        if "Contents" in objects:
            delete_objects = {
                "Objects": [{"Key": obj["Key"]} for obj in objects["Contents"]]
            }
            localstack_s3_client.delete_objects(
                Bucket=bucket_name, Delete=delete_objects
            )
        
        localstack_s3_client.delete_bucket(Bucket=bucket_name)
    except Exception:
        pass
