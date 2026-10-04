"""AC/ADR-043/R94: ``fetch_inputs``'s cross-bucket parent fetch against
LocalStack (ticket-044). Marked ``integration`` like
``tests/integration/test_publish_localstack.py``: there is no Docker on
the dev host, so this runs only in the CI integration-test job. The
parent's own bucket is created and torn down inside the test, separate
from the ``test_bucket`` fixture's input bucket, to exercise the R94
cross-bucket fetch for real.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from mypy_boto3_s3 import S3Client

from hpc_model_utils.core.lifecycle.fetch import fetch_inputs
from hpc_model_utils.core.state import StateStore
from hpc_model_utils.core.workspace import Workspace
from hpc_model_utils.infra.s3 import Boto3ObjectStore
from tests.support.fake_plugin import ParentPlugin
from tests.support.reporter import RecordingReporter

pytestmark = pytest.mark.integration


def _delete_bucket(client: S3Client, bucket: str) -> None:
    objects = client.list_objects_v2(Bucket=bucket)
    if "Contents" in objects:
        client.delete_objects(
            Bucket=bucket,
            Delete={
                "Objects": [{"Key": obj["Key"]} for obj in objects["Contents"]]
            },
        )
    client.delete_bucket(Bucket=bucket)


def test_fetch_inputs_parent_from_own_bucket_against_localstack(
    test_bucket: str, localstack_s3_client: S3Client, tmp_path: Path
) -> None:
    ws = Workspace.at(tmp_path)
    ws.ensure_layout()
    store = Boto3ObjectStore(localstack_s3_client)
    reporter = RecordingReporter()

    localstack_s3_client.put_object(
        Bucket=test_bucket, Key="entradas/deck.zip", Body=b"deck-bytes"
    )

    parent_bucket = f"{test_bucket}-parent"
    localstack_s3_client.create_bucket(Bucket=parent_bucket)
    try:
        localstack_s3_client.put_object(
            Bucket=parent_bucket,
            Key="artifacts/abc/saidas/metadata.modelops",
            Body=json.dumps(
                {
                    "model_name": "NEWAVE",
                    "status": "SUCCESS",
                    "study_starting_date": "2025-10-01T00:00:00+00:00",
                }
            ).encode("utf-8"),
        )
        localstack_s3_client.put_object(
            Bucket=parent_bucket,
            Key="artifacts/abc/saidas/cortes.zip",
            Body=b"cortes-bytes",
        )

        state = fetch_inputs(
            ws,
            ParentPlugin(),
            store,
            reporter,
            StateStore(ws),
            f"s3://{test_bucket}/entradas/deck.zip",
            parent_path=f"s3://{parent_bucket}/artifacts/abc",
            delete=True,
        )

        assert ws.eco_deck_path.read_bytes() == b"deck-bytes"
        assert (ws.parent_dir / "cortes.zip").read_bytes() == b"cortes-bytes"
        assert state.parent is not None
        assert state.parent.path == f"s3://{parent_bucket}/artifacts/abc"

        with pytest.raises(ClientError):
            localstack_s3_client.head_object(
                Bucket=test_bucket, Key="entradas/deck.zip"
            )
    finally:
        _delete_bucket(localstack_s3_client, parent_bucket)
