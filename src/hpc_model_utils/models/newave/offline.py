"""ADR-003/ADR-011/R10/R93/R96: the NEWAVE offline-run ingestion body
(v1's ``NEWAVE.ingest_offline_run``,
``app/adapter/repository/newave.py:1018-1125``), reusing
``core.lifecycle.prepare``'s extraction/normalizer/sanitize/licence
helpers (ticket-045) and ``deck``'s readers/writers (ticket-046) --
never duplicating either.

The ``eco_deck.zip`` echo is rebuilt from the deck's own
``input_files`` *before* ``deck.set_process_manager`` points
``caso.dat`` at ``assets/`` (v1 order): the echo must carry the
offline deck's original ``caso.dat``. Unlike ``prepare``, there is no
stale-output purge here (the outputs archive holds this run's real
outputs) and no title change (the offline study name is preserved).

Only the deck's input files are encoding-sanitized, never the extracted
outputs: those include multi-GB binaries that ``sanitize_file`` reads
whole, which exhausts the head node's memory and kills the ingest. A
cluster run never sanitizes its outputs either.
"""

from __future__ import annotations

from pathlib import Path

from hpc_model_utils.core.lifecycle import prepare
from hpc_model_utils.core.plugin import ModelPlugin
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.infra.archive import write_flat
from hpc_model_utils.models.newave import deck


def ingest_offline(
    plugin: ModelPlugin, ws: Workspace, archives: tuple[Path, ...]
) -> None:
    for archive in archives:
        prepare.extract_archive(ws, archive)

    staging_dir = archives[0].parent
    for archive in archives:
        archive.unlink()
    if not any(staging_dir.iterdir()):
        staging_dir.rmdir()

    prepare.run_name_normalizer(ws, plugin)
    input_files = deck.input_files(ws)
    prepare.sanitize_files(ws, plugin, input_files)
    prepare.move_licences(ws, plugin)

    write_flat(
        ws.eco_deck_path,
        [ws.root / name for name in input_files if (ws.root / name).is_file()],
    )
    deck.set_process_manager(ws)
