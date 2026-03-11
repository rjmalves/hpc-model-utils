import json
from datetime import datetime
from logging import getLogger
from os import chdir, curdir, environ, listdir, remove
from os.path import isfile
from pathlib import Path
from shutil import rmtree
from tempfile import mkdtemp
from unittest.mock import MagicMock, patch

import pytest
from idecomp.decomp import Dadger, InviabUnic, Relato

from app.adapter.repository.decomp import DECOMP
from app.adapter.repository.newave import NEWAVE
from app.models.runstatus import RunStatus
from app.utils.constants import (
    METADATA_FILE,
    METADATA_MODEL_NAME,
    METADATA_MODEL_VERSION,
    METADATA_PARENT_ID,
    METADATA_PARENT_STARTING_DATE,
    METADATA_STATUS,
    METADATA_STUDY_STARTING_DATE,
    MPICH_PATH,
    SLURM_PATH,
)
from tests.mocks.decomp import (
    MOCK_ARQUIVOS_DAT,
    MOCK_CASO_DAT,
    MOCK_DADGER,
    MOCK_INVIAB_UNIC,
    MOCK_RELATO,
)

TEST_VERSION = "1.0"
TEST_BUCKET = "my-bucket"
TEST_INPUT = "deck.zip"
TEST_PARENT_ID = "parent-id"
TEST_JOB_ID = "42"
TEST_QUEUE = "batch"
TEST_CORE_COUNT = 42
TEST_PARENT_STARTING_DATE = datetime(2024, 12, 1)
TEST_DATE = datetime(2025, 1, 1)

EXECUTABLE_FILES = [
    METADATA_FILE,
    DECOMP.LICENSE_FILENAME,
    DECOMP.NAMECAST_PROGRAM_NAME,
]

INPUT_FILES = [
    METADATA_FILE,
    TEST_INPUT,
    NEWAVE.CUT_FILE,
    "cortesh.dat",
    "cortes-001.dat",
]

EXTRACTING_INPUTS = {
    NEWAVE.CUT_FILE: ["cortesh.dat", "cortes.dat", "cortes-012.dat"],
}

WRITE_INPUT_MOCKS = {
    DECOMP.MODEL_ENTRY_FILE: MOCK_CASO_DAT,
    "rv0": MOCK_ARQUIVOS_DAT,
    "dadger.rv0": MOCK_DADGER,
    "relato.rv0": MOCK_RELATO,
    "id.modelops": ["unique_id"],
}


@pytest.fixture
def fetching_executables():
    for file in EXECUTABLE_FILES:
        with open(file, "w") as f:
            f.write("{ }")
    yield EXECUTABLE_FILES
    for file in EXECUTABLE_FILES:
        if isfile(file):
            remove(file)


@pytest.fixture
def fetching_inputs():
    for file in INPUT_FILES:
        with open(file, "w") as f:
            f.write(
                f'{{"{METADATA_PARENT_STARTING_DATE}": "{TEST_PARENT_STARTING_DATE.isoformat()}" }}'
            )
    yield INPUT_FILES
    for file in INPUT_FILES:
        if isfile(file):
            remove(file)


@pytest.fixture
def run_in_tempdir():
    current_path = Path(curdir).resolve()
    tempdir = mkdtemp()
    chdir(tempdir)
    yield tempdir
    chdir(current_path)
    rmtree(tempdir)


@pytest.fixture
def writing_input_mocks():
    for filename, file_content in WRITE_INPUT_MOCKS.items():
        with open(filename, "w") as f:
            f.writelines(file_content)


def _model_obj() -> DECOMP:
    return DECOMP(logger=getLogger("test"))


@patch(
    "app.adapter.repository.decomp.check_and_download_bucket_items",
    MagicMock(return_value=EXECUTABLE_FILES),
)
def test_decomp_check_and_fetch_executables(fetching_executables):
    model = _model_obj()
    model.check_and_fetch_executables(version=TEST_VERSION, bucket=TEST_BUCKET)
    assert METADATA_FILE in listdir()
    with open(METADATA_FILE, "r") as f:
        metadata = json.load(f)
    assert METADATA_MODEL_NAME in metadata
    assert METADATA_MODEL_VERSION in metadata
    assert metadata[METADATA_MODEL_NAME] == DECOMP.MODEL_NAME.upper()
    assert metadata[METADATA_MODEL_VERSION] == TEST_VERSION
    assert all([f in listdir() for f in fetching_executables])


@patch(
    "app.adapter.repository.decomp.check_and_download_bucket_items",
    MagicMock(return_value=INPUT_FILES),
)
@patch(
    "app.adapter.repository.decomp.check_and_delete_bucket_item",
    MagicMock(return_value=None),
)
@patch(
    "app.adapter.repository.decomp.check_and_get_bucket_item",
    lambda bucket, filepath, logger: json.dumps({
        METADATA_MODEL_NAME: NEWAVE.MODEL_NAME.upper(),
        METADATA_STATUS: RunStatus.SUCCESS.value,
        METADATA_STUDY_STARTING_DATE: TEST_DATE.isoformat(),
    }),
)
def test_decomp_check_and_fetch_inputs(fetching_inputs):
    model = _model_obj()
    model.check_and_fetch_inputs(
        filename=TEST_INPUT,
        bucket=TEST_BUCKET,
        parent_id=TEST_PARENT_ID,
        delete=True,
    )
    assert METADATA_FILE in listdir()
    with open(METADATA_FILE, "r") as f:
        metadata = json.load(f)
    assert METADATA_PARENT_ID in metadata
    assert METADATA_PARENT_STARTING_DATE in metadata
    assert metadata[METADATA_PARENT_ID] == TEST_PARENT_ID
    assert metadata[METADATA_PARENT_STARTING_DATE] == TEST_DATE.isoformat()
    assert all([f in listdir() for f in fetching_inputs])


