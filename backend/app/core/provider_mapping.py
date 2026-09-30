"""Pure helpers for declarative custom-provider mappings.

Nothing here evaluates code: request bodies are JSON templates with a fixed set
of ``{{placeholder}}`` variables, and response values are selected with a small
JSONPath subset (``$.a.b[0]`` and ``[*]``).
"""

import re
from typing import Any

TEMPLATE_VARIABLES = frozenset(
    {"prompt", "model", "n", "width", "height", "size", "quality", "output_format", "background"}
)
MAX_PATH_TOKENS = 12
MAX_TEMPLATE_DEPTH = 6
MAX_TEMPLATE_NODES = 200

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_WHOLE_PLACEHOLDER = re.compile(r"^\s*\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}\s*$")
_PATH_TOKEN = re.compile(r"\.([A-Za-z_][A-Za-z0-9_\-]*)|\[(\d{1,4})\]|\[(\*)\]")


class ProviderMappingError(ValueError):
    pass


class _Wildcard:
    def __repr__(self) -> str:
        return "[*]"


WILDCARD = _Wildcard()
PathToken = str | int | _Wildcard


def parse_json_path(path: str) -> tuple[PathToken, ...]:
    text = str(path or "").strip()
    if not text.startswith("$"):
        raise ProviderMappingError(f"path must start with '$': {path!r}")
    tokens: list[PathToken] = []
    position = 1
    while position < len(text):
        match = _PATH_TOKEN.match(text, position)
        if not match:
            raise ProviderMappingError(f"unsupported path syntax: {path!r}")
        key, index, wildcard = match.groups()
        if key is not None:
            tokens.append(key)
        elif index is not None:
            tokens.append(int(index))
        else:
            tokens.append(WILDCARD)
        position = match.end()
    if len(tokens) > MAX_PATH_TOKENS:
        raise ProviderMappingError(f"path is too deep: {path!r}")
    return tuple(tokens)


def select_all(data: Any, path: str) -> list[Any]:
    """Return every value matched by ``path`` (missing keys yield nothing)."""
    current = [data]
    for token in parse_json_path(path):
        following: list[Any] = []
        for node in current:
            if isinstance(token, _Wildcard):
                if isinstance(node, list):
                    following.extend(node)
            elif isinstance(token, int):
                if isinstance(node, list) and token < len(node):
                    following.append(node[token])
            elif isinstance(node, dict) and token in node:
                following.append(node[token])
        current = following
    return current


def select_first(data: Any, path: str) -> Any | None:
    values = select_all(data, path)
    return values[0] if values else None


def template_variables(node: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(node, str):
        names.update(_PLACEHOLDER.findall(node))
    elif isinstance(node, dict):
        for value in node.values():
            names.update(template_variables(value))
    elif isinstance(node, list):
        for value in node:
            names.update(template_variables(value))
    return names


def validate_template_shape(node: Any) -> None:
    """Reject bodies that are too deep/large or not plain JSON values."""
    count = 0

    def visit(value: Any, depth: int) -> None:
        nonlocal count
        count += 1
        if depth > MAX_TEMPLATE_DEPTH:
            raise ProviderMappingError("request template is nested too deeply")
        if count > MAX_TEMPLATE_NODES:
            raise ProviderMappingError("request template is too large")
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise ProviderMappingError("request template keys must be strings")
                if _PLACEHOLDER.search(key):
                    raise ProviderMappingError("placeholders are only allowed in values, not keys")
                visit(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                visit(child, depth + 1)
        elif not (value is None or isinstance(value, (str, int, float, bool))):
            raise ProviderMappingError("request template may only contain JSON values")

    visit(node, 0)


def render_template(node: Any, variables: dict[str, Any]) -> Any:
    """Substitute placeholders. A value that is exactly one placeholder keeps its type."""
    if isinstance(node, str):
        whole = _WHOLE_PLACEHOLDER.match(node)
        if whole:
            return _variable(variables, whole.group(1))
        return _PLACEHOLDER.sub(lambda m: str(_variable(variables, m.group(1))), node)
    if isinstance(node, dict):
        return {key: render_template(value, variables) for key, value in node.items()}
    if isinstance(node, list):
        return [render_template(value, variables) for value in node]
    return node


def _variable(variables: dict[str, Any], name: str) -> Any:
    if name not in TEMPLATE_VARIABLES:
        raise ProviderMappingError(f"unknown template variable: {name}")
    value = variables.get(name)
    if value is None:
        raise ProviderMappingError(f"template variable '{name}' has no value for this request")
    return value
