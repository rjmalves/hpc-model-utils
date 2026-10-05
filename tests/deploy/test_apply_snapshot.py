"""Tests for deploy.modelops.apply's ``snapshot`` command (ticket-060a).

Acceptance criteria, selected with ``-k <keyword>``:
``snapshot_writes``, ``config``, ``transport``, ``redact``,
``out_guard``.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from click.testing import CliRunner

from deploy.modelops.apply import cli
from deploy.modelops.modelops_api import (
    ModelOpsApiError,
    ModelOpsClient,
    redact,
)
from tests.support.fake_modelops import FakeModelOpsServer

TOKEN = "test-token-abc123"  # noqa: S105 -- fixture value, never a real secret
_GIT_TIMEOUT = 30


def _env(url: str, token: str | None = TOKEN) -> dict[str, str | None]:
    return {"MODELOPS_URL": url, "MODELOPS_TOKEN": token}


@pytest.fixture
def fake() -> Iterator[FakeModelOpsServer]:
    with FakeModelOpsServer() as server:
        yield server


@pytest.fixture
def evil() -> Iterator[FakeModelOpsServer]:
    """A second local server standing in for a redirect target / proxy
    that must never receive a request or the bearer token."""
    with FakeModelOpsServer() as server:
        yield server


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    env = {**os.environ, "HOME": str(root), "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(
        ["git", "init", "-q", str(root)],
        env=env,
        check=True,
        timeout=_GIT_TIMEOUT,
    )
    (root / ".gitignore").write_text("ignored/\n")
    return root


def _one_stderr_line(stderr: str) -> str:
    lines = stderr.splitlines()
    assert len(lines) == 1, stderr
    return lines[0]


# --------------------------------------------------------------- AC: snapshot_writes


def test_snapshot_writes_round_trips_and_prints_one_summary_line(
    fake: FakeModelOpsServer, tmp_path: Path
) -> None:
    fake.workflows = [{"_id": "w1", "name": "NEWAVE - PEM"}, {"_id": "w2"}]
    fake.tasks = [{"_id": "t1"}, {"_id": "t2"}, {"_id": "t3"}]
    out = tmp_path / "snapshot"

    result = CliRunner().invoke(
        cli, ["snapshot", "--out", str(out)], env=_env(fake.url)
    )

    assert result.exit_code == 0, result.stderr
    assert result.stderr == ""
    assert json.loads((out / "workflows.json").read_text()) == fake.workflows
    assert json.loads((out / "tasks.json").read_text()) == fake.tasks
    meta = json.loads((out / "meta.json").read_text())
    assert meta["workflowCount"] == 2
    assert meta["taskCount"] == 3
    assert "takenAt" in meta
    assert result.stdout == f"snapshot: 2 workflows, 3 tasks -> {out}\n"
    assert not (out / "workflows.json.part").exists()


def test_snapshot_writes_creates_missing_parent_directories(
    fake: FakeModelOpsServer, tmp_path: Path
) -> None:
    fake.workflows = []
    fake.tasks = []
    out = tmp_path / "nested" / "snapshot"

    result = CliRunner().invoke(
        cli, ["snapshot", "--out", str(out)], env=_env(fake.url)
    )

    assert result.exit_code == 0, result.stderr
    assert sorted(p.name for p in out.iterdir()) == [
        "meta.json",
        "tasks.json",
        "workflows.json",
    ]


def test_snapshot_writes_partial_rename_failure_leaves_no_json(
    fake: FakeModelOpsServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake.workflows = [{"_id": "w1"}]
    fake.tasks = [{"_id": "t1"}]
    out = tmp_path / "snapshot"
    real_replace = os.replace

    def _flaky_replace(src: Path, dst: Path) -> None:
        if Path(dst).name == "tasks.json":
            raise OSError("forced failure for test")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _flaky_replace)

    result = CliRunner().invoke(
        cli, ["snapshot", "--out", str(out)], env=_env(fake.url)
    )

    assert result.exit_code == 1, result.stderr
    assert list(out.glob("*.json")) == []
    assert list(out.glob("*.part")) == []


# --------------------------------------------------------------- AC: config / transport


def test_apply_snapshot_config_missing_token_exits_2(
    fake: FakeModelOpsServer, tmp_path: Path
) -> None:
    out = tmp_path / "snapshot"

    result = CliRunner().invoke(
        cli, ["snapshot", "--out", str(out)], env=_env(fake.url, token=None)
    )

    assert result.exit_code == 2, result.stderr
    assert _one_stderr_line(result.stderr).startswith("apply: ")
    assert fake.requests == []
    assert not out.exists()


def test_apply_snapshot_transport_rejects_http_for_non_localhost(
    tmp_path: Path,
) -> None:
    out = tmp_path / "snapshot"

    result = CliRunner().invoke(
        cli,
        ["snapshot", "--out", str(out)],
        env=_env("http://example.com"),
    )

    assert result.exit_code == 2, result.stderr
    assert _one_stderr_line(result.stderr).startswith("apply: ")
    assert not out.exists()


def test_apply_snapshot_transport_rejects_userinfo(
    fake: FakeModelOpsServer, tmp_path: Path
) -> None:
    out = tmp_path / "snapshot"
    port = fake.server_address[1]

    result = CliRunner().invoke(
        cli,
        ["snapshot", "--out", str(out)],
        env=_env(f"http://user:pass@127.0.0.1:{port}"),
    )

    assert result.exit_code == 2, result.stderr
    assert "pass" not in result.stderr
    assert _one_stderr_line(result.stderr).startswith("apply: ")
    assert fake.requests == []
    assert not out.exists()


def test_apply_snapshot_transport_config_error_never_echoes_password(
    tmp_path: Path,
) -> None:
    out = tmp_path / "snapshot"

    result = CliRunner().invoke(
        cli,
        ["snapshot", "--out", str(out)],
        env=_env("https://alice:pw@host/"),
    )

    assert result.exit_code == 2, result.stderr
    assert "pw" not in result.stderr
    assert "alice" not in result.stderr
    assert _one_stderr_line(result.stderr).startswith("apply: ")


def test_apply_snapshot_transport_rejects_query_string(
    fake: FakeModelOpsServer, tmp_path: Path
) -> None:
    out = tmp_path / "snapshot"

    result = CliRunner().invoke(
        cli,
        ["snapshot", "--out", str(out)],
        env=_env(f"{fake.url}?x=1"),
    )

    assert result.exit_code == 2, result.stderr
    assert _one_stderr_line(result.stderr).startswith("apply: ")
    assert fake.requests == []
    assert not out.exists()


def test_apply_snapshot_transport_sends_bearer_token_header(
    fake: FakeModelOpsServer, tmp_path: Path
) -> None:
    fake.workflows = []
    fake.tasks = []
    out = tmp_path / "snapshot"

    result = CliRunner().invoke(
        cli, ["snapshot", "--out", str(out)], env=_env(fake.url)
    )

    assert result.exit_code == 0, result.stderr
    assert [r.path for r in fake.requests] == [
        "/api/Workflow/all",
        "/api/Task/all",
    ]
    for recorded in fake.requests:
        assert recorded.method == "GET"
        assert recorded.headers["Authorization"] == f"Bearer {TOKEN}"
        assert recorded.headers["Accept"] == "application/json"


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_apply_snapshot_transport_refuses_redirect_to_second_host(
    fake: FakeModelOpsServer,
    evil: FakeModelOpsServer,
    tmp_path: Path,
    code: int,
) -> None:
    fake.script(
        "/api/Workflow/all",
        status=code,
        body=b"",
        headers={"Location": f"{evil.url}/api/Workflow/all"},
    )
    out = tmp_path / "snapshot"

    result = CliRunner().invoke(
        cli, ["snapshot", "--out", str(out)], env=_env(fake.url)
    )

    assert result.exit_code == 1, result.stderr
    assert _one_stderr_line(result.stderr).startswith("apply: ")
    assert evil.requests == []
    assert not out.exists()


def test_apply_snapshot_transport_redirect_raises_modelopsapierror(
    fake: FakeModelOpsServer, evil: FakeModelOpsServer
) -> None:
    fake.script(
        "/api/Workflow/all",
        status=302,
        body=b"",
        headers={"Location": f"{evil.url}/api/Workflow/all"},
    )
    client = ModelOpsClient(base_url=fake.url, token=TOKEN)

    with pytest.raises(ModelOpsApiError, match="HTTP 302") as excinfo:
        client.get_json("/api/Workflow/all")

    assert evil.requests == []
    assert TOKEN not in str(excinfo.value)


_PARTIAL_RESPONSE = b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n[{"


@contextmanager
def _one_shot_server(reply: bytes, *, reset: bool) -> Iterator[str]:
    """Serve one connection: read the request, write ``reply``, then
    close -- with an RST instead of a FIN when ``reset``."""
    with socket.create_server(("127.0.0.1", 0)) as listener:
        listener.settimeout(5)

        def serve() -> None:
            conn, _ = listener.accept()
            with conn, conn.makefile("rb") as request:
                while request.readline() not in (b"\r\n", b""):
                    pass
                conn.sendall(reply)
                if reset:
                    conn.setsockopt(
                        socket.SOL_SOCKET,
                        socket.SO_LINGER,
                        struct.pack("ii", 1, 0),
                    )

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
        thread.join(timeout=5)


@pytest.mark.parametrize(
    ("reply", "reset"),
    [
        pytest.param(b"", False, id="closed-before-response"),
        pytest.param(_PARTIAL_RESPONSE, True, id="reset-mid-response"),
        pytest.param(_PARTIAL_RESPONSE, False, id="truncated-body"),
    ],
)
def test_apply_snapshot_transport_dropped_connection_raises_modelopsapierror(
    reply: bytes, reset: bool
) -> None:
    path = f"/api/Workflow/all?probe={TOKEN}"

    with _one_shot_server(reply, reset=reset) as url:
        client = ModelOpsClient(base_url=url, token=TOKEN)
        with pytest.raises(
            ModelOpsApiError, match=r"^GET /api/Workflow/all"
        ) as excinfo:
            client.get_json(path)

    assert TOKEN not in str(excinfo.value)
    assert "<redacted>" in str(excinfo.value)


def test_apply_snapshot_transport_http_error_with_truncated_body_raises_modelopsapierror() -> (
    None
):
    reply = b"HTTP/1.1 500 Err\r\nContent-Length: 100\r\n\r\nxx"
    path = f"/api/Workflow/all?probe={TOKEN}"

    with _one_shot_server(reply, reset=False) as url:
        client = ModelOpsClient(base_url=url, token=TOKEN)
        with pytest.raises(
            ModelOpsApiError, match=r"^GET /api/Workflow/all.*HTTP 500"
        ) as excinfo:
            client.get_json(path)

    assert TOKEN not in str(excinfo.value)
    assert "<redacted>" in str(excinfo.value)


def test_apply_snapshot_transport_ignores_http_proxy_env(
    fake: FakeModelOpsServer,
    evil: FakeModelOpsServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake.workflows = []
    fake.tasks = []
    monkeypatch.setenv("HTTP_PROXY", evil.url)
    out = tmp_path / "snapshot"

    result = CliRunner().invoke(
        cli, ["snapshot", "--out", str(out)], env=_env(fake.url)
    )

    assert result.exit_code == 0, result.stderr
    assert evil.requests == []
    assert [r.path for r in fake.requests] == [
        "/api/Workflow/all",
        "/api/Task/all",
    ]


@pytest.mark.parametrize(
    ("value", "token", "expected"),
    [
        pytest.param(
            "prefix secret-value suffix",
            "secret-value",
            "prefix <redacted> suffix",
            id="literal_token",
        ),
        pytest.param(
            "Authorization: Bearer tok123",
            "",
            "Authorization: <redacted>",
            id="bearer_header",
        ),
        pytest.param(
            "nothing sensitive here",
            "token-not-present",
            "nothing sensitive here",
            id="plain_string",
        ),
    ],
)
def test_redact_function_masks_token_and_bearer_header(
    value: str, token: str, expected: str
) -> None:
    assert redact(value, token) == expected


# --------------------------------------------------------------- AC: redact


def test_apply_snapshot_redact_hides_token_from_cli_output(
    fake: FakeModelOpsServer, tmp_path: Path
) -> None:
    fake.script(
        "/api/Workflow/all",
        status=500,
        body=f"upstream failure, Authorization: Bearer {TOKEN}".encode(),
    )
    out = tmp_path / "snapshot"

    result = CliRunner().invoke(
        cli, ["snapshot", "--out", str(out)], env=_env(fake.url)
    )

    assert result.exit_code == 1, result.stderr
    assert TOKEN not in result.stdout
    assert TOKEN not in result.stderr
    assert _one_stderr_line(result.stderr).startswith("apply: ")
    assert not out.exists()


def test_modelops_api_redact_excludes_token_from_raised_error(
    fake: FakeModelOpsServer,
) -> None:
    fake.script(
        "/api/Workflow/all",
        status=500,
        body=f"echo Authorization: Bearer {TOKEN}".encode(),
    )
    client = ModelOpsClient(base_url=fake.url, token=TOKEN)

    with pytest.raises(ModelOpsApiError, match="HTTP 500") as excinfo:
        client.get_json("/api/Workflow/all")

    assert TOKEN not in str(excinfo.value)


# --------------------------------------------------------------- AC: out_guard


def test_apply_snapshot_out_guard_unignored_path_exits_2_and_writes_nothing(
    git_repo: Path, fake: FakeModelOpsServer
) -> None:
    fake.workflows = []
    fake.tasks = []
    out = git_repo / "unignored" / "snapshot"

    result = CliRunner().invoke(
        cli, ["snapshot", "--out", str(out)], env=_env(fake.url)
    )

    assert result.exit_code == 2, result.stderr
    assert _one_stderr_line(result.stderr).startswith("apply: ")
    assert not out.exists()


def test_apply_snapshot_out_guard_nonempty_directory_exits_2_and_writes_nothing(
    git_repo: Path, fake: FakeModelOpsServer
) -> None:
    fake.workflows = []
    fake.tasks = []
    out = git_repo / "ignored" / "snapshot"
    out.mkdir(parents=True)
    (out / "stale.txt").write_text("leftover\n")

    result = CliRunner().invoke(
        cli, ["snapshot", "--out", str(out)], env=_env(fake.url)
    )

    assert result.exit_code == 2, result.stderr
    assert _one_stderr_line(result.stderr).startswith("apply: ")
    assert [p.name for p in out.iterdir()] == ["stale.txt"]


def test_apply_snapshot_out_guard_ignored_path_writes_all_three_files(
    git_repo: Path, fake: FakeModelOpsServer
) -> None:
    fake.workflows = [{"_id": "w1"}]
    fake.tasks = [{"_id": "t1"}]
    out = git_repo / "ignored" / "snapshot"

    result = CliRunner().invoke(
        cli, ["snapshot", "--out", str(out)], env=_env(fake.url)
    )

    assert result.exit_code == 0, result.stderr
    assert sorted(p.name for p in out.iterdir()) == [
        "meta.json",
        "tasks.json",
        "workflows.json",
    ]
