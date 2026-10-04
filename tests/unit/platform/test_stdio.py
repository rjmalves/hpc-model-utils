"""ADR-006/AM-001/AM-002 tests for fd-level stdio isolation (ticket-016)."""

from __future__ import annotations

import io
import os
import subprocess
import sys

import pytest

from hpc_model_utils.platform import stdio
from hpc_model_utils.platform.encoding import TRIGGER_PATTERNS
from hpc_model_utils.platform.stdio import (
    NeutralizingStream,
    RawProtocolChannel,
    StdioChannels,
    write_fatal,
)

# ---------------------------------------------------------------------------
# NeutralizingStream.write: complete-line neutralization and AC4
# ---------------------------------------------------------------------------


def test_write_complete_line_is_neutralized_and_flushed() -> None:
    target = io.StringIO()
    stream = NeutralizingStream(target)

    result = stream.write("Submitted batch job 1\n")

    assert result == len("Submitted batch job 1\n")
    assert target.getvalue() == "Submitted_batch_job 1\n"


def test_write_ac4_split_writes_matches_expected_wire() -> None:
    target = io.StringIO()
    stream = NeutralizingStream(target)

    stream.write("a ${")
    stream.write("b}\nSubmitted batch job 1\n")

    assert target.getvalue() == "a $ {b}\nSubmitted_batch_job 1\n"


# ---------------------------------------------------------------------------
# AM-002: flush/close end the line instead of holding text back
# ---------------------------------------------------------------------------


def test_flush_partial_line_is_forwarded_with_added_newline() -> None:
    target = io.StringIO()
    stream = NeutralizingStream(target)

    stream.write("hello world")
    stream.flush()

    assert target.getvalue() == "hello world\n"


def test_flush_empty_buffer_writes_nothing() -> None:
    target = io.StringIO()
    stream = NeutralizingStream(target)

    stream.flush()

    assert target.getvalue() == ""


def test_close_forwards_buffered_partial_line_with_newline() -> None:
    target = io.StringIO()
    stream = NeutralizingStream(target)

    stream.write("Created temporary")
    stream.close()

    assert target.getvalue() == "Created temporary\n"


def test_write_after_close_raises_value_error() -> None:
    stream = NeutralizingStream(io.StringIO())
    stream.close()

    with pytest.raises(ValueError, match="closed"):
        stream.write("x")


def test_flush_after_close_raises_value_error() -> None:
    stream = NeutralizingStream(io.StringIO())
    stream.close()

    with pytest.raises(ValueError, match="closed"):
        stream.flush()


def test_close_twice_is_idempotent() -> None:
    target = io.StringIO()
    stream = NeutralizingStream(target)

    stream.write("hello")
    stream.close()
    stream.close()

    assert target.getvalue() == "hello\n"


# ---------------------------------------------------------------------------
# AM-002/SR-015-SR-017: the hold-back bypass regressions
# ---------------------------------------------------------------------------


def _assert_wire_has_no_dollar_brace_or_trigger(target: io.StringIO) -> None:
    for line in target.getvalue().split("\n"):
        assert "${" not in line
        for pattern in TRIGGER_PATTERNS:
            assert pattern.search(line) is None


def test_flush_sr015_reassembly_past_64_char_cap_has_no_trigger_match() -> None:
    target = io.StringIO()
    stream = NeutralizingStream(target)

    stream.write("x Submitted" + " " * 52 + "batc")
    stream.flush()
    stream.write("reated temporary dir /x;id\n")

    _assert_wire_has_no_dollar_brace_or_trigger(target)


def test_flush_sr016_dollar_before_held_trigger_prefix_has_no_dollar_brace() -> (
    None
):
    target = io.StringIO()
    stream = NeutralizingStream(target)

    stream.write("R$Sub")
    stream.flush()
    stream.write("mitted batch job 5\n")

    _assert_wire_has_no_dollar_brace_or_trigger(target)


