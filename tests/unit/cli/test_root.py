"""ADR-004/ADR-005/ADR-006/R36/R37/R33/R69/R91/R122/AM-001c tests for the
v2 CLI root group and fatal-path chokepoint (ticket-017).
"""

from __future__ import annotations

import io
import logging
import subprocess
import sys
from collections.abc import Iterator

import click
import pytest
from click.testing import CliRunner

from hpc_model_utils.cli.root import (
    AppContext,
    HpcmuCommand,
    HpcmuGroup,
    _emits_terminal_status,
    _fatal_path,
    _map_failure,
    _PipeAwareStreamHandler,
    _scrub_url_query,
    _warn_if_platform_disabled,
    cli,
    configure_logging,
)
from hpc_model_utils.core.diagnosis import RunStatus
from hpc_model_utils.core.errors import DataError, ExitCode, SchedulerError
from hpc_model_utils.platform.modelops import Reporter
from tests.support.hooks import Hook, parse_hooks


class _ListChannel:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write_line(self, line: str) -> None:
        self.lines.append(line)


class _RaisingChannel:
    def write_line(self, line: str) -> None:
        raise OSError("boom")


def _make_app_context() -> tuple[AppContext, _ListChannel]:
    channel = _ListChannel()
    reporter = Reporter(channel, enabled=True)
    return AppContext(reporter=reporter, channels=None), channel


def _hooks_of(channel: _ListChannel) -> list[Hook]:
    return parse_hooks("\n".join(channel.lines))


# ---------------------------------------------------------------------------
# HpcmuGroup.invoke: name resolution and duration (a fresh HpcmuGroup, per
# the ticket's Out of Scope note - no concrete command is in cli itself).
# ---------------------------------------------------------------------------


def test_invoke_kebab_case_command_resolves_snake_case_duration_key() -> None:
    group = HpcmuGroup(name="test")
    app_ctx, channel = _make_app_context()

    @group.command(name="check_and_fetch_inputs")
    def _cmd() -> None:
        pass

    result = CliRunner().invoke(group, ["check-and-fetch-inputs"], obj=app_ctx)

    assert result.exit_code == 0
    assert app_ctx.command_name == "check_and_fetch_inputs"
    keys = [h.args[0] for h in _hooks_of(channel) if h.method == "SetMetadata"]
    assert keys == ["duration_seconds.check_and_fetch_inputs"]


def test_invoke_unknown_subcommand_emits_no_duration_hook() -> None:
    group = HpcmuGroup(name="test")
    app_ctx, channel = _make_app_context()

    result = CliRunner().invoke(group, ["no-such-command"], obj=app_ctx)

    assert result.exit_code == 2
    assert app_ctx.command_name == ""
    assert channel.lines == []


def test_invoke_snake_case_command_direct_match_resolves_same_name() -> None:
    group = HpcmuGroup(name="test")
    app_ctx, channel = _make_app_context()

    @group.command(name="run_ok")
    def _cmd() -> None:
        pass

    result = CliRunner().invoke(group, ["run_ok"], obj=app_ctx)

    assert result.exit_code == 0
    assert app_ctx.command_name == "run_ok"
    keys = [h.args[0] for h in _hooks_of(channel) if h.method == "SetMetadata"]
    assert keys == ["duration_seconds.run_ok"]


def test_help_lists_snake_case_command_name_not_kebab() -> None:
    group = HpcmuGroup(name="test")

    @group.command(name="check_and_fetch_inputs")
    def _cmd() -> None:
        pass

    result = CliRunner().invoke(group, ["--help"])

    assert "check_and_fetch_inputs" in result.output
    assert "check-and-fetch-inputs" not in result.output


def test_invoke_duration_emitted_on_success() -> None:
    group = HpcmuGroup(name="test")
    app_ctx, channel = _make_app_context()

    @group.command(name="run_ok")
    def _cmd() -> None:
        click.echo("done")

    result = CliRunner().invoke(group, ["run-ok"], obj=app_ctx)

    assert result.exit_code == 0
    hooks = _hooks_of(channel)
    assert any(
        h.method == "SetMetadata" and h.args[0] == "duration_seconds.run_ok"
        for h in hooks
    )


