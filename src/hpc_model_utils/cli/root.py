"""ADR-004/ADR-005/ADR-006/R36/R37/R33/R69/R91/R122: the v2 CLI root group
and the single fatal-path chokepoint (ticket-017).

Startup order in `main()` is load-bearing (AM-001c/AM-002): install stdio,
configure logging, warn once if the platform is disabled without SLURM,
build the one `Reporter`, build the `AppContext`, then run the group. The
fatal path (`_fatal_path`) mirrors that care: each externally-facing step
is contained so a secondary failure (a broken status hook, a broken fatal
write) can never mask the command's real outcome or skip the single
allowed stderr line.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from typing import TextIO

import click

from hpc_model_utils.core.diagnosis import RunStatus
from hpc_model_utils.core.errors import ExitCode, Failure, classify
from hpc_model_utils.core.settings import EngineSettings
from hpc_model_utils.platform.modelops import Reporter
from hpc_model_utils.platform.stdio import StdioChannels, install, write_fatal

logger = logging.getLogger(__name__)

_URL_QUERY_PATTERN = re.compile(r"(\w+://[^\s?]*)\?\S*")


def _normalize_kebab_to_snake(token: str) -> str:
    return token.replace("-", "_")


@dataclass
class AppContext:
    reporter: Reporter
    channels: StdioChannels | None
    command_name: str = ""
    settings: EngineSettings = field(default_factory=EngineSettings)


class HpcmuCommand(click.Command):
    def __init__(
        self,
        name: str | None,
        context_settings: MutableMapping[str, object] | None = None,
        callback: Callable[..., object] | None = None,
        params: list[click.Parameter] | None = None,
        help: str | None = None,
        epilog: str | None = None,
        short_help: str | None = None,
        options_metavar: str | None = "[OPTIONS]",
        add_help_option: bool = True,
        no_args_is_help: bool = False,
        hidden: bool = False,
        deprecated: bool | str = False,
        *,
        emits_terminal_status: bool = True,
    ) -> None:
        super().__init__(
            name,
            context_settings=context_settings,
            callback=callback,
            params=params,
            help=help,
            epilog=epilog,
            short_help=short_help,
            options_metavar=options_metavar,
            add_help_option=add_help_option,
            no_args_is_help=no_args_is_help,
            hidden=hidden,
            deprecated=deprecated,
        )
        self.emits_terminal_status = emits_terminal_status


class HpcmuGroup(click.Group):
    command_class: type[click.Command] | None = HpcmuCommand

    def __init__(
        self,
        name: str | None = None,
        commands: MutableMapping[str, click.Command]
        | Sequence[click.Command]
        | None = None,
        invoke_without_command: bool = False,
        no_args_is_help: bool | None = None,
        subcommand_metavar: str | None = None,
        chain: bool = False,
        result_callback: Callable[..., object] | None = None,
        context_settings: MutableMapping[str, object] | None = None,
    ) -> None:
        if context_settings is None:
            context_settings = {
                "token_normalize_func": _normalize_kebab_to_snake
            }
        super().__init__(
            name,
            commands=commands,
            invoke_without_command=invoke_without_command,
            no_args_is_help=no_args_is_help,
            subcommand_metavar=subcommand_metavar,
            chain=chain,
            result_callback=result_callback,
            context_settings=context_settings,
        )

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        # The public hook for the canonical (already-normalized) name:
        # click sets `ctx.invoked_subcommand` to this same resolved name,
        # but only *after* calling this method, and only for the one
        # non-chain invocation path - resolving it here instead means this
        # also covers chain mode and runs exactly where click itself
        # determines the name, with no dependence on click's internal,
        # unparsed-argument bookkeeping (public-but-deprecated on `ctx`,
        # or private underneath it).
        name, cmd, remaining = super().resolve_command(ctx, args)
        if cmd is not None and isinstance(ctx.obj, AppContext):
            ctx.obj.command_name = cmd.name or ""
        return name, cmd, remaining

    def invoke(self, ctx: click.Context) -> object:
        start = time.perf_counter()
        try:
            return super().invoke(ctx)
        finally:
            elapsed = time.perf_counter() - start
            app_ctx = ctx.obj
            if isinstance(app_ctx, AppContext) and app_ctx.command_name:
                try:
                    app_ctx.reporter.duration(app_ctx.command_name, elapsed)
                except Exception:
                    # A duration-metadata failure must never replace the
                    # command's real outcome: letting it escape this
                    # `finally` would shadow the exception `try` is
                    # already propagating (or the success it is already
                    # returning).
                    logger.exception("reporter.duration() failed")


cli = HpcmuGroup(name="cli")
cli = click.version_option(package_name="hpc-model-utils")(cli)


class _PipeAwareStreamHandler(logging.StreamHandler[TextIO]):
    """R45 amendment (2026-10-02): re-raise an in-flight
    ``BrokenPipeError`` instead of letting ``StreamHandler.handleError``
    swallow it into a ``--- Logging error ---`` traceback on stderr, so
    D2's broken-pipe path (``core.lifecycle.signals.cancel_on_
    termination``) actually sees it. Every other ``emit()`` failure
    keeps the default ``handleError`` behaviour.
    """

    def handleError(self, record: logging.LogRecord) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, BrokenPipeError):
            raise exc
        super().handleError(record)


def configure_logging() -> None:
    handler = _PipeAwareStreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    app_logger = logging.getLogger("hpc_model_utils")
    app_logger.setLevel(os.environ.get("LOGLEVEL", "INFO"))
    app_logger.addHandler(handler)
    # logging.captureWarnings(True) (set by stdio.install()) attaches a
    # NullHandler to "py.warnings" on first use, which silently drops
    # every captured warning: "py.warnings" is not an ancestor of
    # "hpc_model_utils", so it needs this same handler directly.
    logging.getLogger("py.warnings").addHandler(handler)


def _warn_if_platform_disabled(env: Mapping[str, str] = os.environ) -> None:
    if env.get("HPCMU_PLATFORM", "on") == "off" and not env.get("SLURM_JOB_ID"):
        logger.warning(
            "HPCMU_PLATFORM=off and SLURM_JOB_ID is unset; ModelOps hooks "
            "are disabled for this run"
        )


def _scrub_url_query(text: str) -> str:
    return _URL_QUERY_PATTERN.sub(r"\1", text)


def _map_failure(exc: Exception) -> Failure:
    if isinstance(exc, click.ClickException):
        return Failure(
            RunStatus.RUNTIME_ERROR,
            ExitCode.USAGE,
            "UsageError",
            exc.format_message(),
        )
    if isinstance(exc, click.exceptions.Abort):
        return Failure(
            RunStatus.RUNTIME_ERROR, ExitCode.USAGE, "UsageError", "Aborted!"
        )
    return classify(exc)


def _emits_terminal_status(command_name: str) -> bool:
    cmd = cli.commands.get(command_name)
    if isinstance(cmd, HpcmuCommand):
        return cmd.emits_terminal_status
    return True


def _fatal_path(
    exc: Exception, ctx: AppContext, *, emits_terminal_status: bool
) -> int:
    """Requirement 6: map, maybe report a terminal status, log, write the
    single fatal line, and return the exit code - in that order, with
    every externally-facing step contained so it cannot fail open.
    """
    failure = _map_failure(exc)
    command_label = ctx.command_name or "-"
    message = _scrub_url_query(failure.message)

    if emits_terminal_status and not ctx.reporter.terminal_emitted:
        try:
            ctx.reporter.terminal(
                failure.status,
                f"{failure.status}: {command_label}: {message}",
            )
        except Exception:
            # ModelOps finalizes a status-less Running execution as
            # Success, but a failed status hook must never block the
            # fatal line below: that line is the only signal left once
            # the status hook itself is unreachable.
            logger.exception("reporter.terminal() failed in the fatal path")

    try:
        logger.error("unhandled exception in %s", command_label, exc_info=exc)
    except Exception:
        pass

    line = f"hpc-model-utils {command_label}: {failure.category}: {message}"
    try:
        if ctx.channels is not None:
            write_fatal(ctx.channels, line)
        else:
            # CliRunner (in-process tests) swaps sys.stderr itself; there
            # is no real StdioChannels to write through.
            sys.stderr.write(line + "\n")
    except Exception:
        logger.exception("failed to write the fatal line")

    return failure.exit_code


def main(argv: Sequence[str] | None = None) -> int:
    channels = install()
    configure_logging()
    _warn_if_platform_disabled()
    reporter = Reporter.from_env(channels.protocol)
    ctx = AppContext(reporter=reporter, channels=channels)
    try:
        # Amendment (2026-10-02): built inside the try so an invalid
        # HPCMU_* override (EngineSettings.from_env() raises UsageError)
        # is caught by this same fatal path, never a bare traceback.
        ctx.settings = EngineSettings.from_env()
        result = cli.main(
            args=argv,
            prog_name="hpc-model-utils",
            standalone_mode=False,
            obj=ctx,
        )
    except Exception as exc:
        return _fatal_path(
            exc,
            ctx,
            emits_terminal_status=_emits_terminal_status(ctx.command_name),
        )
    return result if isinstance(result, int) else 0


# ticket-036: registers the hidden `finalize` command on `cli` by import
# side effect. Imported here, after `cli`/`HpcmuCommand`/`AppContext`
# are defined, because `cli.finalize` imports them back from this same
# module -- a top-of-file import would be a real cycle.
import hpc_model_utils.cli.finalize  # noqa: E402,F401

# ticket-054: registers the two C2 toolbox commands on `cli` by import
# side effect, for the same reason as the `finalize` import above.
import hpc_model_utils.cli.toolbox  # noqa: E402,F401

# ticket-053: registers the eight C1 workflow commands on `cli` by
# import side effect, for the same reason as the `finalize` import
# above.
import hpc_model_utils.cli.workflow  # noqa: E402,F401
