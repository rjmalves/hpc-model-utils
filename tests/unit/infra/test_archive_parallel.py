"""ticket-048b: member-level parallel compression with bounded memory.

AC1 (serial equivalence), AC2 (conformance and ZIP64), the memory half
of AC3, and AC4 (failure safety), plus the extra unit tests the ticket
calls out (duplicate-name warning, empty input, the ``workers=0``
clamp).
"""

from __future__ import annotations

import random
import shutil
import subprocess
import tracemalloc
import zipfile
from collections.abc import Sequence
from pathlib import Path

import pytest

from hpc_model_utils.infra import archive
from hpc_model_utils.infra.errors import UnsafeArchiveError

_MEMBER_META = (
    "filename",
    "compress_type",
    "CRC",
    "file_size",
    "date_time",
    "external_attr",
)


def _serial_reference(path: Path, entries: Sequence[tuple[Path, str]]) -> None:
    """Pre-048b ``_write_zip`` body: the AC1 contract pin."""
    with zipfile.ZipFile(
        path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
    ) as zf:
        for src, arcname in entries:
            zf.write(src, arcname, compress_type=archive.compression_for(src))


def _build_fixture(root: Path) -> list[Path]:
    sub = root / "sub"
    sub.mkdir()
    files: list[Path] = []
    for index in range(10):
        parent = sub if index % 3 == 0 else root
        target = parent / f"m{index:02d}.dat"
        if index % 2 == 0:
            target.write_text(f"{index:020d}\n" * 200)
        else:
            target.write_bytes(random.Random(index).randbytes(4096))
        files.append(target)
    parquet = root / "data.parquet"
    parquet.write_bytes(b"parquet-payload" * 50)
    files.append(parquet)
    empty = root / "empty.dat"
    empty.write_bytes(b"")
    files.append(empty)
    return files


def _meta(info: zipfile.ZipInfo) -> tuple[object, ...]:
    return tuple(getattr(info, field) for field in _MEMBER_META)


def _assert_matches_reference(out: Path, reference: Path) -> None:
    with zipfile.ZipFile(out) as actual, zipfile.ZipFile(reference) as ref:
        names = ref.namelist()
        assert actual.namelist() == names
        for name in names:
            assert _meta(actual.getinfo(name)) == _meta(ref.getinfo(name))
            assert actual.read(name) == ref.read(name)


# -- AC1: serial equivalence (flat, tree, STORE) --------------------------


