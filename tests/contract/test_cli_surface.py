"""AC1 (R10/R69/R104): the golden-parse contract over the C1 surface.

Every line of ``fixtures/prd_command_lines.txt``, rendered in all 4
spelling/model-case variants, must parse to exactly the lifecycle
call its label names, with every argument -- including the raw
``''``/``\"''\"`` parent-path tokens and the quoted execution name --
arriving verbatim.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner

from hpc_model_utils.cli import workflow
from hpc_model_utils.cli.root import AppContext, cli
from hpc_model_utils.core.launch import Resources, Toolchain
from hpc_model_utils.core.lifecycle.run import SubmitRequest
from hpc_model_utils.core.plugin import ModelPlugin
from hpc_model_utils.core.settings import EngineSettings
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.models import get_plugin
from hpc_model_utils.platform.modelops import Reporter
from tests.support.command_lines import load_command_lines, render

_FIXTURE = Path(__file__).parent / "fixtures" / "prd_command_lines.txt"
_COMMAND_LINES = load_command_lines(_FIXTURE)

_EXECUTION_NAME = "PMO Água 'x' \"y\" $z"
_CLI_BIN = Path("/opt/hpc-model-utils/bin/hpc-model-utils")
_DEFAULT_MPICH_PATH = Path("/usr/local/mpich-4.2.0/bin")
_DEFAULT_SLURM_PATH = Path("/opt/slurm/bin")

_FIXED_VALUES: Mapping[str, str] = {
    "versions_uri": "s3://test-bucket/versoes/newave/v1.0/",
    "input_uri": "s3://test-bucket/entradas/deck.zip",
    "parent_path": "parent/dir two",
    "execution_name": _EXECUTION_NAME,
    "queue": "test-queue",
    "cores": "64",
    "max_cores_per_node": "32",
    "max_job_time_hours": "24",
    "mpich_path": "/opt/test/mpich/bin",
    "slurm_path": "/opt/test/slurm/bin",
    "synthesis_bin": "/opt/test/sintetizador/bin/sintetizador",
    "artifacts_uri": "s3://test-bucket/saidas/run-1/",
    "job_id": "123456",
    "inputs_uri": "s3://test-bucket/offline/inputs.zip",
    "outputs_uri": "s3://test-bucket/offline/outputs.zip",
    "cuts_uri": "s3://test-bucket/offline/cortes.zip",
}

_LABEL_TO_LIFECYCLE: Mapping[str, str] = {
    "fetch_executables": "fetch_executables",
    "fetch_inputs": "fetch_inputs",
    "fetch_inputs_empty_parent": "fetch_inputs",
    "fetch_inputs_quoted_empty_parent": "fetch_inputs",
    "extract_sanitize": "extract_sanitize_inputs",
    "preprocess": "preprocess",
    "run_full": "run",
    "run_minimal": "run",
    "run_skip": "run",
    "run_synthesis_bin": "run",
    "result_upload": "publish",
    "cancel": "cancel",
    "cancel_empty_job_id": "cancel",
    "ingest_offline": "ingest_offline_run",
}

_LIFECYCLE_NAMES: tuple[str, ...] = (
    "fetch_executables",
    "fetch_inputs",
    "extract_sanitize_inputs",
    "preprocess",
    "run",
    "publish",
    "cancel",
    "ingest_offline_run",
)


@dataclass(frozen=True)
class _RunExpectation:
    max_cores_per_node: int | None
    max_job_time_hours: int | None
    mpich_path: Path
    slurm_path: Path
    skip: bool
    synthesis_bin: Path | None


_RUN_EXPECTATIONS: Mapping[str, _RunExpectation] = {
    "run_full": _RunExpectation(
        max_cores_per_node=32,
        max_job_time_hours=24,
        mpich_path=Path(_FIXED_VALUES["mpich_path"]),
        slurm_path=Path(_FIXED_VALUES["slurm_path"]),
        skip=False,
        synthesis_bin=None,
    ),
    "run_minimal": _RunExpectation(
        max_cores_per_node=None,
        max_job_time_hours=None,
        mpich_path=_DEFAULT_MPICH_PATH,
        slurm_path=_DEFAULT_SLURM_PATH,
        skip=False,
        synthesis_bin=None,
    ),
    "run_skip": _RunExpectation(
        max_cores_per_node=None,
        max_job_time_hours=None,
        mpich_path=Path(_FIXED_VALUES["mpich_path"]),
        slurm_path=Path(_FIXED_VALUES["slurm_path"]),
        skip=True,
        synthesis_bin=None,
    ),
    "run_synthesis_bin": _RunExpectation(
        max_cores_per_node=None,
        max_job_time_hours=None,
        mpich_path=Path(_FIXED_VALUES["mpich_path"]),
        slurm_path=Path(_FIXED_VALUES["slurm_path"]),
        skip=False,
        synthesis_bin=Path(_FIXED_VALUES["synthesis_bin"]),
    ),
}


@dataclass
class _Call:
    args: tuple[object, ...]
    kwargs: dict[str, object]


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[_Call] = []

    def __call__(self, *args: object, **kwargs: object) -> None:
        self.calls.append(_Call(args, kwargs))


class _ListChannel:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write_line(self, line: str) -> None:
        self.lines.append(line)


def _app_context() -> AppContext:
    return AppContext(
        reporter=Reporter(_ListChannel(), enabled=True),
        channels=None,
        settings=EngineSettings(cli_bin=_CLI_BIN),
    )


@pytest.fixture
def _recorders(monkeypatch: pytest.MonkeyPatch) -> dict[str, _Recorder]:
    recorders = {name: _Recorder() for name in _LIFECYCLE_NAMES}
    for name, recorder in recorders.items():
        monkeypatch.setattr(workflow, name, recorder)
    return recorders


def _assert_run_call(
    label: str, call: _Call, plugin: ModelPlugin, values: Mapping[str, str]
) -> None:
    expected = _RUN_EXPECTATIONS[label]
    assert call.args[1] is plugin
    req = call.args[5]
    assert isinstance(req, SubmitRequest)
    assert req.resources == Resources(
        values["queue"],
        int(values["cores"]),
        expected.max_cores_per_node,
        expected.max_job_time_hours,
    )
    assert req.tools == Toolchain(
        expected.mpich_path, expected.slurm_path, _CLI_BIN
    )
    assert req.skip_model is expected.skip
    assert req.synthesis_bin == expected.synthesis_bin


def _assert_fetch_inputs_call(
    label: str, call: _Call, plugin: ModelPlugin, values: Mapping[str, str]
) -> None:
    expected_parent = {
        "fetch_inputs": values["parent_path"],
        "fetch_inputs_empty_parent": "",
        "fetch_inputs_quoted_empty_parent": "''",
    }[label]
    assert call.args[1] is plugin
    assert call.args[5] == values["input_uri"]
    assert call.kwargs["parent_path"] == expected_parent
    assert call.kwargs["delete"] is True


def _assert_call(
    label: str, call: _Call, plugin: ModelPlugin, values: Mapping[str, str]
) -> None:
    assert isinstance(call.args[0], Workspace)
    if label == "fetch_executables":
        assert call.args[1] is plugin
        assert call.args[5] == values["versions_uri"]
    elif label in (
        "fetch_inputs",
        "fetch_inputs_empty_parent",
        "fetch_inputs_quoted_empty_parent",
    ):
        _assert_fetch_inputs_call(label, call, plugin, values)
    elif label == "extract_sanitize":
        assert call.args[1] is plugin
    elif label == "preprocess":
        assert call.args[1] is plugin
        assert call.args[2] == values["execution_name"]
    elif label in _RUN_EXPECTATIONS:
        _assert_run_call(label, call, plugin, values)
    elif label == "result_upload":
        assert call.args[1] is plugin
        assert call.args[5] == values["artifacts_uri"]
    elif label in ("cancel", "cancel_empty_job_id"):
        expected_job_id = {
            "cancel": values["job_id"],
            "cancel_empty_job_id": None,
        }[label]
        assert call.kwargs["job_id"] == expected_job_id
    elif label == "ingest_offline":
        assert call.args[1] is plugin
        assert call.args[5] == (
            values["inputs_uri"],
            values["outputs_uri"],
            values["cuts_uri"],
        )
    else:
        raise AssertionError(f"no assertion wired for label {label!r}")


_CASES = list(
    itertools.product(_COMMAND_LINES, ("snake", "kebab"), ("NEWAVE", "newave"))
)
_CASE_IDS = [f"{label}-{spelling}-{token}" for label, spelling, token in _CASES]


@pytest.mark.parametrize("label,spelling,model_token", _CASES, ids=_CASE_IDS)
def test_golden_parse_renders_expected_lifecycle_call(
    label: str,
    spelling: str,
    model_token: str,
    _recorders: dict[str, _Recorder],
) -> None:
    values = {**_FIXED_VALUES, "model": model_token}
    args = render(_COMMAND_LINES[label], values)
    if spelling == "kebab":
        args = [args[0].replace("_", "-"), *args[1:]]

    result = CliRunner().invoke(cli, args, obj=_app_context())

    assert result.exit_code == 0, result.output
    lifecycle_name = _LABEL_TO_LIFECYCLE[label]
    for name, recorder in _recorders.items():
        expected_count = 1 if name == lifecycle_name else 0
        assert len(recorder.calls) == expected_count, name

    plugin = get_plugin(model_token)
    _assert_call(label, _recorders[lifecycle_name].calls[0], plugin, values)
