from __future__ import annotations

import pytest

from hpc_model_utils.infra.s3 import S3Uri


def test_parse_valid_uri_returns_s3uri() -> None:
    assert S3Uri.parse("s3://my-bucket/path/to/key.txt") == S3Uri(
        "my-bucket", "path/to/key.txt"
    )


def test_parse_empty_key_returns_s3uri_with_empty_key() -> None:
    assert S3Uri.parse("s3://my-bucket") == S3Uri("my-bucket", "")
    assert S3Uri.parse("s3://my-bucket/") == S3Uri("my-bucket", "")


def test_parse_single_slash_scheme_raises_value_error() -> None:
    with pytest.raises(ValueError, match="not an s3://"):
        S3Uri.parse("s3:/x")


def test_parse_empty_bucket_raises_value_error() -> None:
    with pytest.raises(ValueError, match="invalid bucket name"):
        S3Uri.parse("s3://")


def test_parse_invalid_bucket_chars_raises_value_error() -> None:
    with pytest.raises(ValueError, match="invalid bucket name"):
        S3Uri.parse("s3://Bad_Bucket/k")


def test_parse_wrong_scheme_raises_value_error() -> None:
    with pytest.raises(ValueError, match="not an s3://"):
        S3Uri.parse("http://b/k")


def test_parse_bucket_with_trailing_newline_raises_value_error() -> None:
    with pytest.raises(ValueError, match="invalid bucket name"):
        S3Uri.parse("s3://bucket\n/k")


def test_join_collapses_duplicate_slashes() -> None:
    base = S3Uri("bucket", "a/")
    assert base.join("b", "c") == S3Uri("bucket", "a/b/c")


def test_join_never_introduces_leading_slash() -> None:
    base = S3Uri("bucket", "")
    assert base.join("a", "b").key == "a/b"


def test_basename_returns_last_segment() -> None:
    assert S3Uri("bucket", "a/b/deck.zip").basename == "deck.zip"


def test_basename_empty_key_returns_empty_string() -> None:
    assert S3Uri("bucket", "").basename == ""
    assert S3Uri("bucket", "a/b/").basename == ""


def test_str_roundtrips_through_parse() -> None:
    uri = S3Uri("my-bucket", "path/to/key.txt")
    assert S3Uri.parse(str(uri)) == uri


def test_str_returns_canonical_format() -> None:
    assert str(S3Uri("bucket", "key")) == "s3://bucket/key"
