"""ADR-007/R32/R125/R11/AM-001: the hook literal encoder and neutralizer tests."""

from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hpc_model_utils.platform.encoding import (
    PLATFORM_IDENTIFIERS,
    TRIGGER_PATTERNS,
    csharp_literal,
    defang_triggers,
    find_platform_identifiers,
    neutralize,
)

_WORD_JOINER = chr(0x2060)
_REPLACEMENT_CHARACTER = chr(0xFFFD)

# ---------------------------------------------------------------------------
# csharp_literal: curated adversarial table (AC3)
# ---------------------------------------------------------------------------

CURATED_TABLE: list[tuple[str, str]] = [
    ("\\", '"' + "\\\\" + '"'),
    ('"', '"' + '\\"' + '"'),
    ("${", '"$("'),
    ("}", '")"'),
    ("\r\n", '"  "'),
    ("$HOME", '"$HOME"'),
    ("Revisão", '"Revisão"'),
    ("a" * 499, '"' + "a" * 499 + '"'),
    ("a" * 500, '"' + "a" * 500 + '"'),
    ("a" * 501, '"' + "a" * 500 + '"'),
    (
        "a" * 499 + chr(0x2603) + "b",
        '"' + "a" * 499 + chr(0x2603) + '"',
    ),
]


@pytest.mark.parametrize(("value", "expected"), CURATED_TABLE)
def test_csharp_literal_curated_table_matches_expected(
    value: str, expected: str
) -> None:
    assert csharp_literal(value, 500) == expected


def test_csharp_literal_control_between_trigger_words_still_defangs() -> None:
    value = "Submitted" + chr(0x7F) + "batch" + chr(0x7F) + "job 4242"
    assert csharp_literal(value, 500) == '"Submitted_batch_job 4242"'


def test_csharp_literal_identifier_substring_breaks_with_word_joiner() -> None:
    expected = '"P' + _WORD_JOINER + 'ath not found"'
    assert csharp_literal("Path not found", 500) == expected


def test_csharp_literal_break_identifiers_false_leaves_value_unchanged() -> (
    None
):
    result = csharp_literal("parent_path", 500, break_identifiers=False)
    assert result == '"parent_path"'


def test_csharp_literal_lone_surrogate_maps_to_replacement_character() -> None:
    result = csharp_literal(chr(0xDCE3), 500)
    assert result == '"' + _REPLACEMENT_CHARACTER + '"'


def test_csharp_literal_nested_identifier_breaks_both_occurrences() -> None:
    expected = '"r' + _WORD_JOINER + "ootP" + _WORD_JOINER + 'athway"'
    assert csharp_literal("rootPathway", 500) == expected


def test_csharp_literal_repeated_identifier_breaks_each_occurrence() -> None:
    expected = '"P' + _WORD_JOINER + "ATHP" + _WORD_JOINER + 'ATH"'
    assert csharp_literal("PATHPATH", 500) == expected


def test_csharp_literal_dotless_i_fold_breaks_identifier() -> None:
    value = "job" + chr(0x131) + "d"
    expected = '"j' + _WORD_JOINER + "ob" + chr(0x131) + 'd"'
    assert csharp_literal(value, 500) == expected


def test_csharp_literal_dotted_capital_i_fold_breaks_identifier() -> None:
    value = "a" + chr(0x130) + "path"
    expected = '"a' + chr(0x130) + "p" + _WORD_JOINER + 'ath"'
    assert csharp_literal(value, 500) == expected


# ---------------------------------------------------------------------------
# find_platform_identifiers: .NET whole-word boundary table
# ---------------------------------------------------------------------------


def test_find_platform_identifiers_underscore_joined_word_returns_empty() -> (
    None
):
    assert find_platform_identifiers("parent_path") == []


def test_find_platform_identifiers_space_surrounded_returns_canonical_name() -> (
    None
):
    assert find_platform_identifiers("a Path b") == ["path"]


def test_find_platform_identifiers_dot_prefix_returns_canonical_name() -> None:
    assert find_platform_identifiers("x.jobid") == ["jobId"]


def test_find_platform_identifiers_superscript_two_prefix_returns_canonical_name() -> (
    None
):
    assert find_platform_identifiers(chr(0xB2) + "path") == ["path"]


def test_find_platform_identifiers_longer_word_suffix_returns_empty() -> None:
    assert find_platform_identifiers("pathway") == []


