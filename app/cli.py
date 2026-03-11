import os

import click

from app.adapter.repository.abstractmodel import ModelFactory
from app.click_types import ModelNameType, PositiveIntType, S3PathType
from app.error_handler import handle_cli_errors
from app.errors import ValidationError
from app.utils.constants import MPICH_PATH, RAW_OUTPUTS_FILE, SLURM_PATH
from app.utils.fs import extract_zip_content
from app.utils.log import Log
from app.utils.s3 import check_and_download_bucket_items, path_to_bucket_and_key
from app.utils.timing import time_command
from app.validation import (
    validate_cancel_run,
    validate_check_and_fetch_executables,
    validate_check_and_fetch_inputs,
    validate_download_executed_run,
    validate_extract_sanitize_inputs,
    validate_fetch_extract_raw_outputs,
    validate_generate_execution_status,
    validate_output_compression_and_cleanup,
    validate_postprocess,
    validate_preprocess,
    validate_result_upload,
    validate_run,
)


@click.group()
@click.version_option(package_name="hpc-model-utils")
def cli():
    """CLI tool for running energy planning models (NEWAVE, DECOMP, DESSEM)
    in HPC clusters with SLURM and AWS S3 integration."""
    pass


def _get_model_with_logger(model_name: str, command_name: str):
    logger = Log.configure_logger()
    try:
        model_type = ModelFactory().factory(model_name, logger)
    except ValueError as exc:
        raise ValidationError(str(exc), command_name=command_name) from exc
    return model_type, logger


@click.command("check_and_fetch_inputs")
@click.argument("model_name", type=ModelNameType())
@click.argument("path", type=S3PathType())
@click.option("--parent-path", type=str, default="")
@click.option("--delete", is_flag=True, default=False)
@time_command("check_and_fetch_inputs")
@handle_cli_errors("check_and_fetch_inputs")
def check_and_fetch_inputs(model_name, path, parent_path, delete):
    validate_check_and_fetch_inputs(model_name, path, parent_path)
    model_type, _ = _get_model_with_logger(model_name, "check_and_fetch_inputs")
    model_type.check_and_fetch_inputs(path, parent_path, delete=delete)


cli.add_command(check_and_fetch_inputs)


@click.command("check_and_fetch_executables")
@click.argument("model_name", type=ModelNameType())
@click.argument("path", type=S3PathType())
@time_command("check_and_fetch_executables")
@handle_cli_errors("check_and_fetch_executables")
def check_and_fetch_executables(model_name, path):
    validate_check_and_fetch_executables(model_name, path)
    model_type, _ = _get_model_with_logger(model_name, "check_and_fetch_executables")
    model_type.check_and_fetch_executables(path)


cli.add_command(check_and_fetch_executables)


@click.command("extract_sanitize_inputs")
@click.argument("model_name", type=ModelNameType())
@time_command("extract_sanitize_inputs")
@handle_cli_errors("extract_sanitize_inputs")
def extract_sanitize_inputs(model_name):
    validate_extract_sanitize_inputs(model_name)
    model_type, _ = _get_model_with_logger(model_name, "extract_sanitize_inputs")
    model_type.extract_sanitize_inputs()


cli.add_command(extract_sanitize_inputs)


@click.command("preprocess")
@click.argument("model_name", type=ModelNameType())
@click.option("--execution-name", type=str, default="")
@time_command("preprocess")
@handle_cli_errors("preprocess")
def preprocess(model_name, execution_name):
    validate_preprocess(model_name)
    model_type, _ = _get_model_with_logger(model_name, "preprocess")
    model_type.preprocess(execution_name)


cli.add_command(preprocess)


@click.command("run")
@click.argument("model_name", type=ModelNameType())
@click.argument("queue", type=str)
@click.argument("core_count", type=PositiveIntType())
@click.option("--max-cores-per-node", type=int, default=None)
@click.option("--max-job-time-hours", type=int, default=None)
@click.option("--mpich-path", type=str, default=MPICH_PATH)
@click.option("--slurm-path", type=str, default=SLURM_PATH)
@click.option("--skip", is_flag=True, default=False)
@time_command("run")
@handle_cli_errors("run")
def run(
    model_name,
    queue,
    core_count,
    max_cores_per_node,
    max_job_time_hours,
    mpich_path,
    slurm_path,
    skip,
):
    validate_run(model_name, queue, core_count, max_cores_per_node, max_job_time_hours)
    model_type, logger = _get_model_with_logger(model_name, "run")
    logger.info(f"Submitting job to SLURM with {core_count} cores in {queue} queue")
    model_type.run(
        queue,
        core_count,
        mpich_path,
        slurm_path,
        max_cores_per_node,
        max_job_time_hours,
        skip_model=skip,
    )
    logger.info("Model execution terminated")


