"""ADR-053/ADR-003/R12/R42/R137 tests for the NEWAVE postprocess
(ticket-048a)."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest

from hpc_model_utils.core.errors import HpcmuError
from hpc_model_utils.core.plugin import PostprocessError
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.infra.errors import ShellCommandError
from hpc_model_utils.infra.shell import ShellResult
from hpc_model_utils.models.newave import NewavePlugin
from hpc_model_utils.models.newave import postprocess as postprocess_module
from hpc_model_utils.models.newave.postprocess import (
    _nwlistcf_arquivos_dat,
    _nwlistcf_dat,
    _nwlistop_dat,
    run_postprocess,
)
from tests.support.decks import newave_workspace
from tests.support.fake_slurm.cli import write_executable_stub

_DGER_VALUE_START = 23
_DGER_VALUE_END = 25


def _blank_dger_field(dger_path: Path, prefix: str) -> None:
    """Overwrite the fixed-width ``IntegerField`` (columns 23-25) of the
    ``dger.dat`` line starting with ``prefix`` with spaces, so inewave's
    own parser (``Field.read`` catching ``ValueError``) yields ``None``
    for that attribute -- no ``setattr``/``cast`` bypass of its typed
    setters."""
    lines = dger_path.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            body = line.splitlines()[0]
            blanked = (
                body[:_DGER_VALUE_START].ljust(_DGER_VALUE_START)
                + " " * (_DGER_VALUE_END - _DGER_VALUE_START)
                + body[_DGER_VALUE_END:]
            )
            newline = "\n" if line.endswith("\n") else ""
            lines[i] = blanked + newline
            dger_path.write_text("".join(lines), encoding="utf-8")
            return
    raise AssertionError(f"no dger.dat line starts with {prefix!r}")


def _raising_shell_run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    on_line: Callable[[str], None] | None = None,
    keep_output: bool = True,
) -> ShellResult:
    raise OSError("disk full")


def _bare_shell_command_error(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    on_line: Callable[[str], None] | None = None,
    keep_output: bool = True,
) -> ShellResult:
    raise ShellCommandError("boom")


# -- control-file generators (pure functions) ------------------------------


def test_nwlistcf_arquivos_dat_month_12_contains_v1_cortes_names() -> None:
    text = _nwlistcf_arquivos_dat(12)
    assert "ARQUIVO DE CORTES DE BENDERS: cortes-012.dat\n" in text
    assert "ARQUIVO DE ESTADOS CORTES   : cortese-012.dat\n" in text
    assert text.count("\n") == 15


def test_nwlistcf_arquivos_dat_december_start_plus_one_is_month_13() -> None:
    text = _nwlistcf_arquivos_dat(13)
    assert "cortes-013.dat" in text
    assert "cortese-013.dat" in text


def test_nwlistcf_dat_option_1_matches_v1_literal_text() -> None:
    assert _nwlistcf_dat(12, 1) == (
        " INI FIM FC (FC = 1: IMPRIME TODOS CORTES, FC = 0: IMPRIME "
        "APENAS CORTES VALIDOS NA ULTIMA ITERACAO)\n"
        " XXX XXX X\n"
        "  12  12 1\n"
        " OPCOES DE IMPRESSAO : 01 - CORTES FCF  02 - ESTADOS FCF  "
        "03 - RESTRICAO SAR\n"
        " XX XX XX (SE 99 CONSIDERA TODAS)\n"
        " 01\n"
    )


def test_nwlistcf_dat_option_2_ends_with_zero_padded_option_line() -> None:
    text = _nwlistcf_dat(12, 2)
    assert re.search(r"(?m)^ 02$", text)
    assert "  12  12 1\n" in text


def test_nwlistop_dat_option_2_matches_v1_literal_text() -> None:
    text = _nwlistop_dat(2, 1, 50)
    assert text.startswith(" 2\n")
    assert " 001 050\n" in text
    assert text.endswith(" 999\n")


def test_nwlistop_dat_pre_study_years_shifts_initial_stage() -> None:
    text = _nwlistop_dat(4, 13, 84)
    assert text.startswith(" 4\n")
    assert " 013 084\n" in text


# -- AC1: control files and invocation order -------------------------------


def test_run_postprocess_success_invokes_in_order_and_restores_index(
    tmp_path: Path,
) -> None:
    dws = newave_workspace(tmp_path)
    ws = dws.ws
    original_index = (ws.root / "arquivos.dat").read_bytes()

    write_executable_stub(
        ws.assets,
        "nwlistcf",
        'echo "nwlistcf $(pwd)" >> order.log\n'
        "{ cat arquivos.dat; echo ---; cat nwlistcf.dat; echo ===; } "
        ">> nwlistcf_calls.log\n",
    )
    write_executable_stub(
        ws.assets,
        "nwlistop",
        'echo "nwlistop $(pwd)" >> order.log\n'
        "{ cat nwlistop.dat; echo ===; } >> nwlistop_calls.log\n",
    )

    run_postprocess(ws)

    order = (ws.root / "order.log").read_text(encoding="utf-8").splitlines()
    assert order == [f"nwlistcf {ws.root}"] * 2 + [f"nwlistop {ws.root}"] * 2

    cf_calls = [
        call
        for call in (ws.root / "nwlistcf_calls.log")
        .read_text(encoding="utf-8")
        .split("===\n")
        if call
    ]
    assert len(cf_calls) == 2
    for call in cf_calls:
        assert "ARQUIVO DE CORTES DE BENDERS: cortes-012.dat" in call
    assert re.search(r"(?m)^ 01$", cf_calls[0])
    assert re.search(r"(?m)^ 02$", cf_calls[1])

    op_calls = [
        call
        for call in (ws.root / "nwlistop_calls.log")
        .read_text(encoding="utf-8")
        .split("===\n")
        if call
    ]
    assert len(op_calls) == 2
    for call in op_calls:
        assert " 001 050" in call
    assert op_calls[0].startswith(" 2\n")
    assert op_calls[1].startswith(" 4\n")

    assert (ws.root / "arquivos.dat").read_bytes() == original_index
    assert (ws.root / "arquivos-nwlistcf.dat").is_file()


# -- AC2: failures are collected, never short-circuited ---------------------


def test_run_postprocess_nwlistcf_exit_and_missing_nwlistop_collects_all_failures(
    tmp_path: Path,
) -> None:
    dws = newave_workspace(tmp_path)
    ws = dws.ws
    original_index = (ws.root / "arquivos.dat").read_bytes()

    write_executable_stub(
        ws.assets, "nwlistcf", "echo ran >> nwlistcf.ran\nexit 3\n"
    )
    # no assets/nwlistop stub: the binary is missing.

    with pytest.raises(
        PostprocessError, match="nwlistcf option 1 exited 3"
    ) as excinfo:
        run_postprocess(ws)

    ran = (ws.root / "nwlistcf.ran").read_text(encoding="utf-8").splitlines()
    assert ran == ["ran", "ran"]

    message = str(excinfo.value)
    assert "nwlistcf option 2 exited 3" in message
    assert "nwlistop could not be executed" in message
    assert str(ws.root) not in message

    assert (ws.root / "arquivos.dat").read_bytes() == original_index


# -- probe 2: the nwlistcf `finally` restore also fires when a step raises --


def test_run_postprocess_oserror_during_invoke_propagates_and_restores_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dws = newave_workspace(tmp_path)
    ws = dws.ws
    original_index = (ws.root / "arquivos.dat").read_bytes()

    monkeypatch.setattr(postprocess_module, "shell_run", _raising_shell_run)

    with pytest.raises(OSError, match="disk full"):
        run_postprocess(ws)

    assert (ws.root / "arquivos.dat").read_bytes() == original_index
    assert not (ws.root / "arquivos.dat.bkp").exists()


# -- probe 3: a bare ShellCommandError with no cause uses fixed fallback ----


def test_invoke_shell_command_error_without_cause_uses_fixed_fallback_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dws = newave_workspace(tmp_path)
    ws = dws.ws

    monkeypatch.setattr(
        postprocess_module, "shell_run", _bare_shell_command_error
    )

    with pytest.raises(
        PostprocessError,
        match="nwlistcf could not be executed: not executable",
    ) as excinfo:
        run_postprocess(ws)

    message = str(excinfo.value)
    assert "nwlistop could not be executed: not executable" in message
    assert str(ws.root) not in message


# -- AC3: timeout and unreadable deck ---------------------------------------


def test_run_postprocess_nwlistop_timeout_raises_within_deadline(
    tmp_path: Path,
) -> None:
    dws = newave_workspace(tmp_path)
    ws = dws.ws
    write_executable_stub(ws.assets, "nwlistcf", "exit 0\n")
    write_executable_stub(
        ws.assets,
        "nwlistop",
        "echo started >> nwlistop.started\nsleep 5\n"
        "echo finished >> nwlistop.finished\n",
    )

    with pytest.raises(
        PostprocessError, match=r"nwlistop option 2 timed out after 0\.5s"
    ):
        run_postprocess(ws, timeout=0.5)

    started = (
        (ws.root / "nwlistop.started").read_text(encoding="utf-8").splitlines()
    )
    assert started == ["started", "started"]
    assert not (ws.root / "nwlistop.finished").exists()


def test_run_postprocess_missing_caso_raises_postprocesserror_not_dataerror(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    with pytest.raises(
        PostprocessError, match=r"deck unreadable: missing caso\.dat"
    ):
        run_postprocess(ws)


# -- Requirements 3/4: the None-field guard failures -------------------------


def test_run_postprocess_none_start_month_records_nwlistcf_and_nwlistop(
    tmp_path: Path,
) -> None:
    dws = newave_workspace(tmp_path)
    ws = dws.ws
    _blank_dger_field(ws.root / "dger.dat", "MES INICIO DO ESTUDO")

    with pytest.raises(
        PostprocessError,
        match=r"nwlistcf: dger\.dat has no study start month",
    ) as excinfo:
        run_postprocess(ws)
    assert "nwlistop: dger.dat lacks the study horizon fields" in str(
        excinfo.value
    )


def test_run_postprocess_none_study_horizon_field_records_nwlistop_only(
    tmp_path: Path,
) -> None:
    dws = newave_workspace(tmp_path)
    ws = dws.ws
    _blank_dger_field(ws.root / "dger.dat", "No. DE ANOS PRE")
    write_executable_stub(ws.assets, "nwlistcf", "exit 0\n")

    with pytest.raises(
        PostprocessError,
        match=r"nwlistop: dger\.dat lacks the study horizon fields",
    ) as excinfo:
        run_postprocess(ws)
    assert "nwlistcf" not in str(excinfo.value)


# -- AC4: the PostprocessError contract type ---------------------------------


def test_postprocesserror_is_exception_but_not_hpcmuerror() -> None:
    assert issubclass(PostprocessError, Exception)
    assert not issubclass(PostprocessError, HpcmuError)


def test_newaveplugin_postprocess_delegates_and_raises_postprocesserror(
    tmp_path: Path,
) -> None:
    dws = newave_workspace(tmp_path)
    ws = dws.ws
    write_executable_stub(
        ws.assets, "nwlistcf", "echo ran >> nwlistcf.ran\nexit 3\n"
    )
    # no assets/nwlistop stub: the binary is missing.

    with pytest.raises(PostprocessError, match="nwlistcf option 1 exited 3"):
        NewavePlugin().postprocess(ws)

    ran = (ws.root / "nwlistcf.ran").read_text(encoding="utf-8").splitlines()
    assert ran == ["ran", "ran"]
