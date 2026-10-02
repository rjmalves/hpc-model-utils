"""ADR-003/R26/R27: the ModelPlugin contract and the explicit registry."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hpc_model_utils import models
from hpc_model_utils.core.diagnosis import JobReport, RunStatus, Verdict
from hpc_model_utils.core.errors import UsageError
from hpc_model_utils.core.launch import Launcher, LaunchSpec, Resources
from hpc_model_utils.core.outputs import OutputPlan
from hpc_model_utils.core.plugin import ExecutableSpec, ModelPlugin, ParentRun
from hpc_model_utils.core.state import StudyInfo
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.models import get_plugin
from tests.support.fake_plugin import (
    FakePlugin,
    fake_registered,
    install_fake_model,
    register_fake,
)


class _MinimalPlugin(ModelPlugin):
    name = "minimal"
    executables = ExecutableSpec(entrypoint="minimal-model")

    def study_info(self, ws: Workspace) -> StudyInfo:
        return StudyInfo(name="minimal", starting_date="2026-01-01")

    def launch(self, ws: Workspace, res: Resources) -> LaunchSpec:
        return LaunchSpec(Launcher.DIRECT, (str(ws.assets / "minimal-model"),))

    def primary_evidence(self, ws: Workspace) -> tuple[Path, ...]:
        return (ws.root / "minimal.out",)

    def diagnose(self, ws: Workspace, job: JobReport) -> Verdict:
        return Verdict(RunStatus.SUCCESS, "core.fake", "ok")

    def input_files(self, ws: Workspace) -> tuple[str, ...]:
        return ()

    def outputs(self, ws: Workspace) -> OutputPlan:
        return OutputPlan(deck_inputs=(), groups=(), raw=())


class _MissingDiagnose(ModelPlugin):
    name = "missing"
    executables = ExecutableSpec(entrypoint="missing-model")

    def study_info(self, ws: Workspace) -> StudyInfo:
        return StudyInfo(name="missing", starting_date="2026-01-01")

    def launch(self, ws: Workspace, res: Resources) -> LaunchSpec:
        return LaunchSpec(Launcher.DIRECT, (str(ws.assets / "missing-model"),))

    def primary_evidence(self, ws: Workspace) -> tuple[Path, ...]:
        return ()

    def input_files(self, ws: Workspace) -> tuple[str, ...]:
        return ()

    def outputs(self, ws: Workspace) -> OutputPlan:
        return OutputPlan(deck_inputs=(), groups=(), raw=())


def _job_report() -> JobReport:
    return JobReport(outcome=None, process_exit=0, log_paths=())


def _parent_run() -> ParentRun:
    return ParentRun(
        uri="s3://bucket/key",
        model_name="newave",
        starting_date="2026-01-01",
        archives=(),
    )


# -- ABC enforcement ---------------------------------------------------


def test_modelplugin_missing_abstract_method_instantiate_raises_type_error() -> (
    None
):
    with pytest.raises(TypeError, match="diagnose"):
        _MissingDiagnose()  # type: ignore[abstract]


def test_modelplugin_has_no_supports_offline_attribute() -> None:
    assert hasattr(ModelPlugin, "supports_offline") is False


# -- __init_subclass__ validation --------------------------------------


def test_init_subclass_bad_name_raises_type_error() -> None:
    with pytest.raises(TypeError, match="name"):

        class _BadName(ModelPlugin):
            name = "Bad-Name"
            executables = ExecutableSpec(entrypoint="x")


def test_init_subclass_trailing_newline_name_raises_type_error() -> None:
    with pytest.raises(TypeError, match="name"):

        class _TrailingNewlineName(ModelPlugin):
            name = "fake\n"
            executables = ExecutableSpec(entrypoint="x")


def test_init_subclass_missing_executables_raises_type_error() -> None:
    with pytest.raises(TypeError, match="executables"):

        class _MissingExecutables(ModelPlugin):
            name = "goodname"


def test_init_subclass_wrong_type_executables_raises_type_error() -> None:
    with pytest.raises(TypeError, match="executables"):

        class _WrongTypeExecutables(ModelPlugin):
            name = "goodname"
            executables = "not-a-spec"  # type: ignore[assignment]


# -- default methods ----------------------------------------------------


def test_fetch_parent_default_raises_usage_error(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    with pytest.raises(UsageError, match="fake does not support"):
        FakePlugin().fetch_parent(ws, _parent_run())


def test_ingest_offline_default_raises_usage_error(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    with pytest.raises(UsageError, match="fake does not support"):
        FakePlugin().ingest_offline(ws, ())


def test_prepare_default_is_noop(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    _MinimalPlugin().prepare(ws, "exec-1")


def test_postprocess_default_is_noop(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    _MinimalPlugin().postprocess(ws)


def test_check_executables_default_is_noop(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    _MinimalPlugin().check_executables(ws)


def test_synthesis_args_default_returns_none() -> None:
    assert _MinimalPlugin().synthesis_args(4) is None


def test_synthesis_args_fake_returns_tuple() -> None:
    assert FakePlugin().synthesis_args(4) == (
        "completa",
        "--processadores",
        "4",
    )


def test_model_name_property_returns_uppercase_name() -> None:
    assert FakePlugin().model_name == "FAKE"


# -- registry -------------------------------------------------------------


def test_get_plugin_case_insensitive_returns_fake_instance() -> None:
    with fake_registered():
        plugin = get_plugin("FAKE")
    assert isinstance(plugin, FakePlugin)


def test_get_plugin_unknown_name_raises_usage_error() -> None:
    with pytest.raises(UsageError, match=r"valid: \[\]"):
        get_plugin("fake")


def test_get_plugin_after_fake_registered_restores_registry() -> None:
    with fake_registered():
        get_plugin("fake")
    with pytest.raises(UsageError, match=r"valid: \[\]"):
        get_plugin("fake")


def test_register_fake_returns_previous_mapping() -> None:
    previous = register_fake()
    try:
        assert dict(previous) == {}
        assert "fake" in models.PLUGINS
    finally:
        models.PLUGINS = previous


def test_plugins_mapping_item_assignment_raises_type_error() -> None:
    with pytest.raises(TypeError):
        models.PLUGINS["fake"] = FakePlugin()  # type: ignore[index]


# -- FakePlugin -------------------------------------------------------------


def test_fakeplugin_launch_returns_direct_spec(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    spec = FakePlugin().launch(ws, Resources(queue="batch", cores=1))
    assert spec == LaunchSpec(
        Launcher.DIRECT, (str(ws.assets / "fake-model"),), ntasks=1
    )


def test_fakeplugin_primary_evidence_returns_fake_out_path(
    tmp_path: Path,
) -> None:
    ws = Workspace(tmp_path)
    assert FakePlugin().primary_evidence(ws) == (ws.root / "fake.out",)


def test_fakeplugin_input_files_returns_deck_txt(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    assert FakePlugin().input_files(ws) == ("deck.txt",)


def test_fakeplugin_outputs_returns_expected_plan(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    plan = FakePlugin().outputs(ws)
    assert plan.deck_inputs == ("deck.txt",)
    assert len(plan.groups) == 1
    group = plan.groups[0]
    assert group.archive == "fake.zip"
    assert group.select.matches("model.fake") is True
    assert group.select.matches("model.txt") is False
    assert len(plan.raw) == 1
    assert plan.raw[0].select.matches("fake.out") is True


def test_fakeplugin_study_info_returns_fixed_value(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    info = FakePlugin().study_info(ws)
    assert info == StudyInfo(name="fake", starting_date="2026-01-01")


def test_fakeplugin_diagnose_unknown_token_returns_unknown(
    tmp_path: Path,
) -> None:
    ws = Workspace(tmp_path)
    (ws.root / "fake.out").write_text("GARBAGE\n", encoding="utf-8")
    verdict = FakePlugin().diagnose(ws, _job_report())
    assert verdict.status is RunStatus.UNKNOWN
    assert verdict.rule_id == "core.fake"


def test_fakeplugin_diagnose_missing_file_raises_file_not_found_error(
    tmp_path: Path,
) -> None:
    ws = Workspace(tmp_path)
    with pytest.raises(FileNotFoundError):
        FakePlugin().diagnose(ws, _job_report())


# -- install_fake_model ------------------------------------------------


def test_install_fake_model_runs_under_bash_produces_outputs(
    tmp_path: Path,
) -> None:
    ws = Workspace(tmp_path)
    script = install_fake_model(
        ws, token="SUCCESS", extra_lines=("hello world",)
    )
    result = subprocess.run(
        ["bash", str(script)],
        cwd=ws.root,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "hello world" in result.stdout
    assert (ws.root / "fake.out").read_text(encoding="utf-8") == "SUCCESS\n"
    assert (ws.root / "model.fake").is_file()
    verdict = FakePlugin().diagnose(ws, _job_report())
    assert verdict.status is RunStatus.SUCCESS


def test_install_fake_model_exit_code_propagates(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    script = install_fake_model(ws, exit_code=7)
    result = subprocess.run(
        ["bash", str(script)],
        cwd=ws.root,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 7
