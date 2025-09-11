from io import BytesIO
from logging import CRITICAL, Logger
from os import getenv, makedirs
from os.path import join

import boto3

from app.utils.constants import AWS_ACCESS_KEY_ID_ENV, AWS_SECRET_ACCESS_KEY_ENV

boto3.set_stream_logger("", CRITICAL)


def path_to_bucket_and_key(path: str) -> dict:
    if path[:5] != "s3://":
        raise ValueError("Path must start with s3://")

    path = path[5:]
    parts = path.split("/")
    bucket = parts[0]
    key = "/".join(parts[1:])
    return {"bucket": bucket, "key": key}


def check_items_in_bucket(
    bucket_name: str,
    remote_prefix: str,
    aws_access_key_id: str | None = None,
    aws_secret_access_key: str | None = None,
) -> list[str]:
    s3 = boto3.resource(
        "s3",
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
    )
    bucket = s3.Bucket(bucket_name)
    objects: list[str] = []
    try:
        objects = [
            obj.key for obj in bucket.objects.filter(Prefix=remote_prefix)
        ]
    except Exception:
        return []
    return list(set(objects))


def upload_file_to_bucket(
    local_filepath: str,
    destination_bucket: str,
    remote_filepath: str,
    aws_access_key_id: str | None = None,
    aws_secret_access_key: str | None = None,
):
    s3 = boto3.resource(
        "s3",
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
    )
    s3.Bucket(destination_bucket).upload_file(local_filepath, remote_filepath)


def download_bucket_items(
    bucket_name: str,
    prefixes: list[str],
    destination: str,
    aws_access_key_id: str | None = None,
    aws_secret_access_key: str | None = None,
) -> list[str]:
    s3 = boto3.resource(
        "s3",
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
    )
    bucket = s3.Bucket(bucket_name)
    makedirs(destination, exist_ok=True)

    success_downloaded_paths = []
    for p in prefixes:
        filename = p.split("/")[-1]
        filepath = join(destination, filename)
        bucket.download_file(p, filepath)
        success_downloaded_paths.append(filepath)
    return list(set(success_downloaded_paths))


def get_bucket_items(
    bucket_name: str,
    prefixes: list[str],
    aws_access_key_id: str | None = None,
    aws_secret_access_key: str | None = None,
) -> dict[str, str]:
    s3 = boto3.resource(
        "s3",
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
    )
    bucket = s3.Bucket(bucket_name)

    success_get_objects = {}
    for p in prefixes:
        io_obj = BytesIO()
        bucket.download_fileobj(p, io_obj)
        io_obj.seek(0)
        success_get_objects[p] = io_obj.getvalue().decode()
    return success_get_objects


def delete_bucket_items(
    bucket_name: str,
    prefixes: list[str],
    aws_access_key_id: str | None = None,
    aws_secret_access_key: str | None = None,
) -> list[str]:
    s3 = boto3.resource(
        "s3",
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
    )

    success_deleted_prefixes = []
    for p in prefixes:
        item = s3.Object(bucket_name, p)
        item.delete()
        success_deleted_prefixes.append(p)
    return success_deleted_prefixes


def check_and_download_bucket_items(
    bucket: str,
    destination: str,
    remote_filepaths: str,
    logger: Logger,
) -> list[str]:
    logger.info(f"Fetching in {bucket} - {remote_filepaths}...")
    # Checks that bucket item exists
    item_prefixes = check_items_in_bucket(
        bucket,
        remote_filepaths,
        aws_access_key_id=getenv(AWS_ACCESS_KEY_ID_ENV),
        aws_secret_access_key=getenv(AWS_SECRET_ACCESS_KEY_ENV),
    )
    if len(item_prefixes) == 0:
        logger.error(f"Item not found: {remote_filepaths}")
        raise FileNotFoundError(
            f"Items {remote_filepaths} not found in {bucket}"
        )
    else:
        logger.debug(f"Found items: {item_prefixes}")

    # Downloads bucket item
    downloaded_filepaths = download_bucket_items(
        bucket,
        item_prefixes,
        destination,
        aws_access_key_id=getenv(AWS_ACCESS_KEY_ID_ENV),
        aws_secret_access_key=getenv(AWS_SECRET_ACCESS_KEY_ENV),
    )
    if len(downloaded_filepaths) != len(item_prefixes):
        logger.error("Failed to download data!")
        raise RuntimeError(f"Items {remote_filepaths} not downloaded")
    else:
        logger.info(f"Downloaded items to: {downloaded_filepaths}")
        return downloaded_filepaths


def check_and_get_bucket_item(
    bucket: str, remote_filepath: str, logger: Logger
) -> str:
    logger.info(f"Fetching in {join(bucket, remote_filepath)}...")
    # Checks that bucket item exists
    item_prefixes = check_items_in_bucket(
        bucket,
        remote_filepath,
        aws_access_key_id=getenv(AWS_ACCESS_KEY_ID_ENV),
        aws_secret_access_key=getenv(AWS_SECRET_ACCESS_KEY_ENV),
    )
    if len(item_prefixes) == 0:
        logger.error(f"File not found: {remote_filepath}")
        raise FileNotFoundError(f"File {remote_filepath} not found in {bucket}")
    else:
        logger.debug(f"Found items: {item_prefixes}")

    # Gets bucket item
    item_to_fetch = item_prefixes[0]
    item_data = get_bucket_items(
        bucket,
        [item_to_fetch],
        aws_access_key_id=getenv(AWS_ACCESS_KEY_ID_ENV),
        aws_secret_access_key=getenv(AWS_SECRET_ACCESS_KEY_ENV),
    )
    if len(item_data) != len(item_prefixes):
        logger.error("Failed to get data!")
        raise RuntimeError(f"File {remote_filepath} not fetched")
    else:
        return item_data[item_to_fetch]


def check_and_delete_bucket_item(
    bucket: str, filename: str, remote_filepath: str, logger: Logger
) -> str:
    logger.info(f"Deleting {filename} in {join(bucket, remote_filepath)}...")
    # Checks that bucket item exists
    item_prefixes = check_items_in_bucket(
        bucket,
        remote_filepath,
        aws_access_key_id=getenv(AWS_ACCESS_KEY_ID_ENV),
        aws_secret_access_key=getenv(AWS_SECRET_ACCESS_KEY_ENV),
    )
    if len(item_prefixes) == 0:
        logger.error(f"File not found: {remote_filepath}")
        raise FileNotFoundError(f"File {remote_filepath} not found in {bucket}")
    else:
        logger.debug(f"Found items: {item_prefixes}")

    # Deletes bucket item
    item_to_delete = item_prefixes[0]
    deleted_prefixes = delete_bucket_items(
        bucket,
        [item_to_delete],
        aws_access_key_id=getenv(AWS_ACCESS_KEY_ID_ENV),
        aws_secret_access_key=getenv(AWS_SECRET_ACCESS_KEY_ENV),
    )
    if len(deleted_prefixes) != len(item_prefixes):
        logger.error("Failed to download data!")
        raise RuntimeError(f"File {remote_filepath} not deleted")
    else:
        logger.debug(f"Deleted item: {deleted_prefixes[0]}")
        return deleted_prefixes[0]
