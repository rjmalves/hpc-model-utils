import json
from os import curdir, environ, getenv, listdir
from os.path import isfile, join
from pathlib import Path
from shutil import move
from typing import Any

from app.adapter.repository.abstractmodel import (
    AbstractModel,
    ModelFactory,
)
from app.models.runstatus import RunStatus
from app.utils.commands import ModelOpsCommands
from app.utils.constants import (
    AWS_ACCESS_KEY_ID_ENV,
    AWS_SECRET_ACCESS_KEY_ENV,
    INPUTS_ECHO_PREFIX,
    METADATA_FILE,
    METADATA_INPUT_FILES,
    METADATA_JOB_ID,
    METADATA_MODEL_NAME,
    METADATA_MODEL_VERSION,
    METADATA_PARENT_PATH,
    METADATA_STATUS,
    MODEL_EXECUTABLE_DIRECTORY,
    MODEL_EXECUTABLE_PERMISSIONS,
    OUTPUTS_PREFIX,
    PROCESSED_DECK_FILE,
    RAW_DECK_FILE,
)
from app.utils.fs import (
    change_file_permission,
    clean_files,
    compress_files_to_zip,
    compress_files_to_zip_parallel,
    extract_zip_content,
    list_files_by_regexes,
)
from app.utils.s3 import (
    check_and_delete_bucket_item,
    check_and_download_bucket_items,
    path_to_bucket_and_key,
    upload_file_to_bucket,
)
from app.utils.terminal import cast_encoding_to_utf8, run_in_terminal
from app.utils.timing import time_and_log


