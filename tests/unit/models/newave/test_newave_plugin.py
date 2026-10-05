"""ADR-003/ADR-051/ADR-011/R16/R19/R21/R28/R90/R93/R100 tests for the
NEWAVE plugin package (ticket-046)."""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from hpc_model_utils import models
from hpc_model_utils.core.diagnosis import Diagnosis, RunStatus
from hpc_model_utils.core.errors import DataError
from hpc_model_utils.core.launch import (
    Launcher,
    LaunchSpec,
    Resources,
    Toolchain,
    render_model_script,
)
from hpc_model_utils.core.lifecycle.fetch import fetch_executables, fetch_inputs
from hpc_model_utils.core.lifecycle.prepare import extract_sanitize_inputs
from hpc_model_utils.core.plugin import ExecutableSpec, ParentRun
from hpc_model_utils.core.state import StateStore, StudyInfo, render_metadata
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.infra.s3 import S3Uri
from hpc_model_utils.models import get_plugin
from hpc_model_utils.models.indices import read_index_libraries
from hpc_model_utils.models.newave import NewavePlugin, deck
from hpc_model_utils.models.newave import diagnosis as newave_diagnosis
from tests.support.decks import (
    FIXTURES,
    decomp_workspace,
    input_zip,
    newave_workspace,
)
from tests.support.object_store import RecordingObjectStore
from tests.support.reporter import RecordingReporter

_GOLDEN_PATH = (
    Path(__file__).resolve().parents[3]
    / "goldens"
    / "v2"
    / "sbatch"
    / "model_mpiexec.sbatch"
)
_PROJECTIONS_PATH = (
    Path(__file__).resolve().parents[3]
    / "goldens"
    / "v1_1_2"
    / "projections.json"
)
_STUDY_NAME = "PMO - NOVEMBRO - 2025 - Niveis para 01/11 - NW Versao 30.0.4"
_STUDY_STARTING_DATE = "2025-11-01T00:00:00+00:00"

_VERSIONS_BUCKET = "versions-bucket"
_INPUTS_BUCKET = "inputs-bucket"
_OUTPUTS_BUCKET = "outputs-bucket"
_VERSOES_URI = f"s3://{_VERSIONS_BUCKET}/versoes/newave/30.0.4/"
_INPUT_URI = f"s3://{_INPUTS_BUCKET}/ingest/deck_newave.zip"
_PARENT_URI = f"s3://{_OUTPUTS_BUCKET}/artifacts/parenthash01"


def _namecast(root: Path) -> None:
    """Mirror the CEPEL ``ConverteNomesArquivos`` stand-in described in
    ``tests/goldens/v1_1_2/README.md``'s ``_emulate_namecast``."""
    for entry in list(root.iterdir()):
        if entry.is_file() and entry.name != entry.name.lower():
            entry.rename(root / entry.name.lower())


def _load_golden_metadata(sequence: str) -> str:
    data = json.loads(_PROJECTIONS_PATH.read_text(encoding="utf-8"))
    return str(data["sequences"][sequence]["metadata.modelops"])


def _empty_zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w"):
        pass
    return buf.getvalue()


# -- deck.py readers ---------------------------------------------------


def test_caso_fixture_returns_object_with_arquivos_dat(tmp_path: Path) -> None:
    dws = newave_workspace(tmp_path)
    assert deck.caso(dws.ws).arquivos == "arquivos.dat"


def test_caso_missing_file_raises_data_error(tmp_path: Path) -> None:
    ws = Workspace.at(tmp_path)
    with pytest.raises(DataError, match="caso.dat"):
        deck.caso(ws)


def test_index_name_fixture_returns_arquivos_dat(tmp_path: Path) -> None:
    dws = newave_workspace(tmp_path)
    assert deck.index_name(dws.ws) == "arquivos.dat"


def test_index_name_empty_value_raises_data_error(tmp_path: Path) -> None:
    ws = Workspace.at(tmp_path)
    (ws.root / "caso.dat").write_text("\n\n", encoding="utf-8")
    with pytest.raises(DataError, match="caso.dat"):
        deck.index_name(ws)


def test_arquivos_fixture_returns_object_with_dger_dat(tmp_path: Path) -> None:
    dws = newave_workspace(tmp_path)
    assert deck.arquivos(dws.ws).dger == "dger.dat"


def test_arquivos_missing_index_file_raises_data_error(tmp_path: Path) -> None:
    dws = newave_workspace(tmp_path)
    (dws.ws.root / "arquivos.dat").unlink()
    with pytest.raises(DataError, match="arquivos.dat"):
        deck.arquivos(dws.ws)


