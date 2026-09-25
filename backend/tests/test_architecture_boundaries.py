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
FRAMEWORK_FREE_LAYERS = frozenset(
    {"core", "integrations", "repositories", "runtime", "schemas", "services"}
)
WEB_FRAMEWORKS = ("fastapi", "starlette")
# runtime/state.py binds the shared starlette State object the app exposes as
# app.state, so it is the one module outside the api layer that may import it.
FRAMEWORK_IMPORT_ALLOWLIST: frozenset[str] = frozenset(
    {"backend/app/runtime/state.py -> starlette"}
)

# Frozen debt: every entry is a mapping the matrix forbids.
ALLOWED_VIOLATIONS: frozenset[str] = frozenset(
    {
    }
)

# Project imports inside function bodies. Some are deliberate cycle breaks that
# Python requires; new entries need a documented reason to be added.
ALLOWED_DEFERRED_IMPORTS: frozenset[str] = frozenset(
    {
        "backend/app/api/routers/settings.py:check_r2_settings_health -> backend.app.services.presets",
        "backend/app/api/routers/settings.py:update_settings -> backend.app.services.presets",
        "backend/app/core/overall_config.py:apply_rows_to_config -> backend.app.core.secrets",
        "backend/app/core/overall_config.py:apply_rows_to_config -> backend.app.core.security",
        "backend/app/core/redaction.py:redact_sensitive_text -> backend.app.core.secrets",
        "backend/app/core/secrets.py:_builtin_entries -> backend.app.core",
        "backend/app/core/secrets.py:active_secret_values -> backend.app.core",
        "backend/app/core/secrets.py:resolve_secret_reference -> backend.app.core.validators",
        "backend/app/core/validators.py:mask_socks5_proxy_url -> backend.app.core.secrets",
        "backend/app/core/validators.py:mask_webhook_url -> backend.app.core.secrets",
        "backend/app/core/validators.py:normalize_secret_env_ref_or_plaintext -> backend.app.core.secrets",
        "backend/app/repositories/db/settings_store.py:save_overall_config_overrides -> backend.app.core.overall_config",
        "backend/app/repositories/db/settings_store.py:sync_overall_config_env_values -> backend.app.core.overall_config",
        "backend/app/runtime/blocking.py:close_blocking_executors -> backend.app.repositories",
        "backend/app/runtime/blocking.py:run_db_operation -> backend.app.repositories",
        "backend/app/runtime/blocking.py:run_db_operation_in_current_thread -> backend.app.repositories",
        "backend/app/services/presets.py:get_exception_message -> backend.app.core.redaction",
        "backend/app/services/runtime_metrics.py:refresh_runtime_metrics_once -> backend.app.services.job_events",
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
        self.deferred_imports: list[tuple[str, str]] = []
        self._scope = ""

    def _visit_scoped(self, node: ast.AST) -> None:
        previous = self._scope
        self._scope = f"{previous}.{node.name}" if previous else node.name
        self.generic_visit(node)
        self._scope = previous

    visit_FunctionDef = _visit_scoped
    visit_AsyncFunctionDef = _visit_scoped
    visit_ClassDef = _visit_scoped

    def _record(self, node: ast.AST, modules: list[str | None]) -> None:
        for module in modules:
            if module:
                if self._scope:
                    self.deferred_imports.append((self._scope, module))
                else:
                    self.module_imports.append((node.lineno, module))

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
        for scope, target in collector.deferred_imports:
            if _layer_of(target) is None:
                continue
            deferred.append(f"{relative_path}:{scope} -> {target}")
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


def _framework_imports() -> list[str]:
    """FastAPI/starlette imports in layers that must stay framework-free."""
    found: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        source_layer = _layer_of(_module_name_for(path))
        if source_layer not in FRAMEWORK_FREE_LAYERS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative_path = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                top_level = module.split(".", 1)[0]
                if top_level in WEB_FRAMEWORKS:
                    found.append(f"{relative_path} -> {top_level}")
    return found


def test_non_api_layers_do_not_import_a_web_framework():
    found = _framework_imports()
    unexpected = sorted(set(found) - FRAMEWORK_IMPORT_ALLOWLIST)
    assert not unexpected, _report(
        "Only the api layer may import FastAPI/starlette (see FRAMEWORK_IMPORT_ALLOWLIST)",
        unexpected,
    )


def test_framework_import_allowlist_has_no_stale_entries():
    stale = sorted(FRAMEWORK_IMPORT_ALLOWLIST - set(_framework_imports()))
    assert not stale, _report(
        "FRAMEWORK_IMPORT_ALLOWLIST entries no longer occur, delete them",
        stale,
    )
