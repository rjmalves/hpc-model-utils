"""ticket-055c: seeds the versoes prefix and generates the NEWAVE/
DECOMP executable stubs the drop-in replay runs for real, through fake
SLURM's ``mpiexec``/``srun`` shims (``os.execvp``) and the legacy
clone's console script.

Every stub is a plain Python script (``#!{sys.executable}``) so it
runs under this machine's own interpreter regardless of the legacy
clone's venv. The entrypoint stubs bake in the absolute path of the
real output fixtures (``tests/fixtures/...``) rather than copying
their bytes into a scratch payload at seed time: ``pmo_bytes
("complete")`` is the vendored ``pmo.dat`` unchanged, and
``relato_bytes("converged")`` is the vendored ``relato.rv0`` plus the
small, literal ``CMO_BLOCK`` text -- both reproduced here without
duplicating the (multi-megabyte) fixtures on disk.
"""

from __future__ import annotations

import sys
from typing import Literal

from hpc_model_utils.infra.s3 import S3Uri
from hpc_model_utils.models import PLUGINS
from tests.support.decks import synthetic_outputs
from tests.support.decomp_outputs import CMO_BLOCK, RELATO_FIXTURE
from tests.support.newave_outputs import PMO_FIXTURE
from tests.support.object_store import RecordingObjectStore

Model = Literal["newave", "decomp"]

VERSIONS_BUCKET = "versions-bucket"

_NEWAVE_EXTRA_EXECUTABLES: tuple[str, ...] = ("nwlistcf", "nwlistop")

_NOOP_STUB = "#!{python}\nimport sys\n\nsys.exit(0)\n"

_DECOMP_EXCLUDED_OUTPUTS = frozenset(
    {"relato.rv0", "inviab.rv0", "inviab_unic.rv0"}
)


def _placeholder_writes(names: tuple[str, ...]) -> str:
    return "".join(
        f"_p = pathlib.Path({name!r})\n"
        "_p.parent.mkdir(parents=True, exist_ok=True)\n"
        '_p.write_bytes(b"x\\n")\n'
        for name in names
    )


def _newave_entrypoint_stub() -> str:
    return (
        f"#!{sys.executable}\n"
        "import pathlib\n"
        "\n"
        f"{_placeholder_writes(synthetic_outputs('newave'))}"
        f"pathlib.Path('pmo.dat').write_bytes("
        f"pathlib.Path({str(PMO_FIXTURE)!r}).read_bytes())\n"
    )


def _decomp_entrypoint_stub() -> str:
    names = tuple(
        name
        for name in synthetic_outputs("decomp")
        if name not in _DECOMP_EXCLUDED_OUTPUTS
    )
    return (
        f"#!{sys.executable}\n"
        "import pathlib\n"
        "\n"
        f"{_placeholder_writes(names)}"
        f"pathlib.Path('relato.rv0').write_bytes("
        f"pathlib.Path({str(RELATO_FIXTURE)!r}).read_bytes() + "
        f"{CMO_BLOCK!r}.encode('ascii'))\n"
    )


_ENTRYPOINT_STUBS = {
    "newave": _newave_entrypoint_stub,
    "decomp": _decomp_entrypoint_stub,
}


def seed_versions(
    store: RecordingObjectStore, model: Model, version: str
) -> str:
    """Seeds ``s3://versions-bucket/versoes/<model>/<version>/`` with
    one object per name in ``PLUGINS[model].executables`` (entrypoint,
    normalizer, licences), plus ``nwlistcf``/``nwlistop`` for NEWAVE.
    Returns the versoes URI."""
    executables = PLUGINS[model].executables
    prefix = S3Uri(VERSIONS_BUCKET, f"versoes/{model}/{version}/")

    store.seed(
        prefix.join(executables.entrypoint), _ENTRYPOINT_STUBS[model]().encode()
    )
    if executables.name_normalizer is not None:
        store.seed(
            prefix.join(executables.name_normalizer),
            _NOOP_STUB.format(python=sys.executable).encode(),
        )
    for name in executables.license_files:
        store.seed(prefix.join(name), f"placeholder licence: {name}\n".encode())
    if model == "newave":
        for name in _NEWAVE_EXTRA_EXECUTABLES:
            store.seed(
                prefix.join(name),
                _NOOP_STUB.format(python=sys.executable).encode(),
            )

    return str(prefix)
