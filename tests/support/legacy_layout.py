"""ticket-055c: builds the prd Task-script run-directory layout --
``./hpc-model-utils`` (a clone of this repository, its venv at
``./hpc-model-utils/venv``) and ``./sintetizador-<model>`` -- so the
drop-in replay suite can run the real v2 CLI through a real
``venv/bin/python`` and a real sintetizador stub, exactly as the prd
Task scripts invoke them.

``resolve_cli_bin`` (ticket-053) derives the finalize job's CLI path
from the *unresolved* ``sys.executable``, so the generated console
script's shebang must name the venv python literally (never
``Path.resolve()``d) and must live at ``venv/bin/hpc-model-utils`` --
the same ``venv/bin`` directory ``sys.executable`` reports when the
script runs.
"""

from __future__ import annotations

import site
import subprocess
import sys
from pathlib import Path

_VENV_CREATE_TIMEOUT = 60.0


def _site_packages(venv_dir: Path) -> Path:
    return (
        venv_dir
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )


def _write_sitecustomize_pth(venv_dir: Path) -> None:
    """One ``import site; site.addsitedir(<dir>)`` line per entry of
    the *running* (dev) interpreter's ``site.getsitepackages()``, so
    the dev environment's editable install of ``hpc_model_utils`` (and
    every other dependency already installed there) resolves inside
    the new venv without installing anything."""
    lines = "".join(
        f"import site; site.addsitedir({entry!r})\n"
        for entry in site.getsitepackages()
    )
    (_site_packages(venv_dir) / "_hpcmu_replay.pth").write_text(
        lines, encoding="utf-8"
    )


_CONSOLE_SCRIPT_TEMPLATE = """\
#!{python}
import sys
from pathlib import Path

sys.path[0:0] = [{repo_root!r}]

import hpc_model_utils.cli
import hpc_model_utils.cli.workflow as _workflow
from tests.support.object_store import DirectoryObjectStore

_workflow.object_store = lambda: DirectoryObjectStore(Path({store_root!r}))
{toolchain_patch}
raise SystemExit(hpc_model_utils.cli.main())
"""

# Amendment during implementation (Option D): the committed
# ``run_minimal`` golden-parse row carries no ``--mpich-path``/
# ``--slurm-path``, so ``run``'s own click defaults (the real cluster's
# absolute paths) would apply; this repoints those two ``click.Option``
# defaults, in the generated script, so ``run_minimal`` still reaches
# fake SLURM without editing ``src/`` or the committed fixture. A
# missing option is a generated-script bug, not a silent no-op, so it
# raises.
_TOOLCHAIN_PATCH_TEMPLATE = """\
_toolchain_bin = Path({toolchain_bin!r})
_mpich_opt = _slurm_opt = None
for _param in _workflow.run_command.params:
    if _param.name == "mpich_path":
        _mpich_opt = _param
    elif _param.name == "slurm_path":
        _slurm_opt = _param
if _mpich_opt is None or _slurm_opt is None:
    raise RuntimeError(
        "run command is missing its mpich_path/slurm_path options"
    )
_mpich_opt.default = _toolchain_bin
_slurm_opt.default = _toolchain_bin
"""

_REPO_ROOT = Path(__file__).resolve().parents[2]


def build_legacy_clone(
    run_dir: Path, *, store_root: Path, toolchain_bin: Path | None = None
) -> Path:
    clone_dir = run_dir / "hpc-model-utils"
    venv_dir = clone_dir / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(venv_dir)],
        timeout=_VENV_CREATE_TIMEOUT,
        check=True,
        capture_output=True,
        text=True,
    )
    _write_sitecustomize_pth(venv_dir)

    venv_python = venv_dir / "bin" / "python"
    script_path = venv_dir / "bin" / "hpc-model-utils"
    toolchain_patch = (
        ""
        if toolchain_bin is None
        else _TOOLCHAIN_PATCH_TEMPLATE.format(toolchain_bin=str(toolchain_bin))
    )
    script_path.write_text(
        _CONSOLE_SCRIPT_TEMPLATE.format(
            python=venv_python,
            repo_root=str(_REPO_ROOT),
            store_root=str(store_root),
            toolchain_patch=toolchain_patch,
        ),
        encoding="utf-8",
    )
    script_path.chmod(0o755)

    (clone_dir / "README.md").write_text("decoy\n", encoding="utf-8")
    decoy_fixtures = clone_dir / "tests" / "fixtures"
    decoy_fixtures.mkdir(parents=True, exist_ok=True)
    (decoy_fixtures / "decoy.dat").write_bytes(b"decoy\n")

    return script_path


_SINTETIZADOR_TEMPLATE = """\
#!{python}
import json
import pathlib
import sys

record = pathlib.Path({record!r})
record.parent.mkdir(parents=True, exist_ok=True)
with record.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")

sintese = pathlib.Path.cwd() / "sintese"
sintese.mkdir(exist_ok=True)
(sintese / {parquet_name!r}).write_bytes(b"x\\n")
"""


def install_sintetizador(run_dir: Path, model: str, *, record: Path) -> None:
    model_dir = run_dir / f"sintetizador-{model}"
    stub_path = model_dir / "venv" / "bin" / f"sintetizador-{model}"
    stub_path.parent.mkdir(parents=True, exist_ok=True)
    stub_path.write_text(
        _SINTETIZADOR_TEMPLATE.format(
            python=sys.executable,
            record=str(record),
            parquet_name=f"{model}_stub.parquet",
        ),
        encoding="utf-8",
    )
    stub_path.chmod(0o755)
    (model_dir / "decoy.dat").write_bytes(b"decoy\n")
