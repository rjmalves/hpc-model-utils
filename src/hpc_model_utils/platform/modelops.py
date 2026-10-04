"""ADR-006/ADR-007/ADR-008/R30/R31/R33/R34/R35/R11/R92: the ModelOps hook
reporter - the only writer of ${CurrentExecution.*} hooks.
"""

from __future__ import annotations

import enum
import logging
import os
import re
from collections.abc import Mapping
from typing import Protocol

from hpc_model_utils.core.diagnosis import RunStatus
from hpc_model_utils.platform.encoding import (
    ANNOTATION_CAP,
    METADATA_VALUE_CAP,
    csharp_literal,
    find_platform_identifiers,
)

logger = logging.getLogger(__name__)

_METADATA_KEY_PATTERN = re.compile(r"[a-z0-9_.]+")
_JOB_ID_PATTERN = re.compile(r"[0-9]+")
_ARTIFACTS_URI_PATTERN = re.compile(
    r"s3://[a-z0-9.-]{3,63}/artifacts/[0-9a-f]{64}/?"
)


class HookMethod(enum.StrEnum):
    SET_SUCCESS = "SetSuccess"
    SET_MODEL_ERROR = "SetModelError"
    SET_DATA_ERROR = "SetDataError"
    SET_RUNTIME_ERROR = "SetRuntimeError"
    SET_ANNOTATION = "SetAnnotation"
    SET_METADATA = "SetMetadata"
    SET_EXECUTION_ARTIFACTS_PATH = "SetExecutionArtifactsPath"


STATUS_HOOKS: Mapping[RunStatus, HookMethod] = {
    RunStatus.SUCCESS: HookMethod.SET_SUCCESS,
    RunStatus.INFEASIBLE: HookMethod.SET_MODEL_ERROR,
    RunStatus.DATA_ERROR: HookMethod.SET_DATA_ERROR,
    RunStatus.RUNTIME_ERROR: HookMethod.SET_RUNTIME_ERROR,
    RunStatus.TIMEOUT: HookMethod.SET_RUNTIME_ERROR,
    RunStatus.INFRA_ERROR: HookMethod.SET_RUNTIME_ERROR,
    RunStatus.LICENSE_ERROR: HookMethod.SET_RUNTIME_ERROR,
    RunStatus.CANCELLED: HookMethod.SET_RUNTIME_ERROR,
    RunStatus.UNKNOWN: HookMethod.SET_RUNTIME_ERROR,
}


class ProtocolChannel(Protocol):
    def write_line(self, line: str) -> None: ...


class Reporter:
    """The only writer of `${CurrentExecution.*}` hook lines."""

    def __init__(self, channel: ProtocolChannel, *, enabled: bool) -> None:
        self._channel = channel
        self._enabled = enabled
        self._emitted_keys: set[str] = set()
        self._artifacts_emitted = False
        self._terminal_emitted = False

    @classmethod
    def from_env(
        cls, channel: ProtocolChannel, env: Mapping[str, str] = os.environ
    ) -> Reporter:
        return cls(channel, enabled=env.get("HPCMU_PLATFORM", "on") != "off")

    @property
    def terminal_emitted(self) -> bool:
        return self._terminal_emitted

    def metadata(self, key: str, value: str) -> None:
        if not _METADATA_KEY_PATTERN.fullmatch(key):
            raise ValueError(f"invalid metadata key: {key!r}")
        if find_platform_identifiers(key):
            raise ValueError(
                f"metadata key collides with a platform identifier: {key!r}"
            )
        if key in self._emitted_keys:
            logger.debug("metadata key %r already emitted; dropping", key)
            return
        if not isinstance(value, str):
            raise TypeError(f"value must be str, got {type(value)!r}")
        line = self._build_line(
            HookMethod.SET_METADATA,
            csharp_literal(key, None, break_identifiers=False),
            csharp_literal(value, METADATA_VALUE_CAP),
        )
        self._emitted_keys.add(key)
        if not self._enabled:
            return
        try:
            self._channel.write_line(line)
        except Exception:
            # One line, one outcome: either it reached the platform or it
            # did not, so on failure the key is un-recorded for a clean
            # retry. The channel is an external Protocol boundary, so its
            # failure type cannot be narrowed further than Exception.
            self._emitted_keys.discard(key)
            raise

    def duration(self, command: str, seconds: float) -> None:
        self.metadata(f"duration_seconds.{command}", f"{seconds:.2f}")

    def check_artifacts_path(self, uri: str) -> None:
        if not _ARTIFACTS_URI_PATTERN.fullmatch(uri):
            raise ValueError(f"invalid artifacts uri: {uri!r}")
        if find_platform_identifiers(uri):
            raise ValueError(
                f"artifacts uri collides with a platform identifier: {uri!r}"
            )

    def artifacts_path(self, uri: str) -> None:
        self.check_artifacts_path(uri)
        if self._artifacts_emitted:
            logger.debug("artifacts path already emitted; dropping")
            return
        line = self._build_line(
            HookMethod.SET_EXECUTION_ARTIFACTS_PATH,
            csharp_literal(uri, None, break_identifiers=False),
        )
        self._artifacts_emitted = True
        if not self._enabled:
            return
        try:
            self._channel.write_line(line)
        except Exception:
            # Same one-line, one-outcome rule as metadata().
            self._artifacts_emitted = False
            raise

    def terminal(self, status: RunStatus, annotation: str) -> bool:
        if self._terminal_emitted:
            return False
        method = STATUS_HOOKS[RunStatus(status)]
        if not isinstance(annotation, str):
            raise TypeError(f"annotation must be str, got {type(annotation)!r}")
        status_line = self._build_line(method)
        annotation_line = self._build_line(
            HookMethod.SET_ANNOTATION,
            csharp_literal(annotation, ANNOTATION_CAP),
        )
        self._terminal_emitted = True
        if not self._enabled:
            return True
        # If the status line's write fails, nothing reached the platform,
        # so the flag is rolled back for a clean retry. If the annotation
        # line's write fails afterward, the status hook already reached
        # the platform, so the flag stays set and the caller must not
        # resend the status hook on retry.
        try:
            self._channel.write_line(status_line)
        except Exception:
            self._terminal_emitted = False
            raise
        self._channel.write_line(annotation_line)
        return True

    def announce_job(self, job_id: str) -> None:
        if not _JOB_ID_PATTERN.fullmatch(job_id):
            raise ValueError(f"invalid job id: {job_id!r}")
        self._channel.write_line(f"Submitted batch job {job_id}")

    def _build_line(self, method: HookMethod, *literals: str) -> str:
        return "${CurrentExecution." + method + "(" + ", ".join(literals) + ")}"
