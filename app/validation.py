"""
Validation primitives for CLI input arguments.

Each function takes a value and optional context (parameter name) for error
message construction. On success the function returns None. On failure it
raises click.BadParameter with a descriptive message, which Click handles by
printing the error and exiting with code 2.

All functions are pure (no I/O, no side effects) and designed to be composed
by per-command validators.
"""

import re

import click


def validate_model_name(value: str, param_name: str = "model_name") -> None:
    """Validate that *value* is a registered model name.

    Valid: value is a key in ModelFactory()._models.
    Invalid: raises click.BadParameter listing the valid model names.

    ModelFactory is imported inside the function body to avoid circular imports
    since model modules import from abstractmodel.py and register themselves at
    import time. Importing ModelFactory at module level here could cause
    validation.py to be imported before model registration is complete.
    """
    from app.adapter.repository.abstractmodel import ModelFactory

    factory = ModelFactory()
    valid_names = sorted(factory._models.keys())
    if value not in factory._models:
        valid_list = ", ".join(valid_names) if valid_names else "(none registered)"
        raise click.BadParameter(
            f"'{value}' is not a valid model name. Valid models are: {valid_list}",
            param_hint=param_name,
        )


def validate_s3_path(value: str, param_name: str = "path") -> None:
    """Validate that *value* is a well-formed S3 path.

    Valid: starts with s3://, has a non-empty bucket name after the prefix,
    and has a non-empty key portion (i.e. at least s3://bucket/key).
    Invalid: raises click.BadParameter showing the expected format.
    """
    prefix = "s3://"
    if not value.startswith(prefix):
        raise click.BadParameter(
            f"Expected format: s3://bucket/key/path — got: '{value}'",
            param_hint=param_name,
        )

    remainder = value[len(prefix):]
    parts = remainder.split("/", 1)
    bucket = parts[0]
    key = parts[1] if len(parts) > 1 else ""

    if not bucket:
        raise click.BadParameter(
            f"Expected format: s3://bucket/key/path — bucket name is missing in: '{value}'",
            param_hint=param_name,
        )

    if not key:
        raise click.BadParameter(
            f"Expected format: s3://bucket/key/path — key portion is missing in: '{value}'",
            param_hint=param_name,
        )


def validate_positive_int(value: int, param_name: str = "value") -> None:
    """Validate that *value* is a positive integer (strictly greater than 0).

    Valid: value > 0.
    Invalid: raises click.BadParameter stating the value must be positive.
    """
    if value <= 0:
        raise click.BadParameter(
            f"'{value}' is not valid — value must be a positive integer (> 0)",
            param_hint=param_name,
        )


def validate_optional_positive_int(
    value: int | None, param_name: str = "value"
) -> None:
    """Validate that *value* is either None or a positive integer.

    Valid: value is None or value > 0.
    Invalid: raises click.BadParameter stating the value must be positive when provided.
    """
    if value is None:
        return

    if value <= 0:
        raise click.BadParameter(
            f"'{value}' is not valid — when provided, value must be a positive integer (> 0)",
            param_hint=param_name,
        )


_QUEUE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_queue_name(value: str, param_name: str = "queue") -> None:
    """Validate that *value* is an acceptable queue name.

    Valid: non-empty string containing only alphanumeric characters, hyphens,
    and underscores.
    Invalid: raises click.BadParameter describing the allowed characters.
    """
    if not value:
        raise click.BadParameter(
            "Queue name must not be empty — allowed characters: alphanumeric, hyphens (-), and underscores (_)",
            param_hint=param_name,
        )

    if not _QUEUE_NAME_RE.match(value):
        raise click.BadParameter(
            f"'{value}' contains invalid characters — allowed characters: alphanumeric, hyphens (-), and underscores (_)",
            param_hint=param_name,
        )


def validate_path_not_empty(value: str, param_name: str = "path") -> None:
    """Validate that *value* is a non-empty path string.

    Valid: non-empty string after stripping whitespace.
    Invalid: raises click.BadParameter stating the path must not be empty.
    """
    if not value.strip():
        raise click.BadParameter(
            "Path must not be empty",
            param_hint=param_name,
        )


def validate_check_and_fetch_inputs(
    model_name: str, path: str, parent_path: str
) -> None:
    """Validate arguments for the check_and_fetch_inputs CLI command."""
    validate_model_name(model_name)
    validate_s3_path(path, "path")
    if parent_path:
        validate_s3_path(parent_path, "parent-path")


def validate_check_and_fetch_executables(model_name: str, path: str) -> None:
    """Validate arguments for the check_and_fetch_executables CLI command."""
    validate_model_name(model_name)
    validate_s3_path(path, "path")


def validate_extract_sanitize_inputs(model_name: str) -> None:
    """Validate arguments for the extract_sanitize_inputs CLI command."""
    validate_model_name(model_name)


def validate_preprocess(model_name: str) -> None:
    """Validate arguments for the preprocess CLI command."""
    validate_model_name(model_name)


def validate_run(
    model_name: str,
    queue: str,
    core_count: int,
    max_cores_per_node: int | None,
    max_job_time_hours: int | None,
) -> None:
    """Validate arguments for the run CLI command."""
    validate_model_name(model_name)
    validate_queue_name(queue)
    validate_positive_int(core_count, "core_count")
    validate_optional_positive_int(max_cores_per_node, "max-cores-per-node")
    validate_optional_positive_int(max_job_time_hours, "max-job-time-hours")


def validate_generate_execution_status(model_name: str) -> None:
    """Validate arguments for the generate_execution_status CLI command."""
    validate_model_name(model_name)


def validate_postprocess(model_name: str) -> None:
    """Validate arguments for the postprocess CLI command."""
    validate_model_name(model_name)


def validate_output_compression_and_cleanup(model_name: str, num_cpus: int) -> None:
    """Validate arguments for the output_compression_and_cleanup CLI command."""
    validate_model_name(model_name)
    validate_positive_int(num_cpus, "num_cpus")


def validate_result_upload(model_name: str, path: str) -> None:
    """Validate arguments for the result_upload CLI command."""
    validate_model_name(model_name)
    validate_s3_path(path, "path")


def validate_cancel_run(model_name: str) -> None:
    """Validate arguments for the cancel_run CLI command."""
    validate_model_name(model_name)


def validate_download_executed_run(model_name: str, artifacts_path: str) -> None:
    """Validate arguments for the download_executed_run CLI command."""
    validate_model_name(model_name)
    validate_s3_path(artifacts_path, "artifacts_path")


def validate_fetch_extract_raw_outputs(outputs_path: str) -> None:
    """Validate arguments for the fetch_extract_raw_outputs CLI command."""
    validate_s3_path(outputs_path, "outputs_path")


def validate_ingest_offline_run(
    model_name: str,
    inputs_path: str,
    outputs_path: str,
    cortes_path: str,
) -> None:
    """Validate arguments for the ingest_offline_run CLI command."""
    validate_model_name(model_name)
    validate_s3_path(inputs_path, "inputs_path")
    validate_s3_path(outputs_path, "outputs_path")
    validate_s3_path(cortes_path, "cortes_path")
