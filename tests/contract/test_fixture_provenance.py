"""Contract tests for vendored fixture provenance (ADR-049, R103)."""

from __future__ import annotations

import hashlib
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_ROOT = REPO_ROOT / "tests" / "fixtures"
PROVENANCE_PATH = FIXTURES_ROOT / "PROVENANCE.md"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_EXT_RE = re.compile(r"(?i)\.(lic|cep)$")
_SECRET_RE = re.compile(rb"AKIA[0-9A-Z]{16}|(?i:aws_secret_access_key)")


@dataclass(frozen=True)
class Row:
    path: str
    source_repo: str
    source_path: str
    commit: str
    sha256: str


def read_provenance(path: Path) -> list[Row]:
    rows: list[Row] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [
            cell.strip().strip("`") for cell in stripped.strip("|").split("|")
        ]
        if len(cells) != 5 or not _SHA256_RE.match(cells[4]):
            continue
        rows.append(Row(*cells))
    return rows


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_hashes(root: Path, rows: list[Row]) -> list[str]:
    errors: list[str] = []
    for row in rows:
        try:
            actual = _sha256(root / row.path)
        except OSError as exc:
            errors.append(f"{row.path}: {exc}")
            continue
        if actual != row.sha256:
            errors.append(
                f"{row.path}: expected sha256 {row.sha256}, got {actual}"
            )
    return errors


def scan_forbidden(root: Path) -> list[str]:
    errors: list[str] = []
    for file_path in sorted(root.rglob("*")):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(root).as_posix()
        if _FORBIDDEN_EXT_RE.search(file_path.name):
            errors.append(f"{rel}: forbidden licence extension")
        if file_path.suffix.lower() != ".zip":
            try:
                content = file_path.read_bytes()
            except OSError as exc:
                errors.append(f"{rel}: {exc}")
                continue
            if _SECRET_RE.search(content):
                errors.append(f"{rel}: forbidden secret pattern")
            continue
        try:
            with zipfile.ZipFile(file_path) as archive:
                for member in archive.namelist():
                    if _FORBIDDEN_EXT_RE.search(member):
                        errors.append(
                            f"{rel}!{member}: forbidden licence extension"
                        )
                    with archive.open(member) as handle:
                        if _SECRET_RE.search(handle.read()):
                            errors.append(
                                f"{rel}!{member}: forbidden secret pattern"
                            )
        except (OSError, zipfile.BadZipFile) as exc:
            errors.append(f"{rel}: {exc}")
    return errors


@pytest.fixture(scope="module")
def provenance_rows() -> list[Row]:
    return read_provenance(PROVENANCE_PATH)


def test_read_provenance_real_table_returns_seven_rows(
    provenance_rows: list[Row],
) -> None:
    assert len(provenance_rows) == 7


def test_verify_hashes_real_tree_returns_empty(
    provenance_rows: list[Row],
) -> None:
    assert verify_hashes(REPO_ROOT, provenance_rows) == []


def test_scan_forbidden_real_tree_returns_empty() -> None:
    assert scan_forbidden(FIXTURES_ROOT) == []


def test_verify_hashes_mutated_copy_names_offending_path(
    provenance_rows: list[Row], tmp_path: Path
) -> None:
    mutated_root = tmp_path / "repo"
    shutil.copytree(FIXTURES_ROOT, mutated_root / "tests" / "fixtures")
    target = mutated_root / "tests" / "fixtures" / "decks" / "deck_decomp.zip"
    mutated = bytearray(target.read_bytes())
    mutated[0] ^= 0xFF
    target.write_bytes(bytes(mutated))

    errors = verify_hashes(mutated_root, provenance_rows)

    assert any(
        error.startswith("tests/fixtures/decks/deck_decomp.zip:")
        for error in errors
    )


def test_verify_hashes_missing_fixture_file_names_path_in_error(
    tmp_path: Path,
) -> None:
    row = Row(
        path="missing.zip",
        source_repo="x",
        source_path="y",
        commit="z",
        sha256="0" * 64,
    )

    errors = verify_hashes(tmp_path, [row])

    assert len(errors) == 1
    assert errors[0].startswith("missing.zip: ")


def test_scan_forbidden_corrupt_zip_names_path_in_error(
    tmp_path: Path,
) -> None:
    corrupt = tmp_path / "corrupt.zip"
    corrupt.write_bytes(b"not a zip file")

    errors = scan_forbidden(tmp_path)

    assert len(errors) == 1
    assert errors[0].startswith("corrupt.zip: ")
