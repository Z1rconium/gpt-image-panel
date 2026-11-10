# Phase 1: 数据与资产 API (Data & Asset API)

## Goal

为可持久化参考资产库（Reference Asset Library）奠定数据基础与后端 API 能力：
1. 数据库支持集合资产别名（`asset_alias`）和成员在集合内的固定位置（`position`）。
2. 仓储层支持设置/清除别名、别名唯一性冲突检查、按统一规则读取成员、以及重新编排成员顺序。
3. API 层提供别名更新、成员重排与读取有序可用参考图（排除 SVG 与 GIF）端点。

## Requirements

1. **数据库迁移（Version 42）**：
   - 迁移脚本在 `backend/app/repositories/db/migrations.py` 增加版本 42 `gallery_collection_assets`。
   - `gallery_collections` 增加 `asset_alias TEXT` 可空列，创建 `idx_gallery_collections_asset_alias` 大小写不敏感（`COLLATE NOCASE`）的部分唯一索引（`WHERE asset_alias IS NOT NULL`）。
   - `gallery_collection_items` 增加 `position INTEGER` 可空列，创建 `idx_gallery_collection_items_order` 索引（`(collection_id, position)`）。
   - 保证迁移幂等性。

2. **别名规范与校验**：
   - 字符集限制为 Unicode 字母、数字、下划线、短横线 `[\p{L}\p{N}_-]`，长度 1–32。
   - 显式拒绝 `round-\d+.*` 以及 `第\d+轮图.*` 格式，防止与现有 Agent 图片引用语法冲突。
   - 大小写不敏感全局唯一。若别名冲突抛出 `GalleryCollectionAliasConflictError`（API 返回 409）。
   - 更新集合时支持将 `asset_alias` 置为 `None`（清空别名），普通集合保持 `asset_alias = None`。

3. **成员统一排序与过滤**：
   - 统一排序规则：`ORDER BY position IS NULL, position, added_at, image_id`。
   - 旧条目及新加入条目 `position` 为 `NULL`，自然排在最后；添加条目接口不改动。
   - 仓储层提供 `list_collection_reference_entries(collection_id)`，按上述统一顺序返回成员详情，包含图片格式与基本元数据。
   - 接口 `/api/gallery/collections/{id}/references`：
     - 返回 `{collection: GalleryCollection, images: list[GalleryEntry], total_count: int, skipped_unsupported: int}`。
     - 自动排除 `.svg` 和 `.gif` 格式条目，`total_count` 为该集合所有成员数，`skipped_unsupported` 为被排除的非光栅静态图数量。

4. **成员重排 API**：
   - 提供 `reorder_gallery_collection_items(collection_id, ids)`：
     - `ids` 必须精确等于当前集合的所有成员 ID 集合（不可多、不可少、无重复）。
     - 若 `ids` 与当前集合成员不完全匹配，接口返回 422。
     - 重排时将对应的 `position` 赋为 `0, 1, 2, ...`，更新 `gallery_collections.updated_at`。

5. **线程与异步安全**：
   - 所有数据库操作均通过 `run_db_operation` 调度执行，符合 `.trellis/spec/backend/db-execution.md`。

## Acceptance Criteria

- [x] 迁移 42 执行成功且幂等，数据库表结构与索引正确建立。
- [x] `PATCH /api/gallery/collections/{id}` 支持传入 `asset_alias`：
  - 合法别名成功设置并持久化。
  - 传 `null` 或空字符串（根据模型语义）能够清空别名恢复为普通集合。
  - 别名格式不符合（如包含空格、特殊字符、以 round- 开头等）返回 422。
  - 别名与已有集合冲突（大小写不敏感）返回 409。
- [x] `GET /api/gallery/collections/{id}/references`：
  - 返回有序参考列表，顺序符合 `position IS NULL, position, added_at, image_id`。
  - 正确排除 `.svg` 与 `.gif` 文件，`total_count` 和 `skipped_unsupported` 计数准确。
  - 集合不存在时返回 404。
- [x] `PUT /api/gallery/collections/{id}/items/order`：
  - 传入完整成员列表能够正确更新各成员的 `position`。
  - 传入部分、额外或包含重复 ID 时返回 422。
- [x] 编写契约测试覆盖迁移幂等性、别名增删改查冲突、参考图过滤及重排逻辑，测试全部通过。