def test_find_platform_identifiers_word_joiner_split_word_returns_empty() -> (
    None
):
    assert find_platform_identifiers("p" + _WORD_JOINER + "ath") == []


def test_find_platform_identifiers_dotted_capital_i_fold_returns_canonical_name() -> (
    None
):
    value = chr(0x130) + "nputFile"
    assert find_platform_identifiers(value) == ["inputFile"]


def test_find_platform_identifiers_dotless_i_fold_returns_canonical_name() -> (
    None
):
    value = chr(0x131) + "nputfile"
    assert find_platform_identifiers(value) == ["inputFile"]


def test_find_platform_identifiers_job_dotted_capital_i_fold_returns_canonical_name() -> (
    None
):
    value = "job" + chr(0x130) + "d"
    assert find_platform_identifiers(value) == ["jobId"]


def test_find_platform_identifiers_aws_key_dotless_i_fold_returns_canonical_name() -> (
    None
):
    value = "awsKey" + chr(0x131) + "d"
    assert find_platform_identifiers(value) == ["awsKeyId"]


def test_find_platform_identifiers_long_s_fold_returns_canonical_name() -> None:
    value = "ver" + chr(0x17F) + "ionsBucket"
    assert find_platform_identifiers(value) == ["versionsBucket"]


def test_find_platform_identifiers_astral_prefix_returns_canonical_name() -> (
    None
):
    value = chr(0x1D400) + "path"
    assert find_platform_identifiers(value) == ["path"]


def test_find_platform_identifiers_astral_suffix_returns_canonical_name() -> (
    None
):
    value = "path" + chr(0x1D400)
    assert find_platform_identifiers(value) == ["path"]


def test_find_platform_identifiers_astral_prefix_in_uri_returns_canonical_name() -> (
    None
):
    value = "s3://b/" + chr(0x1D400) + "Path/x"
    assert find_platform_identifiers(value) == ["path"]


def test_find_platform_identifiers_astral_digit_between_words_returns_canonical_name() -> (
    None
):
    value = "x" + chr(0x1D7CE) + "queue"
    assert find_platform_identifiers(value) == ["queue"]


@pytest.mark.parametrize(
    "name",
    [
        "utilsAppSha",
        "synthesisAppSha",
        "utilsToolDir",
        "synthesisToolDir",
        "maxCoresPerNode",
    ],
)
def test_find_platform_identifiers_v2_parameter_name_returns_canonical_name(
    name: str,
) -> None:
    assert name in PLATFORM_IDENTIFIERS
    assert find_platform_identifiers(name.lower()) == [name]


def test_csharp_literal_cobre_parameter_name_breaks_with_word_joiner() -> None:
    expected = '"x m' + _WORD_JOINER + 'axCoresPerNode y"'
    assert csharp_literal("x maxCoresPerNode y", None) == expected


# ---------------------------------------------------------------------------
# csharp_literal: hypothesis properties (AC2, extended by AM-001)
# ---------------------------------------------------------------------------

_CONTROL_CODEPOINTS = frozenset({*range(0x20), 0x7F, 0x85, 0x2028, 0x2029})

_EDGE_FRAGMENTS = st.sampled_from(
    [
        "\\",
        '"',
        "${",
        "}",
        "{",
        "\r\n",
        chr(0x07),
        chr(0x85),
        chr(0x2028),
        chr(0x2029),
        "\t",
        chr(0xD800),
        chr(0xDFFF),
        chr(0xDCE3),
        "rootPath",
        "parentPath",
        "slurmPath",
        "mpichPath",
        "path",
        "CurrentExecution",
        "jobId",
        chr(0x130),
        chr(0x131),
        chr(0x17F),
        chr(0x212A),
        "job" + chr(0x131) + "d",
        "a" + chr(0x130) + "path",
        chr(0x212A) + "elvin",
    ]
)
_VALUE_STRATEGY = st.one_of(st.text(), _EDGE_FRAGMENTS)


def _reference_surrogates_and_flatten(s: str) -> str:
    result: list[str] = []
    for c in s:
        cp = ord(c)
        if 0xD800 <= cp <= 0xDFFF:
            result.append(_REPLACEMENT_CHARACTER)
        elif cp in _CONTROL_CODEPOINTS:
            result.append(" ")
        else:
            result.append(c)
    return "".join(result)


