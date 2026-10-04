"""deploy.modelops.render: pure rendering, canonicalization and diff of the
ModelOps definitions (ADR-031, ADR-033; ticket-063). No network, no writes:
every function reads the definitions tree and returns a value.

Placeholders are resolved in one pass over the template text, so a
substituted value is never re-scanned and never echoed in an error: a
problem line names the file and the template token, never an env value.
``@@script:FILE@@`` (a proposed ADR-031 amendment) inlines
``scripts/FILE`` verbatim and is allowed only in a ``tasks/*.sh`` script.
"""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ENV_NAME = r"[A-Za-z][A-Za-z0-9]*"
_SLUG = r"[a-z0-9][a-z0-9-]*"
_SLUG_RE = re.compile(_SLUG)
_ID_RE = re.compile(r"[A-Za-z0-9-]{1,64}")
_ENV_TOKEN = re.compile(rf"@@env:({_ENV_NAME})@@")
_TASK_TOKEN = re.compile(rf"@@task:({_SLUG})@@")
_SCRIPT_TOKEN = re.compile(rf"@@script:({_SLUG}\.sh)@@")
_ANY_TOKEN = re.compile(r"@@.*?@@")
_STAMP = re.compile(r"\n?\[deploy/modelops [0-9a-f]{40}\]\Z")
_NOT_COMPARED = frozenset({"timeout"})


class EnvFileError(Exception):
    pass


class RenderError(Exception):
    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = tuple(problems)


@dataclass(frozen=True)
class EnvFile:
    env: Mapping[str, str]
    workflows: Mapping[str, str]
    tasks: Mapping[str, str]
    protected_task_ids: frozenset[str]

    def __post_init__(self) -> None:
        for section, ids in (
            ("workflows", self.workflows),
            ("tasks", self.tasks),
        ):
            for slug, doc_id in ids.items():
                if not _SLUG_RE.fullmatch(slug):
                    raise EnvFileError(
                        f"env file: {section} has a key that is not a slug"
                    )
                if not _ID_RE.fullmatch(doc_id):
                    raise EnvFileError(
                        f"env file: {section}.{slug} is not a valid id"
                    )
        if not all(_ID_RE.fullmatch(i) for i in self.protected_task_ids):
            raise EnvFileError(
                "env file: protectedTaskIds has an entry that is not a valid id"
            )


def _string_map(data: Mapping[str, Any], key: str) -> dict[str, str]:
    section = data.get(key)
    if not isinstance(section, dict) or not all(
        isinstance(value, str) for value in section.values()
    ):
        raise EnvFileError(f"env file: {key} is not an object of strings")
    return dict(section)


def load_env(path: Path) -> EnvFile:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnvFileError(f"env file: cannot read ({exc})") from exc
    if not isinstance(data, dict):
        raise EnvFileError("env file: the top level is not an object")
    protected = data.get("protectedTaskIds")
    if not isinstance(protected, list) or not all(
        isinstance(item, str) for item in protected
    ):
        raise EnvFileError(
            "env file: protectedTaskIds is not a list of strings"
        )
    return EnvFile(
        env=_string_map(data, "env"),
        workflows=_string_map(data, "workflows"),
        tasks=_string_map(data, "tasks"),
        protected_task_ids=frozenset(protected),
    )


