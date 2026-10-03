"""Test-only in-memory ``ObjectStore`` fake (ticket-040) that records
every call it receives so a test can assert the exact upload order
and count.

ticket-055c: ``RecordingObjectStore`` takes its backing mapping as an
injectable ``MutableMapping``, so ``DirectoryObjectStore`` below can
swap in a filesystem-backed one -- one fake, two backends, with every
S3 semantic (exact-key download, the trailing-slash prefix boundary,
flattened ``download_prefix``, ``ObjectNotFoundError``) living in the
inherited methods only once. The directory backend lets a store be
shared across processes (a replayed console script cannot share an
in-memory store with the test process that seeded it)."""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from pathlib import Path, PurePosixPath

from hpc_model_utils.infra.errors import (
    ObjectNotFoundError,
    StorageBackendError,
)
from hpc_model_utils.infra.s3 import ObjectStore, S3Uri


class RecordingObjectStore(ObjectStore):
    def __init__(
        self, objects: MutableMapping[tuple[str, str], bytes] | None = None
    ) -> None:
        self._objects: MutableMapping[tuple[str, str], bytes] = (
            {} if objects is None else objects
        )
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


class _DirectoryObjects(MutableMapping[tuple[str, str], bytes]):
    """Stores ``(bucket, key)`` at ``root/bucket/key``."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @staticmethod
    def _validate(key: str) -> None:
        if not key:
            raise ValueError("object key must not be empty")
        if key.endswith("/"):
            raise ValueError(f"object key must not end with '/': {key!r}")
        pure = PurePosixPath(key)
        if pure.is_absolute() or ".." in pure.parts:
            raise ValueError(
                f"object key must be relative and free of '..': {key!r}"
            )

    def _path(self, key: tuple[str, str]) -> Path:
        bucket, object_key = key
        self._validate(object_key)
        return self._root / bucket / object_key

    def __getitem__(self, key: tuple[str, str]) -> bytes:
        try:
            return self._path(key).read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            raise KeyError(key) from None

    def __setitem__(self, key: tuple[str, str], value: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)

    def __delitem__(self, key: tuple[str, str]) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            raise KeyError(key) from None

    def __iter__(self) -> Iterator[tuple[str, str]]:
        if not self._root.is_dir():
            return
        entries: list[tuple[str, str]] = []
        for path in self._root.rglob("*"):
            if not path.is_file():
                continue
            parts = path.relative_to(self._root).parts
            entries.append((parts[0], "/".join(parts[1:])))
        yield from sorted(entries)

    def __len__(self) -> int:
        return sum(1 for _ in self)


class DirectoryObjectStore(RecordingObjectStore):
    """A ``RecordingObjectStore`` backed by files under ``root`` instead
    of an in-memory dict, so a store seeded by one process can be read
    by a console script running as a separate subprocess (ticket-055c)."""

    def __init__(self, root: Path) -> None:
        super().__init__(objects=_DirectoryObjects(root))