_REFERENCE_NAME_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(re.escape(name), re.IGNORECASE) for name in PLATFORM_IDENTIFIERS
)


def _reference_break_identifiers(s: str) -> str:
    """Independent oracle for `_break_identifiers`.

    Modelled on `re.I` fold semantics directly (one compiled pattern per
    name, scanned at every position) rather than on `str.lower()`, whose
    fold disagrees with `re.I` and can change the string's length (e.g.
    U+0130), which would misalign offsets computed against a lower-cased
    copy of `s`.
    """
    offsets: set[int] = set()
    for pattern in _REFERENCE_NAME_PATTERNS:
        for pos in range(len(s)):
            if pattern.match(s, pos):
                offsets.add(pos + 1)
    if not offsets:
        return s
    pieces: list[str] = []
    previous = 0
    for offset in sorted(offsets):
        pieces.append(s[previous:offset])
        pieces.append(_WORD_JOINER)
        previous = offset
    pieces.append(s[previous:])
    return "".join(pieces)


def _reference_substitute_braces(s: str) -> str:
    return s.replace("{", "(").replace("}", ")")


def _reference_transform(value: str, cap: int) -> str:
    truncated = value[:cap]
    flattened = _reference_surrogates_and_flatten(truncated)
    defanged = defang_triggers(flattened)
    broken = _reference_break_identifiers(defanged)
    return _reference_substitute_braces(broken)


def _reference_unescape(escaped: str) -> str:
    out: list[str] = []
    i = 0
    n = len(escaped)
    while i < n:
        c = escaped[i]
        if c == "\\" and i + 1 < n and escaped[i + 1] in ("\\", '"'):
            out.append(escaped[i + 1])
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


@given(value=_VALUE_STRATEGY, cap=st.integers(1, 600))
def test_csharp_literal_properties(value: str, cap: int) -> None:
    result = csharp_literal(value, cap)

    assert result.startswith('"')
    assert result.endswith('"')

    inner = result[1:-1]
    assert "\r" not in inner
    assert "\n" not in inner
    assert "{" not in inner
    assert "}" not in inner

    for idx, ch in enumerate(result):
        if ch != '"' or idx in (0, len(result) - 1):
            continue
        back_slashes = 0
        j = idx - 1
        while j >= 0 and result[j] == "\\":
            back_slashes += 1
            j -= 1
        assert back_slashes % 2 == 1

    unescaped = _reference_unescape(inner)
    expected = _reference_transform(value, cap)
    assert unescaped == expected


@given(value=_VALUE_STRATEGY, cap=st.integers(1, 600))
def test_csharp_literal_no_trigger_pattern_matches_output(
    value: str, cap: int
) -> None:
    result = csharp_literal(value, cap)
    for pattern in TRIGGER_PATTERNS:
        assert pattern.search(result) is None


@given(value=_VALUE_STRATEGY, cap=st.integers(1, 600))
def test_csharp_literal_break_identifiers_no_identifier_substring_survives(
    value: str, cap: int
) -> None:
    result = csharp_literal(value, cap, break_identifiers=True)
    inner_lower = result[1:-1].lower()
    for name in PLATFORM_IDENTIFIERS:
        assert name.lower() not in inner_lower


def _surrounded(
    alphabet: st.SearchStrategy[str], max_size: int = 3
) -> st.SearchStrategy[str]:
    return st.builds(
        lambda prefix, middle, suffix: prefix + middle + suffix,
        st.text(max_size=5),
        st.text(alphabet=alphabet, min_size=1, max_size=max_size),
        st.text(max_size=5),
    )


_SURROGATE_CS_CHARACTERS = st.characters(categories=["Cs"])
_SURROGATE_VALUE_STRATEGY = st.one_of(
    st.text(),
    st.text(alphabet=_SURROGATE_CS_CHARACTERS, min_size=1, max_size=5),
    _surrounded(_SURROGATE_CS_CHARACTERS),
)


@given(value=_SURROGATE_VALUE_STRATEGY, cap=st.integers(1, 600))
def test_csharp_literal_output_encodes_as_utf8_without_error(
    value: str, cap: int
) -> None:
    csharp_literal(value, cap).encode("utf-8")


@given(value=_SURROGATE_VALUE_STRATEGY)
def test_neutralize_output_encodes_as_utf8_without_error(value: str) -> None:
    neutralize(value).encode("utf-8")


