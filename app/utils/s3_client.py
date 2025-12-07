"""S3 client factory with endpoint URL support for LocalStack testing."""

from os import getenv
from typing import TYPE_CHECKING, Any

import boto3

if TYPE_CHECKING:
    from mypy_boto3_s3.service_resource import S3ServiceResource


def get_s3_resource(
    aws_access_key_id: str | None = None,
    aws_secret_access_key: str | None = None,
    endpoint_url: str | None = None,
) -> "S3ServiceResource":
    """
    Create boto3 S3 resource with optional endpoint override.

    Endpoint URL priority:
    1. endpoint_url parameter (highest)
    2. AWS_ENDPOINT_URL environment variable
    3. None (uses default AWS S3)

    Args:
        aws_access_key_id: AWS access key (optional)
        aws_secret_access_key: AWS secret key (optional)
        endpoint_url: Optional S3 endpoint URL for testing with LocalStack

    Returns:
        Configured boto3 S3 resource

    Example:
        # Production (AWS S3)
        s3 = get_s3_resource()

        # LocalStack testing
        s3 = get_s3_resource(endpoint_url="http://localhost:4566")

        # Or via environment variable
        # export AWS_ENDPOINT_URL=http://localhost:4566
        s3 = get_s3_resource()
    """
    if endpoint_url is None:
        endpoint_url = getenv("AWS_ENDPOINT_URL")

    kwargs: dict[str, Any] = {
        "service_name": "s3",
        "aws_access_key_id": aws_access_key_id,
        "aws_secret_access_key": aws_secret_access_key,
    }

    if endpoint_url is not None:
        kwargs["endpoint_url"] = endpoint_url

    return boto3.resource(**kwargs)
