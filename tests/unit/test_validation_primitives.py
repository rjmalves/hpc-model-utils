"""Unit tests for the 6 primitive validator functions in app/validation.py.

Each function is tested with:
- Valid inputs (expects None return)
- All invalid input branches (expects click.BadParameter)

The per-command validators that compose these primitives are covered separately
in test_validation_per_command.py.
"""

import pytest
import click

from app.adapter.repository.abstractmodel import ModelFactory
from app.validation import (
    validate_model_name,
    validate_s3_path,
    validate_positive_int,
    validate_optional_positive_int,
    validate_queue_name,
    validate_path_not_empty,
)

FAKE_MODEL = "test_primitive_model_abc123"
VALID_S3 = "s3://my-bucket/some/key"


@pytest.fixture(autouse=True)
def register_fake_model():
    factory = ModelFactory()
    factory._models[FAKE_MODEL] = object  # type: ignore[assignment]
    yield
    factory._models.pop(FAKE_MODEL, None)


class TestValidateModelName:
    def test_valid_registered_model_returns_none(self):
        assert validate_model_name(FAKE_MODEL) is None

    def test_valid_custom_param_name_returns_none(self):
        assert validate_model_name(FAKE_MODEL, param_name="model") is None

    def test_invalid_model_raises_bad_parameter(self):
        with pytest.raises(click.BadParameter):
            validate_model_name("unknown_model_xyz")

    def test_invalid_model_param_hint_matches_param_name(self):
        with pytest.raises(click.BadParameter) as exc_info:
            validate_model_name("no_such_model", param_name="model_name")
        assert exc_info.value.param_hint == "model_name"

    def test_invalid_model_error_message_lists_registered_models(self):
        with pytest.raises(click.BadParameter, match=FAKE_MODEL):
            validate_model_name("unknown_model_xyz")

    def test_empty_registry_shows_none_registered_fallback(self):
        factory = ModelFactory()
        saved_models = factory._models.copy()
        factory._models.clear()
        try:
            with pytest.raises(click.BadParameter, match=r"\(none registered\)"):
                validate_model_name("any_model")
        finally:
            factory._models.update(saved_models)


# ---------------------------------------------------------------------------
# validate_s3_path
# ---------------------------------------------------------------------------


class TestValidateS3Path:
    def test_valid_s3_path_returns_none(self):
        assert validate_s3_path("s3://bucket/key") is None

    def test_valid_s3_path_with_deep_key_returns_none(self):
        assert validate_s3_path("s3://my-bucket/folder/sub/file.zip") is None

    def test_valid_custom_param_name_returns_none(self):
        assert validate_s3_path("s3://bucket/key", param_name="artifacts_path") is None

    def test_missing_s3_prefix_raises_bad_parameter(self):
        with pytest.raises(click.BadParameter):
            validate_s3_path("bucket/key")

    def test_missing_s3_prefix_param_hint_matches_param_name(self):
        with pytest.raises(click.BadParameter) as exc_info:
            validate_s3_path("not-s3", param_name="path")
        assert exc_info.value.param_hint == "path"

    def test_missing_s3_prefix_http_url_raises_bad_parameter(self):
        with pytest.raises(click.BadParameter):
            validate_s3_path("http://bucket/key")

    def test_empty_bucket_raises_bad_parameter(self):
        with pytest.raises(click.BadParameter):
            validate_s3_path("s3:///key")

    def test_missing_key_raises_bad_parameter(self):
        with pytest.raises(click.BadParameter):
            validate_s3_path("s3://bucket-only")

    def test_missing_key_param_hint_matches_param_name(self):
        with pytest.raises(click.BadParameter) as exc_info:
            validate_s3_path("s3://bucket-only", param_name="outputs_path")
        assert exc_info.value.param_hint == "outputs_path"


class TestValidatePositiveInt:
    def test_positive_value_returns_none(self):
        assert validate_positive_int(1) is None

    def test_large_positive_value_returns_none(self):
        assert validate_positive_int(1024) is None

    def test_custom_param_name_returns_none(self):
        assert validate_positive_int(8, param_name="core_count") is None

    def test_zero_raises_bad_parameter(self):
        with pytest.raises(click.BadParameter):
            validate_positive_int(0)

    def test_zero_param_hint_matches_param_name(self):
        with pytest.raises(click.BadParameter) as exc_info:
            validate_positive_int(0, param_name="core_count")
        assert exc_info.value.param_hint == "core_count"

    def test_negative_value_raises_bad_parameter(self):
        with pytest.raises(click.BadParameter):
            validate_positive_int(-1)

    def test_negative_large_value_raises_bad_parameter(self):
        with pytest.raises(click.BadParameter):
            validate_positive_int(-100)


