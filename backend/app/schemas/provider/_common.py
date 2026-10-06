"""Shared constants and validators for provider mappings."""

import json
import re

from ...core.provider_mapping import ProviderMappingError, TEMPLATE_VARIABLES, _PLACEHOLDER, parse_json_path, template_variables
from ..common import StrictRequestModel


MAX_PROVIDER_CONFIG_BYTES = 16 * 1024


EDIT_TEMPLATE_VARIABLES = TEMPLATE_VARIABLES | {"reference_images", "mask"}


APP_IMAGE_FORMATS: tuple[str, ...] = ("jpeg", "png", "webp")


_MODEL_PLACEHOLDER = re.compile(r"\{\{\s*model\s*\}\}")


_TASK_ID_PLACEHOLDER = re.compile(r"\{\{\s*task_id\s*\}\}")


_ANY_PLACEHOLDER = re.compile(r"\{\{")


_TOKEN = re.compile(r"^[A-Za-z0-9!#$%&'*+\-.^_`|~]+$")


_PLAIN_PATH = re.compile(r"^(/[A-Za-z0-9._~\-/]*)?$")


def _check_path(path: str, label: str) -> str:
    try:
        parse_json_path(path)
    except ProviderMappingError as exc:
        raise ValueError(f"{label}: {exc}") from exc
    return path.strip()


def _check_url_template(template: str, label: str) -> str:
    value = template.strip()
    if not _TASK_ID_PLACEHOLDER.search(value):
        raise ValueError(f"{label} must contain {{{{task_id}}}}")
    plain = _TASK_ID_PLACEHOLDER.sub("t", value)
    if (
        not plain.startswith("/")
        or len(plain) < 2
        or ".." in plain
        or "//" in plain
        or not _PLAIN_PATH.match(plain)
        or _ANY_PLACEHOLDER.search(plain)
    ):
        raise ValueError(
            f"{label} must be a plain path starting with '/' (letters, digits, . _ ~ - /) "
            "and may only contain {{task_id}}"
        )
    return value


def _check_config_size(model: StrictRequestModel, label: str) -> None:
    if len(json.dumps(model.model_dump(mode="json")).encode("utf-8")) > MAX_PROVIDER_CONFIG_BYTES:
        raise ValueError(f"{label} is too large")


QUERY_VARIABLE_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"
)


_FORM_FIELD = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")


_MAX_QUERY_PARAMS = 16


def _check_query(value: dict[str, str], label: str, *, allowed: frozenset[str]) -> dict[str, str]:
    if len(value) > _MAX_QUERY_PARAMS:
        raise ValueError(f"{label} has too many parameters (max {_MAX_QUERY_PARAMS})")
    for key, template in value.items():
        if not isinstance(template, str):
            raise ValueError(f"{label}.{key} must be a string template")
        if not key or len(key) > 64:
            raise ValueError(f"{label}.{key} is not a valid query parameter name")
        if "{{" in key:
            raise ValueError(f"{label}.{key}: placeholders are not allowed in parameter names")
        for name in template_variables(template):
            if name not in allowed:
                raise ValueError(f"{label}.{key}: template variable '{name}' is not allowed here")
        # The rendered value must survive URL-encoding unchanged, so only
        # characters that are already safe may appear outside placeholders.
        for chunk in _PLACEHOLDER.split(template):
            if any(char not in QUERY_VARIABLE_CHARS for char in chunk):
                raise ValueError(
                    f"{label}.{key}: literal query values may only contain "
                    "letters, digits and - _ . ~"
                )
    return value