class _Renderer:
    def __init__(
        self,
        root: Path,
        env: EnvFile,
        task_ids: Mapping[str, str],
        *,
        workflow: bool,
    ) -> None:
        self._root = root
        self._env = env.env
        self._protected = env.protected_task_ids
        self._task_ids = {**env.tasks, **task_ids}
        self._workflow = workflow
        self.problems: list[str] = []

    def read(self, rel: str) -> str:
        try:
            return (self._root / rel).read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise RenderError([f"{rel}: cannot read ({exc})"]) from exc

    def document(self, rel: str) -> dict[str, Any]:
        try:
            doc = json.loads(self.read(rel))
        except json.JSONDecodeError as exc:
            raise RenderError([f"{rel}: invalid JSON ({exc})"]) from exc
        if not isinstance(doc, dict):
            raise RenderError([f"{rel}: not a JSON object"])
        resolved: dict[str, Any] = self._walk(doc, rel)
        return resolved

    def text(self, text: str, file: str, *, allow_script: bool) -> str:
        if "@@" in _ANY_TOKEN.sub("", text):
            self.problems.append(f"{file}: unpaired @@")
        return _ANY_TOKEN.sub(
            lambda m: self._token(m.group(), file, allow_script), text
        )

    def _walk(self, node: Any, file: str) -> Any:
        if isinstance(node, str):
            return self.text(node, file, allow_script=False)
        if isinstance(node, list):
            return [self._walk(item, file) for item in node]
        if isinstance(node, dict):
            return {
                self.text(key, file, allow_script=False): self._walk(
                    value, file
                )
                for key, value in node.items()
            }
        return node

    def _token(self, token: str, file: str, allow_script: bool) -> str:
        if match := _ENV_TOKEN.fullmatch(token):
            return self._value(self._env.get(match.group(1)), token, file)
        if match := _TASK_TOKEN.fullmatch(token):
            return self._task(match.group(1), token, file)
        if match := _SCRIPT_TOKEN.fullmatch(token):
            return self._script(match.group(1), token, file, allow_script)
        self.problems.append(f"{file}: unresolved {token}")
        return token

    def _value(self, value: str | None, token: str, file: str) -> str:
        if value is None:
            self.problems.append(f"{file}: unresolved {token}")
            return token
        if "@@" in value:
            self.problems.append(f"{file}: {token} resolves to a value with @@")
            return token
        return value

    def _task(self, slug: str, token: str, file: str) -> str:
        task_id = self._task_ids.get(slug)
        reference_only = (
            self._workflow
            and not (self._root / "tasks" / f"{slug}.json").is_file()
        )
        if (
            task_id is not None
            and reference_only
            and task_id not in self._protected
        ):
            self.problems.append(
                f"{file}: {token} has no task file and its id is not in"
                " protectedTaskIds"
            )
            return token
        return self._value(task_id, token, file)

    def _script(
        self, name: str, token: str, file: str, allow_script: bool
    ) -> str:
        if not allow_script:
            self.problems.append(
                f"{file}: {token} is allowed only in a task script"
            )
            return token
        try:
            content = self.read(f"scripts/{name}")
        except RenderError as exc:
            self.problems.extend(exc.problems)
            return token
        if "@@" in content or "{{" in content:
            self.problems.append(
                f"{file}: scripts/{name} contains @@ or {{{{ and cannot be inlined"
            )
            return token
        return content

    def finish(self) -> None:
        if self.problems:
            raise RenderError(self.problems)


def _check_slug(slug: str) -> None:
    if not _SLUG_RE.fullmatch(slug):
        raise RenderError(["slug is not [a-z0-9][a-z0-9-]*"])


def render_task(slug: str, root: Path, env: EnvFile) -> dict[str, Any]:
    """Load ``tasks/<slug>.json`` and set ``script`` from ``tasks/<slug>.sh``,
    with every placeholder resolved; raise ``RenderError`` listing every
    unresolved token."""
    _check_slug(slug)
    renderer = _Renderer(root, env, {}, workflow=False)
    doc = renderer.document(f"tasks/{slug}.json")
    script_file = f"tasks/{slug}.sh"
    doc["script"] = renderer.text(
        renderer.read(script_file), script_file, allow_script=True
    )
    renderer.finish()
    return doc


def render_workflow(
    slug: str, root: Path, env: EnvFile, task_ids: Mapping[str, str]
) -> dict[str, Any]:
    """Load ``workflows/<slug>.json`` with every placeholder resolved.

    ``task_ids`` overrides ``env.tasks``. A ``@@task:SLUG@@`` with no task
    file is a reference-only Task and must name an id in
    ``env.protected_task_ids``.
    """
    _check_slug(slug)
    renderer = _Renderer(root, env, task_ids, workflow=True)
    doc = renderer.document(f"workflows/{slug}.json")
    renderer.finish()
    return doc


def comparison_keys(rendered: Mapping[str, Any]) -> frozenset[str]:
    """The keys compared against live: the rendered document's own, without
    ``timeout`` (accepted on PUT, never returned by GET)."""
    return frozenset(rendered) - _NOT_COMPARED


def canonical(doc: Mapping[str, Any], keys: Collection[str]) -> str:
    projected = {key: doc[key] for key in keys if key in doc}
    observation = projected.get("observation")
    if isinstance(observation, str):
        projected["observation"] = _STAMP.sub("", observation)
    return json.dumps(projected, sort_keys=True, indent=2, ensure_ascii=False)


def diff(rendered: Mapping[str, Any], live: Mapping[str, Any]) -> str:
    keys = comparison_keys(rendered)
    return "\n".join(
        difflib.unified_diff(
            canonical(rendered, keys).splitlines(),
            canonical(live, keys).splitlines(),
            fromfile="rendered",
            tofile="live",
            lineterm="",
        )
    )
