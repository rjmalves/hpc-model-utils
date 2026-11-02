"""ADR-098/ADR-099/ADR-100/ADR-101/ADR-102 tests for the cobre parent-run
checks (ticket-002, ticket-003).

The version gate's regex is a verbatim copy of ``deploy/modelops/apply.py``
with no drift test (R14, operator decision), so the literal case tables
below pin the copy's behaviour: what it accepts and, as much, what it
rejects. The ``restore`` tests pin the check order and that a rejected
parent leaves ``<case>/output`` absent.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from pathlib import Path

import pytest

from hpc_model_utils.core.errors import DataError, UsageError
from hpc_model_utils.core.state import ParentInfo
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.infra.errors import UnsafeArchiveError
from hpc_model_utils.models.cobre import case, policy
from tests.support.cobre_case import (
    case_members,
    cobre_workspace,
    policy_members,
    policy_zip,
)

_PARENT_URI = "s3://outputs-bucket/artifacts/parenthash"
_PARENT = ParentInfo(_PARENT_URI, "COBRE", "2024-01-01T00:00:00+00:00")
_NOT_A_TAG = "is not a release tag (vX.Y.Z) or a patched build of one"
_UNKNOWN_CHILD = (
    "cobre model version unknown: run check_and_fetch_executables first"
)
_NO_PARENT_VERSION = (
    f"parent metadata.modelops has no model_version ({_PARENT_URI})"
)


def _bad_parent_message(version: str) -> str:
    return f"parent model_version {version!r} {_NOT_A_TAG} ({_PARENT_URI})"


def _bad_child_message(version: str) -> str:
    return f"cobre model version {version!r} {_NOT_A_TAG}"


@pytest.mark.parametrize(
    ("parent_version", "child_version"),
    [
        pytest.param("v0.19.1", "v0.19.1", id="identical"),
        pytest.param("v0.19.1", "v0.19.1-fixinflows", id="child-patched"),
        pytest.param("v0.19.1-a", "v0.19.1-b", id="both-patched"),
        pytest.param("v0.19.1-fix.inflows", "v0.19.1", id="dotted-suffix"),
    ],
)
def test_check_versions_same_release_tag_passes(
    caplog: pytest.LogCaptureFixture,
    parent_version: str,
    child_version: str,
) -> None:
    with caplog.at_level(logging.DEBUG, logger=policy.logger.name):
        policy.check_versions(_PARENT, parent_version, child_version)

    assert [r for r in caplog.records if r.name == policy.logger.name] == []


@pytest.mark.parametrize(
    ("parent_version", "parent_tag", "child_version", "child_tag"),
    [
        pytest.param(
            "v0.19.0", "v0.19.0", "v0.19.1", "v0.19.1", id="patch-differs"
        ),
        pytest.param(
            "v0.20.1",
            "v0.20.1",
            "v0.19.1-fixinflows",
            "v0.19.1",
            id="minor-differs-child-patched",
        ),
        pytest.param(
            "v1.19.1", "v1.19.1", "v0.19.1", "v0.19.1", id="major-differs"
        ),
    ],
)
def test_check_versions_release_tag_mismatch_is_data_error(
    parent_version: str,
    parent_tag: str,
    child_version: str,
    child_tag: str,
) -> None:
    with pytest.raises(DataError) as excinfo:
        policy.check_versions(_PARENT, parent_version, child_version)

    assert str(excinfo.value) == (
        f"parent model_version {parent_version!r} (release {parent_tag}) "
        f"does not match this run's {child_version!r} "
        f"(release {child_tag}) ({_PARENT_URI})"
    )


@pytest.mark.parametrize(
    ("parent_version", "message"),
    [
        pytest.param(None, _NO_PARENT_VERSION, id="missing"),
        pytest.param("0.19.1", _bad_parent_message("0.19.1"), id="no-v"),
        pytest.param("v0.19", _bad_parent_message("v0.19"), id="two-parts"),
        pytest.param(
            "v0.19.1-",
            _bad_parent_message("v0.19.1-"),
            id="empty-suffix",
        ),
        pytest.param(
            "v0.19.1+build",
            _bad_parent_message("v0.19.1+build"),
            id="build-metadata",
        ),
        pytest.param(
            "V0.19.1", _bad_parent_message("V0.19.1"), id="uppercase-v"
        ),
        pytest.param(
            "v0.19.1 ",
            _bad_parent_message("v0.19.1 "),
            id="trailing-space",
        ),
    ],
)
def test_check_versions_bad_parent_version_is_data_error(
    parent_version: str | None, message: str
) -> None:
    with pytest.raises(DataError) as excinfo:
        policy.check_versions(_PARENT, parent_version, "v0.19.1")

    assert str(excinfo.value) == message


@pytest.mark.parametrize(
    "child_version",
    [
        pytest.param("0.19.1", id="no-v"),
        pytest.param("v0.19.1+build", id="build-metadata"),
    ],
)
def test_check_versions_bad_child_version_is_data_error(
    child_version: str,
) -> None:
    with pytest.raises(DataError) as excinfo:
        policy.check_versions(_PARENT, "v0.19.1", child_version)

    assert str(excinfo.value) == _bad_child_message(child_version)


@pytest.mark.parametrize(
    "parent_version",
    [
        pytest.param(None, id="parent-missing"),
        pytest.param("garbage", id="parent-garbage"),
        pytest.param("v0.19.1", id="parent-valid"),
    ],
)
def test_check_versions_missing_child_version_is_usage_error_first(
    parent_version: str | None,
) -> None:
    with pytest.raises(UsageError) as excinfo:
        policy.check_versions(_PARENT, parent_version, None)

    assert str(excinfo.value) == _UNKNOWN_CHILD


_CASE_DATE = "2024-01-01T00:00:00+00:00"
_POLICY_PATH_MESSAGE = (
    "caso_cobre/config.json: policy.path must be absent, ./policy or policy "
    "in a parent run (the parent policy is restored only to "
    "caso_cobre/output/policy)"
)
_NO_MANIFEST = "parent policy.zip has no policy/manifest.bin"
_NO_CUTS = "parent policy.zip has no file under policy/cuts/"
_BAD_POLICY_PATHS = ("/abs/policy", "./custom", "./policy/")


def _outside(entry: str) -> str:
    return f"parent policy.zip has an entry outside policy/: {entry!r}"


def _message(excinfo: pytest.ExceptionInfo[Exception], tmp_path: Path) -> str:
    message = str(excinfo.value)
    assert str(tmp_path) not in message
    return message


def _config(value: object) -> bytes:
    return json.dumps(value).encode("utf-8")


def _config_deck(
    tmp_path: Path, config: bytes | None, *, top: str | None = "caso_cobre"
) -> Workspace:
    members = case_members()
    if config is None:
        del members["config.json"]
    else:
        members["config.json"] = config
    return cobre_workspace(tmp_path, top=top, members=members).ws


def _archive(tmp_path: Path, content: Mapping[str, bytes] | bytes) -> Path:
    dest = tmp_path / "parent" / "policy.zip"
    if isinstance(content, bytes):
        dest.parent.mkdir(parents=True)
        dest.write_bytes(content)
        return dest
    return policy_zip(dest, content)


def _output(ws: Workspace) -> Path:
    return case.case_root(ws) / "output"


@pytest.mark.parametrize(
    ("members", "basis"),
    [
        pytest.param(policy_members(), 1, id="with-basis"),
        pytest.param(policy_members(basis=0), 0, id="empty-basis"),
        pytest.param(
            {"policy/": b"", **policy_members()}, 1, id="policy-directory"
        ),
        pytest.param(
            {**policy_members(), "policy/empty/": b""},
            1,
            id="directory-entry-not-extracted",
        ),
    ],
)
def test_restore_extracts_members_creates_basis_and_logs_counts(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    members: Mapping[str, bytes],
    basis: int,
) -> None:
    ws = cobre_workspace(tmp_path).ws
    archive = _archive(tmp_path, members)

    with caplog.at_level(logging.INFO, logger=policy.logger.name):
        policy.restore(ws, archive, _CASE_DATE)

    output = _output(ws)
    files = {n: d for n, d in members.items() if not n.endswith("/")}
    assert {
        p.relative_to(output).as_posix(): p.read_bytes()
        for p in output.rglob("*")
        if p.is_file()
    } == files
    assert {
        p.relative_to(output).as_posix()
        for p in output.rglob("*")
        if p.is_dir()
    } == {"policy", "policy/cuts", "policy/basis", "policy/states"}
    assert [
        r.getMessage() for r in caplog.records if r.name == policy.logger.name
    ] == [
        "parent policy restored into caso_cobre/output/policy: "
        f"manifest.bin, 1 cuts, {basis} basis, 1 states"
    ]


@pytest.mark.parametrize(
    ("content", "error", "message"),
    [
        pytest.param(
            b"not a zip",
            UnsafeArchiveError,
            "not a zip archive: policy.zip",
            id="not-a-zip",
        ),
        pytest.param(
            {**policy_members(), "../escape.bin": b"x"},
            UnsafeArchiveError,
            "member path escapes archive root: '../escape.bin'",
            id="escapes-root",
        ),
        pytest.param(
            {**policy_members(), ".hpcmu/state.json": b"x"},
            UnsafeArchiveError,
            "member path under reserved .hpcmu/: '.hpcmu/state.json'",
            id="reserved-hpcmu",
        ),
        pytest.param(
            {**policy_members(), "README.txt": b"x"},
            DataError,
            _outside("README.txt"),
            id="file-outside",
        ),
        pytest.param(
            {**policy_members(), "policy": b"x"},
            DataError,
            _outside("policy"),
            id="file-named-policy",
        ),
        pytest.param(
            {**policy_members(), "other/": b""},
            DataError,
            _outside("other/"),
            id="directory-outside",
        ),
        pytest.param(
            {**policy_members(), "zzz.txt": b"x", "other/": b""},
            DataError,
            _outside("other/"),
            id="first-sorted-entry-outside",
        ),
        pytest.param(
            policy_members(manifest=False),
            DataError,
            _NO_MANIFEST,
            id="no-manifest",
        ),
        pytest.param(policy_members(cuts=0), DataError, _NO_CUTS, id="no-cuts"),
        pytest.param(
            {**policy_members(cuts=0), "policy/cuts/": b""},
            DataError,
            _NO_CUTS,
            id="cuts-directory-only",
        ),
    ],
)
def test_restore_rejects_bad_archives_before_writing(
    tmp_path: Path,
    content: Mapping[str, bytes] | bytes,
    error: type[Exception],
    message: str,
) -> None:
    ws = cobre_workspace(tmp_path).ws
    archive = _archive(tmp_path, content)

    with pytest.raises(error) as excinfo:
        policy.restore(ws, archive, _CASE_DATE)

    assert _message(excinfo, tmp_path) == message
    assert not _output(ws).exists()


@pytest.mark.parametrize(
    ("config", "pattern"),
    [
        pytest.param(
            None, r"^missing caso_cobre/config\.json$", id="missing-config"
        ),
        pytest.param(
            b"{", r"^caso_cobre/config\.json: invalid JSON", id="invalid-json"
        ),
        pytest.param(
            b"[]",
            r"^caso_cobre/config\.json: not a JSON object$",
            id="not-an-object",
        ),
        *(
            pytest.param(
                _config({"policy": {"path": path}}),
                f"^{re.escape(_POLICY_PATH_MESSAGE)}$",
                id=f"rejected-{path}",
            )
            for path in _BAD_POLICY_PATHS
        ),
        pytest.param(case_members()["config.json"], None, id="no-policy"),
        pytest.param(
            _config({"policy": {"path": "./policy"}}), None, id="dot-policy"
        ),
        pytest.param(
            _config({"policy": {"path": "policy"}}), None, id="bare-policy"
        ),
        pytest.param(_config({"policy": "x"}), None, id="policy-not-object"),
        pytest.param(
            _config({"policy": {"path": 5}}), None, id="path-not-string"
        ),
    ],
)
def test_restore_config_rows(
    tmp_path: Path, config: bytes | None, pattern: str | None
) -> None:
    ws = _config_deck(tmp_path, config)
    archive = _archive(tmp_path, policy_members())

    if pattern is None:
        policy.restore(ws, archive, _CASE_DATE)
        assert (_output(ws) / "policy" / "manifest.bin").is_file()
        return
    with pytest.raises(DataError) as excinfo:
        policy.restore(ws, archive, _CASE_DATE)
    message = _message(excinfo, tmp_path)
    assert re.search(pattern, message)
    assert not any(path in message for path in _BAD_POLICY_PATHS)
    assert not _output(ws).exists()


@pytest.mark.parametrize(
    ("parent_date", "accepted"),
    [
        pytest.param("2024-02-01T00:00:00+00:00", False, id="other-date"),
        pytest.param("2024-13-01", False, id="unparseable"),
        pytest.param("2024-01-01T00:00:00", False, id="naive"),
        pytest.param("2023-12-31T21:00:00-03:00", True, id="same-instant"),
    ],
)
def test_restore_start_date_rows(
    tmp_path: Path, parent_date: str, accepted: bool
) -> None:
    ws = cobre_workspace(tmp_path).ws
    archive = _archive(tmp_path, policy_members())

    if accepted:
        policy.restore(ws, archive, parent_date)
        assert (_output(ws) / "policy" / "manifest.bin").is_file()
        return
    with pytest.raises(DataError) as excinfo:
        policy.restore(ws, archive, parent_date)
    assert _message(excinfo, tmp_path) == (
        f"parent study_starting_date {parent_date!r} does not match this "
        f"case's start date {_CASE_DATE!r} (caso_cobre/stages.json)"
    )
    assert not _output(ws).exists()


@pytest.mark.parametrize(
    ("members", "message"),
    [
        pytest.param(
            {**policy_members(manifest=False), "README.txt": b"x"},
            _outside("README.txt"),
            id="entries-before-manifest",
        ),
        pytest.param(
            policy_members(manifest=False, cuts=0),
            _NO_MANIFEST,
            id="manifest-before-cuts",
        ),
        pytest.param(
            policy_members(manifest=False),
            _NO_MANIFEST,
            id="archive-before-config-and-date",
        ),
        pytest.param(
            policy_members(), _POLICY_PATH_MESSAGE, id="policy-path-before-date"
        ),
    ],
)
def test_restore_checks_run_in_order(
    tmp_path: Path, members: Mapping[str, bytes], message: str
) -> None:
    ws = _config_deck(tmp_path, _config({"policy": {"path": "/abs"}}))
    archive = _archive(tmp_path, members)

    with pytest.raises(DataError) as excinfo:
        policy.restore(ws, archive, "2024-02-01T00:00:00+00:00")

    assert _message(excinfo, tmp_path) == message
    assert not _output(ws).exists()


def test_restore_flat_case_labels(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bad = _config_deck(
        tmp_path / "bad", _config({"policy": {"path": "/abs"}}), top=None
    )
    with pytest.raises(DataError) as excinfo:
        policy.restore(
            bad, _archive(tmp_path / "bad", policy_members()), _CASE_DATE
        )
    assert _message(excinfo, tmp_path) == (
        "config.json: policy.path must be absent, ./policy or policy in a "
        "parent run (the parent policy is restored only to output/policy)"
    )

    good = cobre_workspace(tmp_path / "good", top=None).ws
    with caplog.at_level(logging.INFO, logger=policy.logger.name):
        policy.restore(
            good, _archive(tmp_path / "good", policy_members()), _CASE_DATE
        )
    assert [
        r.getMessage() for r in caplog.records if r.name == policy.logger.name
    ] == [
        "parent policy restored into output/policy: "
        "manifest.bin, 1 cuts, 1 basis, 1 states"
    ]
    assert (good.root / "output" / "policy" / "basis").is_dir()
