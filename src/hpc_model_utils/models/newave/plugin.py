"""ADR-003/ADR-051/ADR-011/R16/R19/R21/R28/R90/R93/R100: ``NewavePlugin``,
the first real ``ModelPlugin`` (ticket-046).

The deck and launch shape are the only concerns here; ``diagnose``,
``outputs``, ``postprocess`` and ``ingest_offline`` each delegate to
their own sibling module (``diagnosis``/``outputs``/``postprocess``/
``offline``, tickets 047/048/048a/049).
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
from hpc_model_utils.infra.archive import extract
from hpc_model_utils.models.newave import (
    deck,
    diagnosis,
    offline,
    outputs,
    postprocess,
)

logger = logging.getLogger(__name__)

# R90: the fixed per-archive member selection fetch_parent applies to
# each of ``parent_artifacts``; ``None`` means "every member".
_PARENT_ARCHIVE_MEMBERS: dict[str, frozenset[str] | None] = {
    "cortes.zip": None,
    "recursos.zip": frozenset(
        {
            "engthd.dat",
            "engfiobac.dat",
            "engfio.dat",
            "engfiob.dat",
            "engnat.dat",
            "engcont.dat",
            "vazthd.dat",
            "vazinat.dat",
        }
    ),
    "simulacao.zip": frozenset({"newdesp.dat"}),
}


class NewavePlugin(ModelPlugin):
    name = "newave"
    executables = ExecutableSpec(
        "newave",
        ("newave.lic", "ddsNEWAVE.cep", "newave.cep", "newave_trial.cep"),
        "ConverteNomesArquivos",
    )
    sanitize_exclude: ClassVar[tuple[str, ...]] = (
        r"cortes[^/]*\.dat",
        r"[^/]+\.zip",
        r"hidr\.dat",
        r"vazoes\.dat",
        r"postos\.dat",
    )
    output_patterns: ClassVar[tuple[str, ...]] = (
        r"pmo\.dat",
        r"parp\.dat",
        r"newave\.tim",
    )
    log_patterns = diagnosis.LOG_PATTERNS
    parent_model = "NEWAVE"
    parent_artifacts: ClassVar[tuple[str, ...]] = (
        "cortes.zip",
        "recursos.zip",
        "simulacao.zip",
    )
    always_write_parent_path = True

    def study_info(self, ws: Workspace) -> StudyInfo:
        return deck.study_info(ws)

    def input_files(self, ws: Workspace) -> tuple[str, ...]:
        return deck.input_files(ws)

    def prepare(self, ws: Workspace, execution_name: str) -> None:
        deck.set_process_manager(ws)
        deck.set_title(ws, execution_name)

    def launch(self, ws: Workspace, res: Resources) -> LaunchSpec:
        return LaunchSpec(
            Launcher.MPIEXEC_HYDRA,
            (str(ws.assets / "newave"),),
            ntasks=res.cores,
            ntasks_per_node=res.max_cores_per_node,
            cpus_per_task=2,
        )

    def synthesis_args(self, cpus: int) -> tuple[str, ...] | None:
        return ("completa", "--processadores", str(cpus))

    def primary_evidence(self, ws: Workspace) -> tuple[Path, ...]:
        return diagnosis.primary_evidence(ws)

    def diagnose(self, ws: Workspace, job: JobReport) -> Verdict:
        return diagnosis.diagnose(ws, job)

    def outputs(self, ws: Workspace) -> OutputPlan:
        return outputs.output_plan(ws)

    def postprocess(self, ws: Workspace) -> None:
        postprocess.run_postprocess(ws)

    def fetch_parent(self, ws: Workspace, parent: ParentRun) -> None:
        for name, archive_path in zip(
            self.parent_artifacts, parent.archives, strict=True
        ):
            extracted = extract(
                archive_path, ws.root, members=_PARENT_ARCHIVE_MEMBERS[name]
            )
            logger.info("fetch_parent extracted %s: %s", name, extracted)

    def ingest_offline(self, ws: Workspace, archives: tuple[Path, ...]) -> None:
        offline.ingest_offline(self, ws, archives)
