"""ADR-007/R32/R125/R11: the hook literal encoder and stdout neutralizer."""

from __future__ import annotations

import re
import unicodedata

ANNOTATION_CAP = 500
METADATA_VALUE_CAP = 1000

TRIGGER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"created\s+temporary\s+dir", re.I),
    re.compile(r"submitted\s+batch\s+job", re.I),
    re.compile(r"hpcmu_tool\s+", re.I),
)

PLATFORM_IDENTIFIERS: frozenset[str] = frozenset(
    {
        "CurrentExecution",
        "awsKeyId",
        "awsRegion",
        "awsSecretKey",
        "coreCount",
        "cutFile",
        "evalprospecAppVersion",
        "executablesFile",
        "inputFile",
        "jobId",
        "jobTimeoutHours",
        "maxCoresPerNode",
        "modelName",
        "modelVersion",
        "mpichPath",
        "outputFile",
        "outputsBucket",
        "parentPath",
        "path",
        "queue",
        "rankingAppVersion",
        "rootPath",
        "simulprospecAppVersion",
        "slurmPath",
        "synthesisAppSha",
        "synthesisAppVersion",
        "synthesisToolDir",
        "uploadCliVersion",
        "utilsAppSha",
        "utilsAppVersion",
        "utilsToolDir",
        "versionsBucket",
    }
)

_WORD_JOINER = chr(0x2060)
_REPLACEMENT_CHARACTER = chr(0xFFFD)

_SURROGATE_CODEPOINTS = range(0xD800, 0xE000)
_CONTROL_CODEPOINTS = (*range(0x20), 0x7F, 0x85, 0x2028, 0x2029)
_C0_EXCEPT_TAB_CODEPOINTS = tuple(cp for cp in range(0x20) if cp != 0x09)

_SURROGATE_AND_CONTROL_TO_SPACE = str.maketrans(
    {
        **{cp: _REPLACEMENT_CHARACTER for cp in _SURROGATE_CODEPOINTS},
        **{cp: " " for cp in _CONTROL_CODEPOINTS},
    }
)
_SURROGATE_TO_REPLACEMENT = str.maketrans(
    {cp: _REPLACEMENT_CHARACTER for cp in _SURROGATE_CODEPOINTS}
)
_STRIP_C0_EXCEPT_TAB = str.maketrans(
    {cp: None for cp in _C0_EXCEPT_TAB_CODEPOINTS}
)
_BRACES_TO_PARENS = str.maketrans({"{": "(", "}": ")"})

_SORTED_IDENTIFIERS: tuple[str, ...] = tuple(
    sorted(PLATFORM_IDENTIFIERS, key=len, reverse=True)
)
_IDENTIFIER_PATTERN = re.compile(
    "|".join(
        f"(?P<g{i}>{re.escape(name)})"
        for i, name in enumerate(_SORTED_IDENTIFIERS)
    ),
    re.IGNORECASE,
)
_CANONICAL_BY_GROUP: dict[str, str] = {
    f"g{i}": name for i, name in enumerate(_SORTED_IDENTIFIERS)
}


def _is_dotnet_word_char(ch: str) -> bool:
    if ord(ch) > 0xFFFF:
        return False
    category = unicodedata.category(ch)
    return (
        category.startswith("L")
        or category in ("Mn", "Nd", "Pc")
        or ch in (chr(0x200C), chr(0x200D))
    )


def _identifier_break_offsets(text: str) -> set[int]:
    """Every position just after the first character of an identifier match.

    A plain non-overlapping scan would consume a long match like `rootPath`
    and never see `path` nested inside it at a later start position, so this
    tests every position independently instead of skipping past matches.
    """
    offsets: set[int] = set()
    for pos in range(len(text)):
        if _IDENTIFIER_PATTERN.match(text, pos):
            offsets.add(pos + 1)
    return offsets


def _break_identifiers(text: str) -> str:
    offsets = _identifier_break_offsets(text)
    if not offsets:
        return text
    pieces: list[str] = []
    previous = 0
    for offset in sorted(offsets):
        pieces.append(text[previous:offset])
        pieces.append(_WORD_JOINER)
        previous = offset
    pieces.append(text[previous:])
    return "".join(pieces)


def find_platform_identifiers(text: str) -> list[str]:
    """Canonical `PLATFORM_IDENTIFIERS` names occurring as .NET whole words.

    Matching is case-insensitive; a boundary requires the neighbouring
    character (when one exists) not to be a .NET word character. Python's
    `\\b` is not used because it disagrees with .NET for characters such
    as `²`, and a character above U+FFFF is never a .NET word character
    either, since .NET sees it as a UTF-16 surrogate pair (category Cs).
    The canonical name comes from the matched group, not from `.lower()`:
    `str.lower()` and the `re.IGNORECASE` fold disagree for U+0130, U+0131
    and U+017F, which made the lookup raise.
    """
    result: list[str] = []
    seen: set[str] = set()
    for match in _IDENTIFIER_PATTERN.finditer(text):
        start, end = match.start(), match.end()
        if start > 0 and _is_dotnet_word_char(text[start - 1]):
            continue
        if end < len(text) and _is_dotnet_word_char(text[end]):
            continue
        group_name = match.lastgroup
        if group_name is None:
            continue
        canonical = _CANONICAL_BY_GROUP[group_name]
        if canonical not in seen:
            seen.add(canonical)
            result.append(canonical)
    return result


def csharp_literal(
    value: str, cap: int | None, *, break_identifiers: bool = True
) -> str:
    """Encode `value` as a DynamicExpresso-safe C# string literal.

    Order is load-bearing: truncate, flatten, defang, break identifiers,
    escape, substitute braces, wrap. Defanging runs right after flattening
    (SR-002) so a control character sitting between the words of a trigger
    phrase cannot be flattened into a space and rebuild the phrase later.
    Truncating after escaping could split a `\\"` pair and leave a dangling
    backslash that escapes the closing quote.
    """
    truncated = value if cap is None else value[:cap]
    flattened = truncated.translate(_SURROGATE_AND_CONTROL_TO_SPACE)
    defanged = defang_triggers(flattened)
    broken = _break_identifiers(defanged) if break_identifiers else defanged
    escaped = broken.replace("\\", "\\\\").replace('"', '\\"')
    substituted = escaped.translate(_BRACES_TO_PARENS)
    return f'"{substituted}"'


def defang_triggers(text: str) -> str:
    """Replace whitespace runs inside each `TRIGGER_PATTERNS` match with `_`."""
    result = text
    for pattern in TRIGGER_PATTERNS:
        result = pattern.sub(lambda m: re.sub(r"\s+", "_", m.group(0)), result)
    return result


def neutralize(line: str) -> str:
    """Make one stdout line (without its trailing newline) safe to relay.

    Strip order matters: stripping C0 controls before the `${` substitution
    means a control sitting between `$` and `{` cannot reopen a platform
    code block once removed.
    """
    desurrogated = line.translate(_SURROGATE_TO_REPLACEMENT)
    stripped = desurrogated.translate(_STRIP_C0_EXCEPT_TAB)
    substituted = stripped.replace("${", "$ {")
    return defang_triggers(substituted)