@patch("app.adapter.repository.decomp.extract_zip_content")
@patch("app.adapter.repository.decomp.run_in_terminal")
@patch("app.adapter.repository.decomp.cast_encoding_to_utf8")
def test_decomp_sanitize_inputs(
    cast_encoding_mock: MagicMock,
    run_terminal_mock: MagicMock,
    extract_mock: MagicMock,
    run_in_tempdir,
    fetching_inputs,
    writing_input_mocks,
):
    run_terminal_mock.return_value = [0, [None, None]]
    model = _model_obj()
    model.extract_sanitize_inputs(compressed_input_file=TEST_INPUT)
    cast_encoding_mock.assert_called()
    run_terminal_mock.assert_called()
    for zip_file, files in EXTRACTING_INPUTS.items():
        assert zip_file in [
            call.args[0] for call in extract_mock.call_args_list
        ]
        assert files in [
            call.kwargs.get("members") for call in extract_mock.call_args_list
        ]


def test_decomp_generate_unique_input_id(run_in_tempdir):
    model = _model_obj()
    parent_id = model.generate_unique_input_id(
        version=TEST_VERSION, parent_id=TEST_PARENT_ID
    )
    assert parent_id == "cc4306b33a27a796620b8e145c95bc67"


def test_decomp_preprocess(
    run_in_tempdir, fetching_inputs, writing_input_mocks
):
    model = _model_obj()
    model.preprocess()

    dadger_obj = Dadger.read("dadger.rv0")
    assert dadger_obj.fc(tipo="NEWV21").caminho == "cortesh.dat"
    assert dadger_obj.fc(tipo="NEWCUT").caminho == "cortes-001.dat"


@patch("app.utils.scheduler.run_in_terminal")
def test_decomp_run(
    run_terminal_mock: MagicMock, run_in_tempdir, writing_input_mocks
):
    run_terminal_mock.return_value = [
        0,
        [f"Submitted batch job {TEST_JOB_ID}", ""],
    ]
    model = _model_obj()
    model.run(
        queue=TEST_QUEUE,
        core_count=TEST_CORE_COUNT,
        mpich_path=MPICH_PATH,
        slurm_path=SLURM_PATH,
    )
    assert MPICH_PATH in environ["PATH"]
    assert SLURM_PATH in environ["PATH"]
    assert run_terminal_mock.call_count == 2
    assert (
        f"--partition={TEST_QUEUE}"
        in run_terminal_mock.call_args_list[0].args[0]
    )
    assert str(TEST_CORE_COUNT) in run_terminal_mock.call_args_list[0].args[0]


def test_decomp__evaluate_data_error():
    model = _model_obj()
    relato_obj = Relato.read(DECOMP.DATA_ERROR_PATTERN)
    assert model._evaluate_data_error(relato_obj)


def test_decomp__runtime_error():
    model = _model_obj()
    relato_obj = Relato.read(DECOMP.MAX_ITERATIONS_PATTERN)
    assert model._evaluate_max_iterations(relato_obj)
    relato_obj = Relato.read(DECOMP.NEGATIVE_GAP_PATTERN)
    assert model._evaluate_negative_gap(relato_obj)


def test_decomp__infeasible():
    model = _model_obj()
    dadger_obj = Dadger.read("")
    inviabunic_obj = InviabUnic.read("".join(MOCK_INVIAB_UNIC))
    assert model._evaluate_feasibility(inviabunic_obj, dadger_obj)


def test_decomp_generate_execution_status(run_in_tempdir, writing_input_mocks):
    model = _model_obj()
    status = model.generate_execution_status(job_id=TEST_JOB_ID)
    assert status == RunStatus.SUCCESS.value


def test_decomp_postprocess():
    model = _model_obj()
    model.postprocess()


@patch("app.adapter.repository.decomp.compress_files_to_zip")
@patch("app.adapter.repository.decomp.compress_files_to_zip_parallel")
@patch("app.adapter.repository.decomp.moves_content_to_rootdir")
def test_decomp_output_compression_and_cleanup(
    move_content_mock: MagicMock,
    compress_parallel_mock: MagicMock,
    compress_serial_mock: MagicMock,
    run_in_tempdir,
    writing_input_mocks,
):
    compress_serial_mock.return_value = [0, [None, None]]
    model = _model_obj()
    model.output_compression_and_cleanup(1)
    assert compress_serial_mock.call_count == 1
    assert move_content_mock.call_count == 1
    assert compress_parallel_mock.call_count == 2


@patch("app.adapter.repository.decomp.upload_file_to_bucket")
def test_decomp_result_upload(
    file_upload_mock: MagicMock,
    run_in_tempdir,
    writing_input_mocks,
):
    model = _model_obj()
    model.result_upload(
        compressed_input_file=TEST_INPUT,
        inputs_bucket=TEST_BUCKET,
        outputs_bucket=TEST_BUCKET,
    )
    file_upload_mock.assert_called()
