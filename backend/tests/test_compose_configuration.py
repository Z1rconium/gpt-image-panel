"""Keep Compose's environment boundary small and its defaults backend-owned."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.yml"
REFERENCE = ROOT / "CONFIGURATION.md"


def forwarded_names() -> list[str]:
    text = COMPOSE.read_text()
    match = re.search(r"^    environment:\n((?:      - [^\n]+\n)+)", text, re.MULTILINE)
    assert match, "web must have an explicit environment list"
    entries = re.findall(r"^      - (.+)$", match.group(1), re.MULTILINE)
    assert all(re.fullmatch(r"[A-Z][A-Z0-9_]*", entry) for entry in entries)
    assert len(entries) == len(set(entries))
    assert not re.search(r"^\s*env_file:", text, re.MULTILINE)
    return entries


def test_compose_forwards_only_named_settings_and_documents_each():
    names = forwarded_names()
    assert {"ACCESS_KEY", "MAX_FILE_SIZE_MB", "ENABLE_METRICS"} <= set(names)
    retired = {"UPSTREAM_HOST_ALLOWLIST", "UPSTREAM_PROXY_HOST_ALLOWLIST",
               "PROMPT_OPTIMIZER_HOST_ALLOWLIST", "WEBHOOK_HOST_ALLOWLIST",
               "R2_ENDPOINT_HOST_ALLOWLIST", "RUNTIME_METRICS_REFRESH_SECONDS",
               "DB_EXECUTOR_WORKERS", "PROMPT_OPTIMIZER_MAX_OUTPUT_CHARS"}
    assert not retired.intersection(names)
    reference = REFERENCE.read_text()
    assert all(f"| `{name}` |" in reference for name in names)


def test_compose_project_env_forwarding(tmp_path):
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("Docker Compose is not installed")
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)}
    version = subprocess.run([docker, "compose", "version"], env=env, capture_output=True, check=False)
    if version.returncode:
        pytest.skip("Docker Compose is not available")
    (tmp_path / "compose.yaml").write_text(COMPOSE.read_text())
    (tmp_path / ".env").write_text(
        "ACCESS_KEY=example-password\nMAX_FILE_SIZE_MB=77\n"
        "DEFAULT_API_URL=\nUNLISTED_PROJECT_VALUE=do-not-forward\n"
    )
    env["UNLISTED_HOST_VALUE"] = "do-not-forward"
    result = subprocess.run(
        [docker, "compose", "--project-directory", str(tmp_path), "config", "--format", "json"],
        cwd=tmp_path, env=env, text=True, capture_output=True, check=True,
    )
    container_env = json.loads(result.stdout)["services"]["web"]["environment"]
    assert container_env["ACCESS_KEY"] == "example-password"
    assert container_env["MAX_FILE_SIZE_MB"] == "77"
    assert container_env["DEFAULT_API_URL"] == ""
    assert container_env.get("ENABLE_METRICS") is None
    assert container_env.get("MAX_ACTIVE_GENERATE_JOBS") is None
    assert "UNLISTED_PROJECT_VALUE" not in container_env
    assert "UNLISTED_HOST_VALUE" not in container_env