# ---------------------------------------------------------------------------
# validate_optional_positive_int
# ---------------------------------------------------------------------------


class TestValidateOptionalPositiveInt:
    def test_none_returns_immediately_without_raising(self):
        assert validate_optional_positive_int(None) is None

    def test_positive_value_returns_none(self):
        assert validate_optional_positive_int(5) is None

    def test_large_positive_value_returns_none(self):
        assert validate_optional_positive_int(999) is None

    def test_custom_param_name_positive_returns_none(self):
        assert validate_optional_positive_int(2, param_name="max-cores-per-node") is None

    def test_zero_raises_bad_parameter(self):
        with pytest.raises(click.BadParameter):
            validate_optional_positive_int(0)

    def test_zero_param_hint_matches_param_name(self):
        with pytest.raises(click.BadParameter) as exc_info:
            validate_optional_positive_int(0, param_name="max-job-time-hours")
        assert exc_info.value.param_hint == "max-job-time-hours"

    def test_negative_value_raises_bad_parameter(self):
        with pytest.raises(click.BadParameter):
            validate_optional_positive_int(-3)


class TestValidateQueueName:
    def test_alphanumeric_name_returns_none(self):
        assert validate_queue_name("normal") is None

    def test_name_with_hyphen_returns_none(self):
        assert validate_queue_name("high-priority") is None

    def test_name_with_underscore_returns_none(self):
        assert validate_queue_name("batch_queue") is None

    def test_mixed_valid_chars_returns_none(self):
        assert validate_queue_name("Queue_01-abc") is None

    def test_custom_param_name_returns_none(self):
        assert validate_queue_name("normal", param_name="queue") is None

    def test_empty_string_raises_bad_parameter(self):
        with pytest.raises(click.BadParameter):
            validate_queue_name("")

    def test_empty_string_param_hint_matches_param_name(self):
        with pytest.raises(click.BadParameter) as exc_info:
            validate_queue_name("", param_name="queue")
        assert exc_info.value.param_hint == "queue"

    def test_space_in_name_raises_bad_parameter(self):
        with pytest.raises(click.BadParameter):
            validate_queue_name("invalid queue")

    def test_dot_in_name_raises_bad_parameter(self):
        with pytest.raises(click.BadParameter):
            validate_queue_name("queue.name")

    def test_slash_in_name_raises_bad_parameter(self):
        with pytest.raises(click.BadParameter):
            validate_queue_name("queue/name")

    def test_invalid_chars_param_hint_matches_param_name(self):
        with pytest.raises(click.BadParameter) as exc_info:
            validate_queue_name("bad queue!", param_name="queue")
        assert exc_info.value.param_hint == "queue"


class TestValidatePathNotEmpty:
    def test_non_empty_string_returns_none(self):
        assert validate_path_not_empty("/some/local/path") is None

    def test_single_character_returns_none(self):
        assert validate_path_not_empty("x") is None

    def test_custom_param_name_returns_none(self):
        assert validate_path_not_empty("/path/to/file", param_name="output") is None

    def test_empty_string_raises_bad_parameter(self):
        with pytest.raises(click.BadParameter):
            validate_path_not_empty("")

    def test_empty_string_param_hint_matches_param_name(self):
        with pytest.raises(click.BadParameter) as exc_info:
            validate_path_not_empty("", param_name="path")
        assert exc_info.value.param_hint == "path"

    def test_whitespace_only_raises_bad_parameter(self):
        with pytest.raises(click.BadParameter):
            validate_path_not_empty("   ")

    def test_tab_only_raises_bad_parameter(self):
        with pytest.raises(click.BadParameter):
            validate_path_not_empty("\t")

    def test_whitespace_only_param_hint_matches_param_name(self):
        with pytest.raises(click.BadParameter) as exc_info:
            validate_path_not_empty("  ", param_name="artifacts_path")
        assert exc_info.value.param_hint == "artifacts_path"
