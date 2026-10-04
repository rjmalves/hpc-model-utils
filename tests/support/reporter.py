"""Test-only ``StatusReporter`` fake that records every ``metadata``
call it receives and raises on any other call, so a test can assert
exactly which metadata keys/values a lifecycle step reports without
ever exercising ``announce_job``/``terminal``/``artifacts_path``/
``check_artifacts_path``.

Satisfies ``hpc_model_utils.core.lifecycle.run.StatusReporter``
structurally, the same way ``platform.modelops.Reporter`` does --
without inheriting from it.
"""

from __future__ import annotations

from hpc_model_utils.core.diagnosis import RunStatus


class RecordingReporter:
    def __init__(self) -> None:
        self.metadata_calls: list[tuple[str, str]] = []

    def announce_job(self, job_id: str) -> None:
        raise AssertionError("unexpected announce_job call")

    def terminal(self, status: RunStatus, annotation: str) -> bool:
        raise AssertionError("unexpected terminal call")

    def metadata(self, key: str, value: str) -> None:
        self.metadata_calls.append((key, value))

    def artifacts_path(self, uri: str) -> None:
        raise AssertionError("unexpected artifacts_path call")

    def check_artifacts_path(self, uri: str) -> None:
        raise AssertionError("unexpected check_artifacts_path call")
