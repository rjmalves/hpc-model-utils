"""ADR-006/ADR-007/ADR-008/R30/R31/R33/R34/R35/R11/R92/AM-001/AM-001b tests
for the ModelOps reporter (ticket-015).
"""

from __future__ import annotations

from typing import Protocol

import pytest

from hpc_model_utils.core.diagnosis import RunStatus
from hpc_model_utils.platform.encoding import (
    TRIGGER_PATTERNS,
    find_platform_identifiers,
)
from hpc_model_utils.platform.modelops import STATUS_HOOKS, Reporter
from tests.support.hooks import Hook, parse_hooks

_WORD_JOINER = chr(0x2060)

_EXPECTED_STATUS_HOOKS: dict[RunStatus, str] = {
    RunStatus.SUCCESS: "SetSuccess",
    RunStatus.INFEASIBLE: "SetModelError",
    RunStatus.DATA_ERROR: "SetDataError",
    RunStatus.RUNTIME_ERROR: "SetRuntimeError",
    RunStatus.TIMEOUT: "SetRuntimeError",
    RunStatus.INFRA_ERROR: "SetRuntimeError",
    RunStatus.LICENSE_ERROR: "SetRuntimeError",
    RunStatus.CANCELLED: "SetRuntimeError",
    RunStatus.UNKNOWN: "SetRuntimeError",
}


class _ListChannel:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write_line(self, line: str) -> None:
        self.lines.append(line)


class _FlakyChannel:
    def __init__(self, fail_on: set[int]) -> None:
        self.lines: list[str] = []
        self._fail_on = fail_on
        self._calls = 0

    def write_line(self, line: str) -> None:
        self._calls += 1
        if self._calls in self._fail_on:
            raise OSError("boom")
        self.lines.append(line)


class _HasLines(Protocol):
    lines: list[str]


def _hooks_of(channel: _HasLines) -> list[Hook]:
    return parse_hooks("\n".join(channel.lines))


def test_terminal_cancelled_status_emits_runtime_error_and_annotation_once() -> (
    None
):
    annotation = "CANCELLED: job 5 cancelled by 0 [slurm.cancelled]"
    assert find_platform_identifiers(annotation) == []
    channel = _ListChannel()
    reporter = Reporter(channel, enabled=True)

    first = reporter.terminal(RunStatus.CANCELLED, annotation)

    assert first is True
    assert _hooks_of(channel) == [
        Hook("SetRuntimeError", ()),
        Hook("SetAnnotation", (annotation,)),
    ]

    lines_before = list(channel.lines)
    second = reporter.terminal(RunStatus.CANCELLED, "ignored")

    assert second is False
    assert channel.lines == lines_before
    assert reporter.terminal_emitted is True


def test_status_hooks_table_matches_d7_mapping() -> None:
    assert {
        status: method.value for status, method in STATUS_HOOKS.items()
    } == _EXPECTED_STATUS_HOOKS


@pytest.mark.parametrize("status", list(RunStatus))
def test_terminal_first_hook_method_matches_status_hooks_table(
    status: RunStatus,
) -> None:
    channel = _ListChannel()
    reporter = Reporter(channel, enabled=True)

    reporter.terminal(status, "annotation text")

    first_method = _hooks_of(channel)[0].method
    assert first_method == _EXPECTED_STATUS_HOOKS[status]
    assert (status is RunStatus.INFEASIBLE) == (first_method == "SetModelError")


def test_metadata_repeated_key_dropped_and_duration_seconds_two_decimals() -> (
    None
):
    channel = _ListChannel()
    reporter = Reporter(channel, enabled=True)

    reporter.metadata("status", "A")
    reporter.metadata("status", "B")
    reporter.duration("run", 1.234)

    hooks = _hooks_of(channel)
    assert hooks.count(Hook("SetMetadata", ("status", "A"))) == 1
    assert Hook("SetMetadata", ("status", "B")) not in hooks
    assert Hook("SetMetadata", ("duration_seconds.run", "1.23")) in hooks


def test_reporter_from_env_platform_off_only_writes_announce_job_line() -> None:
    channel = _ListChannel()
    reporter = Reporter.from_env(channel, {"HPCMU_PLATFORM": "off"})

    reporter.terminal(RunStatus.SUCCESS, "ok")
    reporter.metadata("k", "v")
    reporter.artifacts_path(f"s3://bucket/artifacts/{'a' * 64}")
    reporter.announce_job("42")

    assert channel.lines == ["Submitted batch job 42"]