def test_flush_sr016_two_wrappers_sharing_target_has_no_dollar_brace() -> None:
    target = io.StringIO()
    wrapper_a = NeutralizingStream(target)
    wrapper_b = NeutralizingStream(target)

    wrapper_a.write("price $Created")
    wrapper_a.flush()
    wrapper_b.write("{CurrentExecution.SetSuccess()}\n")

    _assert_wire_has_no_dollar_brace_or_trigger(target)


def test_close_sr017_two_wrappers_sharing_target_has_no_trigger_match() -> None:
    target = io.StringIO()
    wrapper_a = NeutralizingStream(target)
    wrapper_b = NeutralizingStream(target)

    wrapper_a.write("Submitted batch ")
    wrapper_a.close()
    wrapper_b.write("job 4242")
    wrapper_b.close()

    _assert_wire_has_no_dollar_brace_or_trigger(target)


# ---------------------------------------------------------------------------
# writable/isatty/encoding/errors
# ---------------------------------------------------------------------------


def test_writable_returns_true_and_isatty_returns_false() -> None:
    stream = NeutralizingStream(io.StringIO())

    assert stream.writable() is True
    assert stream.isatty() is False


def test_encoding_and_errors_properties_delegate_to_target() -> None:
    target = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")
    stream = NeutralizingStream(target)

    assert stream.encoding == "utf-8"
    assert stream.errors == "strict"


# ---------------------------------------------------------------------------
# RawProtocolChannel
# ---------------------------------------------------------------------------


def test_raw_protocol_channel_write_line_flushes_pending_text_first() -> None:
    target = io.StringIO()
    neutral = NeutralizingStream(target)
    channel = RawProtocolChannel(target, neutral)

    neutral.write("partial")
    channel.write_line("HOOK")

    assert target.getvalue() == "partial\nHOOK\n"


# ---------------------------------------------------------------------------
# write_fatal (in-process: exercises the module-level first-wins latch,
# so it is reset around the test for isolation from other tests).
# ---------------------------------------------------------------------------


def test_write_fatal_flattens_embedded_newline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stdio, "_fatal_sent", False)
    fatal = io.StringIO()
    channel = RawProtocolChannel(
        io.StringIO(), NeutralizingStream(io.StringIO())
    )
    ch = StdioChannels(
        protocol=channel,
        fatal=fatal,
        saved_stdout=io.StringIO(),
        saved_stderr=io.StringIO(),
        saved_fd2=-1,
        stdout_wrapper=NeutralizingStream(io.StringIO()),
        stderr_wrapper=NeutralizingStream(io.StringIO()),
        retargeted_handlers=(),
        prior_capture_warnings=False,
    )

    write_fatal(ch, "fatal\nline")

    assert fatal.getvalue() == "fatal line\n"


# ---------------------------------------------------------------------------
# Subprocess tests (AC2, AC3, AC5, SR-008): never call install() in-process.
# ---------------------------------------------------------------------------


