"""Capture the frozen v1.1.2 projection goldens from the real legacy code.

ADR-048 (ticket-007) freezes the `metadata.modelops` and `status.modelops`
bytes that the real `app/` code (unchanged since tag v1.1.2) produces, before
ticket-057 deletes `app/`. `projections.json` is the frozen output: it is
never regenerated from v2 code. This script is deleted alongside `app/` in
ticket-057; git history keeps it.

Run: `uv run python tests/goldens/v1_1_2/capture.py`
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import zipfile
from collections.abc import Callable
from logging import Logger, getLogger
from os.path import join
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, PropertyMock, patch

from app.adapter.repository.decomp import DECOMP
from app.adapter.repository.newave import NEWAVE
from app.utils.constants import (
    METADATA_FILE,
    METADATA_MODEL_NAME,
    METADATA_STUDY_NAME,
    OUTPUTS_PREFIX,
    RAW_DECK_FILE,
    STATUS_DIAGNOSIS_FILE,
    SYNTHESIS_DIR,
)
from app.utils.s3 import path_to_bucket_and_key

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "decks"
OUTPUT_PATH = Path(__file__).resolve().parent / "projections.json"
ARCHIVES_OUTPUT_PATH = Path(__file__).resolve().parent / "archives.json"

# ADR-048 (ticket-008): the fixed result_upload() destination used to derive
# the "uploads" golden, with its key stripped from each recorded S3 key.
ARCHIVE_UPLOAD_PATH = "s3://outputs-bucket/artifacts/hash01/"
ARCHIVE_UPLOAD_KEY_PREFIX = "artifacts/hash01/"

# F13 (plans/v2-architecture/design/cluster-facts.md, ANSWERED):
# check_and_fetch_executables runs before ingest_offline_run in prd.
UPLOAD_NEWAVE_ORDER = "executables-first"

DownloadFn = Callable[[str, str, str, Logger], list[str]]
SequenceBody = Callable[[contextlib.ExitStack, Path], list[str]]


def _key(uri: str) -> str:
    return path_to_bucket_and_key(uri)["key"]


def _fake_download(mapping: dict[str, list[Path]]) -> DownloadFn:
    def _download(
        bucket: str, destination: str, key: str, logger: Logger
    ) -> list[str]:
        dest_dir = Path(destination)
        dest_dir.mkdir(parents=True, exist_ok=True)
        paths: list[str] = []
        for source in mapping[key]:
            dest = dest_dir / source.name
            dest.write_bytes(source.read_bytes())
            paths.append(str(dest))
        return paths

    return _download


def _placeholder_file(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"placeholder")
    return path


def _placeholder_zip(
    directory: Path, name: str, members: dict[str, bytes]
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    with zipfile.ZipFile(path, "w") as zf:
        for member_name, content in members.items():
            zf.writestr(member_name, content)
    return path


def _offline_archives(staging: Path) -> dict[str, Path]:
    """Build the three offline-upload archives from the NEWAVE fixture deck.

    `inputs.zip` holds every deck member, `outputs.zip` holds one `pmo.dat`
    placeholder and `cortes.zip` holds one `cortesh.dat` placeholder.
    """
    deck = FIXTURES_DIR / "deck_newave.zip"
    with zipfile.ZipFile(deck) as source:
        members = {name: source.read(name) for name in source.namelist()}
    return {
        "inputs": _placeholder_zip(staging, "inputs.zip", members),
        "outputs": _placeholder_zip(
            staging, "outputs.zip", {"pmo.dat": b"placeholder"}
        ),
        "cortes": _placeholder_zip(
            staging, "cortes.zip", {"cortesh.dat": b"placeholder"}
        ),
    }


def _extract_bare_newave_deck(destination: Path) -> None:
    with zipfile.ZipFile(FIXTURES_DIR / "deck_newave.zip") as zf:
        zf.extractall(destination)


def _patch_newave_success(stack: contextlib.ExitStack) -> None:
    pmo = SimpleNamespace(convergencia=1, custo_operacao_series_simuladas=100.0)
    stack.enter_context(
        patch.object(NEWAVE, "pmo", new_callable=PropertyMock, return_value=pmo)
    )


def _patch_decomp_success(stack: contextlib.ExitStack) -> None:
    for name in ("relato", "inviab_unic", "inviab"):
        stack.enter_context(
            patch.object(
                DECOMP,
                name,
                new_callable=PropertyMock,
                return_value=MagicMock(),
            )
        )
    for name in (
        "_evaluate_data_error",
        "_evaluate_max_iterations",
        "_evaluate_feasibility",
        "_evaluate_negative_gap",
        "_evaluate_relato_outputs",
    ):
        stack.enter_context(patch.object(DECOMP, name, return_value=False))


def run_sequence(
    name: str, body: SequenceBody, staging: Path
) -> dict[str, Any]:
    original_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix=f"hpcmu-{name}-") as tmpdir:
        os.chdir(tmpdir)
        try:
            NEWAVE.DECK_DATA_CACHING.clear()
            DECOMP.DECK_DATA_CACHING.clear()
            with contextlib.ExitStack() as stack:
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                stack.enter_context(
                    patch("app.adapter.repository.newave.cast_encoding_to_utf8")
                )
                stack.enter_context(
                    patch("app.adapter.repository.decomp.cast_encoding_to_utf8")
                )
                commands = body(stack, staging / name)
            metadata_text = Path(METADATA_FILE).read_text(encoding="ascii")
            status_text = Path(STATUS_DIAGNOSIS_FILE).read_text(
                encoding="ascii"
            )
        finally:
            os.chdir(original_cwd)
    return {
        "commands": commands,
        "metadata.modelops": metadata_text,
        "status.modelops": status_text,
    }


def _newave_no_parent(stack: contextlib.ExitStack, staging: Path) -> list[str]:
    executables_path = "s3://versions-bucket/versoes/newave/30.0.4/"
    inputs_path = "s3://inputs-bucket/ingest/deck_newave.zip"
    executable = _placeholder_file(staging, "executable")
    stack.enter_context(
        patch(
            "app.adapter.repository.newave.check_and_download_bucket_items",
            side_effect=_fake_download(
                {
                    _key(executables_path): [executable],
                    _key(inputs_path): [FIXTURES_DIR / "deck_newave.zip"],
                }
            ),
        )
    )
    stack.enter_context(
        patch("app.adapter.repository.newave.check_and_delete_bucket_item")
    )
    _patch_newave_success(stack)

    model = NEWAVE(getLogger("capture"))
    model.check_and_fetch_executables(executables_path)
    model.check_and_fetch_inputs(inputs_path, "", delete=False)
    model.extract_sanitize_inputs()
    model.generate_execution_status("1001")
    return [
        f'check_and_fetch_executables("{executables_path}")',
        f'check_and_fetch_inputs("{inputs_path}", "", delete=False)',
        "extract_sanitize_inputs()",
        'generate_execution_status("1001")',
    ]


def _newave_with_parent(
    stack: contextlib.ExitStack, staging: Path
) -> list[str]:
    executables_path = "s3://versions-bucket/versoes/newave/30.0.4/"
    inputs_path = "s3://inputs-bucket/ingest/deck_newave.zip"
    parent_path = "s3://outputs-bucket/artifacts/parenthash01"
    parent_metadata = {
        "model_name": "NEWAVE",
        "status": "SUCCESS",
        "study_starting_date": "2025-10-01T00:00:00+00:00",
    }
    executable = _placeholder_file(staging, "executable")
    parent_key = _key(parent_path)
    cortes = _placeholder_zip(staging, "cortes.zip", {})
    recursos = _placeholder_zip(staging, "recursos.zip", {})
    simulacao = _placeholder_zip(staging, "simulacao.zip", {})
    stack.enter_context(
        patch(
            "app.adapter.repository.newave.check_and_download_bucket_items",
            side_effect=_fake_download(
                {
                    _key(executables_path): [executable],
                    _key(inputs_path): [FIXTURES_DIR / "deck_newave.zip"],
                    join(parent_key, OUTPUTS_PREFIX, NEWAVE.CUT_FILE): [cortes],
                    join(parent_key, OUTPUTS_PREFIX, NEWAVE.RESOURCES_FILE): [
                        recursos
                    ],
                    join(parent_key, OUTPUTS_PREFIX, NEWAVE.SIMULATION_FILE): [
                        simulacao
                    ],
                }
            ),
        )
    )
    stack.enter_context(
        patch("app.adapter.repository.newave.check_and_delete_bucket_item")
    )
    stack.enter_context(
        patch(
            "app.adapter.repository.newave.check_and_get_bucket_item",
            lambda bucket, filepath, logger: json.dumps(parent_metadata),
        )
    )
    _patch_newave_success(stack)

    model = NEWAVE(getLogger("capture"))
    model.check_and_fetch_executables(executables_path)
    model.check_and_fetch_inputs(inputs_path, parent_path, delete=False)
    model.extract_sanitize_inputs()
    model.generate_execution_status("1001")
    return [
        f'check_and_fetch_executables("{executables_path}")',
        (
            f'check_and_fetch_inputs("{inputs_path}", "{parent_path}", '
            "delete=False)"
        ),
        "extract_sanitize_inputs()",
        'generate_execution_status("1001")',
    ]


def _decomp_no_parent(stack: contextlib.ExitStack, staging: Path) -> list[str]:
    executables_path = "s3://versions-bucket/versoes/decomp/32.0/"
    inputs_path = "s3://inputs-bucket/ingest/deck_decomp.zip"
    executable = _placeholder_file(staging, "executable")
    stack.enter_context(
        patch(
            "app.adapter.repository.decomp.check_and_download_bucket_items",
            side_effect=_fake_download(
                {
                    _key(executables_path): [executable],
                    _key(inputs_path): [FIXTURES_DIR / "deck_decomp.zip"],
                }
            ),
        )
    )
    stack.enter_context(
        patch("app.adapter.repository.decomp.check_and_delete_bucket_item")
    )
    _patch_decomp_success(stack)

    model = DECOMP(getLogger("capture"))
    model.check_and_fetch_executables(executables_path)
    model.check_and_fetch_inputs(inputs_path, "", delete=False)
    model.extract_sanitize_inputs()
    model.generate_execution_status("2001")
    return [
        f'check_and_fetch_executables("{executables_path}")',
        f'check_and_fetch_inputs("{inputs_path}", "", delete=False)',
        "extract_sanitize_inputs()",
        'generate_execution_status("2001")',
    ]


def _decomp_with_parent(
    stack: contextlib.ExitStack, staging: Path
) -> list[str]:
    executables_path = "s3://versions-bucket/versoes/decomp/32.0/"
    inputs_path = "s3://inputs-bucket/ingest/deck_decomp.zip"
    parent_path = "s3://outputs-bucket/artifacts/parenthash02"
    parent_metadata = {
        "model_name": "NEWAVE",
        "status": "SUCCESS",
        "study_starting_date": "2025-11-01T00:00:00+00:00",
    }
    executable = _placeholder_file(staging, "executable")
    parent_key = _key(parent_path)
    cortes = _placeholder_zip(
        staging,
        "cortes.zip",
        {
            "cortesh.dat": b"placeholder",
            "cortes.dat": b"placeholder",
            "cortes-012.dat": b"placeholder",
        },
    )
    stack.enter_context(
        patch(
            "app.adapter.repository.decomp.check_and_download_bucket_items",
            side_effect=_fake_download(
                {
                    _key(executables_path): [executable],
                    _key(inputs_path): [FIXTURES_DIR / "deck_decomp.zip"],
                    join(parent_key, OUTPUTS_PREFIX, DECOMP.CUT_FILE): [cortes],
                }
            ),
        )
    )
    stack.enter_context(
        patch("app.adapter.repository.decomp.check_and_delete_bucket_item")
    )
    stack.enter_context(
        patch(
            "app.adapter.repository.decomp.check_and_get_bucket_item",
            lambda bucket, filepath, logger: json.dumps(parent_metadata),
        )
    )
    _patch_decomp_success(stack)

    model = DECOMP(getLogger("capture"))
    model.check_and_fetch_executables(executables_path)
    model.check_and_fetch_inputs(inputs_path, parent_path, delete=False)
    model.extract_sanitize_inputs()
    model.generate_execution_status("2001")
    return [
        f'check_and_fetch_executables("{executables_path}")',
        (
            f'check_and_fetch_inputs("{inputs_path}", "{parent_path}", '
            "delete=False)"
        ),
        "extract_sanitize_inputs()",
        'generate_execution_status("2001")',
    ]


def _newave_offline(stack: contextlib.ExitStack, staging: Path) -> list[str]:
    executables_path = "s3://versions-bucket/versoes/newave/30.0.4/"
    inputs_path = "s3://inputs-bucket/ingest/offline/inputs.zip"
    outputs_path = "s3://inputs-bucket/ingest/offline/outputs.zip"
    cortes_path = "s3://inputs-bucket/ingest/offline/cortes.zip"
    executable = _placeholder_file(staging, "executable")
    archives = _offline_archives(staging)
    stack.enter_context(
        patch(
            "app.adapter.repository.newave.check_and_download_bucket_items",
            side_effect=_fake_download(
                {
                    _key(executables_path): [executable],
                    _key(inputs_path): [archives["inputs"]],
                    _key(outputs_path): [archives["outputs"]],
                    _key(cortes_path): [archives["cortes"]],
                }
            ),
        )
    )
    _patch_newave_success(stack)

    model = NEWAVE(getLogger("capture"))
    # F13 (ANSWERED, cluster-facts.md): executables fetched before the
    # offline run is ingested.
    model.check_and_fetch_executables(executables_path)
    model.ingest_offline_run(inputs_path, outputs_path, cortes_path)
    model.generate_execution_status("1005")
    return [
        f'check_and_fetch_executables("{executables_path}")',
        (
            f'ingest_offline_run("{inputs_path}", "{outputs_path}", '
            f'"{cortes_path}")'
        ),
        'generate_execution_status("1005")',
    ]


def _newave_toolbox_bare(
    stack: contextlib.ExitStack, staging: Path
) -> list[str]:
    _extract_bare_newave_deck(Path.cwd())
    _patch_newave_success(stack)
    model = NEWAVE(getLogger("capture"))
    model.generate_execution_status("777")
    return ['generate_execution_status("777")']


def _newave_toolbox_no_job_id(
    stack: contextlib.ExitStack, staging: Path
) -> list[str]:
    _extract_bare_newave_deck(Path.cwd())
    _patch_newave_success(stack)
    model = NEWAVE(getLogger("capture"))
    model.generate_execution_status("")
    return ['generate_execution_status("")']


def _newave_toolbox_existing_metadata(
    stack: contextlib.ExitStack, staging: Path
) -> list[str]:
    _extract_bare_newave_deck(Path.cwd())
    Path(METADATA_FILE).write_text(
        json.dumps(
            {
                METADATA_MODEL_NAME: "NEWAVE",
                METADATA_STUDY_NAME: "old",
            }
        ),
        encoding="ascii",
    )
    _patch_newave_success(stack)
    model = NEWAVE(getLogger("capture"))
    model.generate_execution_status("778")
    return [
        (
            'metadata.modelops pre-seeded with {"model_name": "NEWAVE", '
            '"study_name": "old"}'
        ),
        'generate_execution_status("778")',
    ]


_SEQUENCE_BODIES: dict[str, SequenceBody] = {
    "newave_no_parent": _newave_no_parent,
    "newave_with_parent": _newave_with_parent,
    "decomp_no_parent": _decomp_no_parent,
    "decomp_with_parent": _decomp_with_parent,
    "newave_offline": _newave_offline,
    "newave_toolbox_bare": _newave_toolbox_bare,
    "newave_toolbox_no_job_id": _newave_toolbox_no_job_id,
    "newave_toolbox_existing_metadata": _newave_toolbox_existing_metadata,
}

# ADR-048 (ticket-008): synthetic output files written over each extracted
# fixture deck before `output_compression_and_cleanup(2)` runs, so the real
# legacy archive-grouping and cleanup logic has something to sort through.
_NEWAVE_ARCHIVE_SYNTHETIC_OUTPUTS: tuple[str, ...] = (
    "pmo.dat",
    "parp.dat",
    "simfinal.dat",
    "newave.tim",
    "prociter.rel",
    "mensagens.csv",
    "runstate.dat",
    "alertainv001.rel",
    "nwv_eco_evap.csv",
    "cortes.dat",
    "cortesh.dat",
    "cortes-001.dat",
    "cortes-012.dat",
    "arquivos-nwlistcf.dat",
    "nwlistcf.rel",
    "cortese.dat",
    "cortese-001.dat",
    "estados.rel",
    "forward.dat",
    "forwarh.dat",
    "newdesp.dat",
    "planej.dat",
    "saida.rel",
    "energiaf001.dat",
    "vazaof001.dat",
    "engnat.dat",
    "vazthd.dat",
    "mlt.dat",
    "nwlistop.dat",
    "cmarg001.out",
    "MEDIAS-MERC.CSV",
    "svc0001",
    "fort.10",
    "format.tmp",
    "ETAPA.TMP",
    "out/cmarg002.out",
    "log/newave_2025.log",
    "extra_output.dat",
)

_DECOMP_ARCHIVE_SYNTHETIC_OUTPUTS: tuple[str, ...] = (
    "relato.rv0",
    "relato2.rv0",
    "sumario.rv0",
    "inviab_unic.rv0",
    "inviab.rv0",
    "relgnl.rv0",
    "custos.rv0",
    "decomp.tim",
    "cortdeco.rv0",
    "mapcut.rv0",
    "dec_oper_usih.csv",
    "dec_oper_sist.csv",
    "cmar001.csv",
    "energia.rv0",
    "osl_001",
    "deco_001.msg",
    "dimpl_001",
    "cusfut.rv0",
    "deconf.rv0",
    "CONVERG.TMP",
    "out/relato_extra.rv0",
)


def _write_synthetic_outputs(names: tuple[str, ...]) -> None:
    for name in names:
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x\n")


def _emulate_namecast(cwd: Path) -> None:
    """Stand in for the CEPEL ``ConverteNomesArquivos``/``convertenomesdecomp``
    binaries, which a real run's `extract_sanitize_inputs` uses to lowercase
    deck filenames and which are not available locally: renames every
    extracted deck file in `cwd` whose name has uppercase characters to its
    lowercase name. `RAW_DECK_FILE` (already lowercase) is left untouched.
    """
    entries = [
        p for p in cwd.iterdir() if p.is_file() and p.name != RAW_DECK_FILE
    ]
    renames = {
        p.name: p.name.lower() for p in entries if p.name != p.name.lower()
    }
    existing_names = {p.name for p in entries}
    targets_seen: dict[str, str] = {}
    for old_name, new_name in renames.items():
        collision_source = targets_seen.get(new_name)
        if collision_source is not None:
            raise ValueError(
                "Namecast emulation collision: "
                f"{old_name!r} and {collision_source!r} both lowercase to "
                f"{new_name!r}"
            )
        if new_name in existing_names and new_name not in renames:
            raise ValueError(
                f"Namecast emulation collision: {old_name!r} would "
                f"overwrite existing {new_name!r}"
            )
        targets_seen[new_name] = old_name
    for old_name, new_name in renames.items():
        (cwd / old_name).rename(cwd / new_name)


def _record_archives(cwd: Path) -> dict[str, list[str]]:
    archives: dict[str, list[str]] = {}
    for zip_path in sorted(cwd.glob("*.zip")):
        with zipfile.ZipFile(zip_path) as zf:
            archives[zip_path.name] = sorted(zf.namelist())
    return archives


def _record_remaining(cwd: Path) -> list[str]:
    return sorted(p.name for p in cwd.iterdir() if p.is_file())


def _capture_model_archives(
    model_cls: type[NEWAVE] | type[DECOMP],
    module_path: str,
    deck_filename: str,
    synthetic_outputs: tuple[str, ...],
) -> dict[str, Any]:
    original_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(
        prefix=f"hpcmu-archives-{model_cls.MODEL_NAME}-"
    ) as tmpdir:
        cwd = Path(tmpdir)
        os.chdir(cwd)
        try:
            model_cls.DECK_DATA_CACHING.clear()
            deck_path = FIXTURES_DIR / deck_filename
            # Emulates check_and_fetch_inputs' move(filename, RAW_DECK_FILE):
            # a real run always leaves the raw deck archive in place.
            (cwd / RAW_DECK_FILE).write_bytes(deck_path.read_bytes())
            with zipfile.ZipFile(deck_path) as zf:
                zf.extractall(cwd)
            _emulate_namecast(cwd)
            _write_synthetic_outputs(synthetic_outputs)

            model = model_cls(getLogger("capture"))
            recorded_uploads: list[str] = []

            def _record_upload(
                local_filepath: str,
                destination_bucket: str,
                remote_filepath: str,
                aws_access_key_id: str | None = None,
                aws_secret_access_key: str | None = None,
            ) -> None:
                recorded_uploads.append(remote_filepath)

            with contextlib.redirect_stdout(io.StringIO()):
                model.output_compression_and_cleanup(2)
                archives = _record_archives(cwd)
                remaining = _record_remaining(cwd)

                Path(METADATA_FILE).write_text(
                    json.dumps({"status": "SUCCESS"}), encoding="ascii"
                )
                synthesis_dir = Path(SYNTHESIS_DIR)
                synthesis_dir.mkdir(parents=True, exist_ok=True)
                (synthesis_dir / "example.parquet").write_bytes(b"x\n")

                with patch(
                    f"{module_path}.upload_file_to_bucket", new=_record_upload
                ):
                    model.result_upload(ARCHIVE_UPLOAD_PATH)
        finally:
            os.chdir(original_cwd)

    uploads = sorted(
        key.removeprefix(ARCHIVE_UPLOAD_KEY_PREFIX) for key in recorded_uploads
    )
    return {
        "synthetic_outputs": sorted(synthetic_outputs),
        "archives": archives,
        "remaining": remaining,
        "uploads": uploads,
    }


def capture_archives() -> dict[str, Any]:
    return {
        "newave": _capture_model_archives(
            NEWAVE,
            "app.adapter.repository.newave",
            "deck_newave.zip",
            _NEWAVE_ARCHIVE_SYNTHETIC_OUTPUTS,
        ),
        "decomp": _capture_model_archives(
            DECOMP,
            "app.adapter.repository.decomp",
            "deck_decomp.zip",
            _DECOMP_ARCHIVE_SYNTHETIC_OUTPUTS,
        ),
    }


def main() -> None:
    with tempfile.TemporaryDirectory(
        prefix="hpcmu-goldens-staging-"
    ) as staging_dir:
        staging = Path(staging_dir)
        sequences = {
            name: run_sequence(name, body, staging)
            for name, body in _SEQUENCE_BODIES.items()
        }

    projections_obj = {
        "_meta": {
            "legacy_version": "1.1.2",
            "script": "tests/goldens/v1_1_2/capture.py",
            "upload_newave_order": UPLOAD_NEWAVE_ORDER,
        },
        "sequences": sequences,
    }
    archives_obj = capture_archives()

    # Both documents are built in memory above before either is written, so
    # a legacy exception never leaves a partial golden on disk.
    OUTPUT_PATH.write_text(
        json.dumps(projections_obj, indent=2, ensure_ascii=True, sort_keys=True)
        + "\n",
        encoding="ascii",
    )
    ARCHIVES_OUTPUT_PATH.write_text(
        json.dumps(archives_obj, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )


if __name__ == "__main__":
    main()
