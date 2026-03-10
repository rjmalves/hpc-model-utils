"""Unit tests for the 3 custom Click parameter types in app/click_types.py.

Each type's ``convert()`` method is tested with:
- Valid inputs (expects the converted value returned)
- All invalid input branches (expects click.exceptions.BadParameter)

The ``ModelNameType.convert()`` ImportError fallback is tested by patching
the lazy import inside the method.
"""

from unittest.mock import patch

import pytest
import click

from app.adapter.repository.abstractmodel import ModelFactory
from app.click_types import ModelNameType, S3PathType, PositiveIntType

FAKE_MODEL = "test_click_types_model_xyz987"


@pytest.fixture(autouse=True)
def register_fake_model():
    factory = ModelFactory()
    factory._models[FAKE_MODEL] = object  # type: ignore[assignment]
    yield
    factory._models.pop(FAKE_MODEL, None)


class TestModelNameType:
    def setup_method(self):
        self.type = ModelNameType()

    def test_valid_model_returns_value_string(self):
        result = self.type.convert(FAKE_MODEL, None, None)
        assert result == FAKE_MODEL

    def test_valid_model_returns_same_string_instance(self):
        result = self.type.convert(FAKE_MODEL, None, None)
        assert isinstance(result, str)

    def test_invalid_model_raises_bad_parameter(self):
        with pytest.raises(click.exceptions.BadParameter):
            self.type.convert("no_such_model_abc", None, None)

    def test_non_string_input_raises_bad_parameter(self):
        with pytest.raises(click.exceptions.BadParameter):
            self.type.convert(42, None, None)

    def test_none_input_raises_bad_parameter(self):
        with pytest.raises(click.exceptions.BadParameter):
            self.type.convert(None, None, None)

    def test_list_input_raises_bad_parameter(self):
        with pytest.raises(click.exceptions.BadParameter):
            self.type.convert(["model"], None, None)

    def test_import_error_fallback_returns_value_unchanged(self):
        with patch.dict(
            "sys.modules", {"app.adapter.repository.abstractmodel": None}
        ):
            result = self.type.convert("any_model_name", None, None)
        assert result == "any_model_name"

    def test_type_name_attribute(self):
        assert self.type.name == "MODEL_NAME"


class TestS3PathType:
    def setup_method(self):
        self.type = S3PathType()

    def test_valid_s3_path_returns_string(self):
        result = self.type.convert("s3://bucket/key", None, None)
        assert result == "s3://bucket/key"

    def test_valid_s3_path_with_deep_key_returns_string(self):
        result = self.type.convert("s3://my-bucket/folder/sub/file.zip", None, None)
        assert result == "s3://my-bucket/folder/sub/file.zip"

    def test_missing_s3_prefix_raises_bad_parameter(self):
        with pytest.raises(click.exceptions.BadParameter):
            self.type.convert("bucket/key", None, None)

    def test_http_url_raises_bad_parameter(self):
        with pytest.raises(click.exceptions.BadParameter):
            self.type.convert("http://bucket/key", None, None)

    def test_empty_bucket_raises_bad_parameter(self):
        with pytest.raises(click.exceptions.BadParameter):
            self.type.convert("s3:///key", None, None)

    def test_missing_key_raises_bad_parameter(self):
        with pytest.raises(click.exceptions.BadParameter):
            self.type.convert("s3://bucket-only", None, None)

    def test_missing_key_with_trailing_slash_raises_bad_parameter(self):
        with pytest.raises(click.exceptions.BadParameter):
            self.type.convert("s3://bucket/", None, None)

    def test_non_string_input_raises_bad_parameter(self):
        with pytest.raises(click.exceptions.BadParameter):
            self.type.convert(123, None, None)

    def test_none_input_raises_bad_parameter(self):
        with pytest.raises(click.exceptions.BadParameter):
            self.type.convert(None, None, None)

    def test_type_name_attribute(self):
        assert self.type.name == "S3_PATH"


class TestPositiveIntType:
    def setup_method(self):
        self.type = PositiveIntType()

    def test_none_passthrough_returns_none(self):
        result = self.type.convert(None, None, None)
        assert result is None

    def test_valid_int_returns_int(self):
        result = self.type.convert(5, None, None)
        assert result == 5
        assert isinstance(result, int)

    def test_valid_large_int_returns_int(self):
        result = self.type.convert(1024, None, None)
        assert result == 1024

    def test_valid_string_digit_returns_int(self):
        result = self.type.convert("8", None, None)
        assert result == 8
        assert isinstance(result, int)

    def test_valid_string_large_number_returns_int(self):
        result = self.type.convert("256", None, None)
        assert result == 256

    def test_zero_int_raises_bad_parameter(self):
        with pytest.raises(click.exceptions.BadParameter):
            self.type.convert(0, None, None)

    def test_zero_string_raises_bad_parameter(self):
        with pytest.raises(click.exceptions.BadParameter):
            self.type.convert("0", None, None)

    def test_negative_int_raises_bad_parameter(self):
        with pytest.raises(click.exceptions.BadParameter):
            self.type.convert(-1, None, None)

    def test_negative_string_raises_bad_parameter(self):
        with pytest.raises(click.exceptions.BadParameter):
            self.type.convert("-5", None, None)

    def test_non_numeric_string_raises_bad_parameter(self):
        with pytest.raises(click.exceptions.BadParameter):
            self.type.convert("abc", None, None)

    def test_float_string_raises_bad_parameter(self):
        with pytest.raises(click.exceptions.BadParameter):
            self.type.convert("3.14", None, None)

    def test_bool_true_raises_bad_parameter(self):
        with pytest.raises(click.exceptions.BadParameter):
            self.type.convert(True, None, None)

    def test_bool_false_raises_bad_parameter(self):
        with pytest.raises(click.exceptions.BadParameter):
            self.type.convert(False, None, None)

    def test_list_input_raises_bad_parameter(self):
        with pytest.raises(click.exceptions.BadParameter):
            self.type.convert([1], None, None)

    def test_type_name_attribute(self):
        assert self.type.name == "POSITIVE_INT"
