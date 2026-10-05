"""ticket-083a contract: runbook ``PIN <tag> <sha12> ok`` lines match the pins.

``apply sync`` prints one ``PIN {tag} {sha[:12]} {status}`` line per pinned
pair and the apply runbooks quote those lines by hand. The checks compare the
quoted values with the ``<x>AppVersion``/``<x>AppSha`` defaults committed in
``workflows/*.json``. ``check_runbook_pins`` takes the ``deploy/modelops``
directory and the runbooks directory so mutated copies in ``tmp_path`` are
checked the same way as the real tree.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.deploy.test_v2_workflows import (
    SHA,
    TAG,
    _default,
    _load,
    _params,
    _rewrite,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELOPS = REPO_ROOT / "deploy" / "modelops"
RUNBOOKS = REPO_ROOT / "docs" / "runbooks"

PIN_LINE = re.compile(r"PIN (v\d+\.\d+\.\d+) ([0-9a-f]{12}) ok")


def committed_pins(modelops: Path) -> dict[str, set[str]]:
    """Tag -> 40-hex SHAs of every pinned pair in ``workflows/*.json``."""
    pins: dict[str, set[str]] = {}
    for path in (modelops / "workflows").glob("*.json"):
        try:
            params = _params(_load(path))
        except json.JSONDecodeError as err:
            err.add_note(str(path))
            raise
        for name in params:
            if not name.endswith("AppVersion"):
                continue
            tag = _default(params, name)
            sha = _default(params, f"{name.removesuffix('Version')}Sha")
            if TAG.fullmatch(tag) and SHA.fullmatch(sha):
                pins.setdefault(tag, set()).add(sha)
    return pins


def runbook_pins(runbooks: Path) -> dict[str, set[str]]:
    """Tag -> 12-hex values of every ``PIN <tag> <sha12> ok`` in ``*.md``."""
    pins: dict[str, set[str]] = {}
    for path in runbooks.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        for tag, sha12 in PIN_LINE.findall(text):
            pins.setdefault(tag, set()).add(sha12)
    return pins


def check_runbook_pins(modelops: Path, runbooks: Path) -> list[str]:
    """Errors for runbook PIN lines that contradict the committed pins.

    A runbook value must equal every committed 12-character prefix of its tag;
    a tag no workflow commits only has to agree across the runbooks.
    """
    committed = committed_pins(modelops)
    documented = runbook_pins(runbooks)
    errors: list[str] = []
    for tag in sorted(committed.keys() | documented.keys()):
        values = sorted(documented.get(tag, set()))
        shas = committed.get(tag, set())
        if len(values) > 1:
            errors.append(f"{tag}: runbooks disagree: {values}")
        if len(shas) > 1:
            errors.append(f"{tag}: committed with several SHAs")
        errors += [
            f"{tag}: runbook {value} != committed {prefix}"
            for value in values
            for prefix in sorted({sha[:12] for sha in shas})
            if value != prefix
        ]
    return errors


@pytest.fixture
def runbooks(tmp_path: Path) -> Path:
    shutil.copytree(RUNBOOKS, tmp_path / "runbooks")
    return tmp_path / "runbooks"


def _pin_utils(sha: str) -> Callable[[dict[str, Any]], None]:
    def change(doc: dict[str, Any]) -> None:
        params = _params(doc)
        params["utilsAppVersion"]["defaultValue"] = "v9.9.9"
        params["utilsAppSha"]["defaultValue"] = sha

    return change


def test_runbook_pins_match_the_committed_definitions() -> None:
    assert check_runbook_pins(MODELOPS, RUNBOOKS) == []


def test_check_runbook_pins_wrong_sha_is_reported(runbooks: Path) -> None:
    sweep = runbooks / "v2-sweep.md"
    text = sweep.read_text(encoding="utf-8")
    old = "PIN v1.0.1 f61dd5633c8c ok"
    assert old in text
    sweep.write_text(
        text.replace(old, "PIN v1.0.1 f61dd5633c12 ok", 1), encoding="utf-8"
    )

    assert check_runbook_pins(MODELOPS, runbooks) == [
        "v1.0.1: runbooks disagree: ['f61dd5633c12', 'f61dd5633c8c']",
        "v1.0.1: runbook f61dd5633c12 != committed f61dd5633c8c",
    ]


def test_check_runbook_pins_uncommitted_tag_only_needs_agreement(
    runbooks: Path,
) -> None:
    (runbooks / "history-a.md").write_text(
        "PIN v2.0.1 ad11dfe660bc ok\n", encoding="utf-8"
    )
    assert check_runbook_pins(MODELOPS, runbooks) == []

    (runbooks / "history-b.md").write_text(
        "PIN v2.0.1 ad11dfe660bd ok\n", encoding="utf-8"
    )

    assert check_runbook_pins(MODELOPS, runbooks) == [
        "v2.0.1: runbooks disagree: ['ad11dfe660bc', 'ad11dfe660bd']"
    ]


def test_check_runbook_pins_tag_committed_with_two_shas_is_reported(
    tmp_path: Path,
) -> None:
    shutil.copytree(MODELOPS / "workflows", tmp_path / "workflows")
    _rewrite(tmp_path / "workflows" / "newave-pem.json", _pin_utils("1" * 40))
    _rewrite(tmp_path / "workflows" / "cobre.json", _pin_utils("2" * 40))
    (tmp_path / "runbooks").mkdir()
    (tmp_path / "runbooks" / "pins.md").write_text(
        "PIN v9.9.9 111111111111 ok\n", encoding="utf-8"
    )

    assert check_runbook_pins(tmp_path, tmp_path / "runbooks") == [
        "v9.9.9: committed with several SHAs",
        "v9.9.9: runbook 111111111111 != committed 222222222222",
    ]


def test_committed_pins_unparsable_workflow_raises_naming_the_file(
    tmp_path: Path,
) -> None:
    (tmp_path / "workflows").mkdir()
    broken = tmp_path / "workflows" / "broken.json"
    broken.write_text("{", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError) as excinfo:
        committed_pins(tmp_path)

    assert str(broken) in excinfo.value.__notes__


def test_runbook_pins_placeholder_line_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "placeholder.md").write_text(
        "PIN v2.1.0 <utils-v2.1.0-sha12> ok\n", encoding="utf-8"
    )

    assert runbook_pins(tmp_path) == {}
    assert check_runbook_pins(MODELOPS, tmp_path) == []
