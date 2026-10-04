"""Lifecycle layer: the steps the CLI commands run. Each module owns
one step or one family of steps; the cli package wires them to
commands, and this package never imports cli (ADR-002)."""

from __future__ import annotations