# ---------------------------------------------------------------------------
# find_platform_identifiers: hypothesis properties (SR-007)
# ---------------------------------------------------------------------------

_FOLD_CHARACTERS = st.sampled_from(
    [chr(0x130), chr(0x131), chr(0x17F), chr(0x212A)]
)
_FOLD_VALUE_STRATEGY = st.one_of(
    st.text(alphabet=_FOLD_CHARACTERS, min_size=1, max_size=5),
    _surrounded(_FOLD_CHARACTERS),
)
_ASTRAL_CHARACTERS = st.characters(
    min_codepoint=0x10000, max_codepoint=0x10FFFF
)
_ASTRAL_VALUE_STRATEGY = st.one_of(
    st.text(alphabet=_ASTRAL_CHARACTERS, min_size=1, max_size=5),
    _surrounded(_ASTRAL_CHARACTERS),
)
_FIND_IDENTIFIERS_FUZZ_STRATEGY = st.one_of(
    st.text(),
    _SURROGATE_VALUE_STRATEGY,
    _FOLD_VALUE_STRATEGY,
    _ASTRAL_VALUE_STRATEGY,
)


@given(_FIND_IDENTIFIERS_FUZZ_STRATEGY)
def test_find_platform_identifiers_never_raises_for_any_text(text: str) -> None:
    find_platform_identifiers(text)


# ---------------------------------------------------------------------------
# neutralize
# ---------------------------------------------------------------------------


def test_neutralize_dollar_brace_sequence_inserts_space() -> None:
    assert neutralize("${") == "$ {"


def test_neutralize_mixed_case_multiple_spaces_trigger_defanged() -> None:
    assert neutralize("CREATED   TEMPORARY  DIR") == "CREATED_TEMPORARY_DIR"


def test_neutralize_submitted_batch_job_mixed_case_multiple_spaces_defanged() -> (
    None
):
    assert neutralize("SUBMITTED   Batch   JOB 99") == "SUBMITTED_Batch_JOB 99"


def test_neutralize_tab_whitespace_trigger_defanged() -> None:
    assert neutralize("Submitted\tbatch\tjob") == "Submitted_batch_job"


def test_neutralize_hpcmu_tool_trigger_defanged() -> None:
    assert (
        neutralize("HPCMU_TOOL hpc-model-utils /x")
        == "HPCMU_TOOL_hpc-model-utils /x"
    )


@pytest.mark.parametrize(
    "line",
    [
        "HPCMU_TOOL hpc-model-utils /x",
        "hpcmu_tool\thpc-model-utils /x",
        "Hpcmu_Tool   x",
    ],
)
def test_neutralize_hpcmu_tool_variants_leave_no_trigger_match(
    line: str,
) -> None:
    assert re.search(r"hpcmu_tool\s", neutralize(line), re.I) is None


def test_csharp_literal_hpcmu_tool_trigger_defanged() -> None:
    assert "HPCMU_TOOL_a" in csharp_literal("HPCMU_TOOL a", 500)


def test_neutralize_c0_controls_stripped() -> None:
    assert neutralize("a" + chr(0x07) + "b") == "ab"


def test_neutralize_tab_preserved() -> None:
    assert neutralize("a\tb") == "a\tb"


def test_neutralize_control_between_dollar_and_brace_does_not_reopen() -> None:
    assert neutralize("$" + chr(0x07) + "{") == "$ {"


def test_neutralize_lone_surrogate_maps_to_replacement_character() -> None:
    assert neutralize(chr(0xDCE3)) == _REPLACEMENT_CHARACTER


def test_neutralize_repeated_application_is_idempotent() -> None:
    line = "Submitted batch job 12 ${CurrentExecution.SetSuccess()}" + chr(0x07)
    once = neutralize(line)
    assert neutralize(once) == once


@given(_SURROGATE_VALUE_STRATEGY)
def test_neutralize_idempotent_property(line: str) -> None:
    once = neutralize(line)
    assert neutralize(once) == once


def test_neutralize_ac4_example_matches_expected() -> None:
    line = "Submitted batch job 12 ${CurrentExecution.SetSuccess()}" + chr(0x07)
    expected = "Submitted_batch_job 12 $ {CurrentExecution.SetSuccess()}"
    assert neutralize(line) == expected
