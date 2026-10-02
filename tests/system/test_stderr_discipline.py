"""ADR-005/ADR-006/R36/R91/R122/AM-001c subprocess tests for the v2 CLI's
single fatal-path chokepoint (ticket-017). Each script runs in a fresh
interpreter, registers a temporary command on the real `cli` group, and
calls `main()` directly - so these never call `install()` in the pytest
process itself.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence

from tests.support.hooks import Hook, parse_hooks

_FETCH_THINGS_SCRIPT = (
    "from hpc_model_utils.cli.root import cli, main, HpcmuCommand\n"
    "from hpc_model_utils.core.errors import DataError\n"
    "def _cb() -> None:\n"
    "    raise DataError('missing input')\n"
    "cli.add_command(HpcmuCommand(name='fetch_things', callback=_cb))\n"
    "raise SystemExit(main(['fetch-things']))\n"
)

_USAGE_ERROR_SCRIPT = (
    "from hpc_model_utils.cli.root import cli, main, HpcmuCommand\n"
    "def _cb() -> None:\n"
    "    pass\n"
    "cli.add_command(HpcmuCommand(name='usage_demo', callback=_cb))\n"
    "raise SystemExit(main(['usage-demo', '--bogus']))\n"
)

_INTERNAL_CRASH_SCRIPT = (
    "from hpc_model_utils.cli.root import cli, main, HpcmuCommand\n"
    "def _cb() -> None:\n"
    "    raise RuntimeError('boom')\n"
    "cli.add_command(HpcmuCommand(name='crash_demo', callback=_cb))\n"
    "raise SystemExit(main(['crash-demo']))\n"
)

_UNKNOWN_SUBCOMMAND_SCRIPT = (
    "from hpc_model_utils.cli.root import main\n"
    "raise SystemExit(main(['no-such-command']))\n"
)

_SCHEDULER_NO_TERMINAL_SCRIPT = (
    "from hpc_model_utils.cli.root import cli, main, HpcmuCommand\n"
    "from hpc_model_utils.core.errors import SchedulerError\n"
    "def _cb() -> None:\n"
    "    raise SchedulerError('x')\n"
    "cli.add_command(\n"
    "    HpcmuCommand(\n"
    "        name='scheduler_demo', callback=_cb, emits_terminal_status=False\n"
    "    )\n"
    ")\n"
    "raise SystemExit(main(['scheduler-demo']))\n"
)


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True)


def test_fetch_things_separate_pipes_stderr_is_exactly_one_line() -> None:
    result = _run([sys.executable, "-c", _FETCH_THINGS_SCRIPT])

    assert result.returncode == 5
    assert result.stderr == (
        "hpc-model-utils fetch_things: DataError: missing input\n"
    )


def test_fetch_things_separate_pipes_hooks_in_order() -> None:
    result = _run([sys.executable, "-c", _FETCH_THINGS_SCRIPT])

    hooks = parse_hooks(result.stdout)
    assert hooks == [
        Hook(
            "SetMetadata",
            (
                "duration_seconds.fetch_things",
                hooks[0].args[1] if hooks else "",
            ),
        ),
        Hook("SetDataError", ()),
        Hook(
            "SetAnnotation",
            ("DATA_ERROR: fetch_things: missing input",),
        ),
    ]


def test_fetch_things_merged_pipe_status_hook_precedes_stderr_line() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _FETCH_THINGS_SCRIPT],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    merged = result.stdout
    data_error_index = merged.index("${CurrentExecution.SetDataError()}")
    stderr_line_index = merged.index(
        "hpc-model-utils fetch_things: DataError: missing input"
    )
    assert data_error_index < stderr_line_index


def test_usage_error_unknown_option_exit_code_2() -> None:
    result = _run([sys.executable, "-c", _USAGE_ERROR_SCRIPT])

    assert result.returncode == 2
    stderr_lines = result.stderr.splitlines()
    assert len(stderr_lines) == 1
    assert "UsageError" in stderr_lines[0]
    assert "${CurrentExecution.SetRuntimeError()}" in result.stdout.splitlines()


def test_unknown_subcommand_exit_code_2_no_duration_hook() -> None:
    result = _run([sys.executable, "-c", _UNKNOWN_SUBCOMMAND_SCRIPT])

    assert result.returncode == 2
    stderr_lines = result.stderr.splitlines()
    assert len(stderr_lines) == 1
    assert "UsageError" in stderr_lines[0]
    hooks = parse_hooks(result.stdout)
    assert all(
        not (
            h.method == "SetMetadata"
            and h.args[0].startswith("duration_seconds")
        )
        for h in hooks
    )


def test_internal_crash_maps_to_exit_99_without_set_model_error() -> None:
    result = _run([sys.executable, "-c", _INTERNAL_CRASH_SCRIPT])

    assert result.returncode == 99
    stderr_lines = result.stderr.splitlines()
    assert len(stderr_lines) == 1
    assert "InternalError" in stderr_lines[0]
    hooks = parse_hooks(result.stdout)
    assert all(h.method != "SetModelError" for h in hooks)
    assert any(h.method == "SetRuntimeError" for h in hooks)


def test_emits_terminal_status_false_subprocess_has_no_status_hook() -> None:
    result = _run([sys.executable, "-c", _SCHEDULER_NO_TERMINAL_SCRIPT])

    assert result.returncode == 3
    stderr_lines = result.stderr.splitlines()
    assert len(stderr_lines) == 1
    assert (
        stderr_lines[0] == "hpc-model-utils scheduler_demo: SchedulerError: x"
    )
    hooks = parse_hooks(result.stdout)
    assert all(h.method != "SetAnnotation" for h in hooks)
    assert all(
        h.method not in ("SetSuccess", "SetDataError", "SetRuntimeError")
        for h in hooks
    )


def test_python_dash_m_version_prints_version_with_empty_stderr() -> None:
    result = _run([sys.executable, "-m", "hpc_model_utils.cli", "--version"])

    assert result.returncode == 0
    assert result.stderr == ""
    assert "hpc-model-utils" in result.stdout
