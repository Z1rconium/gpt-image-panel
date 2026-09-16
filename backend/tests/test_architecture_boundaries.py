"""Layer import rules for backend/app, enforced by AST scanning."""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "backend" / "app"
APP_PACKAGE = "backend.app"

ALLOWED_TARGET_LAYERS = {
    "core": frozenset({"core"}),
    "schemas": frozenset({"core", "schemas"}),
    "runtime": frozenset({"core", "runtime"}),
    "repositories": frozenset({"core", "schemas", "runtime", "repositories"}),
    "integrations": frozenset({"core", "schemas", "runtime", "integrations"}),
    "services": frozenset(
        {"core", "schemas", "runtime", "repositories", "integrations", "services"}
    ),
    "api": frozenset(
        {
            "api",
            "core",
            "integrations",
            "repositories",
            "runtime",
            "schemas",
            "services",
        }
    ),
}

# Frozen debt: every entry is a mapping the dependency matrix forbids. Each
# architecture phase deletes the entries it fixes; the list may only shrink.
ALLOWED_VIOLATIONS: frozenset[str] = frozenset(
    {
        "backend/app/integrations/upstream/errors.py:22 -> backend.app.repositories.gallery.mutations",
        "backend/app/integrations/upstream/generation.py:23 -> backend.app.repositories.gallery.mutations",
        "backend/app/integrations/upstream/payloads.py:21 -> backend.app.repositories.gallery.mutations",
        "backend/app/integrations/upstream/transport.py:23 -> backend.app.repositories.gallery.mutations",
    }
)

# Project imports inside function bodies. Some are deliberate cycle breaks that
# Python requires; new entries need a documented reason to be added.
ALLOWED_DEFERRED_IMPORTS: frozenset[str] = frozenset(
    {
        "backend/app/api/routers/settings.py:324 -> backend.app.services.presets",
        "backend/app/api/routers/settings.py:333 -> backend.app.services.presets",
        "backend/app/api/routers/settings.py:338 -> backend.app.services.presets",
        "backend/app/api/routers/settings.py:370 -> backend.app.services.presets",
        "backend/app/core/overall_config.py:322 -> backend.app.core.secrets",
        "backend/app/core/overall_config.py:329 -> backend.app.core.security",
        "backend/app/core/redaction.py:63 -> backend.app.core.secrets",
        "backend/app/core/secrets.py:138 -> backend.app.core",
        "backend/app/core/secrets.py:320 -> backend.app.core.validators",
        "backend/app/core/secrets.py:353 -> backend.app.core",
        "backend/app/core/validators.py:38 -> backend.app.core.secrets",
        "backend/app/core/validators.py:446 -> backend.app.core.secrets",
        "backend/app/core/validators.py:500 -> backend.app.core.secrets",
        "backend/app/repositories/db/__init__.py:1964 -> backend.app.core.overall_config",
        "backend/app/repositories/db/__init__.py:2032 -> backend.app.core.overall_config",
        "backend/app/runtime/blocking.py:145 -> backend.app.repositories",
        "backend/app/runtime/blocking.py:188 -> backend.app.repositories",
        "backend/app/runtime/blocking.py:311 -> backend.app.repositories",
        "backend/app/services/presets.py:46 -> backend.app.core.redaction",
        "backend/app/services/runtime_metrics.py:71 -> backend.app.services.job_events",
    }
)


def _module_name_for(path: Path) -> str:
    relative = path.relative_to(APP_DIR).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join([APP_PACKAGE, *parts]) if parts else APP_PACKAGE


def _layer_of(module: str) -> str | None:
    prefix = f"{APP_PACKAGE}."
    if not module.startswith(prefix):
        return None
    parts = module[len(prefix) :].split(".")
    return parts[0] if parts and parts[0] in ALLOWED_TARGET_LAYERS else None


def _resolve_import_source(node: ast.ImportFrom, package: str) -> str | None:
    if not node.level:
        return node.module
    parts = package.split(".")
    drop = node.level - 1
    if drop > len(parts):
        return None
    parts = parts[: len(parts) - drop] if drop else parts
    if node.module:
        parts = [*parts, *node.module.split(".")]
    return ".".join(parts)


class _ImportCollector(ast.NodeVisitor):
    def __init__(self, package: str) -> None:
        self.package = package
        self.module_imports: list[tuple[int, str]] = []
        self.deferred_imports: list[tuple[int, str]] = []
        self._scoped = False

    def _visit_scoped(self, node: ast.AST) -> None:
        previous = self._scoped
        self._scoped = True
        self.generic_visit(node)
        self._scoped = previous

    visit_FunctionDef = _visit_scoped
    visit_AsyncFunctionDef = _visit_scoped
    visit_ClassDef = _visit_scoped

    def _record(self, node: ast.AST, modules: list[str | None]) -> None:
        target = self.deferred_imports if self._scoped else self.module_imports
        for module in modules:
            if module:
                target.append((node.lineno, module))

    def visit_Import(self, node: ast.Import) -> None:
        self._record(node, [alias.name for alias in node.names])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._record(node, [_resolve_import_source(node, self.package)])


def _scan() -> tuple[list[str], list[str]]:
    violations: list[str] = []
    deferred: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        module = _module_name_for(path)
        source_layer = _layer_of(module)
        package = module.rsplit(".", 1)[0] if not path.name == "__init__.py" else module
        if source_layer is None:
            continue
        collector = _ImportCollector(package)
        collector.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        relative_path = path.relative_to(REPO_ROOT).as_posix()
        for lineno, target in collector.module_imports:
            target_layer = _layer_of(target)
            if target_layer is None or target_layer in ALLOWED_TARGET_LAYERS[source_layer]:
                continue
            violations.append(f"{relative_path}:{lineno} -> {target}")
        for lineno, target in collector.deferred_imports:
            if _layer_of(target) is None:
                continue
            deferred.append(f"{relative_path}:{lineno} -> {target}")
    return violations, deferred


@pytest.fixture(scope="module")
def scan_result() -> tuple[list[str], list[str]]:
    return _scan()


def _report(title: str, entries: list[str]) -> str:
    listed = "\n".join(f'    "{entry}",' for entry in entries)
    return f"{title}:\n{listed}"


def test_layer_imports_follow_the_dependency_matrix(scan_result):
    violations = scan_result[0]
    unexpected = sorted(set(violations) - ALLOWED_VIOLATIONS)
    assert not unexpected, _report(
        "Layers may only import from lower layers (see ALLOWED_TARGET_LAYERS)",
        unexpected,
    )


def test_deferred_project_imports_stay_frozen(scan_result):
    deferred = scan_result[1]
    unexpected = sorted(set(deferred) - ALLOWED_DEFERRED_IMPORTS)
    assert not unexpected, _report(
        "New function-local project imports need a documented reason in ALLOWED_DEFERRED_IMPORTS",
        unexpected,
    )


def test_boundary_allowlists_have_no_stale_entries(scan_result):
    violations, deferred = scan_result
    stale_violations = sorted(ALLOWED_VIOLATIONS - set(violations))
    stale_deferred = sorted(ALLOWED_DEFERRED_IMPORTS - set(deferred))
    assert not stale_violations, _report(
        "ALLOWED_VIOLATIONS entries no longer occur, delete them",
        stale_violations,
    )
    assert not stale_deferred, _report(
        "ALLOWED_DEFERRED_IMPORTS entries no longer occur, delete them",
        stale_deferred,
    )