class GEVAZP(AbstractModel):
    MODEL_NAME = "gevazp"
    MODEL_ENTRY_FILE = "caso.dat"
    LICENSE_FILENAMES = ["gevazp.lic", "gevazp.cep"]
    OUTPUTS_FILE = "saidas.zip"
    GEVAZP_JOB_PATH = "hpc-model-utils/assets/jobs/gevazp.sh"
    GEVAZP_JOB_TIMEOUT = 900  # 15min

    def _update_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        if isfile(METADATA_FILE):
            with open(METADATA_FILE, "r") as f:
                metadata = {**json.load(f), **metadata}
        with open(METADATA_FILE, "w") as f:
            json.dump(metadata, f)
        return metadata

    def check_and_fetch_executables(self, path: str):
        self._log.info(f"Fetching executables in {path}...")
        path_data = path_to_bucket_and_key(path)
        bucket = path_data["bucket"]
        key = path_data["key"]
        version = key.split("/")[-1]

        downloaded_filepaths = check_and_download_bucket_items(
            bucket, MODEL_EXECUTABLE_DIRECTORY, key, self._log
        )
        for filepath in downloaded_filepaths:
            change_file_permission(filepath, MODEL_EXECUTABLE_PERMISSIONS)
            self._log.info(
                f"Changed {filepath} permissions to"
                + f" {MODEL_EXECUTABLE_PERMISSIONS:o}"
            )

        metadata = {
            METADATA_MODEL_NAME: self.MODEL_NAME.upper(),
            METADATA_MODEL_VERSION: version,
        }
        self._update_metadata(metadata)
        for key, value in metadata.items():
            ModelOpsCommands.set_metadata(key=key, value=value)
        self._log.info("Executables successfully fetched and ready!")

    def check_and_fetch_inputs(
        self,
        path: str,
        parent_path: str,
        delete: bool = True,
    ):
        self._log.info(f"Fetching input data in {path}...")

        path_data = path_to_bucket_and_key(path)
        bucket = path_data["bucket"]
        key = path_data["key"]
        filename = key.split("/")[-1]

        check_and_download_bucket_items(
            bucket, str(Path(curdir).resolve()), key, self._log
        )

        if delete:
            self._log.info(f"Removing inputs from {path}...")
            check_and_delete_bucket_item(bucket, filename, key, self._log)

        self._log.info(f"Renaming input file to {RAW_DECK_FILE}")
        move(filename, RAW_DECK_FILE)

        if len(parent_path) > 0:
            self._log.info("GEVAZP does not support parent cases!")
        else:
            self._log.info("No parent id was given!")

        metadata = {METADATA_PARENT_PATH: parent_path}
        self._update_metadata(metadata)
        ModelOpsCommands.set_metadata(METADATA_PARENT_PATH, parent_path)
        self._log.info("Inputs successfully fetched!")

    def extract_sanitize_inputs(self):
        extracted_files = (
            extract_zip_content(RAW_DECK_FILE) if isfile(RAW_DECK_FILE) else []
        )
        self._log.info(f"Extracted input files: {extracted_files}")

        self._log.info("Forcing encoding to utf-8")
        for f in listdir():
            if f in self.LICENSE_FILENAMES:
                self._log.info(f"Ignoring license file: {f}")
                continue
            cast_encoding_to_utf8(f)

        # Copies license file
        for license_filename in self.LICENSE_FILENAMES:
            license_path = join(MODEL_EXECUTABLE_DIRECTORY, license_filename)
            if isfile(license_path):
                move(
                    license_path,
                    join(curdir, license_filename),
                )
                self._log.info(f"Moved {license_filename} to executables dir")

        metadata = {
            METADATA_INPUT_FILES: extracted_files,
        }
        self._update_metadata(metadata)
        for key, value in metadata.items():
            ModelOpsCommands.set_metadata(key=key, value=value)

    def preprocess(self, execution_name: str):
        pass

    def run(
        self, queue: str, core_count: int, mpich_path: str, slurm_path: str
    ):
        self._log.info(f"Job script file: {self.GEVAZP_JOB_PATH}")
        code, _ = run_in_terminal(
            [self.GEVAZP_JOB_PATH],
            log_output=True,
        )
        if code != 0:
            self._log.warning(f"Running {self.MODEL_NAME.upper()} resulted in:")

    def generate_execution_status(self, job_id: str) -> str:
        self._log.info(
            f"Model {self.MODEL_NAME.upper()} does not support status generation..."
        )

        status_value = RunStatus.UNKNOWN.value

        metadata = {METADATA_JOB_ID: job_id, METADATA_STATUS: status_value}
        self._update_metadata(metadata)
        for key, value in metadata.items():
            ModelOpsCommands.set_metadata(key=key, value=value)
        return status_value

    def postprocess(self):
        with time_and_log("Postprocessing GEVAZP", logger=self._log):
            pass

    def _list_input_files(self) -> list[str]:
        metadata = self._update_metadata({})
        input_files = metadata.get(METADATA_INPUT_FILES, [])

        self._log.info(f"Files considered as input: {input_files}")
        return input_files

    def _list_output_files(self, input_files: list[str]) -> list[str]:
        existing_files = listdir()
        ignored_files = (
            input_files
            + self.LICENSE_FILENAMES
            + [
                RAW_DECK_FILE,
                PROCESSED_DECK_FILE,
                METADATA_FILE,
                "metadata.modelops",
                "stdout.modelops",
                "stderr.modelops",
            ]
        )
        ignored_files_regex = [r"^fort\..*"]
        ignored_files += list_files_by_regexes(input_files, ignored_files_regex)
        output_files = [
            f for f in existing_files if isfile(f) and f not in ignored_files
        ]
        self._log.info(f"Files considered as output: {output_files}")
        return output_files

    def _cleanup_files(
        self,
        input_files: list[str],
        output_files: list[str],
    ):
        keeping_files = [
            "gevazp.rel",
        ]
        keeping_files = [a for a in keeping_files if a is not None]
        compressed_files = input_files + output_files
        cleaning_files = [a for a in compressed_files if a not in keeping_files]
        cleaning_files_regex = [r"^fort\..*"]
        cleaning_files += list_files_by_regexes(
            input_files, cleaning_files_regex
        )
        self._log.info(f"Cleaning files: {cleaning_files}")
        clean_files(cleaning_files)

    def output_compression_and_cleanup(self, num_cpus: int):
        with time_and_log("Output compression and cleanup", logger=self._log):
            input_files = self._list_input_files()
            compress_files_to_zip(
                input_files, PROCESSED_DECK_FILE.rstrip(".zip")
            )

            # Parallel compression
            output_files = self._list_output_files(input_files)
            compress_files_to_zip_parallel(output_files, "saidas", num_cpus)

            self._cleanup_files(input_files, output_files)

    def _upload_input_echo(self, path: str):
        path_data = path_to_bucket_and_key(path)
        bucket = path_data["bucket"]
        key = path_data["key"]
        with time_and_log("Time for uploading input echo", logger=self._log):
            if isfile(RAW_DECK_FILE):
                upload_file_to_bucket(
                    RAW_DECK_FILE,
                    bucket,
                    join(key, INPUTS_ECHO_PREFIX, RAW_DECK_FILE),
                    aws_access_key_id=getenv(AWS_ACCESS_KEY_ID_ENV),
                    aws_secret_access_key=getenv(AWS_SECRET_ACCESS_KEY_ENV),
                )
            if isfile(PROCESSED_DECK_FILE):
                upload_file_to_bucket(
                    PROCESSED_DECK_FILE,
                    bucket,
                    join(key, INPUTS_ECHO_PREFIX, PROCESSED_DECK_FILE),
                    aws_access_key_id=getenv(AWS_ACCESS_KEY_ID_ENV),
                    aws_secret_access_key=getenv(AWS_SECRET_ACCESS_KEY_ENV),
                )

    def _upload_outputs(self, path: str):
        path_data = path_to_bucket_and_key(path)
        bucket = path_data["bucket"]
        key = path_data["key"]
        with time_and_log("Time for uploading outputs", logger=self._log):
            output_files = [
                "newave.tim",
                "saidas.zip",
            ]
            output_files += list_files_by_regexes(
                [], [r".*\.dat", r".*\.modelops"]
            )
            for f in output_files:
                if isfile(f):
                    self._log.info(f"Uploading {f}")
                    upload_file_to_bucket(
                        f,
                        bucket,
                        join(key, OUTPUTS_PREFIX, f),
                        aws_access_key_id=getenv(AWS_ACCESS_KEY_ID_ENV),
                        aws_secret_access_key=getenv(AWS_SECRET_ACCESS_KEY_ENV),
                    )

    def _set_status(self):
        metadata = self._update_metadata({})
        try:
            status = RunStatus.factory(metadata[METADATA_STATUS])

            if status == RunStatus.SUCCESS:
                ModelOpsCommands.set_success()
            elif status == RunStatus.DATA_ERROR:
                ModelOpsCommands.set_data_error()
            elif status == RunStatus.UNKNOWN:
                # TODO - remove this when we are able to set proper status
                ModelOpsCommands.set_success()
            else:
                ModelOpsCommands.set_model_error()

        except Exception:
            ModelOpsCommands.set_model_error()

    def result_upload(self, path: str):
        ModelOpsCommands.set_execution_artifacts_path(path)

        self._set_status()

        self._log.info(f"Uploading results for {self.MODEL_NAME}")
        self._upload_input_echo(path)
        self._upload_outputs(path)

    def cancel_run(self, job_id: str, slurm_path: str):
        if job_id:
            environ["PATH"] += f":{slurm_path}"
            self._log.info(f"Canceling job {job_id}")


ModelFactory().register(GEVAZP.MODEL_NAME, GEVAZP)