def test_dger_name_fixture_returns_dger_dat(tmp_path: Path) -> None:
    dws = newave_workspace(tmp_path)
    assert deck.dger_name(dws.ws) == "dger.dat"


def test_dger_fixture_returns_object_with_expected_fields(
    tmp_path: Path,
) -> None:
    dws = newave_workspace(tmp_path)
    info = deck.dger(dws.ws)
    assert info.nome_caso == _STUDY_NAME
    assert info.ano_inicio_estudo == 2025
    assert info.mes_inicio_estudo == 11


def test_dger_missing_file_raises_data_error(tmp_path: Path) -> None:
    dws = newave_workspace(tmp_path)
    (dws.ws.root / "dger.dat").unlink()
    with pytest.raises(DataError, match="dger.dat"):
        deck.dger(dws.ws)


def test_study_info_fixture_returns_expected_value(tmp_path: Path) -> None:
    dws = newave_workspace(tmp_path)
    assert deck.study_info(dws.ws) == StudyInfo(
        name=_STUDY_NAME, starting_date=_STUDY_STARTING_DATE
    )


def test_study_info_empty_workspace_raises_data_error_matching_caso_dat(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    with pytest.raises(DataError, match="caso.dat"):
        deck.study_info(ws)


def test_study_info_falsy_year_raises_data_error(tmp_path: Path) -> None:
    from inewave.newave import Dger

    dws = newave_workspace(tmp_path)
    dger_path = dws.ws.root / "dger.dat"
    info = Dger.read(str(dger_path))
    info.ano_inicio_estudo = 0
    info.write(str(dger_path))
    with pytest.raises(DataError, match="study year or month"):
        deck.study_info(dws.ws)


def test_read_index_libraries_newave_fixture_returns_three_entries(
    tmp_path: Path,
) -> None:
    dws = newave_workspace(tmp_path)
    result = read_index_libraries(dws.ws.root / "indices.csv")
    assert result == (
        "restricao-eletrica.csv",
        "polinjus.csv",
        "volumes-referencia.csv",
    )


def test_read_index_libraries_decomp_fixture_dedups_renovaveis(
    tmp_path: Path,
) -> None:
    dws = decomp_workspace(tmp_path)
    result = read_index_libraries(dws.ws.root / "indices.csv")
    assert result == ("polinjus.csv", "renovaveis.csv")


def test_read_index_libraries_short_line_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "indices.csv"
    path.write_text("A;B\nC;D;E\n", encoding="utf-8")
    assert read_index_libraries(path) == ("E",)


# -- deck.py writers (AC3) ----------------------------------------------


def test_set_process_manager_fixture_sets_gerenciador_processos(
    tmp_path: Path,
) -> None:
    dws = newave_workspace(tmp_path)
    ws = dws.ws
    deck.set_process_manager(ws)
    assert deck.caso(ws).gerenciador_processos == f"{ws.assets}/"


def test_set_title_truncates_to_80_chars_and_encodes_utf8(
    tmp_path: Path,
) -> None:
    dws = newave_workspace(tmp_path)
    ws = dws.ws
    name = "Á" * 40 + "ç\"'$" + "x" * 80
    assert len(name) == 124
    deck.set_title(ws, name)
    dger_path = ws.root / deck.dger_name(ws)
    dger_path.read_bytes().decode("utf-8")
    assert deck.dger(ws).nome_caso == name[:80]


# -- plugin.py ------------------------------------------------------------


def test_newaveplugin_declared_attributes_match_requirement_4() -> None:
    plugin = NewavePlugin()
    assert plugin.name == "newave"
    assert plugin.executables == ExecutableSpec(
        "newave",
        ("newave.lic", "ddsNEWAVE.cep", "newave.cep", "newave_trial.cep"),
        "ConverteNomesArquivos",
    )
    assert plugin.sanitize_exclude == (
        r"cortes[^/]*\.dat",
        r"[^/]+\.zip",
        r"hidr\.dat",
        r"vazoes\.dat",
        r"postos\.dat",
    )
    assert plugin.output_patterns == (r"pmo\.dat", r"parp\.dat", r"newave\.tim")
    assert plugin.log_patterns == newave_diagnosis.LOG_PATTERNS
    assert plugin.parent_model == "NEWAVE"
    assert plugin.parent_artifacts == (
        "cortes.zip",
        "recursos.zip",
        "simulacao.zip",
    )
    assert plugin.always_write_parent_path is True


def test_get_plugin_both_cases_return_same_registered_instance() -> None:
    assert get_plugin("NEWAVE") is get_plugin("newave")
    assert get_plugin("newave") is models.PLUGINS["newave"]
    assert isinstance(get_plugin("newave"), NewavePlugin)


def test_import_models_package_does_not_import_inewave() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, hpc_model_utils.models\n"
            "assert 'inewave' not in sys.modules\n",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_newaveplugin_launch_with_max_cores_per_node_sets_ntasks_per_node(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    res = Resources(
        queue="batch", cores=64, max_cores_per_node=32, time_limit_hours=24
    )
    spec = NewavePlugin().launch(ws, res)
    assert spec == LaunchSpec(
        Launcher.MPIEXEC_HYDRA,
        (str(ws.assets / "newave"),),
        ntasks=64,
        ntasks_per_node=32,
        cpus_per_task=2,
    )


def test_newaveplugin_launch_without_max_cores_per_node_omits_ntasks_per_node(
    tmp_path: Path,
) -> None:
    ws = Workspace.at(tmp_path)
    res = Resources(queue="batch", cores=4)
    spec = NewavePlugin().launch(ws, res)
    assert spec.ntasks_per_node is None
    assert spec.prepend_mpich_lib is False
    assert spec.env == {}


def test_newaveplugin_synthesis_args_returns_expected_tuple(
    tmp_path: Path,
) -> None:
    assert NewavePlugin().synthesis_args(Workspace.at(tmp_path), 12) == (
        "completa",
        "--processadores",
        "12",
    )


# -- AC2: the regenerable NEWAVE-shaped golden ---------------------------


def test_render_model_script_newave_launch_matches_golden(
    tmp_path: Path,
) -> None:
    root = tmp_path / "study-newave"
    root.mkdir()
    ws = Workspace.at(root)
    tools = Toolchain(
        mpich_bin=Path("/opt/mpich/bin"),
        slurm_bin=Path("/opt/slurm/bin"),
        cli_bin=Path("/opt/hpcmu/bin"),
    )
    res = Resources(
        queue="batch", cores=64, max_cores_per_node=32, time_limit_hours=24
    )
    spec = NewavePlugin().launch(ws, res)
    rendered = render_model_script(ws, spec, res, tools)
    normalized = rendered.replace(str(ws.root), "<WS>")

    expected = _GOLDEN_PATH.read_text(encoding="utf-8")
    assert normalized == expected
    assert "LD_LIBRARY_PATH" not in rendered


# -- AC3: prepare (R19, O14) ----------------------------------------------


def test_newaveplugin_prepare_sets_process_manager_and_title(
    tmp_path: Path,
) -> None:
    dws = newave_workspace(tmp_path)
    ws = dws.ws
    before = deck.dger(ws)
    name = "Á" * 40 + "ç\"'$" + "x" * 80
    assert len(name) == 124

    NewavePlugin().prepare(ws, name)

    assert deck.caso(ws).gerenciador_processos == f"{ws.assets}/"
    after = deck.dger(ws)
    assert after.nome_caso == name[:80]
    (ws.root / deck.dger_name(ws)).read_bytes().decode("utf-8")
    assert after.tipo_execucao == before.tipo_execucao
    assert after.tipo_simulacao_final == before.tipo_simulacao_final
    assert after.ano_inicio_estudo == before.ano_inicio_estudo
    assert after.mes_inicio_estudo == before.mes_inicio_estudo
    assert after.num_anos_estudo == before.num_anos_estudo


# -- AC4: deck sets and purge invariants ---------------------------------


def test_newaveplugin_input_files_after_namecast_matches_expected_set(
    tmp_path: Path,
) -> None:
    dws = newave_workspace(tmp_path)
    ws = dws.ws
    _namecast(ws.root)

    files = deck.input_files(ws)

    for expected in (
        "bid.dat",
        "itaipu.dat",
        "elnino.dat",
        "ensoaux.dat",
        "indices.csv",
        "restricao-eletrica.csv",
        "polinjus.csv",
        "volumes-referencia.csv",
    ):
        assert expected in files
    assert "cdefvar.dat" not in files


def test_newaveplugin_output_patterns_pmo_dat_fullmatches() -> None:
    assert any(
        re.fullmatch(pattern, "pmo.dat", re.IGNORECASE)
        for pattern in NewavePlugin.output_patterns
    )


def test_newaveplugin_output_patterns_exclude_input_files_and_cortes(
    tmp_path: Path,
) -> None:
    dws = newave_workspace(tmp_path)
    ws = dws.ws
    _namecast(ws.root)
    candidates = deck.input_files(ws) + ("cortes.dat", "cortesh.dat")

    for name in candidates:
        assert not any(
            re.fullmatch(pattern, name, re.IGNORECASE)
            for pattern in NewavePlugin.output_patterns
        )


def test_newaveplugin_sanitize_exclude_matches_expected_names() -> None:
    for name in ("hidr.dat", "vazoes.dat", "postos.dat", "cortesh-pos.dat"):
        assert any(
            re.fullmatch(pattern, name)
            for pattern in NewavePlugin.sanitize_exclude
        )


# -- AC5: fetch_parent ----------------------------------------------------


def test_newaveplugin_fetch_parent_extracts_declared_members_only(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    ws = Workspace.at(root)
    archives_dir = tmp_path / "archives"
    cortes = input_zip(
        {"cortes-001.dat": b"c1", "cortesh.dat": b"ch", "nwlistcf.rel": b"rel"},
        archives_dir / "cortes.zip",
    )
    recursos = input_zip(
        {"engnat.dat": b"en", "vazthd.dat": b"vz", "energiaf001.dat": b"ef"},
        archives_dir / "recursos.zip",
    )
    simulacao = input_zip(
        {"newdesp.dat": b"nd", "forward.dat": b"fw"},
        archives_dir / "simulacao.zip",
    )
    parent = ParentRun(
        uri="s3://b/p",
        model_name="NEWAVE",
        starting_date="2025-01-01",
        archives=(cortes, recursos, simulacao),
    )

    NewavePlugin().fetch_parent(ws, parent)

    landed = {p.name for p in ws.root.iterdir() if p.is_file()}
    assert landed == {
        "cortes-001.dat",
        "cortesh.dat",
        "nwlistcf.rel",
        "engnat.dat",
        "vazthd.dat",
        "newdesp.dat",
    }


# -- AC7: golden lifecycle sequences ---------------------------------------


def _run_newave_sequence(tmp_path: Path, *, parent_path: str) -> str:
    root = tmp_path / "ws"
    root.mkdir()
    ws = Workspace.at(root)
    store = RecordingObjectStore()
    store.seed(
        S3Uri(_VERSIONS_BUCKET, "versoes/newave/30.0.4/ConverteNomesArquivos"),
        b"#!/bin/bash\nexit 0\n",
    )
    store.seed(
        S3Uri(_INPUTS_BUCKET, "ingest/deck_newave.zip"),
        (FIXTURES / "decks" / "deck_newave.zip").read_bytes(),
    )
    if parent_path:
        store.seed(
            S3Uri(
                _OUTPUTS_BUCKET,
                "artifacts/parenthash01/saidas/metadata.modelops",
            ),
            json.dumps(
                {
                    "model_name": "NEWAVE",
                    "status": "SUCCESS",
                    "study_starting_date": "2025-10-01T00:00:00+00:00",
                }
            ).encode("utf-8"),
        )
        for name in NewavePlugin.parent_artifacts:
            store.seed(
                S3Uri(_OUTPUTS_BUCKET, f"artifacts/parenthash01/saidas/{name}"),
                _empty_zip_bytes(),
            )

    plugin = NewavePlugin()
    state_store = StateStore(ws)
    reporter = RecordingReporter()

    fetch_executables(ws, plugin, store, reporter, state_store, _VERSOES_URI)
    fetch_inputs(
        ws,
        plugin,
        store,
        reporter,
        state_store,
        _INPUT_URI,
        parent_path=parent_path,
        delete=False,
    )
    state = extract_sanitize_inputs(ws, plugin, state_store, reporter)

    final_state = replace(
        state,
        diagnosis=Diagnosis(
            status=RunStatus.SUCCESS, rule_id="newave.manual", reason="ok"
        ),
        reported_job_id="1001",
    )
    rendered = render_metadata(
        final_state, always_write_parent_path=plugin.always_write_parent_path
    )
    assert rendered is not None
    return rendered


def test_golden_lifecycle_sequence_newave_no_parent_matches_metadata(
    tmp_path: Path,
) -> None:
    rendered = _run_newave_sequence(tmp_path, parent_path="")
    assert rendered == _load_golden_metadata("newave_no_parent")


def test_golden_lifecycle_sequence_newave_with_parent_matches_metadata(
    tmp_path: Path,
) -> None:
    rendered = _run_newave_sequence(tmp_path, parent_path=_PARENT_URI)
    assert rendered == _load_golden_metadata("newave_with_parent")