cli.add_command(run)


@click.command("generate_execution_status")
@click.argument("model_name", type=ModelNameType())
@click.option("--job-id", type=str, default="")
@time_command("generate_execution_status")
@handle_cli_errors("generate_execution_status")
def generate_execution_status(model_name, job_id):
    validate_generate_execution_status(model_name)
    model_type, logger = _get_model_with_logger(model_name, "generate_execution_status")
    status = model_type.generate_execution_status(job_id)
    logger.info(f"Generated execution status: {status}")


cli.add_command(generate_execution_status)


@click.command("postprocess")
@click.argument("model_name", type=ModelNameType())
@time_command("postprocess")
@handle_cli_errors("postprocess")
def postprocess(model_name):
    validate_postprocess(model_name)
    model_type, _ = _get_model_with_logger(model_name, "postprocess")
    model_type.postprocess()


cli.add_command(postprocess)


@click.command("output_compression_and_cleanup")
@click.argument("model_name", type=ModelNameType())
@click.argument("num_cpus", type=PositiveIntType())
@time_command("output_compression_and_cleanup")
@handle_cli_errors("output_compression_and_cleanup")
def output_compression_and_cleanup(model_name, num_cpus):
    validate_output_compression_and_cleanup(model_name, num_cpus)
    model_type, _ = _get_model_with_logger(model_name, "output_compression_and_cleanup")
    model_type.output_compression_and_cleanup(num_cpus)


cli.add_command(output_compression_and_cleanup)


@click.command("result_upload")
@click.argument("model_name", type=ModelNameType())
@click.argument("path", type=S3PathType())
@time_command("result_upload")
@handle_cli_errors("result_upload")
def result_upload(model_name, path):
    validate_result_upload(model_name, path)
    model_type, _ = _get_model_with_logger(model_name, "result_upload")
    model_type.result_upload(path)


cli.add_command(result_upload)


@click.command("cancel_run")
@click.argument("model_name", type=ModelNameType())
@click.option("--job-id", type=str, default="")
@click.option("--slurm-path", type=str, default=SLURM_PATH)
@time_command("cancel_run")
@handle_cli_errors("cancel_run")
def cancel_run(model_name, job_id, slurm_path):
    validate_cancel_run(model_name)
    model_type, logger = _get_model_with_logger(model_name, "cancel_run")
    model_type.cancel_run(job_id, slurm_path)
    logger.info(f"Cancelled job: {job_id}")


cli.add_command(cancel_run)


@click.command("download_executed_run")
@click.argument("model_name", type=ModelNameType())
@click.argument("artifacts_path", type=S3PathType())
@click.option("--fetch-inputs", is_flag=True, default=False)
@time_command("download_executed_run")
@handle_cli_errors("download_executed_run")
def download_executed_run(model_name, artifacts_path, fetch_inputs):
    validate_download_executed_run(model_name, artifacts_path)
    model_type, logger = _get_model_with_logger(model_name, "download_executed_run")
    model_type.download_executed_run(artifacts_path, fetch_inputs=fetch_inputs)
    logger.info(f"Downloaded executed run artifacts from: {artifacts_path}")


cli.add_command(download_executed_run)


@click.command("fetch_extract_raw_outputs")
@click.argument("outputs_path", type=S3PathType())
@time_command("fetch_extract_raw_outputs")
@handle_cli_errors("fetch_extract_raw_outputs")
def fetch_extract_raw_outputs(outputs_path):
    logger = Log.configure_logger()
    validate_fetch_extract_raw_outputs(outputs_path)
    parsed = path_to_bucket_and_key(outputs_path)
    downloaded = check_and_download_bucket_items(
        parsed["bucket"], ".", parsed["key"], logger
    )
    os.rename(downloaded[0], RAW_OUTPUTS_FILE)
    extract_zip_content(RAW_OUTPUTS_FILE)
    os.remove(RAW_OUTPUTS_FILE)
    logger.info(f"Extracted outputs from: {outputs_path}")


cli.add_command(fetch_extract_raw_outputs)