def test_write_flat_workers_four_matches_serial_reference(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    files = _build_fixture(root)
    flat_out = tmp_path / "flat.zip"
    reference = tmp_path / "reference.zip"
    _serial_reference(reference, [(f, f.name) for f in files])

    archive.write_flat(flat_out, files, workers=4)

    _assert_matches_reference(flat_out, reference)


def test_write_tree_workers_four_matches_serial_reference(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    files = _build_fixture(root)
    tree_out = tmp_path / "tree.zip"
    reference = tmp_path / "reference.zip"
    entries = [
        (f, f.resolve().relative_to(root.resolve()).as_posix()) for f in files
    ]
    _serial_reference(reference, entries)

    archive.write_tree(tree_out, root, files, workers=4)

    _assert_matches_reference(tree_out, reference)


def test_write_flat_parquet_member_is_stored_with_equal_sizes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    files = _build_fixture(root)
    out = tmp_path / "flat.zip"

    archive.write_flat(out, files, workers=4)

    with zipfile.ZipFile(out) as zf:
        info = zf.getinfo("data.parquet")
        assert info.compress_type == zipfile.ZIP_STORED
        assert info.compress_size == info.file_size


def test_write_flat_workers_one_and_four_produce_identical_bytes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    files = _build_fixture(root)
    out1 = tmp_path / "w1.zip"
    out4 = tmp_path / "w4.zip"

    archive.write_flat(out1, files, workers=1)
    archive.write_flat(out4, files, workers=4)

    assert out1.read_bytes() == out4.read_bytes()


# -- AC2: conformance and ZIP64 --------------------------------------------


def test_write_flat_plain_and_zip64_patched_pass_testzip_and_unzip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert shutil.which("unzip") is not None, "unzip not found on PATH"
    root = tmp_path / "root"
    root.mkdir()
    files = _build_fixture(root)
    plain = tmp_path / "plain.zip"
    patched = tmp_path / "patched.zip"

    archive.write_flat(plain, files, workers=2)

    monkeypatch.setattr(zipfile, "ZIP64_LIMIT", 1024)
    monkeypatch.setattr(zipfile, "ZIP_FILECOUNT_LIMIT", 2)
    archive.write_flat(patched, files, workers=2)

    for out in (plain, patched):
        with zipfile.ZipFile(out) as zf:
            assert zf.testzip() is None
        result = subprocess.run(
            ["unzip", "-tq", str(out)], capture_output=True, check=False
        )
        assert result.returncode == 0

    patched_bytes = patched.read_bytes()
    assert b"PK\x06\x06" in patched_bytes
    assert b"PK\x06\x07" in patched_bytes
    with zipfile.ZipFile(patched) as zf:
        for info in zf.infolist():
            if info.file_size > 1024:
                assert info.extra.startswith(b"\x01\x00")


# -- AC3 (memory half): bounded traced memory ------------------------------


def test_write_flat_two_40mib_members_bounds_traced_memory_peak(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    size = 40 * 2**20
    numeric = root / "numeric.dat"
    numeric.write_text("1234567890" * (size // 10))
    noisy = root / "random.dat"
    noisy.write_bytes(random.Random(0).randbytes(size))
    out = tmp_path / "out.zip"

    tracemalloc.start()
    try:
        archive.write_flat(out, [numeric, noisy], workers=2)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < 12 * 2**20


# -- AC4: failure safety ----------------------------------------------------


def test_write_flat_duplicate_basename_leaves_directory_unchanged(
    tmp_path: Path,
) -> None:
    a_dir = tmp_path / "a"
    b_dir = tmp_path / "b"
    a_dir.mkdir()
    b_dir.mkdir()
    (a_dir / "dup.dat").write_text("a")
    (b_dir / "dup.dat").write_text("b")
    out = tmp_path / "out.zip"
    before = sorted(p.name for p in out.parent.iterdir())

    with pytest.raises(UnsafeArchiveError, match="dup.dat"):
        archive.write_flat(
            out, [a_dir / "dup.dat", b_dir / "dup.dat"], workers=4
        )

    assert sorted(p.name for p in out.parent.iterdir()) == before


def test_write_tree_symlink_escaping_root_leaves_directory_unchanged(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.dat"
    outside.write_text("secret")
    link = root / "link.dat"
    link.symlink_to(outside)
    out = tmp_path / "out.zip"
    before = sorted(p.name for p in out.parent.iterdir())

    with pytest.raises(UnsafeArchiveError, match="escapes root"):
        archive.write_tree(out, root, [link], workers=4)

    assert sorted(p.name for p in out.parent.iterdir()) == before


def test_write_flat_fifth_member_worker_failure_leaves_prior_archive_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    files = [root / f"m{i:02d}.dat" for i in range(12)]
    for path in files:
        path.write_text(path.name)
    out = tmp_path / "out.zip"
    out.write_bytes(b"pre-existing-bytes")
    before_bytes = out.read_bytes()
    before = sorted(p.name for p in out.parent.iterdir())

    real_compress = archive._compress_member

    def _flaky(path: Path, arcname: str, scratch: Path) -> object:
        if path.name == "m04.dat":
            raise OSError("forced")
        return real_compress(path, arcname, scratch)

    monkeypatch.setattr(archive, "_compress_member", _flaky)

    with pytest.raises(OSError, match="forced"):
        archive.write_flat(out, files, workers=4)

    assert out.read_bytes() == before_bytes
    assert sorted(p.name for p in out.parent.iterdir()) == before


# -- Extra unit tests --------------------------------------------------------


def test_write_tree_duplicate_entry_emits_duplicate_name_warning(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    member = root / "a.dat"
    member.write_text("a")
    out = tmp_path / "out.zip"

    with pytest.warns(UserWarning, match="Duplicate name"):
        archive.write_tree(out, root, [member, member], workers=2)

    with zipfile.ZipFile(out) as zf:
        assert zf.namelist() == ["a.dat", "a.dat"]


def test_write_flat_no_files_writes_empty_namelist_passing_testzip(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out.zip"

    names = archive.write_flat(out, [], workers=4)

    assert names == []
    with zipfile.ZipFile(out) as zf:
        assert zf.namelist() == []
        assert zf.testzip() is None


def test_write_flat_workers_zero_matches_workers_one_bytes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    files = _build_fixture(root)
    out0 = tmp_path / "w0.zip"
    out1 = tmp_path / "w1.zip"

    archive.write_flat(out0, files, workers=0)
    archive.write_flat(out1, files, workers=1)

    assert out0.read_bytes() == out1.read_bytes()
