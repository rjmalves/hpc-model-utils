"""Unit tests for app/errors.py — error hierarchy and exit code constants."""

import pytest

from app.errors import (
    EXIT_MODEL_ERROR,
    EXIT_S3_ERROR,
    EXIT_SLURM_ERROR,
    EXIT_SUCCESS,
    EXIT_UNKNOWN_ERROR,
    EXIT_VALIDATION_ERROR,
    CLIError,
    ModelExecutionError,
    S3Error,
    SlurmError,
    ValidationError,
)
from app.utils.scheduler import JobCompletionInfo


# ---------------------------------------------------------------------------
# Exit code constants
# ---------------------------------------------------------------------------


class TestExitCodeConstants:
    def test_exit_success(self) -> None:
        assert EXIT_SUCCESS == 0

    def test_exit_model_error(self) -> None:
        assert EXIT_MODEL_ERROR == 1

    def test_exit_validation_error(self) -> None:
        assert EXIT_VALIDATION_ERROR == 2

    def test_exit_slurm_error(self) -> None:
        assert EXIT_SLURM_ERROR == 3

    def test_exit_s3_error(self) -> None:
        assert EXIT_S3_ERROR == 4

    def test_exit_unknown_error(self) -> None:
        assert EXIT_UNKNOWN_ERROR == 99


# ---------------------------------------------------------------------------
# CLIError base class
# ---------------------------------------------------------------------------


class TestCLIError:
    def test_is_exception_subclass(self) -> None:
        assert issubclass(CLIError, Exception)

    def test_stores_message(self) -> None:
        err = CLIError("something broke", exit_code=99)
        assert err.message == "something broke"

    def test_default_command_name_is_empty(self) -> None:
        err = CLIError("msg", exit_code=99)
        assert err.command_name == ""

    def test_default_detail_is_empty(self) -> None:
        err = CLIError("msg", exit_code=99)
        assert err.detail == ""

    def test_stores_exit_code(self) -> None:
        err = CLIError("msg", exit_code=42)
        assert err.exit_code == 42

    def test_stores_command_name(self) -> None:
        err = CLIError("msg", command_name="run", exit_code=1)
        assert err.command_name == "run"

    def test_stores_detail(self) -> None:
        err = CLIError("msg", detail="extra context", exit_code=1)
        assert err.detail == "extra context"

    def test_str_without_command_name(self) -> None:
        err = CLIError("something broke", exit_code=99)
        assert str(err) == "[CLIError] something broke"

    def test_str_with_command_name(self) -> None:
        err = CLIError("something broke", command_name="run", exit_code=99)
        assert str(err) == "[CLIError] run: something broke"

    def test_is_catchable_as_exception(self) -> None:
        with pytest.raises(Exception):
            raise CLIError("oops", exit_code=1)


# ---------------------------------------------------------------------------
# ValidationError
# ---------------------------------------------------------------------------


class TestValidationError:
    def test_is_cli_error(self) -> None:
        err = ValidationError("bad input")
        assert isinstance(err, CLIError)
        assert isinstance(err, ValidationError)

    def test_default_exit_code(self) -> None:
        err = ValidationError("bad input")
        assert err.exit_code == EXIT_VALIDATION_ERROR
        assert err.exit_code == 2

    def test_str_with_command_name(self) -> None:
        err = ValidationError("bad model name", command_name="run")
        assert str(err) == "[ValidationError] run: bad model name"

    def test_str_without_command_name(self) -> None:
        err = ValidationError("bad model name")
        assert str(err) == "[ValidationError] bad model name"

    def test_default_detail(self) -> None:
        err = ValidationError("bad input")
        assert err.detail == ""

    def test_detail_stored(self) -> None:
        err = ValidationError("bad input", detail="field 'model' is required")
        assert err.detail == "field 'model' is required"

    def test_exit_code_override(self) -> None:
        err = ValidationError("bad input", exit_code=10)
        assert err.exit_code == 10

    def test_all_fields(self) -> None:
        err = ValidationError(
            "bad input",
            command_name="validate",
            detail="missing field",
            exit_code=2,
        )
        assert err.message == "bad input"
        assert err.command_name == "validate"
        assert err.detail == "missing field"
        assert err.exit_code == 2

    def test_does_not_inherit_from_click_bad_parameter(self) -> None:
        click = pytest.importorskip(
            "click",
            reason="click not installed in this environment; skipping separation test",
        )
        assert not issubclass(ValidationError, click.BadParameter)


# ---------------------------------------------------------------------------
# SlurmError
# ---------------------------------------------------------------------------


def _make_completion_info(**kwargs: str) -> JobCompletionInfo:
    defaults = {
        "job_id": "12345",
        "state": "FAILED",
        "exit_code": "1:0",
        "elapsed": "00:01:00",
        "max_rss": "512K",
        "raw_output": "12345|FAILED|1:0|00:01:00|512K",
    }
    defaults.update(kwargs)
    return JobCompletionInfo(**defaults)