def test_metadata_key_uppercase_raises_value_error() -> None:
    reporter = Reporter(_ListChannel(), enabled=True)
    with pytest.raises(ValueError, match="invalid metadata key"):
        reporter.metadata("Status", "x")


def test_metadata_key_trailing_newline_raises_value_error() -> None:
    reporter = Reporter(_ListChannel(), enabled=True)
    with pytest.raises(ValueError, match="invalid metadata key"):
        reporter.metadata("status\n", "x")


def test_metadata_key_parent_path_accepted() -> None:
    channel = _ListChannel()
    reporter = Reporter(channel, enabled=True)

    reporter.metadata("parent_path", "v")

    assert _hooks_of(channel) == [Hook("SetMetadata", ("parent_path", "v"))]


def test_metadata_key_duration_seconds_path_raises_value_error() -> None:
    reporter = Reporter(_ListChannel(), enabled=True)
    with pytest.raises(ValueError, match="platform identifier"):
        reporter.metadata("duration_seconds.path", "v")


def test_announce_job_valid_digits_writes_plain_line() -> None:
    channel = _ListChannel()
    reporter = Reporter(channel, enabled=True)

    reporter.announce_job("42")

    assert channel.lines == ["Submitted batch job 42"]


def test_announce_job_trailing_newline_raises_value_error() -> None:
    reporter = Reporter(_ListChannel(), enabled=True)
    with pytest.raises(ValueError, match="invalid job id"):
        reporter.announce_job("12\n")


def test_announce_job_empty_string_raises_value_error() -> None:
    reporter = Reporter(_ListChannel(), enabled=True)
    with pytest.raises(ValueError, match="invalid job id"):
        reporter.announce_job("")


def test_announce_job_fullwidth_digits_raises_value_error() -> None:
    reporter = Reporter(_ListChannel(), enabled=True)
    with pytest.raises(ValueError, match="invalid job id"):
        reporter.announce_job("\uff11\uff12")


def test_artifacts_path_valid_uri_emits_once_and_repeat_is_dropped() -> None:
    uri = f"s3://my-bucket/artifacts/{'a' * 64}"
    channel = _ListChannel()
    reporter = Reporter(channel, enabled=True)

    reporter.artifacts_path(uri)
    reporter.artifacts_path(uri)

    assert _hooks_of(channel) == [Hook("SetExecutionArtifactsPath", (uri,))]


def test_artifacts_path_invalid_uri_raises_value_error() -> None:
    reporter = Reporter(_ListChannel(), enabled=True)
    with pytest.raises(ValueError, match="invalid artifacts uri"):
        reporter.artifacts_path("s3://bad")


def test_artifacts_path_identifier_collision_raises_value_error() -> None:
    uri = f"s3://my-queue-bucket/artifacts/{'0' * 64}"
    reporter = Reporter(_ListChannel(), enabled=True)
    with pytest.raises(ValueError, match="platform identifier"):
        reporter.artifacts_path(uri)


def test_check_artifacts_path_valid_uri_returns_none_without_side_effects() -> (
    None
):
    uri = f"s3://my-bucket/artifacts/{'a' * 64}"
    channel = _ListChannel()
    reporter = Reporter(channel, enabled=True)

    reporter.check_artifacts_path(uri)

    assert channel.lines == []
    assert reporter._artifacts_emitted is False


def test_check_artifacts_path_invalid_uri_raises_value_error() -> None:
    reporter = Reporter(_ListChannel(), enabled=True)
    with pytest.raises(ValueError, match="invalid artifacts uri"):
        reporter.check_artifacts_path("s3://bad")


def test_check_artifacts_path_identifier_collision_raises_value_error() -> None:
    uri = f"s3://my-queue-bucket/artifacts/{'0' * 64}"
    reporter = Reporter(_ListChannel(), enabled=True)
    with pytest.raises(ValueError, match="platform identifier"):
        reporter.check_artifacts_path(uri)


def test_check_artifacts_path_then_artifacts_path_emits_exactly_once() -> None:
    uri = f"s3://my-bucket/artifacts/{'a' * 64}"
    channel = _ListChannel()
    reporter = Reporter(channel, enabled=True)

    reporter.check_artifacts_path(uri)
    reporter.artifacts_path(uri)

    assert _hooks_of(channel) == [Hook("SetExecutionArtifactsPath", (uri,))]


