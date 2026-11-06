import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .common import (
    GalleryExportJobStatusValue,
    GalleryImportJobStatusValue,
    GallerySyncJobStatusValue,
    GalleryThumbnailStatus,
    ShortId,
    StrictRequestModel,
)

class GalleryEntry(BaseModel):
    id: str
    prompt: str
    size: str
    filename: str
    image_url: Optional[str] = None
    thumbnail_filename: Optional[str] = None
    thumbnail_url: Optional[str] = None
    thumbnail_status: GalleryThumbnailStatus = "missing"
    created_at: str
    completed_at: Optional[str] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    sent_prompt: Optional[str] = None
    revised_prompt: Optional[str] = None
    reported_size: Optional[str] = None
    reported_quality: Optional[str] = None
    upstream_duration_ms: Optional[int] = None
    chroma_status: Optional[str] = None
    diagnostics: list[str] = Field(default_factory=list)
    model: Optional[str] = None
    quality: Optional[str] = None
    output_format: Optional[str] = None
    output_compression: Optional[int] = None
    complexity: Optional[str] = None
    frame_count: Optional[int] = None
    frame_duration_ms: Optional[int] = None
    markup_applied: Optional[bool] = None
    background: Optional[str] = None
    response_format: Optional[str] = None
    safety_identifier: Optional[str] = None
    n: Optional[int] = None
    api_path: Optional[str] = None
    api_preset_name: Optional[str] = None
    duration: Optional[str] = None
    favorite: bool = False
    bytes: Optional[int] = None
    mask_coverage: Optional[float] = None
    paste_back: Optional[str] = None
    paste_back_scale: Optional[float] = None


class GalleryFavoriteRequest(StrictRequestModel):
    favorite: bool


class GalleryThumbnailStatusRequest(StrictRequestModel):
    ids: list[ShortId] = Field(min_length=1, max_length=100)

    @field_validator("ids")
    @classmethod
    def validate_ids(cls, value: list[str]) -> list[str]:
        ids = [image_id.strip() for image_id in value if image_id.strip()]
        if len(set(ids)) != len(ids):
            raise ValueError("ids must not contain duplicates")
        return ids


class GalleryThumbnailState(BaseModel):
    id: str
    thumbnail_filename: Optional[str] = None
    thumbnail_url: Optional[str] = None
    thumbnail_status: GalleryThumbnailStatus = "missing"


class GallerySelectionFilterRequest(StrictRequestModel):
    prompt: Optional[str] = Field(default="", max_length=4000)
    model: Optional[str] = Field(default="", max_length=200)
    preset: Optional[str] = Field(default="", max_length=200)
    size: Optional[str] = Field(default="", max_length=64)
    date_from: Optional[str] = Field(default="", max_length=64)
    date_to: Optional[str] = Field(default="", max_length=64)
    favorite: Optional[bool] = None
    mask_only: Optional[bool] = None
    collection_id: Optional[str] = Field(default=None, max_length=128)


class GallerySearchRequest(GallerySelectionFilterRequest):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=9, ge=1, le=100)
    include_total_bytes: bool = False
    include_counts: bool = True
    include_filter_options: bool = True
    cursor: Optional[str] = Field(default=None, max_length=512)
    direction: str = Field(default="next", max_length=8)


class GallerySelectionTokenRequest(StrictRequestModel):
    filters: GallerySelectionFilterRequest = Field(default_factory=GallerySelectionFilterRequest)


class GallerySelectionTokenResponse(BaseModel):
    selection_token: str
    count: int
    expires_at: str