class TestSlurmError:
    def test_is_cli_error(self) -> None:
        err = SlurmError("job failed")
        assert isinstance(err, CLIError)
        assert isinstance(err, SlurmError)

    def test_default_exit_code(self) -> None:
        err = SlurmError("job failed")
        assert err.exit_code == EXIT_SLURM_ERROR
        assert err.exit_code == 3

    def test_default_completion_info_is_none(self) -> None:
        err = SlurmError("timeout")
        assert err.completion_info is None

    def test_completion_info_stored(self) -> None:
        info = _make_completion_info()
        err = SlurmError("job failed", completion_info=info)
        assert err.completion_info is info
        assert err.completion_info.job_id == "12345"
        assert err.completion_info.state == "FAILED"

    def test_str_with_command_name(self) -> None:
        err = SlurmError("squeue failed", command_name="run")
        assert str(err) == "[SlurmError] run: squeue failed"

    def test_str_without_command_name(self) -> None:
        err = SlurmError("squeue failed")
        assert str(err) == "[SlurmError] squeue failed"

    def test_exit_code_override(self) -> None:
        err = SlurmError("failure", exit_code=10)
        assert err.exit_code == 10

    def test_all_fields(self) -> None:
        info = _make_completion_info(state="TIMEOUT")
        err = SlurmError(
            "timeout waiting for job",
            command_name="run",
            detail="exceeded 3600s",
            exit_code=3,
            completion_info=info,
        )
        assert err.message == "timeout waiting for job"
        assert err.command_name == "run"
        assert err.detail == "exceeded 3600s"
        assert err.exit_code == 3
        assert err.completion_info.state == "TIMEOUT"


# ---------------------------------------------------------------------------
# S3Error
# ---------------------------------------------------------------------------


class TestS3Error:
    def test_is_cli_error(self) -> None:
        err = S3Error("bucket not found")
        assert isinstance(err, CLIError)
        assert isinstance(err, S3Error)

    def test_default_exit_code(self) -> None:
        err = S3Error("bucket not found")
        assert err.exit_code == EXIT_S3_ERROR
        assert err.exit_code == 4

    def test_str_with_command_name(self) -> None:
        err = S3Error("upload failed", command_name="upload")
        assert str(err) == "[S3Error] upload: upload failed"

    def test_str_without_command_name(self) -> None:
        err = S3Error("upload failed")
        assert str(err) == "[S3Error] upload failed"

    def test_detail_stored(self) -> None:
        err = S3Error("upload failed", detail="s3://bucket/key")
        assert err.detail == "s3://bucket/key"

    def test_exit_code_override(self) -> None:
        err = S3Error("failure", exit_code=10)
        assert err.exit_code == 10

    def test_all_fields(self) -> None:
        err = S3Error(
            "upload failed",
            command_name="upload",
            detail="invalid path",
            exit_code=4,
        )
        assert err.message == "upload failed"
        assert err.command_name == "upload"
        assert err.detail == "invalid path"
        assert err.exit_code == 4


# ---------------------------------------------------------------------------
# ModelExecutionError
# ---------------------------------------------------------------------------


class TestModelExecutionError:
    def test_is_cli_error(self) -> None:
        err = ModelExecutionError("segfault")
        assert isinstance(err, CLIError)
        assert isinstance(err, ModelExecutionError)

    def test_default_exit_code(self) -> None:
        err = ModelExecutionError("segfault")
        assert err.exit_code == EXIT_MODEL_ERROR
        assert err.exit_code == 1

    def test_str_without_command_name(self) -> None:
        err = ModelExecutionError("segfault")
        assert str(err) == "[ModelExecutionError] segfault"

    def test_str_with_command_name(self) -> None:
        err = ModelExecutionError("segfault", command_name="run")
        assert str(err) == "[ModelExecutionError] run: segfault"

    def test_detail_stored(self) -> None:
        err = ModelExecutionError("segfault", detail="core dumped")
        assert err.detail == "core dumped"

    def test_exit_code_override(self) -> None:
        err = ModelExecutionError("failure", exit_code=10)
        assert err.exit_code == 10

    def test_all_fields(self) -> None:
        err = ModelExecutionError(
            "execution failed",
            command_name="run",
            detail="non-zero return",
            exit_code=1,
        )
        assert err.message == "execution failed"
        assert err.command_name == "run"
        assert err.detail == "non-zero return"
        assert err.exit_code == 1


# ---------------------------------------------------------------------------
# Cross-class: isinstance checks and no cross-contamination
# ---------------------------------------------------------------------------


class TestHierarchyIsolation:
    def test_validation_not_slurm(self) -> None:
        assert not isinstance(ValidationError("x"), SlurmError)

    def test_slurm_not_s3(self) -> None:
        assert not isinstance(SlurmError("x"), S3Error)

    def test_s3_not_model_execution(self) -> None:
        assert not isinstance(S3Error("x"), ModelExecutionError)

    def test_model_execution_not_validation(self) -> None:
        assert not isinstance(ModelExecutionError("x"), ValidationError)

    def test_all_are_cli_error(self) -> None:
        errors: list[CLIError] = [
            ValidationError("v"),
            SlurmError("s"),
            S3Error("s3"),
            ModelExecutionError("m"),
        ]
        for err in errors:
            assert isinstance(err, CLIError)

    def test_all_catchable_as_cli_error(self) -> None:
        for klass in (ValidationError, SlurmError, S3Error, ModelExecutionError):
            with pytest.raises(CLIError):
                raise klass("test")  # type: ignore[call-arg]
