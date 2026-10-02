"""ADR-002: leaf-layer adapter exceptions with no dependency on core."""

from __future__ import annotations


class InfraError(Exception):
    pass


class ShellCommandError(InfraError):
    pass


class ObjectNotFoundError(InfraError):
    pass


class StorageBackendError(InfraError):
    pass


class SchedulerCommandError(InfraError):
    pass


class UnsafeArchiveError(InfraError):
    pass
