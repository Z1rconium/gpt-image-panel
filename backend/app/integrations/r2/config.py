import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from ...core import secrets
from ...core.validators import normalize_r2_endpoint_url, validate_r2_endpoint_url


HealthStatus = str
ProgressCallback = Callable[[dict[str, Any]], None]
ClientFactory = Callable[["R2EffectiveSettings"], Any]
SyncStateRecorder = Callable[[Iterable[dict[str, Any]]], None]

R2_REMOTE_LISTING_FALLBACK_THRESHOLD = 100_000
R2_REMOTE_HEAD_LOOKUP_THRESHOLD = 1_000
R2_SYNC_BATCH_SIZE = 500


HEALTH_STATUS_RANK = {"ok": 0, "warning": 1, "error": 2}
logger = logging.getLogger(__name__)


class R2ConfigurationError(ValueError):
    pass


class R2SyncError(RuntimeError):
    def __init__(self, message: str, result: "R2SyncResult"):
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class R2EffectiveSettings:
    enabled: bool
    endpoint_url: str
    bucket_name: str
    region: str
    key_prefix: str
    access_key_id: str
    secret_access_key: str


@dataclass
class R2SyncResult:
    total_count: int = 0
    compared_count: int = 0
    uploaded_count: int = 0
    pending_upload_count: int = 0
    skipped_existing_count: int = 0
    missing_local_count: int = 0
    failed_count: int = 0
    bytes_total: int = 0
    bytes_uploaded: int = 0

    def to_updates(self) -> dict[str, int]:
        return asdict(self)


@dataclass
class RemoteKeyLookup:
    keys: set[str]
    etags: dict[str, str] = field(default_factory=dict)
    use_head_fallback: bool = False


@dataclass(frozen=True)
class LocalSyncCandidate:
    entry: Any
    filename: str
    path: Path
    byte_size: int
    key: str


@dataclass(frozen=True)
class CandidateSyncOutcome:
    candidate: LocalSyncCandidate
    uploaded: bool = False
    skipped_existing: bool = False
    error: str | None = None


def _health_status(checks: list[dict[str, str]]) -> HealthStatus:
    if not checks:
        return "error"
    return max(
        (check["status"] for check in checks),
        key=lambda status: HEALTH_STATUS_RANK.get(status, 2),
    )


def _check(
    checks: list[dict[str, str]],
    name: str,
    status: HealthStatus,
    message: str,
) -> None:
    checks.append({"name": name, "status": status, "message": message})


def normalize_key_prefix(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parts = [part for part in raw.strip("/").split("/") if part]
    return f"{'/'.join(parts)}/" if parts else ""


def _resolve_secret(
    value: Any,
    field_name: str,
    *,
    purpose: secrets.SecretPurpose,
    endpoint_url: str,
) -> str:
    return secrets.resolve_secret_reference_or_raise(
        value,
        purpose=purpose,
        target_url=endpoint_url,
        field_name=field_name,
        error_type=R2ConfigurationError,
    )


def resolve_r2_backup_settings(
    settings: dict[str, Any] | None,
    *,
    require_enabled: bool = False,
) -> R2EffectiveSettings:
    raw = settings or {}
    enabled = bool(raw.get("enabled", False))
    if require_enabled and not enabled:
        raise R2ConfigurationError("R2 backup is disabled.")

    endpoint_url = normalize_r2_endpoint_url(raw.get("endpoint_url"))
    if not endpoint_url:
        raise R2ConfigurationError("R2 endpoint URL is not configured.")
    validate_r2_endpoint_url(endpoint_url)

    bucket_name = str(raw.get("bucket_name") or "").strip()
    if not bucket_name:
        raise R2ConfigurationError("R2 bucket name is not configured.")

    region = str(raw.get("region") or "auto").strip() or "auto"
    key_prefix = normalize_key_prefix(raw.get("key_prefix"))
    access_key_id = _resolve_secret(
        raw.get("access_key_id"),
        "R2 access key ID",
        purpose="r2_access_key_id",
        endpoint_url=endpoint_url,
    )
    secret_access_key = _resolve_secret(
        raw.get("secret_access_key"),
        "R2 secret access key",
        purpose="r2_secret_access_key",
        endpoint_url=endpoint_url,
    )
    if not access_key_id:
        raise R2ConfigurationError("R2 access key ID is not configured.")
    if not secret_access_key:
        raise R2ConfigurationError("R2 secret access key is not configured.")

    return R2EffectiveSettings(
        enabled=enabled,
        endpoint_url=endpoint_url,
        bucket_name=bucket_name,
        region=region,
        key_prefix=key_prefix,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )



__all__ = [name for name in globals() if not name.startswith("__")]
