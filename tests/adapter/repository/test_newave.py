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
from inewave.newave import Caso

from app.adapter.repository.newave import NEWAVE
from app.models.runstatus import RunStatus
from app.utils.constants import (
    EXECUTION_SOURCE_OFFLINE,
    METADATA_EXECUTION_SOURCE,
    METADATA_FILE,
    METADATA_MODEL_NAME,
    METADATA_MODEL_VERSION,
    METADATA_PARENT_PATH,
    METADATA_PARENT_STARTING_DATE,
    METADATA_STATUS,
    METADATA_STUDY_STARTING_DATE,
    MODEL_EXECUTABLE_DIRECTORY,
    MPICH_PATH,
    RAW_DECK_FILE,
    SLURM_PATH,
)
from tests.mocks.newave import (
    MOCK_ARQUIVOS_DAT,
    MOCK_CASO_DAT,
    MOCK_DGER,
    MOCK_PMO,
)

TEST_VERSION = "1.0"
TEST_BUCKET = "my-bucket"
TEST_INPUT = "deck.zip"
TEST_PARENT_PATH = f"s3://{TEST_BUCKET}/executions/parent-id"
TEST_JOB_ID = "42"
TEST_QUEUE = "batch"
TEST_CORE_COUNT = 42
TEST_DATE = datetime(2025, 1, 1)

TEST_EXECUTABLES_PATH = f"s3://{TEST_BUCKET}/versions/newave/{TEST_VERSION}"
TEST_INPUTS_PATH = f"s3://{TEST_BUCKET}/ingest/{TEST_INPUT}"
TEST_OUTPUTS_PATH = f"s3://{TEST_BUCKET}/executions/test-run"

EXECUTABLE_FILES = [
    METADATA_FILE,
    NEWAVE.LICENSE_FILENAMES[0],
    NEWAVE.NAMECAST_PROGRAM_NAME,
]
INPUT_FILES = [
    METADATA_FILE,
    TEST_INPUT,
    NEWAVE.CUT_FILE,
    NEWAVE.RESOURCES_FILE,
    NEWAVE.SIMULATION_FILE,
]

EXTRACTING_INPUTS = {
    NEWAVE.CUT_FILE: None,
    NEWAVE.RESOURCES_FILE: [
        "engthd.dat",
        "engfiobac.dat",
        "engfio.dat",
        "engfiob.dat",
        "engthd.dat",
        "engnat.dat",
        "engcont.dat",
        "vazthd.dat",
        "vazinat.dat",
    ],
    NEWAVE.SIMULATION_FILE: ["newdesp.dat"],
}