def test_invoke_duration_emitted_on_failure() -> None:
    group = HpcmuGroup(name="test")
    app_ctx, channel = _make_app_context()

    @group.command(name="fetch_things")
    def _cmd() -> None:
        raise DataError("missing input")

    result = CliRunner().invoke(group, ["fetch-things"], obj=app_ctx)

    assert isinstance(result.exception, DataError)
    hooks = _hooks_of(channel)
    assert any(
        h.method == "SetMetadata"
        and h.args[0] == "duration_seconds.fetch_things"
        for h in hooks
    )


def test_invoke_duration_failure_does_not_mask_command_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    group = HpcmuGroup(name="test")
    reporter = Reporter(_RaisingChannel(), enabled=True)
    app_ctx = AppContext(reporter=reporter, channels=None)

    @group.command(name="boom")
    def _cmd() -> None:
        raise DataError("missing input")

    with caplog.at_level(logging.ERROR, logger="hpc_model_utils.cli.root"):
        result = CliRunner().invoke(group, ["boom"], obj=app_ctx)

    assert isinstance(result.exception, DataError)
    assert "reporter.duration() failed" in caplog.text


def test_cli_version_option_exits_zero_with_no_stderr() -> None:
    result = CliRunner().invoke(cli, ["--version"], prog_name="hpc-model-utils")

    assert result.exit_code == 0
    assert result.stderr == ""
    assert "hpc-model-utils" in result.output


# ---------------------------------------------------------------------------
# configure_logging: stdout handler on "hpc_model_utils" + a real handler
# on "py.warnings" (refinement input from ticket-016).
# ---------------------------------------------------------------------------


@pytest.fixture
def _restore_root_loggers() -> Iterator[None]:
    app_logger = logging.getLogger("hpc_model_utils")
    warn_logger = logging.getLogger("py.warnings")
    app_handlers = list(app_logger.handlers)
    warn_handlers = list(warn_logger.handlers)
    app_level = app_logger.level
    yield
    app_logger.handlers = app_handlers
    warn_logger.handlers = warn_handlers
    app_logger.level = app_level


def test_configure_logging_attaches_same_handler_to_app_and_warnings_logger(
    _restore_root_loggers: None,
) -> None:
    configure_logging()

    app_logger = logging.getLogger("hpc_model_utils")
    warn_logger = logging.getLogger("py.warnings")
    assert len(app_logger.handlers) >= 1
    assert app_logger.handlers[-1] in warn_logger.handlers
    assert app_logger.level == logging.INFO


