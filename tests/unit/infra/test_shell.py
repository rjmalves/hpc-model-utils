from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from hpc_model_utils.infra import shell
from hpc_model_utils.infra.errors import ShellCommandError


def test_run_metacharacters_in_argv_are_inert(tmp_path: Path) -> None:
    result = shell.run(["echo", "a;touch x"], cwd=tmp_path)

    assert result.output == ("a;touch x",)
    assert (tmp_path / "x").exists() is False


def test_run_duplicate_lines_and_stderr_are_relayed_without_dedup() -> None:
    script = (
        "import sys\n"
        "print('same')\n"
        "print('same')\n"
        "print('err', file=sys.stderr)\n"
    )
    recorded: list[str] = []

    result = shell.run(
        [sys.executable, "-u", "-c", script], on_line=recorded.append
    )

    assert recorded == ["same", "same", "err"]
    assert result.output == ("same", "same", "err")


def test_run_timeout_kills_process_group_and_reports_timed_out() -> None:
    script = (
        "import os, sys, time\n"
        "print(os.getpid())\n"
        "sys.stdout.flush()\n"
        "time.sleep(5)\n"
    )
    recorded: list[str] = []

    start = time.monotonic()
    result = shell.run(
        [sys.executable, "-u", "-c", script],
        timeout=0.5,
        on_line=recorded.append,
    )
    elapsed = time.monotonic() - start

    assert result.timed_out is True
    assert result.returncode == -9
    assert elapsed < 3.0
    pid = int(recorded[0])
    with pytest.raises(ProcessLookupError):
        os.killpg(pid, 0)


def test_run_invalid_utf8_bytes_are_replaced_with_u_fffd() -> None:
    script = "import sys; sys.stdout.buffer.write(b'\\xff\\n')"

    result = shell.run([sys.executable, "-c", script])

    assert result.output == (chr(0xFFFD),)


def test_run_missing_executable_raises_shell_command_error() -> None:
    with pytest.raises(ShellCommandError, match="cannot execute"):
        shell.run(["/no/such/executable-xyz-123"])


def test_run_keep_output_false_returns_empty_output_tuple() -> None:
    result = shell.run(["echo", "hello"], keep_output=False)

    assert result.output == ()


def test_run_empty_argv_raises_value_error() -> None:
    with pytest.raises(ValueError, match="argv"):
        shell.run([])


def test_run_non_str_argv_element_raises_value_error() -> None:
    bad_argv: list[str] = json.loads('["echo", 123]')

    with pytest.raises(ValueError, match="argv"):
        shell.run(bad_argv)


def test_run_escaped_grandchild_holding_pipe_open_does_not_hang() -> None:
    script = (
        "import os, time\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        "    os.setsid()\n"
        "    time.sleep(2)\n"
        "    os._exit(0)\n"
        "os._exit(0)\n"
    )

    start = time.monotonic()
    result = shell.run([sys.executable, "-c", script])
    elapsed = time.monotonic() - start

    assert elapsed < 1.5
    assert result.timed_out is False
    assert result.returncode == 0
    assert result.output == ()


def test_run_long_line_without_trailing_newline_is_relayed() -> None:
    length = 70_000
    script = f"import sys; sys.stdout.write('x' * {length}); sys.stdout.flush()"

    result = shell.run([sys.executable, "-c", script])

    assert result.output == ("x" * length,)


def test_run_slow_on_line_after_exit_still_delivers_every_line() -> None:
    count = 2000
    script = f"for i in range({count}): print(i)"
    recorded: list[str] = []

    def _slow_on_line(line: str) -> None:
        time.sleep(0.001)
        recorded.append(line)

    result = shell.run([sys.executable, "-c", script], on_line=_slow_on_line)

    assert recorded == [str(i) for i in range(count)]
    assert result.output == tuple(str(i) for i in range(count))


def test_run_continuous_grandchild_output_is_bounded_by_hard_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shell, "_POST_EXIT_HARD_CAP", 0.3)
    script = (
        "import os, sys, time\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        "    os.setsid()\n"
        "    for _ in range(1000):\n"
        "        try:\n"
        "            sys.stdout.write('x\\n')\n"
        "            sys.stdout.flush()\n"
        "        except BrokenPipeError:\n"
        "            break\n"
        "        time.sleep(0.01)\n"
        "    os._exit(0)\n"
        "os._exit(0)\n"
    )

    start = time.monotonic()
    result = shell.run([sys.executable, "-c", script])
    elapsed = time.monotonic() - start

    assert elapsed < 1.0
    assert result.timed_out is False
    assert result.returncode == 0


def _chatty_child_script() -> str:
    return (
        "import os, sys, time\n"
        "print(os.getpid())\n"
        "sys.stdout.flush()\n"
        "for i in range(3000):\n"
        "    print(i)\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(0.01)\n"
    )


def _raising_on_line(seen: list[str]) -> Callable[[str], None]:
    def _on_line(line: str) -> None:
        seen.append(line)
        if len(seen) == 3:
            raise RuntimeError("boom")

    return _on_line


def test_run_raising_on_line_kills_child_and_reraises() -> None:
    seen: list[str] = []

    start = time.monotonic()
    with pytest.raises(RuntimeError, match="boom"):
        shell.run(
            [sys.executable, "-u", "-c", _chatty_child_script()],
            on_line=_raising_on_line(seen),
        )
    elapsed = time.monotonic() - start

    assert elapsed < 2.0
    pid = int(seen[0])
    with pytest.raises(ProcessLookupError):
        os.killpg(pid, 0)


def test_run_raising_on_line_finite_timeout_reraises_before_deadline() -> None:
    seen: list[str] = []

    start = time.monotonic()
    with pytest.raises(RuntimeError, match="boom"):
        shell.run(
            [sys.executable, "-u", "-c", _chatty_child_script()],
            timeout=5.0,
            on_line=_raising_on_line(seen),
        )
    elapsed = time.monotonic() - start

    assert elapsed < 2.0
    pid = int(seen[0])
    with pytest.raises(ProcessLookupError):
        os.killpg(pid, 0)
