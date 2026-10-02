"""Contract tests for the pre-push secret scan hook (ADR-049)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_SRC = REPO_ROOT / ".githooks" / "pre-push"
ZERO_SHA = "0" * 40
_REQUIRED_TOOLS = ("bash", "git", "tar", "mktemp", "rm")

_STUB_GITLEAKS = """#!/usr/bin/env bash
set -euo pipefail
json_args="["
first=1
for arg in "$@"; do
  if [[ $first -eq 0 ]]; then
    json_args+=","
  fi
  escaped=${arg//\\\\/\\\\\\\\}
  escaped=${escaped//\\"/\\\\\\"}
  json_args+="\\"${escaped}\\""
  first=0
done
json_args+="]"
echo "$json_args" >> "$GITLEAKS_CALLS_FILE"
case "${1:-}" in
  git) exit "${GITLEAKS_GIT_EXIT_CODE:-0}" ;;
  dir) exit "${GITLEAKS_DIR_EXIT_CODE:-0}" ;;
  *) exit 0 ;;
esac
"""


def _git(
    repo: Path, home: Path, *args: str
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _run_hook(
    repo: Path,
    stdin_text: str,
    *,
    bin_dirs: Sequence[Path],
    calls_file: Path | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.pathsep.join(str(d) for d in bin_dirs)}
    if calls_file is not None:
        env["GITLEAKS_CALLS_FILE"] = str(calls_file)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", ".githooks/pre-push", "origin", "url"],
        input=stdin_text,
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _read_calls(calls_file: Path) -> list[list[str]]:
    if not calls_file.exists():
        return []
    return [
        json.loads(line) for line in calls_file.read_text().splitlines() if line
    ]


@pytest.fixture
def real_bin_dir(tmp_path: Path) -> Path:
    """PATH entries for the real tools the hook needs, excluding gitleaks."""
    bin_dir = tmp_path / "realbin"
    bin_dir.mkdir()
    for tool in _REQUIRED_TOOLS:
        src = shutil.which(tool)
        assert src is not None, f"{tool} not found on PATH"
        (bin_dir / tool).symlink_to(src)
    return bin_dir


@pytest.fixture
def stub_gitleaks_dir(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir()
    stub = bin_dir / "gitleaks"
    stub.write_text(_STUB_GITLEAKS)
    stub.chmod(0o755)
    return bin_dir


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str, str]:
    """A temporary git repo with two commits and the hook installed."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    home_dir = tmp_path / "home"
    home_dir.mkdir()

    hooks_dir = repo_dir / ".githooks"
    hooks_dir.mkdir()
    shutil.copy2(HOOK_SRC, hooks_dir / "pre-push")
    (hooks_dir / "pre-push").chmod(0o755)

    _git(repo_dir, home_dir, "init", "-q")
    (repo_dir / "a.txt").write_text("one\n")
    _git(repo_dir, home_dir, "add", "a.txt")
    _git(
        repo_dir,
        home_dir,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t.com",
        "commit",
        "-q",
        "-m",
        "one",
    )
    sha1 = _git(repo_dir, home_dir, "rev-parse", "HEAD").stdout.strip()

    (repo_dir / "b.txt").write_text("two\n")
    _git(repo_dir, home_dir, "add", "b.txt")
    _git(
        repo_dir,
        home_dir,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t.com",
        "commit",
        "-q",
        "-m",
        "two",
    )
    sha2 = _git(repo_dir, home_dir, "rev-parse", "HEAD").stdout.strip()

    return repo_dir, sha1, sha2


def test_missing_gitleaks_exits_1_with_message(
    repo: tuple[Path, str, str], real_bin_dir: Path
) -> None:
    repo_dir, sha1, sha2 = repo
    stdin = f"refs/heads/main {sha2} refs/heads/main {sha1}\n"

    result = _run_hook(repo_dir, stdin, bin_dirs=[real_bin_dir])

    assert result.returncode == 1
    assert "gitleaks not found" in result.stderr


