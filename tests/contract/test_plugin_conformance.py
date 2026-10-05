"""ADR-003/ADR-014/ADR-043/R16/R26/R27/R82: the cross-plugin
conformance contract (ticket-055b).

``core/plugin.py::ModelPlugin.__init_subclass__`` validates only
``name`` and ``executables``. This module checks every invariant a
registered plugin must hold for core to stay model-agnostic, through
each plugin's public contract only -- no test may branch on a
plugin's name. ``CONFORMANCE_WORKSPACES`` is the extension point a
new plugin (ticket-072's cobre) must register a builder in before its
own invariants are exercised here.

Amended at dispatch (2026-10-03): ``input_files`` is first-seen order,
not sorted (ticket-046/050 ship it that way), and an entry may be
absent from the deck (ticket-046 Decision B's optional
``arquivos.dat`` attributes). The two amended checks below replace
the outline's "sorted" and "names existing files" clauses.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import ClassVar

import pytest

from hpc_model_utils.core.diagnosis import JobReport, RunStatus, Verdict
from hpc_model_utils.core.launch import Launcher, LaunchSpec, Resources
from hpc_model_utils.core.lifecycle.finalize import diagnose_workspace
from hpc_model_utils.core.outputs import OutputPlan, realize
from hpc_model_utils.core.plugin import ExecutableSpec, ModelPlugin
from hpc_model_utils.core.state import StudyInfo
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.models import PLUGINS, get_plugin
from tests.support.cobre_case import cobre_workspace, write_elf_stub
from tests.support.decks import decomp_workspace, newave_workspace


def _cobre_conformance_workspace(p: Path) -> Workspace:
    ws = cobre_workspace(p).ws
    write_elf_stub(ws.assets)
    return ws


CONFORMANCE_WORKSPACES: Mapping[str, Callable[[Path], Workspace]] = {
    "cobre": _cobre_conformance_workspace,
    "decomp": lambda p: decomp_workspace(p).ws,
    "newave": lambda p: newave_workspace(p).ws,
}

_FIXED_JOB_REPORT = JobReport(outcome=None, process_exit=0, log_paths=())


@pytest.fixture(params=sorted(PLUGINS))
def plugin_case(
    request: pytest.FixtureRequest, tmp_path: Path
) -> tuple[ModelPlugin, Workspace]:
    name: str = request.param
    return PLUGINS[name], CONFORMANCE_WORKSPACES[name](tmp_path)


@pytest.fixture(params=sorted(PLUGINS))
def empty_plugin_case(
    request: pytest.FixtureRequest, tmp_path: Path
) -> tuple[ModelPlugin, Workspace]:
    name: str = request.param
    root = tmp_path / "empty"
    root.mkdir()
    return PLUGINS[name], Workspace.at(root)


# -- assertion helpers, one per invariant ----------------------------------


def _assert_registry_declarations(key: str, plugin: ModelPlugin) -> None:
    assert plugin.name == key
    assert get_plugin(key.upper()) is plugin


def _assert_plain_basename(value: str, label: str) -> None:
    assert "/" not in value, f"{label} must be a basename, no '/': {value!r}"
    assert value != "..", f"{label} must not be '..': {value!r}"


def _assert_executable_names_are_safe(spec: ExecutableSpec) -> None:
    _assert_plain_basename(spec.entrypoint, "entrypoint")
    if spec.name_normalizer is not None:
        _assert_plain_basename(spec.name_normalizer, "name_normalizer")
    for license_file in spec.license_files:
        _assert_plain_basename(license_file, "license_files entry")


def _assert_log_pattern_namespace(plugin: ModelPlugin) -> None:
    prefix = f"{plugin.name}."
    for pattern in plugin.log_patterns:
        assert pattern.rule_id.startswith(prefix), (
            f"{pattern.rule_id!r} does not start with {prefix!r}"
        )


def _assert_parent_pairing(plugin: ModelPlugin) -> None:
    assert (plugin.parent_model is None) == (plugin.parent_artifacts == ())
    if plugin.parent_model is not None:
        assert plugin.parent_model.lower() in PLUGINS


def _assert_study_info_tz_aware(plugin: ModelPlugin, ws: Workspace) -> None:
    info = plugin.study_info(ws)
    parsed = datetime.fromisoformat(info.starting_date)
    assert parsed.tzinfo is not None


def _assert_input_files_well_formed(plugin: ModelPlugin, ws: Workspace) -> None:
    """Amended invariant (dispatch note above): unique, deterministic
    and non-empty, every entry a safe relative path, every *existing*
    entry a regular file, with at least one entry existing -- order is
    plugin-defined and an entry may legitimately be absent."""
    first = plugin.input_files(ws)
    assert first == plugin.input_files(ws)
    assert first, "input_files must be non-empty"
    assert len(first) == len(set(first)), "input_files must be unique"
    found_existing = False
    for entry in first:
        pure = PurePosixPath(entry)
        assert not pure.is_absolute(), f"unsafe input_files entry: {entry!r}"
        assert ".." not in pure.parts, f"unsafe input_files entry: {entry!r}"
        path = ws.root / entry
        if path.exists():
            assert path.is_file() and not path.is_symlink(), (
                f"input_files entry exists but is not a regular file: {entry!r}"
            )
            found_existing = True
    assert found_existing, "input_files must have at least one existing entry"


def _assert_purge_disjoint(plugin: ModelPlugin, ws: Workspace) -> None:
    for pattern in plugin.output_patterns:
        for entry in plugin.input_files(ws):
            assert re.fullmatch(pattern, entry, re.IGNORECASE) is None, (
                f"output_patterns {pattern!r} matches input_files entry "
                f"{entry!r}"
            )


def _assert_outputs_deterministic(plugin: ModelPlugin, ws: Workspace) -> None:
    assert plugin.outputs(ws) == plugin.outputs(ws)


def _assert_deck_inputs_match_input_files(
    plugin: ModelPlugin, ws: Workspace
) -> None:
    assert set(plugin.outputs(ws).deck_inputs) == set(plugin.input_files(ws))


def _assert_realize_produces_deck(plugin: ModelPlugin, ws: Workspace) -> None:
    realized = realize(plugin.outputs(ws), ws, workers=1)
    assert realized.deck is not None


def _assert_chaining(plugin: ModelPlugin, parent_plan: OutputPlan) -> None:
    archive_names = {group.archive for group in parent_plan.groups}
    missing = set(plugin.parent_artifacts) - archive_names
    assert not missing, (
        f"{plugin.name}.parent_artifacts not in parent groups: "
        f"{sorted(missing)}"
    )


def _assert_fail_closed(plugin: ModelPlugin, ws: Workspace) -> None:
    evidence_paths = plugin.primary_evidence(ws)
    assert evidence_paths, "primary_evidence must return at least one path"
    inside_root = any(path.is_relative_to(ws.root) for path in evidence_paths)
    assert inside_root, "primary_evidence must return a path inside ws.root"

    diag = diagnose_workspace(ws, plugin, _FIXED_JOB_REPORT, job_id="1")
    assert diag.status is not RunStatus.SUCCESS

    root_str = str(ws.root)
    for item in diag.evidence:
        assert root_str not in item.source, item.source
        assert root_str not in item.detail, item.detail


def _assert_synthesis_args_shape(plugin: ModelPlugin) -> None:
    args = plugin.synthesis_args(4)
    assert args is None or (args and all(args))


def _assert_launch_returns_spec(plugin: ModelPlugin, ws: Workspace) -> None:
    spec = plugin.launch(ws, Resources("q", 4, max_cores_per_node=2))
    assert isinstance(spec, LaunchSpec)


# -- test-local probes for the two production helpers above ----------------


class _StaleOutputProbe(ModelPlugin):
    """AC2's broken probe: ``caso.dat`` is both a declared input and
    something the purge would remove, so it must trip
    ``_assert_purge_disjoint``."""

    name = "stale_output_probe"
    executables = ExecutableSpec(entrypoint="probe-model")
    output_patterns: ClassVar[tuple[str, ...]] = (r"caso\.dat",)

    def study_info(self, ws: Workspace) -> StudyInfo:
        return StudyInfo(name="probe", starting_date="2026-01-01")

    def launch(self, ws: Workspace, res: Resources) -> LaunchSpec:
        return LaunchSpec(Launcher.DIRECT, (str(ws.assets / "probe-model"),))

    def primary_evidence(self, ws: Workspace) -> tuple[Path, ...]:
        return (ws.root / "probe.out",)

    def diagnose(self, ws: Workspace, job: JobReport) -> Verdict:
        return Verdict(RunStatus.SUCCESS, "core.probe", "ok")

    def input_files(self, ws: Workspace) -> tuple[str, ...]:
        return ("caso.dat",)

    def outputs(self, ws: Workspace) -> OutputPlan:
        return OutputPlan(deck_inputs=self.input_files(ws), groups=(), raw=())


class _OptimisticProbe(ModelPlugin):
    """The fail-closed-helper probe: no evidence at all, yet
    ``diagnose`` claims success. Must trip ``_assert_fail_closed``."""

    name = "optimistic_probe"
    executables = ExecutableSpec(entrypoint="probe-model")

    def study_info(self, ws: Workspace) -> StudyInfo:
        return StudyInfo(name="probe", starting_date="2026-01-01")

    def launch(self, ws: Workspace, res: Resources) -> LaunchSpec:
        return LaunchSpec(Launcher.DIRECT, (str(ws.assets / "probe-model"),))

    def primary_evidence(self, ws: Workspace) -> tuple[Path, ...]:
        return ()

    def diagnose(self, ws: Workspace, job: JobReport) -> Verdict:
        return Verdict(RunStatus.SUCCESS, "core.probe", "ok")

    def input_files(self, ws: Workspace) -> tuple[str, ...]:
        return ()

    def outputs(self, ws: Workspace) -> OutputPlan:
        return OutputPlan(deck_inputs=(), groups=(), raw=())


def test_assert_purge_disjoint_stale_output_probe_raises_naming_caso_dat(
    tmp_path: Path,
) -> None:
    with pytest.raises(AssertionError, match="caso.dat"):
        _assert_purge_disjoint(_StaleOutputProbe(), Workspace(tmp_path))


def test_assert_fail_closed_optimistic_probe_raises_on_empty_evidence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    with pytest.raises(AssertionError, match="primary_evidence"):
        _assert_fail_closed(_OptimisticProbe(), Workspace.at(root))


# -- AC1: declarations -------------------------------------------------


@pytest.mark.parametrize("name", sorted(PLUGINS))
def test_plugin_declarations_registry_key_and_get_plugin_agree(
    name: str,
) -> None:
    _assert_registry_declarations(name, PLUGINS[name])


@pytest.mark.parametrize("name", sorted(PLUGINS))
def test_plugin_declarations_executable_names_are_plain_basenames(
    name: str,
) -> None:
    _assert_executable_names_are_safe(PLUGINS[name].executables)


@pytest.mark.parametrize("name", sorted(PLUGINS))
def test_plugin_declarations_log_patterns_share_plugin_namespace(
    name: str,
) -> None:
    _assert_log_pattern_namespace(PLUGINS[name])


@pytest.mark.parametrize("name", sorted(PLUGINS))
def test_plugin_declarations_parent_pairing_resolves_in_registry(
    name: str,
) -> None:
    _assert_parent_pairing(PLUGINS[name])


def test_plugin_declarations_parent_model_newave_resolves_to_newave_plugin() -> (
    None
):
    assert PLUGINS["newave"].parent_model == "NEWAVE"
    assert PLUGINS["newave"].parent_model.lower() in PLUGINS
    assert PLUGINS[PLUGINS["newave"].parent_model.lower()] is PLUGINS["newave"]


# -- AC2: deck contract --------------------------------------------------


def test_plugin_conformance_study_info_starting_date_is_tz_aware(
    plugin_case: tuple[ModelPlugin, Workspace],
) -> None:
    plugin, ws = plugin_case
    _assert_study_info_tz_aware(plugin, ws)


def test_plugin_conformance_input_files_unique_deterministic_and_safe(
    plugin_case: tuple[ModelPlugin, Workspace],
) -> None:
    plugin, ws = plugin_case
    _assert_input_files_well_formed(plugin, ws)


def test_plugin_conformance_output_patterns_never_match_input_files(
    plugin_case: tuple[ModelPlugin, Workspace],
) -> None:
    plugin, ws = plugin_case
    _assert_purge_disjoint(plugin, ws)


# -- AC2: output contract -------------------------------------------------


def test_plugin_conformance_outputs_plan_is_deterministic(
    plugin_case: tuple[ModelPlugin, Workspace],
) -> None:
    plugin, ws = plugin_case
    _assert_outputs_deterministic(plugin, ws)


def test_plugin_conformance_deck_inputs_equal_input_files_as_sets(
    plugin_case: tuple[ModelPlugin, Workspace],
) -> None:
    plugin, ws = plugin_case
    _assert_deck_inputs_match_input_files(plugin, ws)


def test_plugin_conformance_realize_produces_non_none_deck(
    plugin_case: tuple[ModelPlugin, Workspace],
) -> None:
    plugin, ws = plugin_case
    _assert_realize_produces_deck(plugin, ws)


# -- AC3: chaining (ADR-043) ----------------------------------------------


def test_plugin_conformance_parent_artifacts_are_parent_plan_archive_names(
    plugin_case: tuple[ModelPlugin, Workspace], tmp_path: Path
) -> None:
    plugin, _ = plugin_case
    if plugin.parent_model is None:
        return
    parent = PLUGINS[plugin.parent_model.lower()]
    parent_ws = CONFORMANCE_WORKSPACES[parent.name](tmp_path / "parent")
    _assert_chaining(plugin, parent.outputs(parent_ws))


# -- AC4: fail-closed diagnosis -------------------------------------------


def test_plugin_conformance_fail_closed_on_empty_workspace(
    empty_plugin_case: tuple[ModelPlugin, Workspace],
) -> None:
    plugin, ws = empty_plugin_case
    _assert_fail_closed(plugin, ws)


def test_plugin_conformance_fail_closed_on_deck_only_workspace(
    plugin_case: tuple[ModelPlugin, Workspace],
) -> None:
    plugin, ws = plugin_case
    _assert_fail_closed(plugin, ws)


# -- AC4: optional hooks ---------------------------------------------------


def test_plugin_conformance_synthesis_args_none_or_nonempty_strings(
    plugin_case: tuple[ModelPlugin, Workspace],
) -> None:
    plugin, _ = plugin_case
    _assert_synthesis_args_shape(plugin)


def test_plugin_conformance_launch_returns_launch_spec(
    plugin_case: tuple[ModelPlugin, Workspace],
) -> None:
    plugin, ws = plugin_case
    _assert_launch_returns_spec(plugin, ws)


# -- AC4: builder coverage -------------------------------------------------


def test_conformance_workspaces_builder_coverage_matches_plugins() -> None:
    missing = sorted(set(PLUGINS) - set(CONFORMANCE_WORKSPACES))
    assert not missing, (
        f"register a conformance workspace builder for: {', '.join(missing)}"
    )
