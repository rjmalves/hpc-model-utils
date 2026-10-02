from __future__ import annotations

import os
import stat
import struct
import time
import zipfile
from hashlib import sha256
from pathlib import Path

import pytest

from hpc_model_utils.infra.encoding import (
    BINARY_BYTES,
    TextEncoding,
    classify,
    sanitize_file,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DECK = REPO_ROOT / "tests" / "fixtures" / "decks" / "deck_newave.zip"

_FIXTURE_BINARY_MEMBERS = frozenset(
    {"format.tmp", "hidr.dat", "mensag.tmp", "postos.dat", "vazoes.dat"}
)
_FIXTURE_LATIN1_MEMBERS = frozenset(
    {"agrint.dat", "restricao-eletrica.csv", "dsvagua.dat"}
)


def test_binary_bytes_matches_adr018_table() -> None:
    assert BINARY_BYTES == (
        frozenset(range(0x00, 0x09))
        | frozenset(range(0x0E, 0x1B))
        | frozenset(range(0x1C, 0x20))
    )


def test_classify_empty_bytes_returns_utf8() -> None:
    assert classify(b"") is TextEncoding.UTF8


def test_classify_bel_byte_returns_binary() -> None:
    assert classify(b"NOME\x07X\n") is TextEncoding.BINARY


def test_classify_bs_byte_returns_binary() -> None:
    assert classify(b"NOME\x08X\n") is TextEncoding.BINARY


def test_classify_six_text_control_bytes_returns_utf8() -> None:
    assert classify(b"A\tB\x0bC\x0cD\x1b[0m\r\n") is TextEncoding.UTF8


def test_classify_ascii_returns_utf8() -> None:
    assert classify(b"plain ascii text\n") is TextEncoding.UTF8


def test_classify_fixture_deck_matches_hardcoded_table(
    tmp_path: Path,
) -> None:
    with zipfile.ZipFile(FIXTURE_DECK) as zf:
        zf.extractall(tmp_path)
        names = zf.namelist()

    for name in names:
        data = (tmp_path / name).read_bytes()
        result = classify(data)
        if name in _FIXTURE_BINARY_MEMBERS:
            assert result is TextEncoding.BINARY, name
        elif name in _FIXTURE_LATIN1_MEMBERS:
            assert result is TextEncoding.LATIN1, name
        else:
            assert result is TextEncoding.UTF8, name


def test_classify_50mb_zero_free_latin1_buffer_under_budget() -> None:
    data = bytes([0x41, 0xE9]) * 25_000_000

    start = time.perf_counter()
    result = classify(data)
    elapsed = time.perf_counter() - start

    assert result is TextEncoding.LATIN1
    assert elapsed < 2.0


def test_classify_50mb_valid_utf8_buffer_under_budget() -> None:
    data = b"A" * 50_000_000

    start = time.perf_counter()
    result = classify(data)
    elapsed = time.perf_counter() - start

    assert result is TextEncoding.UTF8
    assert elapsed < 2.0


def test_sanitize_file_latin1_bytes_converts_to_utf8_lf(
    tmp_path: Path,
) -> None:
    path = tmp_path / "agrint.dat"
    path.write_bytes(b"NOME ACENTUA\xc7\xc3O\r\nX\r\n")

    result = sanitize_file(path)

    assert result is TextEncoding.LATIN1
    assert path.read_bytes() == "NOME ACENTUA\xc7\xc3O\nX\n".encode("utf-8")


def test_sanitize_file_binary_doubles_sha256_unchanged(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cortes-012.dat"
    payload = struct.pack("<4d", 1.5, -2.25, 3e-9, 42.0)
    path.write_bytes(payload)
    before = sha256(path.read_bytes()).hexdigest()

    result = sanitize_file(path)

    assert result is TextEncoding.BINARY
    assert sha256(path.read_bytes()).hexdigest() == before


def test_sanitize_file_utf8_crlf_untouched(tmp_path: Path) -> None:
    path = tmp_path / "utf8_crlf.dat"
    original = b"plain ascii\r\nsecond line\r\n"
    path.write_bytes(original)

    result = sanitize_file(path)

    assert result is TextEncoding.UTF8
    assert path.read_bytes() == original


def test_sanitize_file_empty_file_returns_utf8_untouched(
    tmp_path: Path,
) -> None:
    path = tmp_path / "empty.dat"
    path.write_bytes(b"")

    result = sanitize_file(path)

    assert result is TextEncoding.UTF8
    assert path.read_bytes() == b""


def test_sanitize_file_preserves_mode_bits(tmp_path: Path) -> None:
    path = tmp_path / "agrint.dat"
    path.write_bytes(b"NOME ACENTUA\xc7\xc3O\r\nX\r\n")
    os.chmod(path, 0o640)

    sanitize_file(path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_sanitize_file_replace_failure_leaves_original_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "agrint.dat"
    original = b"NOME ACENTUA\xc7\xc3O\r\nX\r\n"
    path.write_bytes(original)

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", _raise)

    with pytest.raises(OSError, match="replace failed"):
        sanitize_file(path)

    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
