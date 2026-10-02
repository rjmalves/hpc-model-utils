"""Lifecycle layer: one module per step, so epic-02 tickets do not
edit one file concurrently. finalize is job-side and is the only
step wired to a CLI command so far (hidden); run, cancel, publish
and signals are login-side steps with no CLI command yet."""

from __future__ import annotations