def test_install_subprocess_stderr_is_exactly_one_fatal_line() -> None:
    code = (
        "from hpc_model_utils.platform.stdio import install, write_fatal\n"
        "ch = install()\n"
        "import sys, warnings\n"
        "print('log ${x}')\n"
        "sys.stderr.write('tb line\\n')\n"
        "warnings.warn('w')\n"
        "ch.protocol.write_line('${CurrentExecution.SetSuccess()}')\n"
        "write_fatal(ch, 'fatal\\nline')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )

    assert result.stderr == "fatal line\n"


def test_install_subprocess_stdout_contains_expected_lines() -> None:
    code = (
        "from hpc_model_utils.platform.stdio import install, write_fatal\n"
        "ch = install()\n"
        "import sys, warnings\n"
        "print('log ${x}')\n"
        "sys.stderr.write('tb line\\n')\n"
        "warnings.warn('w')\n"
        "ch.protocol.write_line('${CurrentExecution.SetSuccess()}')\n"
        "write_fatal(ch, 'fatal\\nline')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )

    lines = result.stdout.splitlines()
    assert "log $ {x}" in lines
    assert "tb line" in lines
    assert "${CurrentExecution.SetSuccess()}" in lines
    for line in lines:
        if line != "${CurrentExecution.SetSuccess()}":
            assert "${" not in line


def test_restore_subprocess_stderr_write_reaches_real_stderr() -> None:
    code = (
        "from hpc_model_utils.platform.stdio import install, restore\n"
        "import sys\n"
        "ch = install()\n"
        "restore(ch)\n"
        "sys.stderr.write('after restore\\n')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )

    assert result.stderr == "after restore\n"


def test_install_subprocess_pythonioencoding_latin1_hook_reaches_stdout_as_utf8() -> (
    None
):
    code = (
        "from hpc_model_utils.platform.stdio import install\n"
        "ch = install()\n"
        "ch.protocol.write_line('a' + chr(0x2060) + 'b')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "latin-1"},
    )

    assert result.stdout == ("a" + chr(0x2060) + "b" + "\n").encode("utf-8")
    assert result.stderr == b""


# ---------------------------------------------------------------------------
# Subprocess tests: AM-002 SR-018/SR-019/SR-020/SR-021.
# ---------------------------------------------------------------------------


def test_install_subprocess_second_install_raises_runtime_error() -> None:
    code = (
        "from hpc_model_utils.platform.stdio import install\n"
        "install()\n"
        "try:\n"
        "    install()\n"
        "    print('no-error')\n"
        "except RuntimeError:\n"
        "    print('raised')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )

    assert result.returncode == 0
    assert result.stdout.splitlines()[-1] == "raised"


def test_restore_subprocess_second_call_is_a_no_op() -> None:
    code = (
        "from hpc_model_utils.platform.stdio import install, restore\n"
        "import sys\n"
        "ch = install()\n"
        "restore(ch)\n"
        "restore(ch)\n"
        "sys.stderr.write('after double restore\\n')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )

    assert result.returncode == 0
    assert result.stderr == "after double restore\n"


def test_install_subprocess_dunder_stdout_write_is_neutralized() -> None:
    code = (
        "from hpc_model_utils.platform.stdio import install\n"
        "install()\n"
        "import sys\n"
        "print('${x}', file=sys.__stdout__)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )

    assert result.stdout == "$ {x}\n"


def test_install_subprocess_preexisting_stream_handler_is_retargeted() -> None:
    code = (
        "import sys, logging\n"
        "logger = logging.getLogger('t')\n"
        "logger.setLevel(logging.INFO)\n"
        "handler = logging.StreamHandler(sys.stdout)\n"
        "handler.setFormatter(logging.Formatter('%(message)s'))\n"
        "logger.addHandler(handler)\n"
        "from hpc_model_utils.platform.stdio import install\n"
        "install()\n"
        "logger.info('${x}')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )

    assert result.stdout == "$ {x}\n"


def test_write_fatal_subprocess_first_call_wins() -> None:
    code = (
        "from hpc_model_utils.platform.stdio import install, write_fatal\n"
        "ch = install()\n"
        "write_fatal(ch, 'first')\n"
        "write_fatal(ch, 'second')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )

    assert result.stderr == "first\n"


def test_write_fatal_subprocess_forked_child_writes_nothing_to_stderr() -> None:
    if not hasattr(os, "fork"):
        pytest.skip("os.fork is unavailable")
    code = (
        "import os\n"
        "from hpc_model_utils.platform.stdio import install, write_fatal\n"
        "ch = install()\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        "    write_fatal(ch, 'from child')\n"
        "    os._exit(0)\n"
        "else:\n"
        "    os.waitpid(pid, 0)\n"
        "    write_fatal(ch, 'from parent')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )

    assert result.stderr == "from parent\n"
