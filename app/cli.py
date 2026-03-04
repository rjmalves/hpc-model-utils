import os

import click

from app.adapter.repository.abstractmodel import ModelFactory
from app.utils.commands import ModelOpsCommands
from app.utils.constants import MPICH_PATH, RAW_OUTPUTS_FILE, SLURM_PATH
from app.utils.fs import extract_zip_content
from app.utils.log import Log
from app.utils.s3 import check_and_download_bucket_items, path_to_bucket_and_key


@click.group()
def cli():
    """
    CLI app that handles job script steps for running
    energy-related models in HPC clusters.
    """
    pass


@click.command("check_and_fetch_inputs")
@click.argument("model_name", type=str)
@click.argument("path", type=str)
@click.option("--parent-path", type=str, default="")
@click.option("--delete", is_flag=True, default=False)
def check_and_fetch_inputs(model_name, path, parent_path, delete):
    """
    Checks and downloads input data from
    a given S3 bucket.
    """
    logger = Log.configure_logger()

    try:
        model_type = ModelFactory().factory(model_name, logger)
        model_type.check_and_fetch_inputs(path, parent_path, delete=delete)
    except Exception as e:
        ModelOpsCommands.set_model_error()
        logger.exception(str(e))
        # raise e


cli.add_command(check_and_fetch_inputs)


@click.command("check_and_fetch_executables")
@click.argument("model_name", type=str)
@click.argument("path", type=str)
def check_and_fetch_executables(model_name, path):
    """
    Checks and downloads model executables from
    a given S3 bucket.
    """
    logger = Log.configure_logger()

    try:
        model_type = ModelFactory().factory(model_name, logger)
        model_type.check_and_fetch_executables(path)
    except Exception as e:
        ModelOpsCommands.set_model_error()
        logger.exception(str(e))
        # raise e


cli.add_command(check_and_fetch_executables)


@click.command("extract_sanitize_inputs")
@click.argument("model_name", type=str)
def extract_sanitize_inputs(model_name):
    """
    Deals with a compressed (.zip) file that contains
    model inputs, extracting and fixing encoding.
    """
    logger = Log.configure_logger()

    try:
        model_type = ModelFactory().factory(model_name, logger)
        model_type.extract_sanitize_inputs()
    except Exception as e:
        ModelOpsCommands.set_model_error()
        logger.exception(str(e))
        # raise e


cli.add_command(extract_sanitize_inputs)


@click.command("preprocess")
@click.argument("model_name", type=str)
@click.option("--execution-name", type=str, default="")
def preprocess(model_name, execution_name):
    """
    Runs model-specific pre-processing.
    """
    logger = Log.configure_logger()

    try:
        model_type = ModelFactory().factory(model_name, logger)
        model_type.preprocess(execution_name)
    except Exception as e:
        ModelOpsCommands.set_model_error()
        logger.exception(str(e))
        # raise e


cli.add_command(preprocess)


@click.command("run")
@click.argument("model_name", type=str)
@click.argument("queue", type=str)
@click.argument("core_count", type=int)
@click.option("--max-cores-per-node", type=int, default=None)
@click.option("--max-job-time-hours", type=int, default=None)
@click.option("--mpich-path", type=str, default=MPICH_PATH)
@click.option("--slurm-path", type=str, default=SLURM_PATH)
@click.option("--skip", is_flag=True, default=False)
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
    """
    Runs the model by submitting to a job scheduler.
    """
    logger = Log.configure_logger()

    try:
        logger.info(
            f"Submitting job to SLURM with {core_count} cores in {queue} queue"
        )
        model_type = ModelFactory().factory(model_name, logger)
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
    except Exception as e:
        ModelOpsCommands.set_model_error()
        logger.exception(str(e))
        # raise e


cli.add_command(run)


@click.command("generate_execution_status")
@click.argument("model_name", type=str)
@click.option("--job-id", type=str, default="")
def generate_execution_status(model_name, job_id):
    """
    Diagnosis the execution status with model-specific
    business rules.
    """
    logger = Log.configure_logger()

    try:
        model_type = ModelFactory().factory(model_name, logger)
        status = model_type.generate_execution_status(job_id)
        logger.info(f"Generated execution status: {status}")
    except Exception as e:
        ModelOpsCommands.set_model_error()
        logger.exception(str(e))
        # raise e


