"""Test-only parser for `${CurrentExecution.*}` hook lines (ticket-015,
AM-001c/SR-014). At least as strict as ModelOps' own StreamReader and
CodeBlockRegex: splits lines the same way, cuts at the first `}` the same
way, and additionally rejects any method/arity/trigger/identifier that the
Reporter itself must never produce.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hpc_model_utils.platform.encoding import (
    TRIGGER_PATTERNS,
    find_platform_identifiers,
)
from hpc_model_utils.platform.modelops import HookMethod

_HOOK_LINE_PATTERN = re.compile(r"\$\{CurrentExecution\.(\w+)\((.*)\)\}")
_LINE_SPLIT_PATTERN = re.compile(r"\r\n|\r|\n")

_EXPECTED_ARG_COUNT: dict[HookMethod, int] = {
    HookMethod.SET_SUCCESS: 0,
    HookMethod.SET_MODEL_ERROR: 0,
    HookMethod.SET_DATA_ERROR: 0,
    HookMethod.SET_RUNTIME_ERROR: 0,
    HookMethod.SET_ANNOTATION: 1,
    HookMethod.SET_EXECUTION_ARTIFACTS_PATH: 1,
    HookMethod.SET_METADATA: 2,
}


@dataclass(frozen=True)
class Hook:
    method: str
    args: tuple[str, ...]


def _parse_literals(raw: str) -> tuple[str, ...]:
    if raw == "":
        return ()
    tokens: list[str] = []
    i = 0
    length = len(raw)
    while i < length:
        if raw[i] != '"':
            raise AssertionError(
                f"expected a literal to start with '\"': {raw!r}"
            )
        i += 1
        chars: list[str] = []
        closed = False
        while i < length:
            ch = raw[i]
            if ch == "\\":
                if i + 1 >= length or raw[i + 1] not in ("\\", '"'):
                    raise AssertionError(f"invalid escape in literal: {raw!r}")
                chars.append(raw[i + 1])
                i += 2
                continue
            if ch == '"':
                i += 1
                closed = True
                break
            chars.append(ch)
            i += 1
        if not closed:
            raise AssertionError(f"unterminated literal: {raw!r}")
        tokens.append("".join(chars))
        if i == length:
            break
        if raw[i : i + 2] != ", ":
            raise AssertionError(f"expected ', ' between literals: {raw!r}")
        i += 2
    return tuple(tokens)


def parse_hooks(stdout: str) -> list[Hook]:
    hooks: list[Hook] = []
    for line in _LINE_SPLIT_PATTERN.split(stdout):
        if "${" not in line:
            continue
        # ModelOps' CodeBlockRegex cuts the code block at the first '}',
        # so any content after it (or a missing '}' altogether) is a
        # mismatch between what we captured and what the platform saw.
        if line.find("}") != len(line) - 1:
            raise AssertionError(
                f"the first '}}' must be the line's last character: {line!r}"
            )
        match = _HOOK_LINE_PATTERN.fullmatch(line)
        if match is None:
            raise AssertionError(f"malformed hook line: {line!r}")
        method_name, raw_args = match.group(1), match.group(2)
        try:
            method = HookMethod(method_name)
        except ValueError as exc:
            raise AssertionError(
                f"unknown hook method: {method_name!r}"
            ) from exc
        args = _parse_literals(raw_args)
        expected_count = _EXPECTED_ARG_COUNT[method]
        if len(args) != expected_count:
            raise AssertionError(
                f"{method} expects {expected_count} argument(s), got "
                f"{len(args)}: {line!r}"
            )
        for pattern in TRIGGER_PATTERNS:
            if pattern.search(line):
                raise AssertionError(
                    f"trigger pattern match on hook line: {line!r}"
                )
        for arg in args:
            if find_platform_identifiers(arg):
                raise AssertionError(
                    f"platform identifier in hook argument: {arg!r}"
                )
        hooks.append(Hook(method=method_name, args=args))
    return hooks