def test_configure_logging_uses_loglevel_env_override(
    _restore_root_loggers: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOGLEVEL", "DEBUG")

    configure_logging()

    assert logging.getLogger("hpc_model_utils").level == logging.DEBUG


# ---------------------------------------------------------------------------
# _PipeAwareStreamHandler: R45 amendment -- a BrokenPipeError propagates so
# D2 can see it; every other handleError failure keeps the default.
# ---------------------------------------------------------------------------


class _RaisingStream(io.StringIO):
    def __init__(self, exc: BaseException) -> None:
        super().__init__()
        self._exc = exc

    def write(self, s: str) -> int:
        raise self._exc


@pytest.fixture
def _pipe_aware_logger() -> Iterator[logging.Logger]:
    logger = logging.getLogger("hpc_model_utils_test.pipe_aware")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    yield logger
    logger.handlers = []


def test_pipe_aware_stream_handler_broken_pipe_write_propagates_empty_stderr(
    _pipe_aware_logger: logging.Logger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    handler = _PipeAwareStreamHandler(_RaisingStream(BrokenPipeError("boom")))
    _pipe_aware_logger.addHandler(handler)

    with pytest.raises(BrokenPipeError, match="boom"):
        _pipe_aware_logger.info("hello")

    assert capsys.readouterr().err == ""


def test_pipe_aware_stream_handler_other_error_uses_default_handle_error(
    _pipe_aware_logger: logging.Logger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    handler = _PipeAwareStreamHandler(_RaisingStream(ValueError("bad stream")))
    _pipe_aware_logger.addHandler(handler)

    _pipe_aware_logger.info("hello")  # must not raise

    assert "--- Logging error ---" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# AM-001c item 4: the operator-decided warning.
# ---------------------------------------------------------------------------


def test_warn_if_platform_disabled_warns_when_slurm_job_id_unset(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="hpc_model_utils.cli.root"):
        _warn_if_platform_disabled({"HPCMU_PLATFORM": "off"})

    assert "HPCMU_PLATFORM" in caplog.text


def test_warn_if_platform_disabled_silent_when_slurm_job_id_set(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="hpc_model_utils.cli.root"):
        _warn_if_platform_disabled(
            {"HPCMU_PLATFORM": "off", "SLURM_JOB_ID": "123"}
        )

    assert caplog.text == ""


def test_warn_if_platform_disabled_silent_when_platform_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="hpc_model_utils.cli.root"):
        _warn_if_platform_disabled({"HPCMU_PLATFORM": "on"})

    assert caplog.text == ""


# ---------------------------------------------------------------------------
# AM-001c item 3: scrub query strings out of URL-looking tokens.
# ---------------------------------------------------------------------------


def test_scrub_url_query_strips_query_string_from_url_token() -> None:
    text = "see https://x.example/report?token=secret&id=5 for details"

    assert _scrub_url_query(text) == "see https://x.example/report for details"


def test_scrub_url_query_leaves_text_without_url_untouched() -> None:
    assert _scrub_url_query("missing input") == "missing input"


# ---------------------------------------------------------------------------
# _map_failure: Requirement 6 item 1.
# ---------------------------------------------------------------------------


def test_map_failure_click_exception_uses_format_message() -> None:
    failure = _map_failure(click.UsageError("bad option"))

    assert failure.exit_code == ExitCode.USAGE
    assert failure.category == "UsageError"
    assert failure.message == "bad option"
    assert failure.status == RunStatus.RUNTIME_ERROR


def test_map_failure_abort_maps_to_usage_error() -> None:
    failure = _map_failure(click.exceptions.Abort())

    assert failure.exit_code == ExitCode.USAGE
    assert failure.category == "UsageError"


def test_map_failure_hpcmu_error_uses_classify() -> None:
    failure = _map_failure(SchedulerError("x"))

    assert failure.exit_code == ExitCode.SCHEDULER
    assert failure.category == "SchedulerError"


def test_map_failure_unmapped_exception_is_internal_error() -> None:
    failure = _map_failure(RuntimeError("boom"))

    assert failure.exit_code == ExitCode.INTERNAL
    assert failure.category == "InternalError"
    assert failure.status == RunStatus.RUNTIME_ERROR


# ---------------------------------------------------------------------------
# _fatal_path: Requirement 6 ordering, AC2-AC5, and the hardening notes.
# ---------------------------------------------------------------------------


def test_fatal_path_data_error_emits_hooks_in_order_then_exit_code() -> None:
    app_ctx, channel = _make_app_context()
    app_ctx.command_name = "fetch_things"

    exit_code = _fatal_path(
        DataError("missing input"), app_ctx, emits_terminal_status=True
    )

    assert exit_code == ExitCode.DATA
    assert _hooks_of(channel) == [
        Hook("SetDataError", ()),
        Hook("SetAnnotation", ("DATA_ERROR: fetch_things: missing input",)),
    ]


def test_fatal_path_writes_fatal_line_to_stderr_when_channels_none(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_ctx, _ = _make_app_context()
    app_ctx.command_name = "fetch_things"

    _fatal_path(DataError("missing input"), app_ctx, emits_terminal_status=True)

    captured = capsys.readouterr()
    assert (
        captured.err
        == "hpc-model-utils fetch_things: DataError: missing input\n"
    )


def test_fatal_path_emits_terminal_status_false_skips_status_and_annotation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_ctx, channel = _make_app_context()
    app_ctx.command_name = "scheduler_cmd"

    exit_code = _fatal_path(
        SchedulerError("x"), app_ctx, emits_terminal_status=False
    )

    assert exit_code == ExitCode.SCHEDULER
    assert _hooks_of(channel) == []
    captured = capsys.readouterr()
    assert captured.err == "hpc-model-utils scheduler_cmd: SchedulerError: x\n"


def test_fatal_path_terminal_already_emitted_skips_second_terminal() -> None:
    app_ctx, channel = _make_app_context()
    app_ctx.command_name = "finalize"
    app_ctx.reporter.terminal(RunStatus.SUCCESS, "already done")
    before = list(channel.lines)

    exit_code = _fatal_path(
        DataError("late"), app_ctx, emits_terminal_status=True
    )

    assert exit_code == ExitCode.DATA
    assert channel.lines[: len(before)] == before
    assert _hooks_of(channel) == [
        Hook("SetSuccess", ()),
        Hook("SetAnnotation", ("already done",)),
    ]


def test_fatal_path_terminal_failure_still_writes_fatal_line_and_returns_code(
    capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    reporter = Reporter(_RaisingChannel(), enabled=True)
    app_ctx = AppContext(
        reporter=reporter, channels=None, command_name="fetch_things"
    )

    with caplog.at_level(logging.ERROR, logger="hpc_model_utils.cli.root"):
        exit_code = _fatal_path(
            DataError("missing input"), app_ctx, emits_terminal_status=True
        )

    assert exit_code == ExitCode.DATA
    captured = capsys.readouterr()
    assert (
        captured.err
        == "hpc-model-utils fetch_things: DataError: missing input\n"
    )
    assert "reporter.terminal() failed" in caplog.text


def test_fatal_path_unmapped_exception_maps_to_internal_error_exit_99() -> None:
    app_ctx, channel = _make_app_context()
    app_ctx.command_name = "crash_demo"

    exit_code = _fatal_path(
        RuntimeError("boom"), app_ctx, emits_terminal_status=True
    )

    assert exit_code == ExitCode.INTERNAL
    hooks = _hooks_of(channel)
    assert hooks[0] == Hook("SetRuntimeError", ())
    assert all(h.method != "SetModelError" for h in hooks)


def test_fatal_path_scrubs_url_query_from_annotation_and_fatal_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_ctx, channel = _make_app_context()
    app_ctx.command_name = "fetch_things"

    _fatal_path(
        DataError("bad https://example.com/x?token=secret"),
        app_ctx,
        emits_terminal_status=True,
    )

    annotation = next(
        h for h in _hooks_of(channel) if h.method == "SetAnnotation"
    ).args[0]
    assert "?" not in annotation
    captured = capsys.readouterr()
    assert "?" not in captured.err


_UNRESOLVED_COMMAND_SCRIPT = (
    "from hpc_model_utils.cli.root import main\n"
    "raise SystemExit(main(['no-such-command']))\n"
)


def test_fatal_path_unresolved_command_uses_dash_not_empty_label() -> None:
    # install() is process-global (raises if already installed), so this
    # drives the real main() chokepoint in a subprocess, mirroring
    # tests/system/test_stderr_discipline.py's pattern.
    result = subprocess.run(
        [sys.executable, "-c", _UNRESOLVED_COMMAND_SCRIPT],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 2
    annotation = next(
        h for h in parse_hooks(result.stdout) if h.method == "SetAnnotation"
    ).args[0]
    assert "RUNTIME_ERROR: -: " in annotation
    assert ": : " not in annotation
    assert result.stderr == (
        "hpc-model-utils -: UsageError: No such command 'no_such_command'.\n"
    )


def test_emits_terminal_status_unresolved_command_defaults_true() -> None:
    assert _emits_terminal_status("no_such_command_registered") is True


def test_emits_terminal_status_reads_flag_from_registered_command() -> None:
    cmd = HpcmuCommand(
        name="emits_flag_probe",
        callback=lambda: None,
        emits_terminal_status=False,
    )
    cli.add_command(cmd)
    try:
        assert _emits_terminal_status("emits_flag_probe") is False
    finally:
        del cli.commands["emits_flag_probe"]
