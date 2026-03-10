"""Unit tests for per-command validator functions in app/validation.py.

Each validator is tested with:
- All-valid arguments (expects None return)
- One invalid argument (expects click.BadParameter)

Special cases:
- validate_check_and_fetch_inputs: empty parent_path skips S3 validation for it
"""

import pytest
import click

from app.adapter.repository.abstractmodel import ModelFactory
from app.validation import (
    validate_check_and_fetch_inputs,
    validate_check_and_fetch_executables,
    validate_extract_sanitize_inputs,
    validate_preprocess,
    validate_run,
    validate_generate_execution_status,
    validate_postprocess,
    validate_output_compression_and_cleanup,
    validate_result_upload,
    validate_cancel_run,
    validate_download_executed_run,
    validate_fetch_extract_raw_outputs,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_MODEL = "test_model_abc123"
VALID_S3 = "s3://my-bucket/some/key"


@pytest.fixture(autouse=True)
def register_fake_model():
    """Register a minimal fake model name into ModelFactory for the duration
    of each test, then remove it to avoid polluting other tests."""
    factory = ModelFactory()
    factory._models[FAKE_MODEL] = object  # type: ignore[assignment]
    yield
    factory._models.pop(FAKE_MODEL, None)


# ---------------------------------------------------------------------------
# validate_check_and_fetch_inputs
# ---------------------------------------------------------------------------


class TestValidateCheckAndFetchInputs:
    def test_valid_with_parent_path(self):
        assert (
            validate_check_and_fetch_inputs(
                FAKE_MODEL, VALID_S3, "s3://bucket/parent"
            )
            is None
        )

    def test_valid_empty_parent_path_skips_s3_check(self):
        # empty parent_path must not raise even though it is not an S3 path
        assert (
            validate_check_and_fetch_inputs(FAKE_MODEL, VALID_S3, "") is None
        )

    def test_invalid_model_name(self):
        with pytest.raises(click.BadParameter):
            validate_check_and_fetch_inputs("unknown_model", VALID_S3, "")

    def test_invalid_path_not_s3(self):
        with pytest.raises(click.BadParameter, match="s3://"):
            validate_check_and_fetch_inputs(FAKE_MODEL, "not-s3-path", "")

    def test_invalid_parent_path_not_s3(self):
        with pytest.raises(click.BadParameter, match="s3://"):
            validate_check_and_fetch_inputs(
                FAKE_MODEL, VALID_S3, "not-an-s3-path"
            )


# ---------------------------------------------------------------------------
# validate_check_and_fetch_executables
# ---------------------------------------------------------------------------


class TestValidateCheckAndFetchExecutables:
    def test_valid(self):
        assert (
            validate_check_and_fetch_executables(FAKE_MODEL, VALID_S3) is None
        )

    def test_invalid_model_name(self):
        with pytest.raises(click.BadParameter):
            validate_check_and_fetch_executables("unknown_model", VALID_S3)

    def test_invalid_path_not_s3(self):
        with pytest.raises(click.BadParameter, match="s3://"):
            validate_check_and_fetch_executables(
                FAKE_MODEL, "not-an-s3-path"
            )


# ---------------------------------------------------------------------------
# validate_extract_sanitize_inputs
# ---------------------------------------------------------------------------


class TestValidateExtractSanitizeInputs:
    def test_valid(self):
        assert validate_extract_sanitize_inputs(FAKE_MODEL) is None

    def test_invalid_model_name(self):
        with pytest.raises(click.BadParameter):
            validate_extract_sanitize_inputs("unknown_model")


# ---------------------------------------------------------------------------
# validate_preprocess
# ---------------------------------------------------------------------------


class TestValidatePreprocess:
    def test_valid(self):
        assert validate_preprocess(FAKE_MODEL) is None

    def test_invalid_model_name(self):
        with pytest.raises(click.BadParameter):
            validate_preprocess("unknown_model")


# ---------------------------------------------------------------------------
# validate_run
# ---------------------------------------------------------------------------


class TestValidateRun:
    def test_valid_optional_nones(self):
        assert validate_run(FAKE_MODEL, "normal", 64, None, None) is None

    def test_valid_with_optionals(self):
        assert validate_run(FAKE_MODEL, "normal", 64, 8, 24) is None

    def test_invalid_model_name(self):
        with pytest.raises(click.BadParameter):
            validate_run("unknown_model", "normal", 64, None, None)

    def test_invalid_queue_empty(self):
        with pytest.raises(click.BadParameter):
            validate_run(FAKE_MODEL, "", 64, None, None)

    def test_invalid_core_count_zero(self):
        with pytest.raises(click.BadParameter, match="core_count"):
            validate_run(FAKE_MODEL, "normal", 0, None, None)

    def test_invalid_core_count_negative(self):
        with pytest.raises(click.BadParameter, match="core_count"):
            validate_run(FAKE_MODEL, "normal", -1, None, None)

    def test_invalid_max_cores_per_node_zero(self):
        with pytest.raises(click.BadParameter, match="max-cores-per-node"):
            validate_run(FAKE_MODEL, "normal", 64, 0, None)

    def test_invalid_max_job_time_hours_negative(self):
        with pytest.raises(click.BadParameter, match="max-job-time-hours"):
            validate_run(FAKE_MODEL, "normal", 64, None, -5)


# ---------------------------------------------------------------------------
# validate_generate_execution_status
# ---------------------------------------------------------------------------


class TestValidateGenerateExecutionStatus:
    def test_valid(self):
        assert validate_generate_execution_status(FAKE_MODEL) is None

    def test_invalid_model_name(self):
        with pytest.raises(click.BadParameter):
            validate_generate_execution_status("unknown_model")


# ---------------------------------------------------------------------------
# validate_postprocess
# ---------------------------------------------------------------------------


class TestValidatePostprocess:
    def test_valid(self):
        assert validate_postprocess(FAKE_MODEL) is None

    def test_invalid_model_name(self):
        with pytest.raises(click.BadParameter):
            validate_postprocess("unknown_model")


# ---------------------------------------------------------------------------
# validate_output_compression_and_cleanup
# ---------------------------------------------------------------------------


class TestValidateOutputCompressionAndCleanup:
    def test_valid(self):
        assert (
            validate_output_compression_and_cleanup(FAKE_MODEL, 4) is None
        )

    def test_invalid_model_name(self):
        with pytest.raises(click.BadParameter):
            validate_output_compression_and_cleanup("unknown_model", 4)

    def test_invalid_num_cpus_zero(self):
        with pytest.raises(click.BadParameter, match="num_cpus"):
            validate_output_compression_and_cleanup(FAKE_MODEL, 0)

    def test_invalid_num_cpus_negative(self):
        with pytest.raises(click.BadParameter, match="num_cpus"):
            validate_output_compression_and_cleanup(FAKE_MODEL, -1)


# ---------------------------------------------------------------------------
# validate_result_upload
# ---------------------------------------------------------------------------


class TestValidateResultUpload:
    def test_valid(self):
        assert validate_result_upload(FAKE_MODEL, VALID_S3) is None

    def test_invalid_model_name(self):
        with pytest.raises(click.BadParameter):
            validate_result_upload("unknown_model", VALID_S3)

    def test_invalid_path_not_s3(self):
        with pytest.raises(click.BadParameter, match="s3://"):
            validate_result_upload(FAKE_MODEL, "/local/path")


# ---------------------------------------------------------------------------
# validate_cancel_run
# ---------------------------------------------------------------------------


class TestValidateCancelRun:
    def test_valid(self):
        assert validate_cancel_run(FAKE_MODEL) is None

    def test_invalid_model_name(self):
        with pytest.raises(click.BadParameter):
            validate_cancel_run("unknown_model")


# ---------------------------------------------------------------------------
# validate_download_executed_run
# ---------------------------------------------------------------------------


class TestValidateDownloadExecutedRun:
    def test_valid(self):
        assert (
            validate_download_executed_run(FAKE_MODEL, VALID_S3) is None
        )

    def test_invalid_model_name(self):
        with pytest.raises(click.BadParameter):
            validate_download_executed_run("unknown_model", VALID_S3)

    def test_invalid_artifacts_path_not_s3(self):
        with pytest.raises(click.BadParameter, match="s3://"):
            validate_download_executed_run(FAKE_MODEL, "relative/path")


# ---------------------------------------------------------------------------
# validate_fetch_extract_raw_outputs
# ---------------------------------------------------------------------------


class TestValidateFetchExtractRawOutputs:
    def test_valid(self):
        assert (
            validate_fetch_extract_raw_outputs(
                "s3://bucket/key/outputs.zip"
            )
            is None
        )

    def test_valid_simple_key(self):
        assert (
            validate_fetch_extract_raw_outputs("s3://mybucket/file.zip")
            is None
        )

    def test_invalid_not_s3(self):
        with pytest.raises(click.BadParameter, match="s3://"):
            validate_fetch_extract_raw_outputs("not-s3-path")

    def test_invalid_missing_key(self):
        with pytest.raises(click.BadParameter, match="s3://"):
            validate_fetch_extract_raw_outputs("s3://bucket-only")
