"""ADR-002: enforce the layered import direction of src/hpc_model_utils/."""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

PACKAGE_NAME = "hpc_model_utils"

ALLOWED_EDGES: dict[str, frozenset[str]] = {
    "infra": frozenset(),
    "core": frozenset({"infra"}),
    "platform": frozenset({"core"}),
    "models": frozenset({"core", "infra"}),
    "cli": frozenset({"core", "platform", "infra", "models"}),
}

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / PACKAGE_NAME

FORBIDDEN_LITERAL = "${CurrentExecution"
ALLOWED_LITERAL_MODULE = Path("platform") / "modelops.py"

RESTRICTED_IMPORTS = frozenset({"inewave", "idecomp"})


def _module_layer(relative_path: Path) -> str | None:
    layer = relative_path.parts[0]
    return layer if layer in ALLOWED_EDGES else None


def _package_parts(relative_path: Path) -> tuple[str, ...]:
    parts = relative_path.parts
    if relative_path.name == "__init__.py":
        return (PACKAGE_NAME, *parts[:-1])
    return (PACKAGE_NAME, *parts[:-1], relative_path.stem)


def _resolve_relative_base(
    own_package: tuple[str, ...], level: int
) -> tuple[str, ...]:
    if level == 1:
        return own_package
    return own_package[: len(own_package) - (level - 1)]


def _iter_import_targets(
    node: ast.Import | ast.ImportFrom,
    package_parts: tuple[str, ...],
    is_package: bool,
) -> list[tuple[str, ...]]:
    if isinstance(node, ast.Import):
        return [tuple(alias.name.split(".")) for alias in node.names]
    if node.level == 0:
        base = tuple(node.module.split(".")) if node.module else ()
    else:
        own_package = package_parts if is_package else package_parts[:-1]
        base = _resolve_relative_base(own_package, node.level)
        if node.module:
            base = (*base, *node.module.split("."))
    return [(*base, alias.name) for alias in node.names]


def _iter_python_files(src_root: Path) -> list[Path]:
    return sorted(src_root.rglob("*.py"))


def _import_nodes(py_file: Path) -> list[ast.Import | ast.ImportFrom]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]


def find_violations(src_root: Path) -> list[str]:
    violations: list[str] = []
    for py_file in _iter_python_files(src_root):
        relative = py_file.relative_to(src_root)
        importer_layer = _module_layer(relative)
        if importer_layer is None:
            continue
        package_parts = _package_parts(relative)
        is_package = py_file.name == "__init__.py"
        for node in _import_nodes(py_file):
            for target in _iter_import_targets(node, package_parts, is_package):
                if len(target) < 2 or target[0] != PACKAGE_NAME:
                    continue
                target_layer = target[1]
                if (
                    target_layer not in ALLOWED_EDGES
                    or target_layer == importer_layer
                ):
                    continue
                if target_layer not in ALLOWED_EDGES[importer_layer]:
                    module = ".".join(package_parts)
                    violations.append(
                        f"{module}: {importer_layer} -> {target_layer}"
                    )
    return violations


def find_forbidden_literal_usages(src_root: Path) -> list[str]:
    violations: list[str] = []
    for py_file in _iter_python_files(src_root):
        relative = py_file.relative_to(src_root)
        if relative == ALLOWED_LITERAL_MODULE:
            continue
        if FORBIDDEN_LITERAL in py_file.read_text(encoding="utf-8"):
            violations.append(str(relative))
    return violations


def find_restricted_model_imports(src_root: Path) -> list[str]:
    violations: list[str] = []
    for py_file in _iter_python_files(src_root):
        relative = py_file.relative_to(src_root)
        if relative.parts[0] == "models":
            continue
        package_parts = _package_parts(relative)
        is_package = py_file.name == "__init__.py"
        restricted = any(
            target and target[0] in RESTRICTED_IMPORTS
            for node in _import_nodes(py_file)
            for target in _iter_import_targets(node, package_parts, is_package)
        )
        if restricted:
            violations.append(str(relative))
    return violations


def test_find_violations_real_tree_reports_none() -> None:
    assert find_violations(SRC_ROOT) == []


def test_find_violations_infra_imports_core_reports_edge(
    tmp_path: Path,
) -> None:
    tree_root = tmp_path / PACKAGE_NAME
    shutil.copytree(SRC_ROOT, tree_root)
    probe = tree_root / "infra" / "_probe.py"
    probe.write_text(
        "from __future__ import annotations\n\nfrom hpc_model_utils.core import errors\n",
        encoding="utf-8",
    )
    violations = find_violations(tree_root)
    assert any("infra -> core" in v for v in violations)


def test_find_violations_relative_import_core_to_platform_reports_edge(
    tmp_path: Path,
) -> None:
    tree_root = tmp_path / PACKAGE_NAME
    shutil.copytree(SRC_ROOT, tree_root)
    probe = tree_root / "core" / "_probe.py"
    probe.write_text(
        "from __future__ import annotations\n\nfrom ..platform import x\n",
        encoding="utf-8",
    )
    violations = find_violations(tree_root)
    assert any("core -> platform" in v for v in violations)


def test_find_forbidden_literal_usages_real_tree_reports_none() -> None:
    assert find_forbidden_literal_usages(SRC_ROOT) == []


def test_find_forbidden_literal_usages_outside_modelops_reports_file(
    tmp_path: Path,
) -> None:
    tree_root = tmp_path / PACKAGE_NAME
    shutil.copytree(SRC_ROOT, tree_root)
    probe = tree_root / "core" / "_probe.py"
    probe.write_text(
        "from __future__ import annotations\n\nVALUE = '${CurrentExecution}/out'\n",
        encoding="utf-8",
    )
    violations = find_forbidden_literal_usages(tree_root)
    assert str(Path("core") / "_probe.py") in violations


def test_find_restricted_model_imports_real_tree_reports_none() -> None:
    assert find_restricted_model_imports(SRC_ROOT) == []


def test_find_restricted_model_imports_outside_models_reports_file(
    tmp_path: Path,
) -> None:
    tree_root = tmp_path / PACKAGE_NAME
    shutil.copytree(SRC_ROOT, tree_root)
    probe = tree_root / "cli" / "_probe.py"
    probe.write_text(
        "from __future__ import annotations\n\nimport inewave\n",
        encoding="utf-8",
    )
    violations = find_restricted_model_imports(tree_root)
    assert str(Path("cli") / "_probe.py") in violations
