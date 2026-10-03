"""R16: the ``indices.csv`` library reader shared by NEWAVE (ticket-046)
and DECOMP (ticket-050).

v1 read this file with pandas (``comment="&"``); this stdlib-only
reimplementation drops everything from the first ``&`` on each line
(a transitive-dependency-free equivalent of that comment convention)
rather than skipping only lines that start with it.
"""

from __future__ import annotations

from pathlib import Path

_MIN_FIELDS = 3
_VALUE_INDEX = 2


def read_index_libraries(path: Path) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("&", 1)[0].strip()
        if not line:
            continue
        fields = line.split(";")
        if len(fields) < _MIN_FIELDS:
            continue
        value = fields[_VALUE_INDEX].strip()
        if value:
            seen.setdefault(value, None)
    return tuple(seen)
