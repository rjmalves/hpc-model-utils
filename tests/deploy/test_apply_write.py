"""Tests for ticket-063a: the guarded write path of ``apply sync --apply``.

Acceptance criteria, selected with ``-k``: ``refuse``, ``put``, ``create``,
``concurrent or executing``. Every test runs against the stateful
``FakeModelOpsServer`` over localhost and a temporary git repository under
``tmp_path``; nothing reaches prd, GitHub or this repository's git state.
The typed confirmation is reached through the injectable ``confirm``
callable of ``execute``, never through a flag or variable.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from deploy.modelops import apply as apply_module
from deploy.modelops.apply import (
    ApplyRefused,
    Entry,
    Plan,
    _name_collisions,
    _ordered_writes,
    _protected_writes,
    _sync,
    _tty_confirm,
    _unmanaged_references,
    cli,
    execute,
    plan,
)
from deploy.modelops.modelops_api import (
    _MAX_BODY_CHARS,
    ModelOpsApiError,
    ModelOpsClient,
)
from deploy.modelops.render import (
    EnvFile,
    load_env,
    render_task,
    render_workflow,
)
from tests.support.fake_modelops import FakeModelOpsServer

TOKEN = "test-token-abc123"
AKIA = "AKIAABCDEFGHIJKLMNOP"  # gitleaks:allow (fake key for redaction tests)
USER = "operator@ons.org.br"
STAMP = "\n[deploy/modelops " + "a" * 40 + "]"
TASK_KEYS = {
    "description": "a task",
    "scriptType": "BASH",
    "tags": [],
    "parameters": [],
    "version": "1.0.0",
    "hidden": False,
}


def _workflow(
    name: str, tasks: list[str], cancel: list[str] | None = None
) -> dict[str, Any]:
    return {
        "workflowName": name,
        "workflowDescription": "a workflow",
        "version": "1.0.0",
        "cancelationCriteria": "",
        "sshConnectionId": "conn-guid",
        "timeout": 0,
        "parameters": [
            {
                "name": "modelName",
                "type": "String",
                "defaultValue": "newave",
                "options": [],
            }
        ],
        "workflowTasks": [
            {
                "workflowTaskId": f"t{i}",
                "taskId": f"@@task:{slug}@@",
                "onFailure": 1,
            }
            for i, slug in enumerate(tasks)
        ],
        "cancelationTasks": [
            {
                "workflowTaskId": f"c{i}",
                "taskId": f"@@task:{slug}@@",
                "executionCriteria": "",
            }
            for i, slug in enumerate(cancel or [])
        ],
        "tags": [],
        "observation": "",
        "isSchedulesActive": False,
        "schedules": [],
    }


def _git(repo: Path, home: Path, *args: str) -> str:
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout.strip()


@dataclass
class Ctx:
    repo: Path
    home: Path
    env_data: dict[str, Any]

    @property
    def defs(self) -> Path:
        return self.repo / "defs"

    @property
    def env_path(self) -> Path:
        return self.repo / "env" / "prd.json"

    def git(self, *args: str) -> str:
        return _git(self.repo, self.home, *args)

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def save_env(self) -> None:
        text = json.dumps(self.env_data)
        self.env_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.env_path.exists() or self.env_path.read_text() != text:
            self.env_path.write_text(text, encoding="utf-8")

    def env(self) -> EnvFile:
        self.save_env()
        return load_env(self.env_path)

    def write(self, rel: str, content: str) -> None:
        path = self.defs / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write_task(self, slug: str, name: str) -> None:
        task = {"taskName": name, **TASK_KEYS, "observation": f"{name} note"}
        self.write(f"tasks/{slug}.json", json.dumps(task, indent=2))
        self.write(f"tasks/{slug}.sh", f"echo {slug}\n")

    def edit(self, rel: str, change: Callable[[dict[str, Any]], None]) -> None:
        doc = json.loads((self.defs / rel).read_text(encoding="utf-8"))
        change(doc)
        self.write(rel, json.dumps(doc, indent=2))

    def commit(self) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "defs")


@pytest.fixture
def ctx(tmp_path: Path) -> Ctx:
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    built = Ctx(
        repo=repo,
        home=home,
        env_data={
            "env": {},
            "workflows": {"flow": "wf-1"},
            "tasks": {"alpha": "task-a", "beta": "task-b"},
            "protectedTaskIds": [],
        },
    )
    (repo / ".gitignore").write_text("env/\n", encoding="utf-8")
    built.write_task("alpha", "Alpha")
    built.write_task("beta", "Beta")
    built.write(
        "workflows/flow.json",
        json.dumps(_workflow("Flow", ["alpha", "beta"], ["beta"]), indent=2),
    )
    built.save_env()
    built.git("-c", "init.defaultBranch=main", "init", "-q")
    built.commit()
    return built


@pytest.fixture
def fake() -> Iterator[FakeModelOpsServer]:
    with FakeModelOpsServer() as server:
        yield server


def _live(doc: dict[str, Any], doc_id: str) -> dict[str, Any]:
    live = {k: v for k, v in doc.items() if k != "timeout"}
    live.update(
        {
            "_id": doc_id,
            "createdBy": "alice",
            "createdDate": "2026-01-01T00:00:00Z",
            "lastChangeBy": "bob",
            "lastChangeDate": "2026-02-02T00:00:00Z",
            "scheduleStatus": "None",
            "observation": doc["observation"] + STAMP,
        }
    )
    return live


def _seed(fake: FakeModelOpsServer, ctx: Ctx) -> None:
    env = ctx.env()
    fake.tasks = [
        _live(render_task(slug, ctx.defs, env), env.tasks[slug])
        for slug in sorted(p.stem for p in (ctx.defs / "tasks").glob("*.json"))
        if slug in env.tasks
    ]
    fake.workflows = [
        _live(render_workflow(slug, ctx.defs, env, {}), env.workflows[slug])
        for slug in sorted(
            p.stem for p in (ctx.defs / "workflows").glob("*.json")
        )
        if slug in env.workflows
    ]


def _doc(items: list[dict[str, Any]], doc_id: str) -> dict[str, Any]:
    (found,) = [item for item in items if item["_id"] == doc_id]
    return found


def _drift(fake: FakeModelOpsServer, doc_id: str = "task-a") -> None:
    _doc(fake.tasks, doc_id)["script"] += "# drift\n"


def _writes(fake: FakeModelOpsServer) -> list[tuple[str, str]]:
    return [
        (r.method, r.path) for r in fake.requests if r.method in ("PUT", "POST")
    ]


def _body(fake: FakeModelOpsServer, method: str, path: str) -> dict[str, Any]:
    (request,) = [
        r for r in fake.requests if (r.method, r.path) == (method, path)
    ]
    body: dict[str, Any] = json.loads(request.body)
    return body


@dataclass
class _Confirm:
    answer: bool = True
    calls: int = 0

    def __call__(self) -> bool:
        self.calls += 1
        return self.answer


@dataclass(frozen=True)
class Outcome:
    code: int
    out: list[str]
    err: list[str]


def _apply(
    fake: FakeModelOpsServer,
    ctx: Ctx,
    capsys: pytest.CaptureFixture[str],
    *,
    confirm: Callable[[], bool] | None = None,
    user: str = USER,
) -> Outcome:
    client = ModelOpsClient(base_url=fake.url, token=TOKEN)
    code = execute(
        plan(ctx.defs, ctx.env(), client),
        client=client,
        user=user,
        head=ctx.head(),
        confirm=confirm or _Confirm(),
    )
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err
    return Outcome(code, captured.out.splitlines(), captured.err.splitlines())


def _dry_run(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> Outcome:
    client = ModelOpsClient(base_url=fake.url, token=TOKEN)
    code = _sync(client, ctx.env(), ctx.defs)
    captured = capsys.readouterr()
    return Outcome(code, captured.out.splitlines(), captured.err.splitlines())


def _add_new_definitions(ctx: Ctx) -> None:
    ctx.write_task("new-one", "New One")
    ctx.write_task("new-two", "New Two")
    ctx.write(
        "workflows/fresh.json",
        json.dumps(_workflow("Fresh", ["new-one", "new-two"]), indent=2),
    )
    ctx.commit()


def _assert_refused(
    outcome: Outcome,
    fake: FakeModelOpsServer,
    reason: str,
    code: int = 1,
) -> None:
    assert outcome.code == code
    assert len(outcome.err) == 1
    assert outcome.err[0].startswith("apply: ")
    assert reason in outcome.err[0]
    assert _writes(fake) == []


# ------------------------------------------------------------ AC: refuse


@pytest.mark.parametrize("dirt", ["untracked", "modified"])
def test_refuse_dirty_tree_before_any_write(
    fake: FakeModelOpsServer,
    ctx: Ctx,
    capsys: pytest.CaptureFixture[str],
    dirt: str,
) -> None:
    _seed(fake, ctx)
    _drift(fake)
    if dirt == "untracked":
        (ctx.repo / "stray.txt").write_text("x", encoding="utf-8")
    else:
        (ctx.defs / "tasks" / "beta.sh").write_text("# edit\n")

    outcome = _apply(fake, ctx, capsys)

    _assert_refused(outcome, fake, "working tree is not clean")


def test_refuse_untracked_file_is_not_confirmed_or_written(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake, ctx)
    _drift(fake)
    (ctx.repo / "stray.txt").write_text("x", encoding="utf-8")
    confirm = _Confirm()

    outcome = _apply(fake, ctx, capsys, confirm=confirm)

    assert outcome.code == 1
    assert confirm.calls == 0


def test_refuse_identifier_gap_before_any_write(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake, ctx)
    _drift(fake)
    fake.workflows.append(
        {
            "_id": "other",
            "workflowName": "Other",
            "parameters": [{"name": "notInSet"}],
            "workflowTasks": [],
            "cancelationTasks": [],
        }
    )

    outcome = _apply(fake, ctx, capsys)

    assert "IDENTIFIER-GAP Other: notInSet" in outcome.out
    _assert_refused(outcome, fake, "dry run reported failures")


def test_refuse_missing_document_before_any_write(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake, ctx)
    _drift(fake)
    fake.tasks = [t for t in fake.tasks if t["_id"] != "task-b"]

    outcome = _apply(fake, ctx, capsys)

    assert "MISSING task beta" in outcome.out
    _assert_refused(outcome, fake, "dry run reported failures")


def test_refuse_render_problem_before_any_request(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx.write("tasks/alpha.sh", "@@env:undefined@@\n")
    ctx.commit()

    outcome = _apply(fake, ctx, capsys)

    assert outcome.code == 1
    assert (
        outcome.err[0] == "apply: tasks/alpha.sh: unresolved @@env:undefined@@"
    )
    assert outcome.err[-1].startswith("apply: the dry run reported failures")
    assert fake.requests == []


@pytest.mark.parametrize("user", ["", "bad user!", "x" * 65, "a;b"])
def test_refuse_missing_or_invalid_operator_user(
    fake: FakeModelOpsServer,
    ctx: Ctx,
    capsys: pytest.CaptureFixture[str],
    user: str,
) -> None:
    _seed(fake, ctx)
    _drift(fake)

    outcome = _apply(fake, ctx, capsys, user=user)

    _assert_refused(outcome, fake, "MODELOPS_USER must be set", code=2)
    assert user not in outcome.err[0] or user == ""


def test_refuse_planned_put_on_protected_task_id(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx.env_data["protectedTaskIds"].append("task-a")
    _seed(fake, ctx)
    _drift(fake)

    outcome = _apply(fake, ctx, capsys)

    assert "CHANGED task alpha PROTECTED" in outcome.out
    _assert_refused(outcome, fake, "task alpha is in protectedTaskIds")


@pytest.mark.parametrize("field", ["workflowTasks", "cancelationTasks"])
def test_refuse_planned_put_on_task_referenced_by_unmanaged_workflow(
    fake: FakeModelOpsServer,
    ctx: Ctx,
    capsys: pytest.CaptureFixture[str],
    field: str,
) -> None:
    _seed(fake, ctx)
    _drift(fake)
    fake.workflows.append(
        {
            "_id": "legacy",
            "workflowName": "Legacy Flow",
            "parameters": [],
            "workflowTasks": [],
            "cancelationTasks": [],
            field: [{"taskId": "task-a"}],
        }
    )

    outcome = _apply(fake, ctx, capsys)

    _assert_refused(
        outcome,
        fake,
        "task alpha is referenced by unmanaged workflow Legacy Flow",
    )


def test_put_unmanaged_workflow_on_an_untouched_task_does_not_block(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake, ctx)
    _drift(fake)
    fake.workflows.append(
        {
            "_id": "legacy",
            "workflowName": "Legacy Flow",
            "parameters": [],
            "workflowTasks": [{"taskId": "task-b"}],
            "cancelationTasks": [],
        }
    )

    outcome = _apply(fake, ctx, capsys)

    assert outcome.code == 0, outcome.err
    assert _writes(fake) == [("PUT", "/api/Task/task-a")]


@pytest.mark.parametrize(
    ("kind", "existing"),
    [("task", "Existing"), ("workflow", "Existing")],
)
def test_refuse_new_document_whose_name_already_exists_live(
    fake: FakeModelOpsServer,
    ctx: Ctx,
    capsys: pytest.CaptureFixture[str],
    kind: str,
    existing: str,
) -> None:
    _seed(fake, ctx)
    _drift(fake)
    if kind == "task":
        ctx.write_task("gamma", existing)
        fake.tasks.append({"_id": "other-id", "taskName": existing})
    else:
        ctx.write(
            "workflows/gamma.json",
            json.dumps(_workflow(existing, ["alpha"])),
        )
        fake.workflows.append(
            {
                "_id": "other-id",
                "workflowName": existing,
                "parameters": [],
                "workflowTasks": [],
                "cancelationTasks": [],
            }
        )
    ctx.commit()

    outcome = _apply(fake, ctx, capsys)

    _assert_refused(
        outcome,
        fake,
        f"{kind} gamma: {kind}Name already exists live; record its id "
        f"under {kind}s.gamma in the env file",
    )


def test_refuse_two_new_tasks_sharing_one_name(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake, ctx)
    ctx.write_task("twin-one", "Twin")
    ctx.write_task("twin-two", "Twin")
    ctx.commit()

    outcome = _apply(fake, ctx, capsys)

    assert outcome.code == 1
    assert len(outcome.err) == 1
    assert "twin-one" in outcome.err[0] and "twin-two" in outcome.err[0]
    assert _writes(fake) == []


def test_refuse_when_confirm_returns_false(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake, ctx)
    _drift(fake)
    confirm = _Confirm(answer=False)

    outcome = _apply(fake, ctx, capsys, confirm=confirm)

    assert "PUT task alpha task-a" in outcome.out
    assert confirm.calls == 1
    _assert_refused(outcome, fake, "confirmation not given")


def test_refuse_tree_dirtied_while_the_confirmation_waits(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake, ctx)
    _drift(fake)

    def dirty_then_confirm() -> bool:
        (ctx.repo / "stray.txt").write_text("x", encoding="utf-8")
        return True

    outcome = _apply(fake, ctx, capsys, confirm=dirty_then_confirm)

    assert "PUT task alpha task-a" in outcome.out
    _assert_refused(outcome, fake, "working tree is not clean")


def test_refuse_head_moved_while_the_confirmation_waits(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake, ctx)
    _drift(fake)
    stamped = ctx.head()

    def commit_then_confirm() -> bool:
        (ctx.repo / "notes.txt").write_text("new commit", encoding="utf-8")
        ctx.commit()
        return True

    outcome = _apply(fake, ctx, capsys, confirm=commit_then_confirm)

    assert ctx.head() != stamped
    assert ctx.git("status", "--porcelain") == ""
    _assert_refused(outcome, fake, "HEAD changed while waiting")


def _cli_apply(
    fake: FakeModelOpsServer,
    ctx: Ctx,
    monkeypatch: pytest.MonkeyPatch,
    *,
    user: str | None = USER,
) -> Any:
    monkeypatch.setattr(apply_module, "_MODELOPS_ROOT", ctx.defs)
    ctx.save_env()
    env: dict[str, str | None] = {
        "MODELOPS_URL": fake.url,
        "MODELOPS_TOKEN": TOKEN,
        "MODELOPS_USER": user,
    }
    return CliRunner().invoke(
        cli, ["sync", "--env-file", str(ctx.env_path), "--apply"], env=env
    )


def test_refuse_non_tty_stdin_through_the_click_command(
    fake: FakeModelOpsServer, ctx: Ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(fake, ctx)
    _drift(fake)

    result = _cli_apply(fake, ctx, monkeypatch)

    assert result.exit_code == 1
    assert "PUT task alpha task-a" in result.stdout.splitlines()
    assert len(result.stderr.splitlines()) == 1
    assert result.stderr.startswith("apply: stdin is not a terminal")
    assert _writes(fake) == []
    assert TOKEN not in result.stdout + result.stderr


def test_refuse_click_command_without_operator_user_exits_2(
    fake: FakeModelOpsServer, ctx: Ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(fake, ctx)
    _drift(fake)

    result = _cli_apply(fake, ctx, monkeypatch, user=None)

    assert result.exit_code == 2
    assert result.stderr.startswith("apply: MODELOPS_USER must be set")
    assert _writes(fake) == []


def test_refuse_click_command_on_a_dirty_tree(
    fake: FakeModelOpsServer, ctx: Ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(fake, ctx)
    _drift(fake)
    (ctx.repo / "stray.txt").write_text("x", encoding="utf-8")

    result = _cli_apply(fake, ctx, monkeypatch)

    assert result.exit_code == 1
    assert "working tree is not clean" in result.stderr
    assert _writes(fake) == []


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_refuse_tty_confirm_when_stdin_is_not_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("apply\n"))

    with pytest.raises(ApplyRefused, match="not a terminal"):
        _tty_confirm()


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("apply\n", True),
        ("Apply\n", False),
        (" apply\n", False),
        ("apply \n", False),
        ("yes\n", False),
        ("\n", False),
        ("", False),
    ],
)
def test_confirm_tty_accepts_only_the_exact_word(
    monkeypatch: pytest.MonkeyPatch, typed: str, expected: bool
) -> None:
    monkeypatch.setattr(sys, "stdin", _Tty(typed))

    assert _tty_confirm() is expected


# ----------------------------------------------------------- AC: put


def test_put_changed_task_and_workflow_send_rendered_body_with_audit_fields(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake, ctx)
    _drift(fake)
    _doc(fake.workflows, "wf-1")["workflowDescription"] = "drifted"
    head = ctx.head()
    stamp = f"\n[deploy/modelops {head}]"
    env = ctx.env()

    outcome = _apply(fake, ctx, capsys)

    assert outcome.code == 0, outcome.err
    task = render_task("alpha", ctx.defs, env)
    flow = render_workflow("flow", ctx.defs, env, {})
    assert _body(fake, "PUT", "/api/Task/task-a") == {
        **task,
        "createdBy": "alice",
        "lastChangeBy": USER,
        "observation": task["observation"] + stamp,
    }
    assert _body(fake, "PUT", "/api/Workflow/wf-1") == {
        **flow,
        "createdBy": "alice",
        "lastChangeBy": USER,
        "observation": flow["observation"] + stamp,
    }
    for items, doc_id in ((fake.tasks, "task-a"), (fake.workflows, "wf-1")):
        stored = _doc(items, doc_id)
        assert stored["createdBy"] == "alice"
        assert stored["lastChangeBy"] == USER
        assert stored["observation"].endswith(f"[deploy/modelops {head}]")
    assert "WRITTEN PUT task alpha task-a" in outcome.out
    assert "WRITTEN PUT workflow flow wf-1" in outcome.out
    assert outcome.err == []


def test_put_followed_by_a_dry_run_reports_both_unchanged(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake, ctx)
    _drift(fake)
    _doc(fake.workflows, "wf-1")["workflowDescription"] = "drifted"
    _apply(fake, ctx, capsys)

    dry = _dry_run(fake, ctx, capsys)

    assert dry.code == 0
    assert "UNCHANGED task alpha" in dry.out
    assert "UNCHANGED workflow flow" in dry.out
    assert dry.out[-1] == "summary: 3 unchanged, 0 changed, 0 new, 0 failures"


def test_put_writes_tasks_before_workflows_and_rereads_before_each_put(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake, ctx)
    _drift(fake, "task-a")
    _drift(fake, "task-b")
    _doc(fake.workflows, "wf-1")["workflowDescription"] = "drifted"

    outcome = _apply(fake, ctx, capsys)

    assert outcome.code == 0, outcome.err
    assert [(r.method, r.path) for r in fake.requests] == [
        ("GET", "/api/Task/all"),
        ("GET", "/api/Workflow/all"),
        ("GET", "/api/Task/task-a"),
        ("PUT", "/api/Task/task-a"),
        ("GET", "/api/Task/task-b"),
        ("PUT", "/api/Task/task-b"),
        ("GET", "/api/Workflow/wf-1"),
        ("PUT", "/api/Workflow/wf-1"),
    ]
    assert [line for line in outcome.out if line.startswith("PUT ")] == [
        "PUT task alpha task-a",
        "PUT task beta task-b",
        "PUT workflow flow wf-1",
    ]


def test_put_with_nothing_changed_writes_nothing_and_never_confirms(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake, ctx)
    (ctx.repo / "env" / "other.txt").write_text("ignored", encoding="utf-8")
    confirm = _Confirm()

    outcome = _apply(fake, ctx, capsys, confirm=confirm)

    assert outcome.code == 0
    assert "nothing to write" in outcome.out
    assert confirm.calls == 0
    assert _writes(fake) == []


def test_put_apply_flag_through_click_writes_as_the_env_user(
    fake: FakeModelOpsServer, ctx: Ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(fake, ctx)
    _drift(fake)
    monkeypatch.setattr(apply_module, "_tty_confirm", lambda: True)

    result = _cli_apply(fake, ctx, monkeypatch)

    assert result.exit_code == 0, result.stderr
    stored = _doc(fake.tasks, "task-a")
    assert stored["lastChangeBy"] == USER
    assert stored["observation"].endswith(f"[deploy/modelops {ctx.head()}]")
    assert result.stderr == ""


def test_put_fake_task_overwrites_created_by_and_workflow_keeps_it(
    fake: FakeModelOpsServer,
) -> None:
    client = ModelOpsClient(base_url=fake.url, token=TOKEN)
    fake.tasks = [{"_id": "t1", "createdBy": "alice"}]
    fake.workflows = [{"_id": "w1", "createdBy": "alice"}]

    client.put_json("/api/Task/t1", {"taskName": "T"})
    client.put_json("/api/Workflow/w1", {"workflowName": "W", "timeout": 5})

    assert fake.tasks[0]["createdBy"] is None
    assert fake.workflows[0]["createdBy"] == "alice"
    assert "timeout" not in fake.workflows[0]


def test_client_put_json_sends_bearer_content_type_and_json_body(
    fake: FakeModelOpsServer,
) -> None:
    client = ModelOpsClient(base_url=fake.url, token=TOKEN)
    fake.tasks = [{"_id": "t1", "createdBy": "alice"}]

    client.put_json("/api/Task/t1", {"createdBy": "bob", "name": "é"})

    (request,) = fake.requests
    headers = {k.lower(): v for k, v in request.headers.items()}
    assert request.method == "PUT"
    assert headers["authorization"] == f"Bearer {TOKEN}"
    assert headers["content-type"] == "application/json"
    assert json.loads(request.body) == {"createdBy": "bob", "name": "é"}


def test_client_put_json_non_2xx_raises_with_status_and_redacted_body(
    fake: FakeModelOpsServer,
) -> None:
    client = ModelOpsClient(base_url=fake.url, token=TOKEN)
    fake.script(
        "/api/Task/t1",
        method="PUT",
        status=422,
        body=f'{{"message": "{TOKEN} rejected"}}'.encode(),
    )

    with pytest.raises(ModelOpsApiError) as excinfo:
        client.put_json("/api/Task/t1", {})

    text = str(excinfo.value)
    assert text.startswith("PUT /api/Task/t1: HTTP 422: ")
    assert TOKEN not in text
    assert "<redacted> rejected" in text


@pytest.mark.parametrize("before_cut", [5, 10, 16])
def test_redact_token_straddling_the_body_cut_leaves_no_prefix(
    fake: FakeModelOpsServer, before_cut: int
) -> None:
    client = ModelOpsClient(base_url=fake.url, token=TOKEN)
    body = "x" * (_MAX_BODY_CHARS - before_cut) + TOKEN + " tail"
    fake.script("/api/Task/t1", method="PUT", status=500, body=body.encode())

    with pytest.raises(ModelOpsApiError) as excinfo:
        client.put_json("/api/Task/t1", {})

    text = str(excinfo.value)
    assert TOKEN[:5] not in text
    assert text.startswith("PUT /api/Task/t1: HTTP 500: xxxx")


def test_redact_token_straddling_the_body_cut_never_reaches_cli_stderr(
    fake: FakeModelOpsServer, ctx: Ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "x" * (_MAX_BODY_CHARS - 5) + TOKEN + " tail"
    fake.script("/api/Task/all", status=500, body=body.encode())

    result = _cli_apply(fake, ctx, monkeypatch)

    assert result.exit_code == 1
    assert len(result.stderr.splitlines()) == 1
    assert TOKEN[:5] not in result.stdout + result.stderr
    assert _writes(fake) == []


@pytest.mark.parametrize("before_cut", [6, 12, 19])
def test_redact_aws_key_straddling_the_body_cut_leaves_only_the_marker(
    fake: FakeModelOpsServer, before_cut: int
) -> None:
    client = ModelOpsClient(base_url=fake.url, token=TOKEN)
    body = "x" * (_MAX_BODY_CHARS - before_cut) + AKIA + " tail"
    fake.script("/api/Task/t1", method="PUT", status=500, body=body.encode())

    with pytest.raises(ModelOpsApiError) as excinfo:
        client.put_json("/api/Task/t1", {})

    text = str(excinfo.value)
    assert "AKIA" not in text
    masked = "x" * (_MAX_BODY_CHARS - before_cut) + "<redacted> tail"
    assert text == "PUT /api/Task/t1: HTTP 500: " + masked[:_MAX_BODY_CHARS]


def test_redact_aws_secret_straddling_the_body_cut_leaves_no_value(
    fake: FakeModelOpsServer,
) -> None:
    client = ModelOpsClient(base_url=fake.url, token=TOKEN)
    prefix = "x" * (_MAX_BODY_CHARS - 26)
    body = prefix + "aws_secret_access_key=SECRETVALUE tail"
    fake.script("/api/Task/t1", method="PUT", status=500, body=body.encode())

    with pytest.raises(ModelOpsApiError) as excinfo:
        client.put_json("/api/Task/t1", {})

    text = str(excinfo.value)
    assert "SECR" not in text
    assert text.endswith(prefix + "<redacted> tail")


def test_redact_aws_key_straddling_the_body_cut_never_reaches_cli_stderr(
    fake: FakeModelOpsServer, ctx: Ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "x" * (_MAX_BODY_CHARS - 6) + AKIA + " tail"
    fake.script("/api/Task/all", status=500, body=body.encode())

    result = _cli_apply(fake, ctx, monkeypatch)

    assert result.exit_code == 1
    assert len(result.stderr.splitlines()) == 1
    assert "AKIA" not in result.stdout + result.stderr
    assert result.stderr.endswith("x" * (_MAX_BODY_CHARS - 6) + "<redac\n")
    assert _writes(fake) == []


@pytest.mark.parametrize("method", ["PUT", "POST"])
def test_client_write_redirect_is_refused_and_target_never_contacted(
    fake: FakeModelOpsServer, method: str
) -> None:
    client = ModelOpsClient(base_url=fake.url, token=TOKEN)
    with FakeModelOpsServer() as target:
        fake.script(
            "/api/Task/t1",
            method=method,
            status=307,
            body=b"",
            headers={"Location": f"{target.url}/api/Task/t1"},
        )
        send = client.put_json if method == "PUT" else client.post_json

        with pytest.raises(ModelOpsApiError, match="redirect"):
            send("/api/Task/t1", {})

        assert target.requests == []


# -------------------------------------------------------- AC: create


def test_create_new_tasks_and_workflow_use_the_ids_the_fake_assigned(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake, ctx)
    _add_new_definitions(ctx)
    env_before = ctx.env_path.read_bytes()
    head = ctx.head()

    outcome = _apply(fake, ctx, capsys)

    assert outcome.code == 0, outcome.err
    ids = {t["taskName"]: t["_id"] for t in fake.tasks}
    workflow_id = _doc(fake.workflows, "new-workflow-3")["_id"]
    assert _writes(fake) == [
        ("POST", "/api/Task"),
        ("POST", "/api/Task"),
        ("POST", "/api/Workflow"),
    ]
    posted = _body(fake, "POST", "/api/Workflow")
    assert [t["taskId"] for t in posted["workflowTasks"]] == [
        ids["New One"],
        ids["New Two"],
    ]
    assert posted["createdBy"] == USER
    assert posted["lastChangeBy"] == USER
    assert posted["observation"] == f"\n[deploy/modelops {head}]"
    assert [line for line in outcome.out if line.startswith("CREATED ")] == [
        f"CREATED task new-one {ids['New One']}  "
        "(add to env file: tasks.new-one)",
        f"CREATED task new-two {ids['New Two']}  "
        "(add to env file: tasks.new-two)",
        f"CREATED workflow fresh {workflow_id}  "
        "(add to env file: workflows.fresh)",
    ]
    assert ctx.env_path.read_bytes() == env_before
    assert ctx.git("status", "--porcelain") == ""


def test_create_provisional_marker_never_reaches_a_request_body(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake, ctx)
    _add_new_definitions(ctx)

    outcome = _apply(fake, ctx, capsys)

    assert outcome.code == 0, outcome.err
    assert fake.requests
    assert all(b"<new:" not in r.body for r in fake.requests)
    assert all("<new:" not in line for line in outcome.err)


def test_create_changed_workflow_with_a_new_task_shows_marker_then_real_id(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake, ctx)
    ctx.write_task("new-one", "New One")
    ctx.edit(
        "workflows/flow.json",
        lambda doc: doc["workflowTasks"].append(
            {
                "workflowTaskId": "t9",
                "taskId": "@@task:new-one@@",
                "onFailure": 1,
            }
        ),
    )
    ctx.commit()

    outcome = _apply(fake, ctx, capsys)

    assert outcome.code == 0, outcome.err
    assert any(
        line.startswith("-") and "<new:new-one>" in line for line in outcome.out
    )
    new_id = next(t["_id"] for t in fake.tasks if t["taskName"] == "New One")
    assert _writes(fake) == [
        ("POST", "/api/Task"),
        ("PUT", "/api/Workflow/wf-1"),
    ]
    put = _body(fake, "PUT", "/api/Workflow/wf-1")
    assert put["workflowTasks"][-1]["taskId"] == new_id
    assert all(b"<new:" not in r.body for r in fake.requests)
    ctx.env_data["tasks"]["new-one"] = new_id

    dry = _dry_run(fake, ctx, capsys)

    assert "UNCHANGED task new-one" in dry.out
    assert "UNCHANGED workflow flow" in dry.out


def test_create_orders_put_before_post_within_tasks_then_workflows(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake, ctx)
    _drift(fake)
    _doc(fake.workflows, "wf-1")["workflowDescription"] = "drifted"
    ctx.write_task("aaa-new", "Aaa New")
    ctx.write(
        "workflows/fresh.json",
        json.dumps(_workflow("Fresh", ["aaa-new"])),
    )
    ctx.commit()

    outcome = _apply(fake, ctx, capsys)

    assert outcome.code == 0, outcome.err
    assert _writes(fake) == [
        ("PUT", "/api/Task/task-a"),
        ("POST", "/api/Task"),
        ("PUT", "/api/Workflow/wf-1"),
        ("POST", "/api/Workflow"),
    ]
    assert [
        line for line in outcome.out if line.startswith(("PUT ", "POST "))
    ] == [
        "PUT task alpha task-a",
        "POST task aaa-new",
        "PUT workflow flow wf-1",
        "POST workflow fresh",
    ]


def test_create_dry_run_lists_a_new_workflow_with_new_tasks_without_failures(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake, ctx)
    _add_new_definitions(ctx)

    dry = _dry_run(fake, ctx, capsys)

    assert dry.code == 0, dry.err
    assert "NEW task new-one" in dry.out
    assert "NEW task new-two" in dry.out
    assert "NEW workflow fresh" in dry.out
    assert dry.out[-1] == "summary: 3 unchanged, 0 changed, 3 new, 0 failures"
    assert _writes(fake) == []


@pytest.mark.parametrize("env_id", [None, "ghost-id"])
def test_create_reference_to_task_without_a_file_still_needs_a_protected_id(
    fake: FakeModelOpsServer,
    ctx: Ctx,
    capsys: pytest.CaptureFixture[str],
    env_id: str | None,
) -> None:
    _seed(fake, ctx)
    ctx.edit(
        "workflows/flow.json",
        lambda doc: doc["workflowTasks"].append(
            {
                "workflowTaskId": "t9",
                "taskId": "@@task:ghost@@",
                "onFailure": 1,
            }
        ),
    )
    if env_id is not None:
        ctx.env_data["tasks"]["ghost"] = env_id

    dry = _dry_run(fake, ctx, capsys)

    assert dry.code == 1
    assert len(dry.err) == 1
    assert "@@task:ghost@@" in dry.err[0]


@pytest.mark.parametrize("matches", ["duplicate", "none"])
def test_create_id_lookup_without_exactly_one_match_aborts_before_the_workflow(
    fake: FakeModelOpsServer,
    ctx: Ctx,
    capsys: pytest.CaptureFixture[str],
    matches: str,
) -> None:
    _seed(fake, ctx)
    _add_new_definitions(ctx)

    def duplicate(server: FakeModelOpsServer) -> None:
        server.tasks.append({"_id": "dup-1", "taskName": "New One"})

    def remove(server: FakeModelOpsServer) -> None:
        server.tasks[:] = [
            t for t in server.tasks if t["taskName"] != "New One"
        ]

    fake.hook(
        "POST",
        "/api/Task",
        duplicate if matches == "duplicate" else remove,
        after=True,
    )

    outcome = _apply(fake, ctx, capsys)

    assert outcome.code == 1
    assert len(outcome.err) == 1
    assert outcome.err[0].startswith(
        "apply: task new-one: expected exactly one live taskName match"
    )
    assert "WRITTEN POST task new-one" in outcome.out
    assert _writes(fake) == [("POST", "/api/Task")]
    assert not any(line.startswith("CREATED ") for line in outcome.out)


def test_create_failure_still_reports_the_ids_already_discovered(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake, ctx)
    _add_new_definitions(ctx)
    fake.script(
        "/api/Workflow",
        method="POST",
        status=500,
        body=b'{"error":"boom"}',
    )

    outcome = _apply(fake, ctx, capsys)

    ids = {t["taskName"]: t["_id"] for t in fake.tasks}
    assert outcome.code == 1
    assert [line for line in outcome.out if line.startswith("CREATED ")] == [
        f"CREATED task new-one {ids['New One']}  "
        "(add to env file: tasks.new-one)",
        f"CREATED task new-two {ids['New Two']}  "
        "(add to env file: tasks.new-two)",
    ]
    assert outcome.err == [
        "apply: POST /api/Workflow: HTTP 500: " + '{"error":"boom"}'
    ]
    assert _writes(fake)[-1] == ("POST", "/api/Workflow")


# ---------------------------------------- AC: concurrent and executing


def test_concurrent_edit_of_a_task_aborts_after_the_earlier_written_lines(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake, ctx)
    _drift(fake, "task-a")
    _drift(fake, "task-b")
    _doc(fake.workflows, "wf-1")["workflowDescription"] = "drifted"
    fake.hook(
        "GET",
        "/api/Task/task-b",
        lambda server: _doc(server.tasks, "task-b").update(script="ui edit"),
    )

    outcome = _apply(fake, ctx, capsys)

    assert outcome.code == 1
    assert "WRITTEN PUT task alpha task-a" in outcome.out
    assert "WRITTEN PUT task beta task-b" not in outcome.out
    assert outcome.err == [
        "apply: task beta changed on the server since the plan was computed"
    ]
    assert (fake.requests[-1].method, fake.requests[-1].path) == (
        "GET",
        "/api/Task/task-b",
    )
    assert _writes(fake) == [("PUT", "/api/Task/task-a")]
    assert _doc(fake.tasks, "task-b")["script"] == "ui edit"


def test_concurrent_edit_of_a_workflow_aborts_after_the_task_writes(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake, ctx)
    _drift(fake)
    _doc(fake.workflows, "wf-1")["workflowDescription"] = "drifted"
    fake.hook(
        "GET",
        "/api/Workflow/wf-1",
        lambda server: _doc(server.workflows, "wf-1").update(
            workflowDescription="edited in the UI"
        ),
    )

    outcome = _apply(fake, ctx, capsys)

    assert outcome.code == 1
    assert "WRITTEN PUT task alpha task-a" in outcome.out
    assert "WRITTEN PUT workflow flow wf-1" not in outcome.out
    assert outcome.err == [
        "apply: workflow flow changed on the server since the plan was computed"
    ]
    assert (fake.requests[-1].method, fake.requests[-1].path) == (
        "GET",
        "/api/Workflow/wf-1",
    )
    assert _writes(fake) == [("PUT", "/api/Task/task-a")]


def test_executing_workflow_put_422_stops_with_the_task_writes_reported(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake, ctx)
    _drift(fake)
    _doc(fake.workflows, "wf-1")["workflowDescription"] = "drifted"
    fake.executing.add("wf-1")

    outcome = _apply(fake, ctx, capsys)

    assert outcome.code == 1
    assert "WRITTEN PUT task alpha task-a" in outcome.out
    assert "WRITTEN PUT workflow flow wf-1" not in outcome.out
    assert len(outcome.err) == 1
    assert outcome.err[0].startswith("apply: PUT /api/Workflow/wf-1: HTTP 422")
    assert "em execução" in outcome.err[0]
    assert (fake.requests[-1].method, fake.requests[-1].path) == (
        "PUT",
        "/api/Workflow/wf-1",
    )
    assert _writes(fake).count(("PUT", "/api/Workflow/wf-1")) == 1


# ------------------------------------------------------ redaction


def test_redact_put_failure_keeps_token_and_aws_key_out_of_stderr(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake, ctx)
    _drift(fake)
    fake.script(
        "/api/Task/task-a",
        method="PUT",
        status=500,
        body=f'{{"detail": "{TOKEN} {AKIA}"}}'.encode(),
    )

    outcome = _apply(fake, ctx, capsys)

    assert outcome.code == 1
    assert len(outcome.err) == 1
    assert "<redacted>" in outcome.err[0]
    assert AKIA not in outcome.err[0]


def test_redact_plan_and_written_lines_go_through_the_scrubber(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx.env_data["tasks"]["alpha"] = TOKEN
    _seed(fake, ctx)
    _drift(fake, TOKEN)

    outcome = _apply(fake, ctx, capsys)

    assert outcome.code == 0, outcome.err
    assert "PUT task alpha <redacted>" in outcome.out
    assert "WRITTEN PUT task alpha <redacted>" in outcome.out


def test_redact_created_lines_go_through_the_scrubber(
    fake: FakeModelOpsServer, ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake, ctx)
    _add_new_definitions(ctx)
    fake.hook(
        "POST",
        "/api/Task",
        lambda server: server.tasks[-1].update(_id=TOKEN),
        after=True,
    )

    outcome = _apply(fake, ctx, capsys)

    assert outcome.code == 0, outcome.err
    assert (
        "CREATED task new-one <redacted>  (add to env file: tasks.new-one)"
        in outcome.out
    )


# ------------------------------- preconditions on in-memory plans


def _entry(
    kind: str,
    slug: str,
    status: str,
    doc_id: str | None = None,
    name: str | None = None,
    protected: bool = False,
) -> Entry:
    key = "taskName" if kind == "task" else "workflowName"
    return Entry(
        kind, slug, status, doc_id, {key: name or slug}, None, protected
    )


def _plan(
    entries: list[Entry],
    tasks: dict[str, dict[str, Any]] | None = None,
    workflows: dict[str, dict[str, Any]] | None = None,
) -> Plan:
    return Plan(
        Path("."),
        EnvFile({}, {}, {}, frozenset()),
        tuple(entries),
        tasks or {},
        workflows or {},
        (),
        (),
        0,
    )


def test_precondition_protected_writes_flag_only_changed_protected_tasks() -> (
    None
):
    in_memory = _plan(
        [
            _entry("task", "a", "CHANGED", "id-a", protected=True),
            _entry("task", "b", "UNCHANGED", "id-b", protected=True),
            _entry("task", "c", "CHANGED", "id-c"),
        ]
    )

    assert _protected_writes(in_memory) == ["task a is in protectedTaskIds"]


def test_precondition_unmanaged_references_scan_both_task_lists() -> None:
    in_memory = _plan(
        [
            _entry("workflow", "flow", "CHANGED", "managed"),
            _entry("task", "a", "CHANGED", "t1"),
            _entry("task", "b", "CHANGED", "t2"),
            _entry("task", "c", "CHANGED", "t3"),
            _entry("task", "d", "UNCHANGED", "t4"),
        ],
        workflows={
            "managed": {"workflowTasks": [{"taskId": "t3"}]},
            "legacy": {
                "workflowName": "Legacy",
                "workflowTasks": [{"taskId": "t1"}, {"taskId": "t4"}],
                "cancelationTasks": [{"taskId": "t2"}],
            },
        },
    )

    assert _unmanaged_references(in_memory) == [
        "task a is referenced by unmanaged workflow Legacy",
        "task b is referenced by unmanaged workflow Legacy",
    ]


def test_precondition_name_collisions_cover_live_and_new_duplicates() -> None:
    in_memory = _plan(
        [
            _entry("task", "a", "NEW", name="Taken"),
            _entry("task", "b", "NEW", name="Twin"),
            _entry("task", "c", "NEW", name="Twin"),
            _entry("task", "d", "NEW", name="Free"),
            _entry("task", "e", "CHANGED", "t5", name="Taken"),
            _entry("workflow", "w", "NEW", name="WTaken"),
        ],
        tasks={"t1": {"taskName": "Taken"}},
        workflows={"w1": {"workflowName": "WTaken"}},
    )

    assert _name_collisions(in_memory) == [
        "task a: taskName already exists live; record its id under "
        "tasks.a in the env file",
        "task b: taskName is shared with another new task",
        "task c: taskName is shared with another new task",
        "workflow w: workflowName already exists live; record its id under "
        "workflows.w in the env file",
    ]


def test_precondition_ordered_writes_put_before_post_tasks_before_workflows() -> (
    None
):
    in_memory = _plan(
        [
            _entry("task", "n", "NEW"),
            _entry("task", "p", "CHANGED", "t1"),
            _entry("task", "u", "UNCHANGED", "t2"),
            _entry("workflow", "wn", "NEW"),
            _entry("workflow", "wp", "CHANGED", "w1"),
        ]
    )

    assert [
        (method, entry.slug) for method, entry in _ordered_writes(in_memory)
    ] == [("PUT", "p"), ("POST", "n"), ("PUT", "wp"), ("POST", "wn")]
