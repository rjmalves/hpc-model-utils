"""ADR-003/R27: the fake ``ModelPlugin`` that drives the E2E/system
tests for the execution-engine epic (ticket-034..042).

Decisions the ticket left open:

- ``study_info`` returns a fixed, deterministic ``StudyInfo``: no
  system test feeds the fake plugin a real deck to derive one from.
- ``diagnose`` lets a missing ``fake.out`` raise (``FileNotFoundError``
  propagates from ``Path.read_text``) rather than returning
  ``RunStatus.UNKNOWN``. ``core.diagnosis.evaluate`` already produces
  a guard ``Verdict`` for a missing primary-evidence file before L3
  (this plugin's ``diagnose``) ever runs, and when L3 does run,
  ``evaluate``'s own wrapper degrades any exception it raises to
  ``UNKNOWN`` -- that is the one sanctioned broad catch in
  ``core.diagnosis``. A direct, un-guarded call (as in this module's
  own unit tests) is the only place the raw exception is observable,
  and surfacing it there is more useful than masking it a second time.
"""

from __future__ import annotations

import contextlib
import shlex
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType

from hpc_model_utils import models
from hpc_model_utils.core.diagnosis import JobReport, RunStatus, Verdict
from hpc_model_utils.core.launch import Launcher, LaunchSpec, Resources
from hpc_model_utils.core.outputs import OutputPlan, RawFile, Selector, ZipGroup
from hpc_model_utils.core.plugin import ExecutableSpec, ModelPlugin
from hpc_model_utils.core.state import StudyInfo
from hpc_model_utils.core.workspace import Workspace


class FakePlugin(ModelPlugin):
    name = "fake"
    executables = ExecutableSpec(entrypoint="fake-model")

    def study_info(self, ws: Workspace) -> StudyInfo:
        return StudyInfo(name="fake", starting_date="2026-01-01")

    def launch(self, ws: Workspace, res: Resources) -> LaunchSpec:
        return LaunchSpec(
            Launcher.DIRECT,
            (str(ws.assets / self.executables.entrypoint),),
            ntasks=1,
        )

    def primary_evidence(self, ws: Workspace) -> tuple[Path, ...]:
        return (ws.root / "fake.out",)

    def diagnose(self, ws: Workspace, job: JobReport) -> Verdict:
        first_line = (
            (ws.root / "fake.out").read_text(encoding="utf-8").splitlines()[0]
        )
        return Verdict(
            RunStatus.parse(first_line),
            "core.fake",
            f"fake.out token {first_line!r}",
        )

    def input_files(self, ws: Workspace) -> tuple[str, ...]:
        return ("deck.txt",)

    def outputs(self, ws: Workspace) -> OutputPlan:
        return OutputPlan(
            deck_inputs=self.input_files(ws),
            groups=(ZipGroup("fake.zip", Selector(patterns=(r".*\.fake",))),),
            raw=(RawFile(Selector(names=("fake.out",))),),
        )

    def synthesis_args(self, cpus: int) -> tuple[str, ...] | None:
        return ("completa", "--processadores", str(cpus))


def install_fake_model(
    ws: Workspace,
    *,
    token: str = "SUCCESS",
    exit_code: int = 0,
    sleep: float = 0.0,
    extra_lines: Sequence[str] = (),
) -> Path:
    """Write an executable bash script at ``ws.assets / "fake-model"``.

    Running it, in order: prints each of ``extra_lines`` to stdout (so
    they reach the job log), sleeps ``sleep`` seconds, writes ``token``
    as the first line of ``ws.root / "fake.out"``, writes one
    ``model.fake`` file directly under ``ws.root`` (so the ``fake.zip``
    output group is non-empty), then exits with ``exit_code``. Every
    interpolated value is ``shlex``-quoted.
    """
    ws.assets.mkdir(parents=True, exist_ok=True)
    fake_out = ws.root / "fake.out"
    fake_file = ws.root / "model.fake"
    lines = ["#!/bin/bash", "set -u"]
    lines += [f"printf '%s\\n' {shlex.quote(line)}" for line in extra_lines]
    lines.append(f"sleep {shlex.quote(str(sleep))}")
    lines.append(
        f"printf '%s\\n' {shlex.quote(token)} > {shlex.quote(str(fake_out))}"
    )
    lines.append(f": > {shlex.quote(str(fake_file))}")
    lines.append(f"exit {shlex.quote(str(exit_code))}")
    script = ws.assets / "fake-model"
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def register_fake() -> Mapping[str, ModelPlugin]:
    previous = models.PLUGINS
    models.PLUGINS = MappingProxyType({FakePlugin.name: FakePlugin()})
    return previous


@contextlib.contextmanager
def fake_registered() -> Iterator[None]:
    previous = register_fake()
    try:
        yield
    finally:
        models.PLUGINS = previous