WRITE_INPUT_MOCKS = {
    NEWAVE.MODEL_ENTRY_FILE: MOCK_CASO_DAT,
    "arquivos.dat": MOCK_ARQUIVOS_DAT,
    "dger.dat": MOCK_DGER,
    "pmo.dat": MOCK_PMO,
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
            f.write("{ }")
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


def _model_obj() -> NEWAVE:
    return NEWAVE(logger=getLogger("test"))


@patch(
    "app.adapter.repository.newave.check_and_download_bucket_items",
    MagicMock(return_value=EXECUTABLE_FILES),
)
def test_newave_check_and_fetch_executables(fetching_executables):
    model = _model_obj()
    model.check_and_fetch_executables(path=TEST_EXECUTABLES_PATH)
    assert METADATA_FILE in listdir()
    with open(METADATA_FILE, "r") as f:
        metadata = json.load(f)
    assert METADATA_MODEL_NAME in metadata
    assert METADATA_MODEL_VERSION in metadata
    assert metadata[METADATA_MODEL_NAME] == NEWAVE.MODEL_NAME.upper()
    assert metadata[METADATA_MODEL_VERSION] == TEST_VERSION
    assert all([f in listdir() for f in fetching_executables])


@patch(
    "app.adapter.repository.newave.check_and_download_bucket_items",
    MagicMock(return_value=INPUT_FILES),
)
@patch(
    "app.adapter.repository.newave.check_and_delete_bucket_item",
    MagicMock(return_value=None),
)
@patch(
    "app.adapter.repository.newave.check_and_get_bucket_item",
    lambda bucket, filepath, logger: json.dumps({
        METADATA_MODEL_NAME: NEWAVE.MODEL_NAME.upper(),
        METADATA_STATUS: RunStatus.SUCCESS.value,
        METADATA_STUDY_STARTING_DATE: TEST_DATE.isoformat(),
    }),
)
def test_newave_check_and_fetch_inputs(fetching_inputs):
    model = _model_obj()
    model.check_and_fetch_inputs(
        path=TEST_INPUTS_PATH,
        parent_path=TEST_PARENT_PATH,
        delete=True,
    )
    assert METADATA_FILE in listdir()
    with open(METADATA_FILE, "r") as f:
        metadata = json.load(f)
    assert METADATA_PARENT_PATH in metadata
    assert METADATA_PARENT_STARTING_DATE in metadata
    assert metadata[METADATA_PARENT_PATH] == TEST_PARENT_PATH
    assert metadata[METADATA_PARENT_STARTING_DATE] == TEST_DATE.isoformat()


@patch("app.adapter.repository.newave.extract_zip_content")
@patch("app.adapter.repository.newave.run_in_terminal")
@patch("app.adapter.repository.newave.cast_encoding_to_utf8")
def test_newave_sanitize_inputs(
    cast_encoding_mock: MagicMock,
    run_terminal_mock: MagicMock,
    extract_mock: MagicMock,
    run_in_tempdir,
    fetching_inputs,
    writing_input_mocks,
):
    run_terminal_mock.return_value = [0, [None, None]]
    model = _model_obj()
    model.extract_sanitize_inputs()
    cast_encoding_mock.assert_called()
    for zip_file, files in EXTRACTING_INPUTS.items():
        assert zip_file in [
            call.args[0] for call in extract_mock.call_args_list
        ]
        assert files in [
            call.kwargs["members"] for call in extract_mock.call_args_list
        ]


def test_newave_preprocess(run_in_tempdir, writing_input_mocks):
    model = _model_obj()
    model.preprocess(execution_name="test")

    caso_obj = Caso.read(NEWAVE.MODEL_ENTRY_FILE)
    assert (
        caso_obj.gerenciador_processos
        == str(Path(MODEL_EXECUTABLE_DIRECTORY).resolve()) + "/"
    )


@patch("app.adapter.repository.newave.follow_submitted_job")
@patch("app.adapter.repository.newave.submit_job")
def test_newave_run(
    submit_job_mock: MagicMock,
    follow_job_mock: MagicMock,
    run_in_tempdir,
    writing_input_mocks,
):
    submit_job_mock.return_value = TEST_JOB_ID
    model = _model_obj()
    model.run(
        queue=TEST_QUEUE,
        core_count=TEST_CORE_COUNT,
        mpich_path=MPICH_PATH,
        slurm_path=SLURM_PATH,
    )
    assert MPICH_PATH in environ["PATH"]
    assert SLURM_PATH in environ["PATH"]
    assert submit_job_mock.call_count == 2
    assert follow_job_mock.call_count == 2


def test_newave_generate_execution_status(run_in_tempdir, writing_input_mocks):
    model = _model_obj()
    status = model.generate_execution_status(job_id=TEST_JOB_ID)
    assert status == RunStatus.SUCCESS.value


@patch("app.adapter.repository.newave.run_in_terminal")
def test_newave_postprocess(
    run_terminal_mock: MagicMock, run_in_tempdir, writing_input_mocks
):
    run_terminal_mock.return_value = [0, [None, None]]
    model = _model_obj()
    model.postprocess()
    assert run_terminal_mock.call_count == 4


@patch("app.adapter.repository.newave.compress_files_to_zip")
@patch("app.adapter.repository.newave.compress_files_to_zip_parallel")
@patch("app.adapter.repository.newave.moves_content_to_rootdir")
def test_newave_output_compression_and_cleanup(
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
    assert move_content_mock.call_count == 4
    assert compress_parallel_mock.call_count == 6


@patch("app.adapter.repository.newave.upload_file_to_bucket")
def test_newave_result_upload(
    file_upload_mock: MagicMock,
    run_in_tempdir,
    writing_input_mocks,
):
    model = _model_obj()
    model.result_upload(path=TEST_OUTPUTS_PATH)
    file_upload_mock.assert_called()


@patch("app.adapter.repository.newave.check_and_download_bucket_items")
@patch("app.adapter.repository.newave.extract_zip_content")
@patch("app.adapter.repository.newave.run_in_terminal")
@patch("app.adapter.repository.newave.cast_encoding_to_utf8")
def test_newave_ingest_offline_run(
    cast_encoding_mock: MagicMock,
    run_terminal_mock: MagicMock,
    extract_mock: MagicMock,
    download_mock: MagicMock,
    run_in_tempdir,
    writing_input_mocks,
):
    # The user uploads three archives (inputs, outputs and Benders cuts) as
    # three explicit S3 object keys that arrive with arbitrary names —
    # ingestion must not depend on the archive names.
    offline_zips = ["a1b2c3.zip", "d4e5f6.zip", "97g8h9.zip"]
    for zip_name in offline_zips:
        with open(zip_name, "w") as f:
            f.write("{ }")
    # One download call per object key, each returning its archive.
    download_mock.side_effect = [[z] for z in offline_zips]
    extract_mock.return_value = []
    run_terminal_mock.return_value = [0, [None, None]]

    model = _model_obj()
    model.ingest_offline_run(
        inputs_path=f"s3://{TEST_BUCKET}/ingest/run1/{offline_zips[0]}",
        outputs_path=f"s3://{TEST_BUCKET}/ingest/run1/{offline_zips[1]}",
        cortes_path=f"s3://{TEST_BUCKET}/ingest/run1/{offline_zips[2]}",
    )

    # One download per key; every uploaded archive is extracted.
    assert download_mock.call_count == len(offline_zips)
    assert extract_mock.call_count == len(offline_zips)
    cast_encoding_mock.assert_called()

    # Provenance and study metadata are recorded.
    assert METADATA_FILE in listdir()
    with open(METADATA_FILE, "r") as f:
        metadata = json.load(f)
    assert metadata[METADATA_EXECUTION_SOURCE] == EXECUTION_SOURCE_OFFLINE
    assert metadata[METADATA_MODEL_NAME] == NEWAVE.MODEL_NAME.upper()
    assert METADATA_STUDY_STARTING_DATE in metadata

    # The raw input deck is rebuilt as the input echo (from the deck contents,
    # not from any named archive), and the source archives are removed.
    assert isfile(RAW_DECK_FILE)
    for zip_name in offline_zips:
        assert not isfile(zip_name)


def test_newave_ingest_offline_run_default_not_implemented():
    from logging import getLogger

    from app.adapter.repository.abstractmodel import AbstractModel

    class _BareModel(AbstractModel):
        def check_and_fetch_executables(self, path): ...
        def check_and_fetch_inputs(self, path, parent_path, delete=True): ...
        def extract_sanitize_inputs(self): ...
        def preprocess(self, execution_name): ...
        def run(self, *args, **kwargs): ...
        def generate_execution_status(self, job_id) -> str:
            return ""
        def postprocess(self): ...
        def output_compression_and_cleanup(self, num_cpus): ...
        def result_upload(self, path): ...
        def cancel_run(self, job_id, slurm_path): ...
        def download_executed_run(self, artifacts_path, fetch_inputs): ...

    model = _BareModel(logger=getLogger("test"))
    with pytest.raises(NotImplementedError):
        model.ingest_offline_run(
            "s3://bucket/in.zip",
            "s3://bucket/out.zip",
            "s3://bucket/cortes.zip",
        )
