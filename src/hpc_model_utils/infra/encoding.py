"""infra.encoding: binary-safe Python encoding classifier and sanitizer.

ADR-018 replaces v1's `file -i`, then `dos2unix -o`, then
`iconv -f <charset> -t UTF-8` command pipeline with pure Python, so that
no deck member name is interpolated into a command line (ADR-028) and
no binary member -- such as the IEEE-754 cut-file coefficients -- is
misclassified as text and corrupted.

`BINARY_BYTES` is libmagic's text-character table minus BEL (0x07) and BS
(0x08): treating them as binary is the safer direction, since a misread
binary member would otherwise be Latin-1-converted and corrupted, while a
misread text member is only left unconverted.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from enum import StrEnum
from pathlib import Path


class TextEncoding(StrEnum):
    UTF8 = "utf-8"
    LATIN1 = "iso-8859-1"
    BINARY = "binary"


BINARY_BYTES = (
    frozenset(range(0x00, 0x09))
    | frozenset(range(0x0E, 0x1B))
    | frozenset(range(0x1C, 0x20))
)

_BINARY_SEARCH = re.compile(rb"[\x00-\x08\x0e-\x1a\x1c-\x1f]").search


def classify(data: bytes) -> TextEncoding:
    if _BINARY_SEARCH(data) is not None:
        return TextEncoding.BINARY
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return TextEncoding.LATIN1
    return TextEncoding.UTF8


def _to_utf8(data: bytes) -> bytes:
    return data.decode("latin-1").replace("\r\n", "\n").encode("utf-8")


def sanitize_file(path: Path) -> TextEncoding:
    data = path.read_bytes()
    encoding = classify(data)
    if encoding is not TextEncoding.LATIN1:
        return encoding
    fd, tmp_name = tempfile.mkstemp(dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            tmp_file.write(_to_utf8(data))
        shutil.copymode(path, tmp_path)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return encoding
