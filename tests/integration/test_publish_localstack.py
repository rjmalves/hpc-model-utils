"""AC6/ADR-042: the full ``publish`` round trip against LocalStack.

Marked ``integration`` like ``tests/integration/infra/test_object_store.py``:
there is no Docker on the dev host, so this runs only in the CI
integration-test job. The PUT order itself is unit-tested against
``RecordingObjectStore`` (``tests/unit/core/test_publish.py``), because
S3 ``LastModified`` timestamps are too coarse to order; this test
checks only that the final bucket listing matches the published
manifest.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mypy_boto3_s3 import S3Client

from hpc_model_utils.core.diagnosis import Diagnosis, RunStatus, utc_now_iso
from hpc_model_utils.core.lifecycle.publish import publish
from hpc_model_utils.core.outputs import RealizedOutputs
from hpc_model_utils.core.state import (
    FinalizeRecord,
    RunState,
    StateStore,
    StepOutcome,
    ToolInfo,
    write_finalize,
)
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.infra.s3 import Boto3ObjectStore, S3Uri
from hpc_model_utils.platform.modelops import Reporter
from tests.support.fake_plugin import FakePlugin

pytestmark = pytest.mark.integration

_HASH = "b" * 64


class _ListChannel:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write_line(self, line: str) -> None:
        self.lines.append(line)


def test_publish_full_round_trip_bucket_listing_matches_manifest(
    test_bucket: str, localstack_s3_client: S3Client, tmp_path: Path
) -> None:
    ws = Workspace.at(tmp_path)
    ws.ensure_layout()
    diagnosis = Diagnosis(
        status=RunStatus.SUCCESS,
        rule_id="core.ok",
        reason="model converged",
        at=utc_now_iso(),
    )
    state = RunState(
        run_id="run-1",
        plugin="fake",
        tool=ToolInfo(name="hpc-model-utils", version="2.0.0"),
        reported_job_id="222",
        diagnosis=diagnosis,
    )
    StateStore(ws).save(state)

    (ws.outputs_dir / "cortes.zip").write_bytes(b"cortes-bytes")
    outputs = RealizedOutputs(
        deck=None, archives=(".hpcmu/outputs/cortes.zip",), raw=()
    )
    write_finalize(
        ws,
        FinalizeRecord(
            run_id=state.run_id,
            diagnosis=diagnosis,
            postprocess=StepOutcome("postprocess", True, "", 0.1),
            synthesis=StepOutcome("synthesis", True, "", 0.1),
            outputs=outputs,
        ),
    )

    store = Boto3ObjectStore(localstack_s3_client)
    channel = _ListChannel()
    reporter = Reporter(channel, enabled=True)
    uri = f"s3://{test_bucket}/artifacts/{_HASH}/"

    publish(ws, FakePlugin(), store, reporter, StateStore(ws), uri)

    listed = localstack_s3_client.list_objects_v2(
        Bucket=test_bucket, Prefix=f"artifacts/{_HASH}/"
    )
    keys = {obj["Key"] for obj in listed.get("Contents", [])}

    run_json = json.loads(
        store.get_bytes(
            S3Uri(test_bucket, f"artifacts/{_HASH}/saidas/run.json")
        )
    )
    manifest_keys = {
        f"artifacts/{_HASH}/{entry['path']}" for entry in run_json["artifacts"]
    }
    manifest_keys.add(f"artifacts/{_HASH}/saidas/run.json")

    assert keys == manifest_keys