def test_terminal_annotation_path_not_found_breaks_identifier_with_word_joiner() -> (
    None
):
    channel = _ListChannel()
    reporter = Reporter(channel, enabled=True)

    reporter.terminal(RunStatus.DATA_ERROR, "Path not found")

    expected = "P" + _WORD_JOINER + "ath not found"
    assert _hooks_of(channel)[1] == Hook("SetAnnotation", (expected,))


def test_terminal_annotation_control_between_trigger_words_has_no_trigger_match() -> (
    None
):
    annotation = "Submitted" + chr(0x7F) + "batch" + chr(0x7F) + "job 9"
    channel = _ListChannel()
    reporter = Reporter(channel, enabled=True)

    reporter.terminal(RunStatus.SUCCESS, annotation)

    emitted_line = channel.lines[-1]
    assert not any(pattern.search(emitted_line) for pattern in TRIGGER_PATTERNS)


def test_terminal_hostile_annotation_round_trips_through_parse_hooks() -> None:
    hostile = 'Say "hi", {ok} ${x}\nend'
    channel = _ListChannel()
    reporter = Reporter(channel, enabled=True)

    reporter.terminal(RunStatus.SUCCESS, hostile)

    annotation_hook = _hooks_of(channel)[1]
    assert annotation_hook == Hook(
        "SetAnnotation", ('Say "hi", (ok) $(x) end',)
    )


def test_parse_hooks_malformed_dollar_brace_line_raises_assertion_error() -> (
    None
):
    with pytest.raises(AssertionError, match="malformed hook line"):
        parse_hooks("${NotCurrentExecution.Foo()}")


def test_parse_hooks_unterminated_literal_raises_assertion_error() -> None:
    with pytest.raises(AssertionError, match="unterminated literal"):
        parse_hooks('${CurrentExecution.SetAnnotation("abc)}')


def test_reporter_disabled_bookkeeping_tracks_keys_and_terminal_without_writing() -> (
    None
):
    channel = _ListChannel()
    reporter = Reporter(channel, enabled=False)

    reporter.metadata("status", "A")
    reporter.metadata("status", "B")
    reporter.artifacts_path(f"s3://my-bucket/artifacts/{'a' * 64}")
    reporter.terminal(RunStatus.SUCCESS, "ok")

    assert channel.lines == []
    assert reporter._emitted_keys == {"status"}
    assert reporter._artifacts_emitted is True
    assert reporter.terminal_emitted is True


# ---------------------------------------------------------------------------
# AM-001c/SR-013: fallible work must precede the once-only state change.
# ---------------------------------------------------------------------------


def test_terminal_unknown_status_raises_value_error_without_state_change() -> (
    None
):
    channel = _ListChannel()
    reporter = Reporter(channel, enabled=True)

    with pytest.raises(ValueError):
        # Deliberately passes an invalid status token at runtime to
        # exercise SR-013's RunStatus(status) validation.
        reporter.terminal("FAILED", "x")  # type: ignore[arg-type]

    assert reporter.terminal_emitted is False
    assert channel.lines == []

    second = reporter.terminal(RunStatus.SUCCESS, "ok")

    assert second is True
    assert _hooks_of(channel) == [
        Hook("SetSuccess", ()),
        Hook("SetAnnotation", ("ok",)),
    ]


def test_terminal_non_str_annotation_raises_type_error_and_retry_succeeds() -> (
    None
):
    channel = _ListChannel()
    reporter = Reporter(channel, enabled=True)

    with pytest.raises(TypeError):
        # Deliberately passes a non-str annotation at runtime to exercise
        # SR-013's isinstance(annotation, str) validation.
        reporter.terminal(RunStatus.DATA_ERROR, None)  # type: ignore[arg-type]

    assert reporter.terminal_emitted is False
    assert channel.lines == []

    second = reporter.terminal(RunStatus.DATA_ERROR, "ok")

    assert second is True
    assert _hooks_of(channel) == [
        Hook("SetDataError", ()),
        Hook("SetAnnotation", ("ok",)),
    ]