class GalleryBatchRequest(StrictRequestModel):
    ids: Optional[list[ShortId]] = Field(default=None, max_length=1000)
    selection_token: Optional[ShortId] = None

    @field_validator("ids")
    @classmethod
    def validate_ids(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value is None:
            return None
        ids = [image_id.strip() for image_id in value if image_id.strip()]
        if not ids:
            raise ValueError("ids must include at least one gallery entry id")
        if len(set(ids)) != len(ids):
            raise ValueError("ids must not contain duplicates")
        return ids

    @field_validator("selection_token")
    @classmethod
    def validate_selection_token(cls, value: Optional[str]) -> Optional[str]:
        token = str(value or "").strip()
        return token or None

    @model_validator(mode="after")
    def validate_batch_target(self):
        if bool(self.ids) == bool(self.selection_token):
            raise ValueError("Provide exactly one of ids or selection_token")
        return self


class GalleryBatchFavoriteRequest(GalleryBatchRequest):
    favorite: bool


class GalleryCollection(BaseModel):
    id: str
    name: str
    asset_alias: Optional[str] = None
    position: int = 0
    is_default: bool = False
    image_count: int = 0
    cover_image_id: Optional[str] = None
    cover_filename: Optional[str] = None
    created_at: str
    updated_at: str


def _normalize_collection_name(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    name = " ".join(value.split())
    if not name:
        raise ValueError("Collection name must not be empty")
    if len(name) > 60:
        raise ValueError("Collection name must be at most 60 characters")
    return name


_ALIAS_DISALLOWED_PREFIX_RE = re.compile(r"^(?:round-\d+|第?\d+轮图)", re.IGNORECASE)
_ALIAS_CHAR_RE = re.compile(r"^[\w-]+$", re.UNICODE)


def _normalize_asset_alias(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    alias = value.strip()
    if not alias:
        return None
    if len(alias) > 32:
        raise ValueError("Asset alias must be at most 32 characters")
    if not _ALIAS_CHAR_RE.match(alias):
        raise ValueError("Asset alias may only contain letters, numbers, underscores, and hyphens")
    if _ALIAS_DISALLOWED_PREFIX_RE.match(alias):
        raise ValueError("Asset alias must not conflict with round references")
    return alias


class GalleryCollectionCreateRequest(StrictRequestModel):
    name: str = Field(..., max_length=200)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _normalize_collection_name(value)  # type: ignore[return-value]


class GalleryCollectionUpdateRequest(StrictRequestModel):
    name: Optional[str] = Field(default=None, max_length=200)
    is_default: Optional[bool] = None
    asset_alias: Optional[str] = Field(default=None, max_length=32)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_collection_name(value)

    @field_validator("asset_alias")
    @classmethod
    def validate_asset_alias(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_asset_alias(value)


class GalleryCollectionOrderRequest(StrictRequestModel):
    ids: list[ShortId] = Field(default_factory=list, max_length=1000)

    @field_validator("ids")
    @classmethod
    def validate_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("ids must not contain duplicates")
        return value


class GalleryCollectionItemsOrderRequest(StrictRequestModel):
    ids: list[ShortId] = Field(default_factory=list, max_length=1000)

    @field_validator("ids")
    @classmethod
    def validate_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("ids must not contain duplicates")
        return value


class GalleryCollectionReferencesResponse(BaseModel):
    collection: GalleryCollection
    images: list[GalleryEntry]
    total_count: int
    skipped_unsupported: int


class GalleryCollectionItemsResponse(BaseModel):
    collection: GalleryCollection
    changed_count: int


class GalleryImageCollectionsResponse(BaseModel):
    image_id: str
    collection_ids: list[str]


class GalleryExportRequest(StrictRequestModel):
    ids: Optional[list[ShortId]] = Field(default=None, max_length=1000)
    selection_token: Optional[ShortId] = None
    collection_id: Optional[ShortId] = None

    @field_validator("ids")
    @classmethod
    def validate_ids(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value is None:
            return None
        ids = [image_id.strip() for image_id in value if image_id.strip()]
        if not ids:
            raise ValueError("ids must include at least one gallery entry id")
        if len(set(ids)) != len(ids):
            raise ValueError("ids must not contain duplicates")
        return ids

    @field_validator("selection_token")
    @classmethod
    def validate_selection_token(cls, value: Optional[str]) -> Optional[str]:
        token = str(value or "").strip()
        return token or None

    @model_validator(mode="after")
    def validate_export_target(self):
        if sum(bool(target) for target in (self.ids, self.selection_token, self.collection_id)) > 1:
            raise ValueError("Provide only one of ids, selection_token or collection_id")
        return self


class GallerySyncRequest(StrictRequestModel):
    full_reconcile: bool = False
    dry_run: bool = False


class GalleryExportJobStatus(BaseModel):
    job_id: str
    status: GalleryExportJobStatusValue
    stage: Optional[str] = None
    message: Optional[str] = None
    progress: int = 0
    filename: Optional[str] = None
    download_url: Optional[str] = None
    requested_count: int = 0
    processed_count: int = 0
    exported_count: int = 0
    missing_count: int = 0
    bytes_total: int = 0
    bytes_written: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    error: Optional[str] = None


class GallerySyncJobStatus(BaseModel):
    job_id: str
    status: GallerySyncJobStatusValue
    stage: Optional[str] = None
    message: Optional[str] = None
    progress: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    error: Optional[str] = None
    total_count: int = 0
    compared_count: int = 0
    uploaded_count: int = 0
    pending_upload_count: int = 0
    skipped_existing_count: int = 0
    missing_local_count: int = 0
    failed_count: int = 0
    bytes_total: int = 0
    bytes_uploaded: int = 0
    dry_run: bool = False
    checkpoint_filename: Optional[str] = None


class GalleryImportJobStatus(BaseModel):
    job_id: str
    status: GalleryImportJobStatusValue
    stage: Optional[str] = None
    message: Optional[str] = None
    progress: int = 0
    requested_count: int = 0
    processed_count: int = 0
    imported_count: int = 0
    skipped_count: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    error: Optional[str] = None


class GalleryBatchResponse(BaseModel):
    status: str
    count: int
    file_count: int = 0
    requested_count: int = 0
    updated_count: int = 0
    missing_count: int = 0
    missing_ids: list[str] = Field(default_factory=list)


class GalleryFilterOptions(BaseModel):
    models: list[str] = Field(default_factory=list)
    presets: list[str] = Field(default_factory=list)
    sizes: list[str] = Field(default_factory=list)


class GalleryResponse(BaseModel):
    total: int
    total_bytes: int = 0
    page: int
    page_size: int
    total_pages: int
    has_prev: bool
    has_next: bool
    next_cursor: Optional[str] = None
    prev_cursor: Optional[str] = None
    images: list[GalleryEntry]
    filter_options: GalleryFilterOptions = Field(default_factory=GalleryFilterOptions)
