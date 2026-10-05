"""ADR-003/ADR-051/ADR-011/R20/R28/R54/R93/R100: ``DecompPlugin``, the
second real ``ModelPlugin`` (ticket-050).

The deck and launch shape are the only concerns here; ``diagnose``,
``outputs`` and ``prepare``'s FC cut coupling each delegate to their
own sibling module (``diagnosis``/``outputs``/``cuts``, tickets
051/052/050a). ``prepare`` accepts ``--execution-name`` but applies
nothing to the deck (R54): the dadger TE title stays as delivered, and
dadger is never opened for writing (R20/D1). ``fetch_parent`` is a
validated no-op: FC, resolved only during ``preprocess``, is ADR-016's
single source of truth for which NEWAVE cut members get extracted.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import ClassVar

from hpc_model_utils.core.diagnosis import JobReport, Verdict
from hpc_model_utils.core.launch import Launcher, LaunchSpec, Resources
from hpc_model_utils.core.outputs import OutputPlan
from hpc_model_utils.core.plugin import ExecutableSpec, ModelPlugin, ParentRun
from hpc_model_utils.core.state import StudyInfo
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.models.decomp import cuts, deck, diagnosis, outputs

logger = logging.getLogger(__name__)


class DecompPlugin(ModelPlugin):
    name = "decomp"
    executables = ExecutableSpec(
        "decomp",
        ("decomp.lic", "ddsDECOMP.cep", "decomp.cep", "decomp_trial.cep"),
        "convertenomesdecomp",
    )
    sanitize_exclude: ClassVar[tuple[str, ...]] = (
        r"cortes[^/]*\.dat",
        r"[^/]+\.zip",
        r"hidr\.dat",
        r"mlt\.dat",
        r"vazoes\.[^/]+",
    )
    output_patterns: ClassVar[tuple[str, ...]] = (
        r"(?:relato|relato2|inviab_unic|inviab|sumario|relgnl|custos|"
        r"cortdeco|mapcut)\.[^/]+",
        r"decomp\.tim",
    )
    log_patterns = diagnosis.LOG_PATTERNS
    parent_model = "NEWAVE"
    parent_artifacts: ClassVar[tuple[str, ...]] = ("cortes.zip",)
    always_write_parent_path = False

    def study_info(self, ws: Workspace) -> StudyInfo:
        return deck.study_info(ws)

    def input_files(self, ws: Workspace) -> tuple[str, ...]:
        return deck.input_files(ws)

    def prepare(self, ws: Workspace, execution_name: str) -> None:
        logger.info(
            "--execution-name %r accepted but not applied: the dadger "
            "TE title stays as delivered (R54, R20)",
            execution_name,
        )
        cuts.apply_coupling(ws)

    def launch(self, ws: Workspace, res: Resources) -> LaunchSpec:
        return LaunchSpec(
            Launcher.MPIEXEC_HYDRA,
            (str(ws.assets / "decomp"),),
            ntasks=res.cores,
            ntasks_per_node=res.max_cores_per_node,
        )

    def synthesis_args(
        self, ws: Workspace, cpus: int
    ) -> tuple[str, ...] | None:
        return ("completa", "--processadores", str(cpus))

    def primary_evidence(self, ws: Workspace) -> tuple[Path, ...]:
        return diagnosis.primary_evidence(ws)

    def diagnose(self, ws: Workspace, job: JobReport) -> Verdict:
        return diagnosis.diagnose(ws, job)

    def outputs(self, ws: Workspace) -> OutputPlan:
        return outputs.output_plan(ws)

    def fetch_parent(self, ws: Workspace, parent: ParentRun) -> None:
        names = tuple(
            archive.name
            for _, archive in zip(
                self.parent_artifacts, parent.archives, strict=True
            )
        )
        logger.info(
            "FC-named cut files are extracted from %s during preprocess "
            "(ticket-050a)",
            names,
        )
