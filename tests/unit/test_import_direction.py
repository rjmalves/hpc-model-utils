"""ADR-002: enforce the layered import direction of src/hpc_model_utils/."""

from __future__ import annotations

import ast
import re
import shutil
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

PACKAGE_NAME = "hpc_model_utils"
PYPROJECT_PATH = Path(__file__).resolve().parents[2] / "pyproject.toml"
_DEPENDENCY_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")

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


def _parse(py_file: Path) -> ast.Module:
    return ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))


def _import_nodes(tree: ast.Module) -> list[ast.Import | ast.ImportFrom]:
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
        for node in _import_nodes(_parse(py_file)):
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
            for node in _import_nodes(_parse(py_file))
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


def _is_type_checking_guard(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _type_checking_lines(tree: ast.Module) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.If) and _is_type_checking_guard(node.test)
        ):
            continue
        for child in ast.walk(node):
            lineno = getattr(child, "lineno", None)
            if lineno is not None:
                lines.add(lineno)
    return lines


def find_runtime_third_party_top_level_names(src_root: Path) -> set[str]:
    names: set[str] = set()
    for py_file in _iter_python_files(src_root):
        tree = _parse(py_file)
        excluded = _type_checking_lines(tree)
        for node in _import_nodes(tree):
            if node.lineno in excluded:
                continue
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names - set(sys.stdlib_module_names) - {PACKAGE_NAME}


def _normalize_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def declared_dependency_names(pyproject_path: Path) -> set[str]:
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    names = set[str]()
    for dependency in data["project"]["dependencies"]:
        match = _DEPENDENCY_NAME_RE.match(dependency)
        assert match, f"could not parse dependency name from {dependency!r}"
        names.add(_normalize_distribution_name(match.group(1)))
    return names


def test_runtime_third_party_imports_equal_declared_dependencies() -> None:
    top_level_names = find_runtime_third_party_top_level_names(SRC_ROOT)
    distributions = packages_distributions()
    imported = {
        _normalize_distribution_name(distributions[name][0])
        for name in top_level_names
    }
    declared = declared_dependency_names(PYPROJECT_PATH)

    undeclared = imported - declared
    unused = declared - imported
    assert imported == declared, (
        f"undeclared runtime imports (add to [project].dependencies): "
        f"{sorted(undeclared)}; declared but unused dependencies (remove "
        f"from [project].dependencies): {sorted(unused)}"
    )
