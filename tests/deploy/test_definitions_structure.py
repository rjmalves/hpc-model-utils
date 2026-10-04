"""ADR-049 structural lint over the deploy/modelops definitions (ADR-031).

Purely structural: it reads no production value, and its messages name files,
JSON paths and line numbers, never the offending value, because CI logs of a
public repository are public. Each ``check_*`` function takes the
``deploy/modelops`` directory so a mutated copy in ``tmp_path`` can be
checked the same way as the real tree.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELOPS = REPO_ROOT / "deploy" / "modelops"

_ENV_NAME = r"[A-Za-z][A-Za-z0-9]*"
_SLUG = r"[a-z0-9][a-z0-9-]*"
ENV_TOKEN = re.compile(rf"@@env:({_ENV_NAME})@@")
TASK_TOKEN = re.compile(rf"@@task:({_SLUG})@@")
SCRIPT_TOKEN = re.compile(rf"@@script:({_SLUG}\.sh)@@")
ANY_TOKEN = re.compile(r"@@.*?@@")
# A bucket name cannot contain '[', so 's3://[' is a regex bracket
# expression in a validation line, never a literal bucket.
UNSAFE_S3 = re.compile(r"s3://(?!\{\{|@@env:|\$|\[)")
DUMMY_VALUE = re.compile(
    r"example[a-z0-9-]*"
    r"|/example(/[a-z0-9._-]+)*/?"
    r"|0{8}-0{4}-0{4}-0{4}-[0-9]{12}"
)

PLACEHOLDER_PARAMS = frozenset(
    {
        "outputsBucket",
        "versionsBucket",
        "rootPath",
        "mpichPath",
        "slurmPath",
        "awsRegion",
        "queue",
    }
)
AUDIT_KEYS = frozenset(
    {
        "_id",
        "createdby",
        "createddate",
        "lastchangeby",
        "lastchangedate",
        "schedulestatus",
    }
)
TASK_KEYS = frozenset(
    {
        "taskName",
        "description",
        "scriptType",
        "tags",
        "parameters",
        "version",
        "hidden",
        "observation",
    }
)
WORKFLOW_KEYS = frozenset(
    {
        "workflowName",
        "workflowDescription",
        "version",
        "cancelationCriteria",
        "sshConnectionId",
        "timeout",
        "parameters",
        "workflowTasks",
        "cancelationTasks",
        "tags",
        "observation",
        "isSchedulesActive",
        "workflowTaskMetadata",
        "cancellationTaskMetadata",
        "taskExecutionCriterias",
        "cancellationTaskExecutionCriterias",
        "schedules",
    }
)
EXAMPLE_KEYS = frozenset({"env", "workflows", "tasks", "protectedTaskIds"})
LITERAL_GUID = "00000000-0000-0000-0000-000000000999"


def _glob(root: Path, pattern: str) -> list[Path]:
    found = sorted(root.glob(pattern))
    if not found:
        raise AssertionError(f"{pattern}: no files under the definitions tree")
    return found


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_definitions(root: Path) -> list[Path]:
    return _glob(root, "workflows/*.json") + _glob(root, "tasks/*.json")


def _definition_texts(root: Path) -> dict[Path, str]:
    paths = _json_definitions(root) + _glob(root, "tasks/*.sh")
    return {path: path.read_text(encoding="utf-8") for path in paths}


def _leaves(node: Any, path: str = "$") -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _leaves(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _leaves(value, f"{path}[{index}]")
    elif isinstance(node, str):
        yield path, node


def _keys(node: Any, path: str = "$") -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield f"{path}.{key}", key
            yield from _keys(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _keys(value, f"{path}[{index}]")


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def require(errors: list[str]) -> None:
    if errors:
        raise AssertionError("\n".join(errors))


def check_placeholder_fields(root: Path) -> list[str]:
    errors: list[str] = []
    placeholder = re.compile(rf"@@env:{_ENV_NAME}@@")
    for path in _glob(root, "workflows/*.json"):
        rel = _rel(root, path)
        doc = _load(path)
        if not placeholder.fullmatch(doc.get("sshConnectionId") or ""):
            errors.append(f"{rel}: sshConnectionId is not an @@env:@@ token")
        for param in doc["parameters"]:
            if param["name"] not in PLACEHOLDER_PARAMS:
                continue
            fields = [("defaultValue", param["defaultValue"])]
            if param["name"] == "queue":
                fields += [("options", option) for option in param["options"]]
            for field, value in fields:
                if not placeholder.fullmatch(value):
                    errors.append(
                        f"{rel}: parameter {param['name']} {field} is not"
                        " an @@env:@@ token"
                    )
    return errors


def check_literals(root: Path) -> list[str]:
    errors: list[str] = []
    for path in _json_definitions(root):
        for json_path, value in _leaves(_load(path)):
            if value.startswith("/"):
                errors.append(
                    f"{_rel(root, path)}: {json_path} starts with '/'"
                )
    for path, text in _definition_texts(root).items():
        for match in UNSAFE_S3.finditer(text):
            errors.append(
                f"{_rel(root, path)}:{_line(text, match.start())}:"
                " s3:// is followed by a literal"
            )
    return errors


def check_audit_fields(root: Path) -> list[str]:
    return [
        f"{_rel(root, path)}: audit field at {json_path}"
        for path in _json_definitions(root)
        for json_path, key in _keys(_load(path))
        if key.lower() in AUDIT_KEYS
    ]


def check_key_sets(root: Path) -> list[str]:
    errors: list[str] = []
    for pattern, expected in (
        ("workflows/*.json", WORKFLOW_KEYS),
        ("tasks/*.json", TASK_KEYS),
    ):
        for path in _glob(root, pattern):
            actual = set(_load(path))
            if actual != expected:
                errors.append(
                    f"{_rel(root, path)}: missing {sorted(expected - actual)},"
                    f" unexpected {sorted(actual - expected)}"
                )
    return errors


def check_pairing(root: Path) -> list[str]:
    errors: list[str] = []
    json_slugs = {path.stem for path in _glob(root, "tasks/*.json")}
    sh_slugs = {path.stem for path in _glob(root, "tasks/*.sh")}
    errors += [
        f"tasks/{s}.json has no .sh" for s in sorted(json_slugs - sh_slugs)
    ]
    errors += [
        f"tasks/{s}.sh has no .json" for s in sorted(sh_slugs - json_slugs)
    ]
    for path in _glob(root, "workflows/*.json"):
        doc = _load(path)
        for node in doc["workflowTasks"] + doc["cancelationTasks"]:
            match = TASK_TOKEN.fullmatch(node.get("taskId") or "")
            if match is None:
                errors.append(f"{_rel(root, path)}: taskId is not @@task:@@")
            elif match.group(1) not in json_slugs:
                errors.append(
                    f"{_rel(root, path)}: @@task:{match.group(1)}@@ has no"
                    " task file"
                )
    return errors


def check_grammar(root: Path) -> list[str]:
    errors: list[str] = []
    for path, text in _definition_texts(root).items():
        allowed = [ENV_TOKEN, TASK_TOKEN]
        if path.suffix == ".sh":
            allowed.append(SCRIPT_TOKEN)
        for match in ANY_TOKEN.finditer(text):
            if not any(p.fullmatch(match.group()) for p in allowed):
                errors.append(
                    f"{_rel(root, path)}:{_line(text, match.start())}:"
                    " token is outside the placeholder grammar"
                )
        if "@@" in ANY_TOKEN.sub("", text):
            errors.append(f"{_rel(root, path)}: unpaired @@")
    return errors


def check_example_dummy_values(root: Path) -> list[str]:
    example = _load(root / "env" / "prd.example.json")
    return [
        f"env/prd.example.json: {json_path} is not a dummy value"
        for json_path, value in _leaves(example)
        if not DUMMY_VALUE.fullmatch(value)
    ]


def check_example_keys(root: Path) -> list[str]:
    example = _load(root / "env" / "prd.example.json")
    if set(example) != EXAMPLE_KEYS:
        return ["env/prd.example.json: top-level keys differ from the shape"]
    texts = _definition_texts(root).values()
    expected = {
        "env": {m.group(1) for t in texts for m in ENV_TOKEN.finditer(t)},
        "tasks": {p.stem for p in _glob(root, "tasks/*.json")}
        | {m.group(1) for t in texts for m in TASK_TOKEN.finditer(t)},
        "workflows": {p.stem for p in _glob(root, "workflows/*.json")},
    }
    errors: list[str] = []
    for section, names in expected.items():
        actual = set(example[section])
        if actual != names:
            errors.append(
                f"env/prd.example.json: {section} missing"
                f" {sorted(names - actual)}, unexpected"
                f" {sorted(actual - names)}"
            )
    protected = example["protectedTaskIds"]
    if not isinstance(protected, list) or not protected:
        errors.append("env/prd.example.json: protectedTaskIds is empty")
    return errors


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    for name in ("workflows", "tasks"):
        shutil.copytree(MODELOPS / name, tmp_path / name)
    (tmp_path / "env").mkdir()
    shutil.copy2(
        MODELOPS / "env" / "prd.example.json",
        tmp_path / "env" / "prd.example.json",
    )
    return tmp_path


def _rewrite(path: Path, change: Callable[[Any], None]) -> None:
    doc = _load(path)
    change(doc)
    path.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _param(doc: dict[str, Any], name: str) -> dict[str, Any]:
    matches: list[dict[str, Any]] = [
        p for p in doc["parameters"] if p["name"] == name
    ]
    (found,) = matches
    return found


def test_check_placeholder_fields_real_tree_reports_nothing() -> None:
    require(check_placeholder_fields(MODELOPS))


def _literal_ssh_connection_id(doc: dict[str, Any]) -> None:
    doc["sshConnectionId"] = LITERAL_GUID


def _literal_default_value(doc: dict[str, Any]) -> None:
    _param(doc, "rootPath")["defaultValue"] = LITERAL_GUID


def _literal_queue_option(doc: dict[str, Any]) -> None:
    _param(doc, "queue")["options"][0] = LITERAL_GUID


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (_literal_ssh_connection_id, r"sshConnectionId is not"),
        (_literal_default_value, r"parameter rootPath defaultValue is not"),
        (_literal_queue_option, r"parameter queue options is not"),
    ],
)
def test_check_placeholder_fields_literal_value_is_reported(
    tree: Path, mutation: Callable[[Any], None], message: str
) -> None:
    _rewrite(_glob(tree, "workflows/*.json")[0], mutation)

    with pytest.raises(AssertionError, match=message):
        require(check_placeholder_fields(tree))


def test_check_literals_real_tree_reports_nothing() -> None:
    require(check_literals(MODELOPS))


def test_check_literals_s3_followed_by_a_literal_bucket_is_reported(
    tree: Path,
) -> None:
    script = _glob(tree, "tasks/*.sh")[0]
    script.write_text(
        script.read_text(encoding="utf-8") + "\n# s3://example-bucket/\n",
        encoding="utf-8",
    )

    with pytest.raises(
        AssertionError, match=r"\.sh:\d+: s3:// is followed by a literal"
    ):
        require(check_literals(tree))


def test_check_audit_fields_real_tree_reports_nothing() -> None:
    require(check_audit_fields(MODELOPS))


def test_check_audit_fields_nested_mixed_case_key_is_reported(
    tree: Path,
) -> None:
    def add_audit_key(doc: dict[str, Any]) -> None:
        doc["workflowTasks"][0]["LastChangeDate"] = "x"

    _rewrite(_glob(tree, "workflows/*.json")[0], add_audit_key)

    with pytest.raises(
        AssertionError, match=r"audit field at \$\.workflowTasks\[0\]\.Last"
    ):
        require(check_audit_fields(tree))


def test_check_key_sets_real_tree_reports_nothing() -> None:
    require(check_key_sets(MODELOPS))


def test_check_pairing_real_tree_reports_nothing() -> None:
    require(check_pairing(MODELOPS))


def test_check_grammar_real_tree_reports_nothing() -> None:
    require(check_grammar(MODELOPS))


def test_check_example_dummy_values_real_tree_reports_nothing() -> None:
    require(check_example_dummy_values(MODELOPS))


def test_check_example_dummy_values_non_dummy_value_is_reported(
    tree: Path,
) -> None:
    def use_real_looking_bucket(doc: dict[str, Any]) -> None:
        doc["env"]["outputsBucket"] = "my-company-outputs"

    _rewrite(tree / "env" / "prd.example.json", use_real_looking_bucket)

    with pytest.raises(
        AssertionError, match=r"\$\.env\.outputsBucket is not a dummy value"
    ):
        require(check_example_dummy_values(tree))


def test_check_example_keys_real_tree_reports_nothing() -> None:
    require(check_example_keys(MODELOPS))