def test_clean_existing_branch_push_passes_and_removes_tmp_tree(
    repo: tuple[Path, str, str],
    real_bin_dir: Path,
    stub_gitleaks_dir: Path,
    tmp_path: Path,
) -> None:
    repo_dir, sha1, sha2 = repo
    calls_file = tmp_path / "calls.jsonl"
    stdin = f"refs/heads/main {sha2} refs/heads/main {sha1}\n"

    result = _run_hook(
        repo_dir,
        stdin,
        bin_dirs=[stub_gitleaks_dir, real_bin_dir],
        calls_file=calls_file,
    )

    assert result.returncode == 0
    assert "secret scan passed (1 ref(s))" in result.stdout

    calls = _read_calls(calls_file)
    assert len(calls) == 2
    git_call, dir_call = calls
    assert git_call[0] == "git"
    assert "--redact" in git_call
    assert f"--log-opts={sha1}..{sha2}" in git_call
    assert dir_call[0] == "dir"
    assert not Path(dir_call[-1]).exists()


def test_git_scan_leak_refuses_push_and_skips_dir_scan(
    repo: tuple[Path, str, str],
    real_bin_dir: Path,
    stub_gitleaks_dir: Path,
    tmp_path: Path,
) -> None:
    repo_dir, sha1, sha2 = repo
    calls_file = tmp_path / "calls.jsonl"
    stdin = f"refs/heads/main {sha2} refs/heads/main {sha1}\n"

    result = _run_hook(
        repo_dir,
        stdin,
        bin_dirs=[stub_gitleaks_dir, real_bin_dir],
        calls_file=calls_file,
        extra_env={"GITLEAKS_GIT_EXIT_CODE": "1"},
    )

    assert result.returncode == 1
    assert "push refused" in result.stderr

    calls = _read_calls(calls_file)
    assert len(calls) == 1
    assert calls[0][0] == "git"


def test_dir_scan_leak_refuses_push_and_removes_tmp_tree(
    repo: tuple[Path, str, str],
    real_bin_dir: Path,
    stub_gitleaks_dir: Path,
    tmp_path: Path,
) -> None:
    repo_dir, sha1, sha2 = repo
    calls_file = tmp_path / "calls.jsonl"
    stdin = f"refs/heads/main {sha2} refs/heads/main {sha1}\n"

    result = _run_hook(
        repo_dir,
        stdin,
        bin_dirs=[stub_gitleaks_dir, real_bin_dir],
        calls_file=calls_file,
        extra_env={"GITLEAKS_DIR_EXIT_CODE": "1"},
    )

    assert result.returncode == 1
    assert "push refused" in result.stderr

    calls = _read_calls(calls_file)
    assert len(calls) == 2
    assert calls[1][0] == "dir"
    assert not Path(calls[1][-1]).exists()


def test_new_branch_and_deletion_lines_scan_once(
    repo: tuple[Path, str, str],
    real_bin_dir: Path,
    stub_gitleaks_dir: Path,
    tmp_path: Path,
) -> None:
    repo_dir, sha1, sha2 = repo
    calls_file = tmp_path / "calls.jsonl"
    stdin = (
        f"refs/heads/feature {sha2} refs/heads/feature {ZERO_SHA}\n"
        f"refs/heads/old {ZERO_SHA} refs/heads/old {sha1}\n"
    )

    result = _run_hook(
        repo_dir,
        stdin,
        bin_dirs=[stub_gitleaks_dir, real_bin_dir],
        calls_file=calls_file,
    )

    assert result.returncode == 0
    assert "secret scan passed (1 ref(s))" in result.stdout

    calls = _read_calls(calls_file)
    assert len(calls) == 2
    git_call = calls[0]
    log_opts_arg = next(a for a in git_call if a.startswith("--log-opts="))
    assert "--not --remotes=origin" in log_opts_arg
