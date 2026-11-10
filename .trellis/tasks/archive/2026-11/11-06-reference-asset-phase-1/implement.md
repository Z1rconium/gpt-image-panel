# Implementation Plan - Phase 1: 数据与资产 API

## Execution Steps

### 1. 数据库迁移 (Migration 42)
- [x] 在 `backend/app/repositories/db/migrations.py` 新增 `_migration_gallery_collection_assets(conn: sqlite3.Connection)`。
- [x] 检查并添加 `gallery_collections.asset_alias` 及部分唯一索引 `idx_gallery_collections_asset_alias`。
- [x] 检查并添加 `gallery_collection_items.position` 及索引 `idx_gallery_collection_items_order`。
- [x] 在 `SCHEMA_MIGRATIONS` 元组中注册 `(42, "gallery_collection_assets", _migration_gallery_collection_assets)`。

### 2. Pydantic 模型与别名校验
- [x] 在 `backend/app/schemas/gallery.py` 新增别名规范化与校验函数 `_normalize_asset_alias(value: Optional[str]) -> Optional[str]`：
  - 长度限制 1–32 字符。
  - 字符集 `[\w-]`（Unicode 字母、数字、下划线、短横线）。
  - 正则前缀拦截：防冲突 `^(?:round-\d+|第?\d+轮图)`。
- [x] 更新 `GalleryCollection` 模型，新增 `asset_alias: Optional[str] = None`。
- [x] 更新 `GalleryCollectionUpdateRequest` 模型，新增 `asset_alias: Optional[str] = None` 并挂载校验器。
- [x] 新增 `GalleryCollectionReferencesResponse`：
  - `collection: GalleryCollection`
  - `images: list[GalleryEntry]`
  - `total_count: int`
  - `skipped_unsupported: int`
- [x] 新增 `GalleryCollectionItemsOrderRequest`：
  - `ids: list[ShortId] = Field(..., max_length=1000)`，校验重复项。

### 3. 仓储层实现 (`repositories/gallery/collections.py`)
- [x] 定义异常类 `GalleryCollectionAliasConflictError(ValueError)`。
- [x] 更新 `_COLLECTION_SELECT_SQL` 与 `_collection_from_row` 支持 `asset_alias`。
- [x] 新增 `_alias_taken_on_conn(conn, alias, *, exclude_id)` 检查别名占用。
- [x] 更新 `update_gallery_collection`：支持 `asset_alias` 参数（使用 `_UNSET` 哨兵区分未传与清空）。
- [x] 实现 `list_collection_reference_entries(collection_id)`：
  - 统一排序：`ORDER BY items.position IS NULL, items.position, items.added_at, items.image_id`。
  - 获取完整画廊条目数据，统计 `total_count`。
  - 过滤排除 `.svg` 和 `.gif`，统计 `skipped_unsupported`。
- [x] 实现 `reorder_gallery_collection_items(collection_id, ids)`：
  - 严格校验 `ids` 与当前集合成员完全一致。
  - 批量更新每张图的 `position`。
  - 更新集合的 `updated_at`。

### 4. API 路由实现 (`api/routers/gallery_collections.py`)
- [x] 更新 `PATCH /api/gallery/collections/{collection_id}`：
  - 捕获 `GalleryCollectionAliasConflictError` 返回 409。
  - 结合 `req.model_fields_set` 传递 `asset_alias`（未设置则不改动，明确传 null/"" 则清空）。
- [x] 新增 `GET /api/gallery/collections/{collection_id}/references`：
  - 调用 `list_collection_reference_entries`。
  - 集合不存在返回 404，成功返回 `GalleryCollectionReferencesResponse`。
- [x] 新增 `PUT /api/gallery/collections/{collection_id}/items/order`：
  - 接收 `GalleryCollectionItemsOrderRequest`。
  - 调用 `reorder_gallery_collection_items`。
  - 集合不存在返回 404，重排列表不匹配返回 422，成功返回最新 `GalleryCollection`。

### 5. 契约测试与验证
- [x] 在 `backend/tests/test_storage_migrations_contract.py` 增加迁移 42 幂等性与字段/索引验证测试。
- [x] 在 `backend/tests/test_gallery_collections_contract.py` 补充：
  - 别名合法设置、清空、格式错误拦截（422）、大小写冲突拦截（409）。
  - 成员重排：成功改序、非法列表返回 422。
  - 参考列表读取：统一排序检查、SVG/GIF 排除检查与计数验证。
- [x] 运行后端测试验证。
