"""infra.s3: S3 object store adapter, used via the ObjectStore protocol.

ADR-034 and R64 require the boto3 default credential provider chain only;
no credential parameters are ever constructed or passed. AWS_ENDPOINT_URL
is honored natively by boto3 and used only to point at LocalStack.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeVar

import boto3
from boto3.exceptions import Boto3Error
from botocore.exceptions import BotoCoreError, ClientError

from hpc_model_utils.infra.errors import (
    ObjectNotFoundError,
    StorageBackendError,
)

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound", "NoSuchBucket"})
_CHUNK_SIZE = 8 * 1024 * 1024

T = TypeVar("T")


@dataclass(frozen=True)
class S3Uri:
    bucket: str
    key: str

    @classmethod
    def parse(cls, uri: str) -> S3Uri:
        if not uri.startswith("s3://"):
            raise ValueError(f"not an s3:// uri: {uri!r}")
        bucket, _, key = uri[len("s3://") :].partition("/")
        if not _BUCKET_RE.fullmatch(bucket):
            raise ValueError(f"invalid bucket name: {bucket!r}")
        return cls(bucket=bucket, key=key)

    def join(self, *parts: str) -> S3Uri:
        raw = "/".join((self.key, *parts))
        return S3Uri(self.bucket, re.sub(r"/+", "/", raw).lstrip("/"))

    @property
    def basename(self) -> str:
        return self.key.rsplit("/", 1)[-1]

    def __str__(self) -> str:
        return f"s3://{self.bucket}/{self.key}"


class ObjectStore(Protocol):
    def exists_any(self, prefix: S3Uri) -> bool: ...

    def get_bytes(self, uri: S3Uri) -> bytes: ...

    def download(self, uri: S3Uri, dest: Path) -> Path: ...

    def download_prefix(self, prefix: S3Uri, dest_dir: Path) -> list[Path]: ...

    def upload(self, src: Path, uri: S3Uri) -> None: ...

    def delete(self, uri: S3Uri) -> None: ...


class Boto3ObjectStore(ObjectStore):
    def __init__(self, client: S3Client | None = None) -> None:
        self._client: S3Client = (
            client if client is not None else boto3.client("s3")
        )

    def exists_any(self, prefix: S3Uri) -> bool:
        response = self._call(
            "exists_any",
            prefix,
            lambda: self._client.list_objects_v2(
                Bucket=prefix.bucket, Prefix=prefix.key, MaxKeys=1
            ),
        )
        return response["KeyCount"] > 0

    def get_bytes(self, uri: S3Uri) -> bytes:
        return self._get_object_bytes("get_bytes", uri)

    def download(self, uri: S3Uri, dest: Path) -> Path:
        if not dest.name or dest.is_dir():
            raise ValueError(f"invalid download destination: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        return self._download_to_path("download", uri, dest)

    def download_prefix(self, prefix: S3Uri, dest_dir: Path) -> list[Path]:
        boundary = prefix.key if prefix.key.endswith("/") else f"{prefix.key}/"
        paginator = self._client.get_paginator("list_objects_v2")
        pages = self._call(
            "download_prefix",
            prefix,
            lambda: list(
                paginator.paginate(Bucket=prefix.bucket, Prefix=prefix.key)
            ),
        )
        keys = [
            obj["Key"]
            for page in pages
            for obj in page.get("Contents", [])
            if obj["Key"].startswith(boundary) and not obj["Key"].endswith("/")
        ]
        if not keys:
            raise ObjectNotFoundError(str(prefix))

        dest_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for key in keys:
            obj_uri = S3Uri(prefix.bucket, key)
            dest = dest_dir / obj_uri.basename
            paths.append(
                self._download_to_path("download_prefix", obj_uri, dest)
            )
        return sorted(paths)

    def upload(self, src: Path, uri: S3Uri) -> None:
        self._call(
            "upload",
            uri,
            lambda: self._client.upload_file(str(src), uri.bucket, uri.key),
        )

    def delete(self, uri: S3Uri) -> None:
        self._call(
            "delete",
            uri,
            lambda: self._client.delete_object(Bucket=uri.bucket, Key=uri.key),
        )

    def _get_object_bytes(self, op: str, uri: S3Uri) -> bytes:
        def _read() -> bytes:
            response = self._client.get_object(Bucket=uri.bucket, Key=uri.key)
            return response["Body"].read()

        return self._call(op, uri, _read)

    def _download_to_path(self, op: str, uri: S3Uri, dest: Path) -> Path:
        tmp = dest.parent / f".{dest.name}.part"

        def _stream() -> Path:
            try:
                response = self._client.get_object(
                    Bucket=uri.bucket, Key=uri.key
                )
                with tmp.open("wb") as handle:
                    shutil.copyfileobj(
                        response["Body"], handle, length=_CHUNK_SIZE
                    )
                os.replace(tmp, dest)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
            return dest

        return self._call(op, uri, _stream)

    def _call(self, op: str, uri: S3Uri, fn: Callable[[], T]) -> T:
        try:
            return fn()
        except ClientError as err:
            code = err.response.get("Error", {}).get("Code", "")
            if code in _NOT_FOUND_CODES:
                raise ObjectNotFoundError(str(uri)) from err
            raise StorageBackendError(f"{op} {uri}: {code}") from err
        except BotoCoreError as err:
            raise StorageBackendError(f"{op} {uri}: {err}") from err
        except Boto3Error as err:
            raise StorageBackendError(f"{op} {uri}: {err}") from err
