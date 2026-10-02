"""Test-only in-memory ``ObjectStore`` fake (ticket-040) that records
every call it receives so a test can assert the exact upload order
and count."""

from __future__ import annotations

from pathlib import Path

from hpc_model_utils.infra.errors import (
    ObjectNotFoundError,
    StorageBackendError,
)
from hpc_model_utils.infra.s3 import ObjectStore, S3Uri


class RecordingObjectStore(ObjectStore):
    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], bytes] = {}
        self.ops: list[tuple[str, str]] = []
        self._fail_on: dict[str, int | None] = {}

    def seed(self, uri: S3Uri, data: bytes) -> None:
        self._objects[(uri.bucket, uri.key)] = data

    def fail_on(self, suffix: str, *, times: int | None = None) -> None:
        self._fail_on[suffix] = times

    def exists_any(self, prefix: S3Uri) -> bool:
        self.ops.append(("exists_any", str(prefix)))
        return any(
            bucket == prefix.bucket and key.startswith(prefix.key)
            for bucket, key in self._objects
        )

    def get_bytes(self, uri: S3Uri) -> bytes:
        self.ops.append(("get_bytes", str(uri)))
        try:
            return self._objects[(uri.bucket, uri.key)]
        except KeyError:
            raise ObjectNotFoundError(str(uri)) from None

    def download(self, uri: S3Uri, dest: Path) -> Path:
        self.ops.append(("download", str(uri)))
        try:
            data = self._objects[(uri.bucket, uri.key)]
        except KeyError:
            raise ObjectNotFoundError(str(uri)) from None
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest

    def download_prefix(self, prefix: S3Uri, dest_dir: Path) -> list[Path]:
        self.ops.append(("download_prefix", str(prefix)))
        boundary = prefix.key if prefix.key.endswith("/") else f"{prefix.key}/"
        keys = [
            key
            for bucket, key in self._objects
            if bucket == prefix.bucket
            and key.startswith(boundary)
            and not key.endswith("/")
        ]
        if not keys:
            raise ObjectNotFoundError(str(prefix))
        dest_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for key in keys:
            dest = dest_dir / key.rsplit("/", 1)[-1]
            dest.write_bytes(self._objects[(prefix.bucket, key)])
            paths.append(dest)
        return sorted(paths)

    def upload(self, src: Path, uri: S3Uri) -> None:
        key = str(uri)
        self.ops.append(("upload", key))
        data = src.read_bytes()
        self._maybe_fail(key)
        self._objects[(uri.bucket, uri.key)] = data

    def delete(self, uri: S3Uri) -> None:
        self.ops.append(("delete", str(uri)))
        self._objects.pop((uri.bucket, uri.key), None)

    def _maybe_fail(self, key: str) -> None:
        for suffix, remaining in list(self._fail_on.items()):
            if not key.endswith(suffix):
                continue
            if remaining is None:
                raise StorageBackendError(f"forced failure for {key}")
            if remaining <= 0:
                continue
            self._fail_on[suffix] = remaining - 1
            raise StorageBackendError(f"forced failure for {key}")
