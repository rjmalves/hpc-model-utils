"""Test-only harness shared by the v2 Task script suites: the execution
identifiers the behavior tests inject, and ``prepare_command``, which
emulates how ModelOps expands ``{{name}}`` references before running a
Task script under bash.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

EXECUTION_ID_PARAMETER = "CurrentExecution.ExecutionId"
EXECUTION_ID = "d14629c2-5a1e-4b6f-9c3d-0123456789ab"
EXECUTION_HASH = "0123456789abcdef" * 4
SHA = "a" * 40

_REFERENCE_NAMES = re.compile(r"\{\{(.*?)\}\}")


def prepare_command(script: str, values: Mapping[str, str]) -> str:
    """Emulate WorkflowExecutionService.PrepareCommand.

    Each distinct ``{{name}}`` is replaced everywhere, in order of first
    appearance in the original script; an unknown name becomes "".
    """
    for name in dict.fromkeys(_REFERENCE_NAMES.findall(script)):
        script = script.replace("{{" + name + "}}", values.get(name, ""))
    return script
