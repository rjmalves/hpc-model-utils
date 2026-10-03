"""deploy.modelops.apply: the ModelOps operator CLI (ADR-033, R98).

Run as ``uv run python -m deploy.modelops.apply <command>``. This
ticket (060a) adds the read-only ``snapshot`` command; ticket-063
adds ``sync``.

Every command-level failure -- a bad ``--out``, a ``ConfigError`` from
the environment, or a ``ModelOpsApiError`` from the API -- is caught
in ``_ApplyGroup.invoke()`` rather than left to Click's own
standalone-mode formatting. ``CliRunner`` (used by the tests) always
runs a command's ``main()`` in standalone mode and never sees a
separate wrapper `main()` defined by this module, so catching these
exceptions here is what makes the single ``apply: <redacted reason>``
stderr line and exit code happen the same way under the tests and
under the real ``python -m`` entry point.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

import click

from deploy.modelops.modelops_api import (
    ConfigError,
    ModelOpsApiError,
    ModelOpsClient,
    redact,
)


class OutGuardError(Exception):
    pass


def _write_snapshot_files(out: Path, files: Mapping[str, Any]) -> None:
    """Write every file's ``.part`` first, then rename all of them, so a
    snapshot is all-or-nothing: a failure at any point removes every
    ``.part`` AND every final file this call already renamed, instead of
    leaving a partial snapshot behind (``--out`` is required to be empty
    or absent, so clearing these specific names on failure is safe).
    """
    parts = {name: out / f"{name}.part" for name in files}
    try:
        for name, payload in files.items():
            parts[name].write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        for name in files:
            os.replace(parts[name], out / name)
    except BaseException:
        for name in files:
            parts[name].unlink(missing_ok=True)
            (out / name).unlink(missing_ok=True)
        raise


def _git_toplevel(cwd: Path) -> Path | None:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip())


def _git_check_ignore(cwd: Path, target: Path) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "-q", str(target)],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return result.returncode == 0


def _first_existing_ancestor(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    raise OutGuardError(f"no existing ancestor for --out: {path}")


def _check_out_guard(out: Path) -> None:
    if out.exists():
        if not out.is_dir():
            raise OutGuardError(f"--out exists and is not a directory: {out}")
        if any(out.iterdir()):
            raise OutGuardError(f"--out is not an empty directory: {out}")
        anchor = out
    else:
        anchor = _first_existing_ancestor(out)
    toplevel = _git_toplevel(anchor)
    if toplevel is None:
        # Not inside any git work tree at all, so nothing can accidentally
        # end up tracked there; the ADR-033 guard below does not apply.
        return
    if not _git_check_ignore(toplevel, out):
        raise OutGuardError(f"--out is not git-ignored: {out}")


def _fail(reason: str, code: int) -> NoReturn:
    token = os.environ.get("MODELOPS_TOKEN", "")
    click.echo(f"apply: {redact(reason, token)}", err=True)
    raise click.exceptions.Exit(code)


class _ApplyGroup(click.Group):
    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except (ConfigError, OutGuardError) as exc:
            _fail(str(exc), 2)
        except ModelOpsApiError as exc:
            _fail(str(exc), 1)
        except OSError as exc:
            _fail(str(exc), 1)


@click.group(cls=_ApplyGroup)
def cli() -> None:
    pass


@cli.command()
@click.option(
    "--out",
    "out",
    required=True,
    type=click.Path(path_type=Path),
)
def snapshot(out: Path) -> None:
    client = ModelOpsClient.from_env(os.environ)
    resolved = out.resolve()
    _check_out_guard(resolved)
    workflows = client.get_json("/api/Workflow/all")
    tasks = client.get_json("/api/Task/all")
    resolved.mkdir(parents=True, exist_ok=True)
    meta = {
        "takenAt": datetime.now(tz=UTC).isoformat(),
        "workflowCount": len(workflows),
        "taskCount": len(tasks),
    }
    _write_snapshot_files(
        resolved,
        {"workflows.json": workflows, "tasks.json": tasks, "meta.json": meta},
    )
    click.echo(
        f"snapshot: {len(workflows)} workflows, {len(tasks)} tasks "
        f"-> {resolved}"
    )


if __name__ == "__main__":
    cli()
