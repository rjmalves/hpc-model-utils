"""ADR-003/ADR-002/R26/R27/R48/R73/R94: the ModelPlugin contract.

G2 (R3) confines model-specific code to four things: deck handling,
the launch shape, status rules and output classification. Plugins are
stateless instances, registered explicitly in ``hpc_model_utils.models``
(R27, no entry points, no module scanning); this module must not
import ``models`` (ADR-002) -- the registry lives there, and core
receives plugin instances as arguments.

Two sketch signatures (design Sec.4.2, R26) are deliberately adjusted
here, both recorded per the ticket's Definition of Done:

- ``diagnose`` returns the L3 ``Verdict`` rather than the full
  ``Diagnosis``; ``core.diagnosis.evaluate`` composes the ``Diagnosis``
  from it.
- ``postprocess(ws)`` drops the sketch's unused ``Resources``
  parameter (YAGNI: no plugin needs it there).

No capability flag beyond the ADR-003 members is added (DES-08): a
plugin that supports offline ingestion says so by overriding
``ingest_offline``, and offline finalize-only runs are decided from
``state.execution_source`` elsewhere.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from hpc_model_utils.core.diagnosis import JobReport, LogPattern, Verdict
from hpc_model_utils.core.errors import UsageError
from hpc_model_utils.core.launch import LaunchSpec, Resources
from hpc_model_utils.core.outputs import OutputPlan
from hpc_model_utils.core.state import StudyInfo
from hpc_model_utils.core.workspace import Workspace

_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]*")


class PostprocessError(Exception):
    """Plugin-contract signal for a recordable ``postprocess`` failure
    (R137/ADR-053, additive to ADR-003): ``finalize`` keeps the run's
    SUCCESS status and records the failure loudly, and the C2 toolbox
    logs it and exits 0. Deliberately not an ``HpcmuError`` -- this is
    a plugin-layer contract type, not a CLI-fatal one. Its message
    must never contain an absolute path.
    """


@dataclass(frozen=True, slots=True)
class ExecutableSpec:
    """Executable names, relative to ``ws.assets``."""

    entrypoint: str
    license_files: tuple[str, ...] = ()
    name_normalizer: str | None = None


@dataclass(frozen=True, slots=True)
class ParentRun:
    uri: str
    model_name: str
    starting_date: str
    archives: tuple[Path, ...]


class ModelPlugin(ABC):
    """The model-specific extension point (R26/ADR-003).

    ``__init_subclass__`` validates every concrete subclass at class
    creation time. ``type.__new__`` invokes it before ``ABCMeta``
    computes ``__abstractmethods__`` for the new class, so
    ``inspect.isabstract(cls)`` would read stale state here; this
    design has no intermediate abstract bases, so ``name`` and
    ``executables`` are validated on every subclass unconditionally.
    A subclass that still leaves an abstract method unimplemented is
    caught separately, by ``ABCMeta`` itself, at instantiation.
    """

    name: ClassVar[str]
    executables: ClassVar[ExecutableSpec]
    sanitize_encoding: ClassVar[bool] = True
    sanitize_exclude: ClassVar[tuple[str, ...]] = ()
    output_patterns: ClassVar[tuple[str, ...]] = ()
    log_patterns: ClassVar[tuple[LogPattern, ...]] = ()
    parent_model: ClassVar[str | None] = None
    parent_artifacts: ClassVar[tuple[str, ...]] = ()
    always_write_parent_path: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        name = getattr(cls, "name", None)
        if not isinstance(name, str) or _NAME_PATTERN.fullmatch(name) is None:
            raise TypeError(
                f"{cls.__name__}.name must match "
                f"{_NAME_PATTERN.pattern!r}: {name!r}"
            )
        executables = getattr(cls, "executables", None)
        if not isinstance(executables, ExecutableSpec):
            raise TypeError(
                f"{cls.__name__}.executables must be an ExecutableSpec, "
                f"got {type(executables).__name__}"
            )

    @property
    def model_name(self) -> str:
        return self.name.upper()

    @abstractmethod
    def study_info(self, ws: Workspace) -> StudyInfo: ...

    @abstractmethod
    def launch(self, ws: Workspace, res: Resources) -> LaunchSpec: ...

    @abstractmethod
    def primary_evidence(self, ws: Workspace) -> tuple[Path, ...]: ...

    @abstractmethod
    def diagnose(self, ws: Workspace, job: JobReport) -> Verdict: ...

    @abstractmethod
    def input_files(self, ws: Workspace) -> tuple[str, ...]: ...

    @abstractmethod
    def outputs(self, ws: Workspace) -> OutputPlan: ...

    def fetch_parent(self, ws: Workspace, parent: ParentRun) -> None:
        raise UsageError(f"{self.name} does not support parent runs")

    def prepare(self, ws: Workspace, execution_name: str) -> None:
        return None

    def postprocess(self, ws: Workspace) -> None:
        return None

    def synthesis_args(
        self, ws: Workspace, cpus: int
    ) -> tuple[str, ...] | None:
        return None

    def check_executables(self, ws: Workspace) -> None:
        return None

    def ingest_offline(self, ws: Workspace, archives: tuple[Path, ...]) -> None:
        raise UsageError(f"{self.name} does not support offline ingestion")
