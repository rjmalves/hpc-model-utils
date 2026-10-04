"""ticket-053/ticket-055c: the `label: <command line>` fixture format
shared by the golden-parse contract test and the drop-in replay
suite.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping
from pathlib import Path

_PLACEHOLDER_PATTERN = re.compile(r"\{(\w+)\}")


def load_command_lines(path: Path) -> dict[str, str]:
    lines: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        label, sep, command = stripped.partition(": ")
        if not sep:
            raise ValueError(f"malformed command-line fixture row: {raw!r}")
        if label in lines:
            raise ValueError(f"duplicate label: {label!r}")
        lines[label] = command
    return lines


def render(line: str, values: Mapping[str, str]) -> list[str]:
    def _substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise KeyError(name)
        return shlex.quote(values[name])

    return shlex.split(_PLACEHOLDER_PATTERN.sub(_substitute, line))