cli.add_command(generate_execution_status)


@click.command("postprocess")
@click.argument("model_name", type=str)
def postprocess(model_name):
    """
    Runs model-specific post-processing steps.
    """
    logger = Log.configure_logger()

    try:
        model_type = ModelFactory().factory(model_name, logger)
        model_type.postprocess()
    except Exception as e:
        ModelOpsCommands.set_model_error()
        logger.exception(str(e))
        # raise e


cli.add_command(postprocess)


@click.command("output_compression_and_cleanup")
@click.argument("model_name", type=str)
@click.argument("num_cpus", type=int)
def output_compression_and_cleanup(model_name, num_cpus):
    """
    Compresses the output files in some groups
    and cleans the root directory.
    """
    logger = Log.configure_logger()

    try:
        model_type = ModelFactory().factory(model_name, logger)
        model_type.output_compression_and_cleanup(num_cpus)
    except Exception as e:
        ModelOpsCommands.set_model_error()
        logger.exception(str(e))
        # raise e


cli.add_command(output_compression_and_cleanup)


@click.command("result_upload")
@click.argument("model_name", type=str)
@click.argument("path", type=str)
def result_upload(model_name, path):
    """
    Uploads the results to an S3 bucket.
    """
    logger = Log.configure_logger()

    try:
        model_type = ModelFactory().factory(model_name, logger)
        model_type.result_upload(path)
    except Exception as e:
        ModelOpsCommands.set_model_error()
        logger.exception(str(e))
        # raise e


cli.add_command(result_upload)


@click.command("cancel_run")
@click.argument("model_name", type=str)
@click.option("--job-id", type=str, default="")
@click.option("--slurm-path", type=str, default=SLURM_PATH)
def cancel_run(model_name, job_id, slurm_path):
    """
    Cancels a job execution and waits for it to leave the queue.
    """
    logger = Log.configure_logger()

    try:
        model_type = ModelFactory().factory(model_name, logger)
        model_type.cancel_run(job_id, slurm_path)
        logger.info(f"Cancelled job: {job_id}")
    except Exception as e:
        ModelOpsCommands.set_model_error()
        logger.exception(str(e))
        # raise e


cli.add_command(cancel_run)


@click.command("download_executed_run")
@click.argument("model_name", type=str)
@click.argument("artifacts_path", type=str)
@click.option("--fetch-inputs", is_flag=True, default=False)
def download_executed_run(model_name, artifacts_path, fetch_inputs):
    """
    Downloads the executed run outputs from a given path.
    """
    logger = Log.configure_logger()

    try:
        model_type = ModelFactory().factory(model_name, logger)
        model_type.download_executed_run(artifacts_path, fetch_inputs=fetch_inputs)
        logger.info(f"Downloaded executed run artifacts from: {artifacts_path}")
    except Exception as e:
        ModelOpsCommands.set_model_error()
        logger.exception(str(e))
        # raise e


cli.add_command(download_executed_run)


@click.command("fetch_extract_raw_outputs")
@click.argument("outputs_path", type=str)
def fetch_extract_raw_outputs(outputs_path):
    """
    Downloads and extracts a user-provided outputs zip from S3
    into the current working directory.
    """
    logger = Log.configure_logger()

    try:
        parsed = path_to_bucket_and_key(outputs_path)
        downloaded = check_and_download_bucket_items(
            parsed["bucket"], ".", parsed["key"], logger
        )
        downloaded_file = downloaded[0]
        os.rename(downloaded_file, RAW_OUTPUTS_FILE)
        extract_zip_content(RAW_OUTPUTS_FILE)
        os.remove(RAW_OUTPUTS_FILE)
        logger.info(f"Extracted outputs from: {outputs_path}")
    except Exception as e:
        ModelOpsCommands.set_model_error()
        logger.exception(str(e))


cli.add_command(fetch_extract_raw_outputs)