def test_metadata_non_str_value_raises_type_error_and_retry_succeeds() -> None:
    channel = _ListChannel()
    reporter = Reporter(channel, enabled=True)

    with pytest.raises(TypeError):
        # Deliberately passes a non-str value at runtime to exercise
        # SR-013's isinstance(value, str) validation.
        reporter.metadata("model_version", 3)  # type: ignore[arg-type]

    reporter.metadata("model_version", "3")

    assert _hooks_of(channel) == [Hook("SetMetadata", ("model_version", "3"))]


def test_terminal_channel_fails_on_first_write_then_retry_emits() -> None:
    channel = _FlakyChannel(fail_on={1})
    reporter = Reporter(channel, enabled=True)

    with pytest.raises(OSError):
        reporter.terminal(RunStatus.SUCCESS, "x")

    assert reporter.terminal_emitted is False
    assert channel.lines == []

    second = reporter.terminal(RunStatus.SUCCESS, "x")

    assert second is True
    assert _hooks_of(channel) == [
        Hook("SetSuccess", ()),
        Hook("SetAnnotation", ("x",)),
    ]


def test_terminal_channel_fails_on_second_write_keeps_flag_set() -> None:
    channel = _FlakyChannel(fail_on={2})
    reporter = Reporter(channel, enabled=True)

    with pytest.raises(OSError):
        reporter.terminal(RunStatus.SUCCESS, "x")

    assert reporter.terminal_emitted is True
    assert _hooks_of(channel) == [Hook("SetSuccess", ())]

    second = reporter.terminal(RunStatus.SUCCESS, "y")

    assert second is False
    assert _hooks_of(channel) == [Hook("SetSuccess", ())]


def test_metadata_write_failure_rolls_back_dedup_and_retry_succeeds() -> None:
    channel = _FlakyChannel(fail_on={1})
    reporter = Reporter(channel, enabled=True)

    with pytest.raises(OSError):
        reporter.metadata("status", "A")

    reporter.metadata("status", "A")

    assert _hooks_of(channel) == [Hook("SetMetadata", ("status", "A"))]


def test_artifacts_path_write_failure_rolls_back_and_retry_succeeds() -> None:
    uri = f"s3://my-bucket/artifacts/{'a' * 64}"
    channel = _FlakyChannel(fail_on={1})
    reporter = Reporter(channel, enabled=True)

    with pytest.raises(OSError):
        reporter.artifacts_path(uri)

    reporter.artifacts_path(uri)

    assert _hooks_of(channel) == [Hook("SetExecutionArtifactsPath", (uri,))]


# ---------------------------------------------------------------------------
# AM-001c/SR-014: parse_hooks must be at least as strict as ModelOps.
# ---------------------------------------------------------------------------


def test_find_platform_identifiers_word_joiner_broken_path_returns_empty() -> (
    None
):
    assert find_platform_identifiers("P" + _WORD_JOINER + "ath not found") == []


def test_parse_hooks_brace_before_line_end_raises_assertion_error() -> None:
    with pytest.raises(AssertionError, match="last character"):
        parse_hooks('${CurrentExecution.SetAnnotation("a}b")}')


def test_parse_hooks_unknown_method_raises_assertion_error() -> None:
    with pytest.raises(AssertionError, match="unknown hook method"):
        parse_hooks("${CurrentExecution.Bogus()}")


def test_parse_hooks_wrong_argument_count_raises_assertion_error() -> None:
    with pytest.raises(AssertionError, match="expects 0 argument"):
        parse_hooks('${CurrentExecution.SetSuccess("x")}')


def test_parse_hooks_trigger_inside_literal_raises_assertion_error() -> None:
    with pytest.raises(AssertionError, match="trigger pattern"):
        parse_hooks(
            '${CurrentExecution.SetAnnotation("submitted batch job 1")}'
        )


def test_parse_hooks_identifier_inside_literal_raises_assertion_error() -> None:
    with pytest.raises(AssertionError, match="platform identifier"):
        parse_hooks('${CurrentExecution.SetAnnotation("path not broken")}')


def test_parse_hooks_u2028_joined_hooks_raises_assertion_error() -> None:
    merged = (
        "${CurrentExecution.SetSuccess()}"
        + chr(0x2028)
        + "${CurrentExecution.SetDataError()}"
    )
    with pytest.raises(AssertionError, match="last character"):
        parse_hooks(merged)
