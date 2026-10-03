"""ADR-005/ADR-011/R12/R31/R122/R123 tests for the C2 toolbox lifecycle
steps (ticket-054)."""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import pytest

from hpc_model_utils.core.diagnosis import JobReport, RunStatus, evaluate
from hpc_model_utils.core.lifecycle.finalize import diagnose_workspace
from hpc_model_utils.core.lifecycle.toolbox import (
    generate_execution_status,
    postprocess,
)
from hpc_model_utils.core.plugin import PostprocessError
from hpc_model_utils.core.state import StateStore
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.models.newave import NewavePlugin
from tests.support.decks import newave_workspace
from tests.support.fake_plugin import FakePlugin
from tests.support.newave_outputs import pmo_bytes
from tests.support.reporter import RecordingReporter


class _PostprocessErrorPlugin(FakePlugin):
    name = "fakepperr"

    def postprocess(self, ws: Workspace) -> None:
        raise PostprocessError("nwlistcf option 1 exited 3")


class _OSErrorPostprocessPlugin(FakePlugin):
    name = "fakeoserr"

    def postprocess(self, ws: Workspace) -> None:
        raise OSError("disk full")


def _bare_fake_workspace(
    tmp_path: Path, *, token: str = "SUCCESS"
) -> Workspace:
    ws = Workspace.at(tmp_path)
    (ws.root / "fake.out").write_text(f"{token}\n", encoding="utf-8")
    return ws


# -- generate_execution_status() ----------------------------------------


def test_generate_execution_status_success_reports_job_id_then_status(
    tmp_path: Path,
) -> None:
    ws = _bare_fake_workspace(tmp_path)
    reporter = RecordingReporter()

    diag = generate_execution_status(ws, FakePlugin(), reporter, job_id="777")

    assert diag.status is RunStatus.SUCCESS
    assert reporter.metadata_calls == [
        ("job_id", "777"),
        ("status", "SUCCESS"),
    ]
    assert not ws.hpcmu_dir.exists()
    assert ws.legacy_metadata_path.read_bytes() == (
        b'{"job_id": "777", "status": "SUCCESS"}'
    )
    assert ws.legacy_status_path.read_bytes() == b"SUCCESS"


def test_generate_execution_status_empty_job_id_diagnosis_job_id_is_none(
    tmp_path: Path,
) -> None:
    ws = _bare_fake_workspace(tmp_path)
    reporter = RecordingReporter()

    diag = generate_execution_status(ws, FakePlugin(), reporter, job_id="")

    assert diag.job_id is None
    assert reporter.metadata_calls[0] == ("job_id", "")


def test_generate_execution_status_preexisting_state_json_stays_byte_identical(
    tmp_path: Path,
) -> None:
    ws = _bare_fake_workspace(tmp_path)
    StateStore(ws).load_or_create("fake")
    before = ws.state_path.read_bytes()
    reporter = RecordingReporter()

    generate_execution_status(ws, FakePlugin(), reporter, job_id="9")

    assert ws.state_path.read_bytes() == before


def test_generate_execution_status_newave_bare_deck_reports_success(
    tmp_path: Path,
) -> None:
    deck = newave_workspace(tmp_path)
    (deck.ws.root / "pmo.dat").write_bytes(pmo_bytes("complete"))
    reporter = RecordingReporter()

    diag = generate_execution_status(
        deck.ws, NewavePlugin(), reporter, job_id="777"
    )

    assert diag.status is RunStatus.SUCCESS
    assert reporter.metadata_calls == [
        ("job_id", "777"),
        ("status", "SUCCESS"),
    ]
    assert not deck.ws.hpcmu_dir.exists()


# -- postprocess() --------------------------------------------------------


def test_postprocess_postprocess_error_logs_warning_and_returns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = Workspace.at(tmp_path)

    with caplog.at_level(
        logging.WARNING, logger="hpc_model_utils.core.lifecycle.toolbox"
    ):
        postprocess(ws, _PostprocessErrorPlugin())

    assert "postprocess failed: nwlistcf option 1 exited 3" in caplog.text


def test_postprocess_oserror_propagates(tmp_path: Path) -> None:
    ws = Workspace.at(tmp_path)

    with pytest.raises(OSError, match="disk full"):
        postprocess(ws, _OSErrorPostprocessPlugin())


# -- diagnose_workspace() (shared seam, R31) ------------------------------


def test_diagnose_workspace_fake_plugin_matches_former_inline_evaluate_call(
    tmp_path: Path,
) -> None:
    ws = _bare_fake_workspace(tmp_path)
    plugin = FakePlugin()
    report = JobReport(outcome=None, process_exit=None, log_paths=())

    direct = evaluate(
        report,
        log_patterns=plugin.log_patterns,
        primary_evidence=plugin.primary_evidence(ws),
        rules=lambda: plugin.diagnose(ws, report),
        job_id="42",
    )
    via_seam = diagnose_workspace(ws, plugin, report, job_id="42")

    assert dataclasses.replace(via_seam, at="") == dataclasses.replace(
        direct, at=""
    )
