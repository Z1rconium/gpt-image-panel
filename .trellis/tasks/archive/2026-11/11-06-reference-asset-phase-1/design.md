# Technical Design - Phase 1: 数据与资产 API

## 1. 范围与边界

本设计覆盖可持久化参考资产库的阶段 1：
- 数据库结构变更（迁移 42）
- 集合别名（`asset_alias`）校验、持久化与冲突处理
- 集合成员顺序列（`position`）与统一排序
- 成员有序参考图读取及非光栅图片（SVG / GIF）过滤接口
- 集合成员重排接口

本阶段明确不包含：
- 编辑链路（`edits.py`、`stores/editSource.ts`）接收多参考图（阶段 2）
- 选图器弹窗与集合管理前端 UI（阶段 3）
- Agent 输入框解析与对话上下文展开（阶段 4）
- 归档导入导出支持（阶段 5）

## 2. 数据结构与迁移 (Migration 42)

在 `backend/app/repositories/db/migrations.py` 注册版本 42 `_migration_gallery_collection_assets`：

```python
def _migration_gallery_collection_assets(conn: sqlite3.Connection):
    columns = _table_columns(conn, "gallery_collections")
    if "asset_alias" not in columns:
        conn.execute("ALTER TABLE gallery_collections ADD COLUMN asset_alias TEXT")
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_gallery_collections_asset_alias
                ON gallery_collections(asset_alias COLLATE NOCASE)
                WHERE asset_alias IS NOT NULL
            """
        )

    item_columns = _table_columns(conn, "gallery_collection_items")
    if "position" not in item_columns:
        conn.execute("ALTER TABLE gallery_collection_items ADD COLUMN position INTEGER")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_collection_items_order
                ON gallery_collection_items(collection_id, position)
            """
        )
```

统一排序规范：
`ORDER BY items.position IS NULL, items.position, items.added_at, items.image_id`

## 3. 别名规范与校验

### 别名规则
- 允许类型：`str`（1–32 字符）或 `None`（普通集合）。
- 字符集：Unicode 字母、数字、下划线、连字符（`[\p{L}\p{N}_-]`）。
- 禁止空格与任何标点符号。
- 禁止形式：不能以 `round-` 加数字开头（`^round-\d+`），不能以 `第` 加数字加 `轮图` 开头（`^第?\d+轮图`），以防与 Agent 图片 mention 语法冲突。
- 唯一性：大小写不敏感唯一（由 `idx_gallery_collections_asset_alias` 及仓储层前置检查保障）。

### 异常定义
```python
class GalleryCollectionAliasConflictError(ValueError):
    """Raised when an asset alias is already used (case-insensitive)."""
```

## 4. 仓储层契约 (`repositories/gallery/collections.py`)

1. **`_COLLECTION_SELECT_SQL` 与 `_collection_from_row`**：
   - 增加 `c.asset_alias` 字段查询与映射。
2. **`update_gallery_collection`**：
   - 入参增加 `asset_alias: str | None | object = _UNSET`。
   - 使用哨兵对象 `_UNSET` 区分“调用方未提供此字段”与“调用方传入 `None` 意图清空别名”。
   - 若传入新别名，检查 `_alias_taken_on_conn(conn, alias, exclude_id)`，冲突抛出 `GalleryCollectionAliasConflictError`。
3. **`list_collection_reference_entries(collection_id)`**：
   - 检查集合是否存在，不存在返回 `None`。
   - 查询该集合下的成员及对应画廊详情，统一排序。
   - 过滤排除非光栅图片（`output_format in {'svg', 'gif'}` 或 `filename.lower().endswith(('.svg', '.gif'))`）。
   - 返回包含 `collection`, `images` (有效条目), `total_count`, `skipped_unsupported` 的元组或数据对象。
4. **`reorder_gallery_collection_items(collection_id, ids: Sequence[str])`**：
   - 查询集合现有全部 `image_id` 集合。
   - 若 `len(ids) != len(existing)` 或 `set(ids) != existing`，返回 `None`（表示非法重排列表）。
   - 事务内依次更新每张图片的 `position = index`，并更新 `gallery_collections.updated_at`。
   - 返回更新后的集合对象。

## 5. API 路由契约 (`api/routers/gallery_collections.py`)

### 5.1 `PATCH /api/gallery/collections/{collection_id}`
- 支持更新 `asset_alias`。
- 请求体未包含 `asset_alias` 则不改动；请求体包含 `"asset_alias": null` 或 `""` 则清空别名；包含非空字符串则规范化并校验。
- 别名校验失败返回 422；别名冲突返回 409。

### 5.2 `GET /api/gallery/collections/{collection_id}/references`
- 响应模型 `GalleryCollectionReferencesResponse`：
  ```python
  class GalleryCollectionReferencesResponse(BaseModel):
      collection: GalleryCollection
      images: list[GalleryEntry]
      total_count: int
      skipped_unsupported: int
  ```
- 集合不存在返回 404。
- 排除 SVG/GIF，保持统一顺序。

### 5.3 `PUT /api/gallery/collections/{collection_id}/items/order`
- 请求模型 `GalleryCollectionItemsOrderRequest`：
  ```python
  class GalleryCollectionItemsOrderRequest(StrictRequestModel):
      ids: list[ShortId] = Field(..., max_length=1000)
  ```
- 集合不存在返回 404；`ids` 集合不匹配返回 422；成功返回更新后的 `GalleryCollection`。

## 6. 线程模型与异常处理

- 所有对仓储层的调用必须通过 `await run_db_operation(...)` 调度到 DB worker 线程池。
- 业务异常转换为标准 FastAPI HTTPException：
  - `GalleryCollectionNameConflictError` -> 409
  - `GalleryCollectionAliasConflictError` -> 409
  - 校验失败（非空/长度/字符集）-> 422
