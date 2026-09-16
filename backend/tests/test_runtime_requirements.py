from pathlib import Path


def test_turnstile_uses_the_shared_session_pool():
    """The app has one HTTP stack; a second client library must not creep in."""
    turnstile = (
        Path(__file__).resolve().parents[1] / "app/integrations/turnstile.py"
    ).read_text(encoding="utf-8")

    assert "from .session_pool import" in turnstile
    assert "httpx" not in turnstile


def test_test_client_http_dependency_is_declared():
    """starlette.testclient imports httpx, so the test requirements need it."""
    dev_requirements = (
        Path(__file__).resolve().parents[1] / "requirements-dev.txt"
    ).read_text(encoding="utf-8")
    runtime_requirements = (
        Path(__file__).resolve().parents[2] / "requirements.txt"
    ).read_text(encoding="utf-8")

    package_names = {
        line.split("[", 1)[0].split("=", 1)[0].split("<", 1)[0].split(">", 1)[0].strip()
        for line in dev_requirements.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "httpx" in package_names
    assert "httpx" not in runtime_requirements


def _requirement_names(text: str) -> set[str]:
    names: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-")):
            continue
        name = stripped.split("[", 1)[0].split(";", 1)[0]
        for separator in ("==", ">=", "<=", "~=", "!=", ">", "<", " "):
            name = name.split(separator, 1)[0]
        names.add(name.strip().lower().replace("_", "-"))
    return names


def test_requirements_lock_pins_every_runtime_requirement_with_hashes():
    """The Docker image installs requirements.lock with --require-hashes, so
    every top-level requirement must be pinned there and every pin hashed;
    otherwise amd64/arm64 images can drift apart (boto3 did in v1.5.2)."""
    root = Path(__file__).resolve().parents[2]
    requirements = _requirement_names((root / "requirements.txt").read_text(encoding="utf-8"))
    lock_text = (root / "requirements.lock").read_text(encoding="utf-8")

    pinned: set[str] = set()
    unhashed: list[str] = []
    lines = lock_text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith((" ", "#")) or "==" not in line:
            continue
        name = line.split("==", 1)[0].strip().lower().replace("_", "-")
        pinned.add(name)
        has_hash = "--hash=" in line
        cursor = index + 1
        while not has_hash and cursor < len(lines) and lines[cursor].startswith(" "):
            has_hash = "--hash=" in lines[cursor]
            cursor += 1
        if not has_hash:
            unhashed.append(name)

    assert requirements <= pinned, sorted(requirements - pinned)
    assert unhashed == []
    # The dev-only reload extra must not leak into the production lock.
    assert "watchfiles" not in pinned
