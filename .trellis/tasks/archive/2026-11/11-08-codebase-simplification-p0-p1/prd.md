# Codebase simplification: P0 and P1 refactoring

## Goal

针对近期参考资产库和编辑管线功能引入的复杂性，稳步实施 P0 与 P1 级别的简化与重构，消除不必要的性能开销、重复管线代码、格式过滤分散以及前端组件膨胀问题，且严格保持对外契约和行为不变。

## Requirements

1. **P0-1：轻量化资产包存在性校验（`has_asset_collections`）**：
   - 在 `backend/app/repositories/gallery/collections.py` 中增加专用的轻量查询 `has_asset_collections() -> bool`，直接通过 `SELECT 1 FROM gallery_collections WHERE asset_alias IS NOT NULL LIMIT 1` 进行判断。
   - 替换 `backend/app/api/routers/gallery_tasks.py` 中 `_has_asset_collections()` 遍历调用 `export_gallery_collections()` 的重型实现，消除直接导出全量元数据的性能与内存开销。

2. **P0-2：统一参考图格式过滤逻辑**：
   - 统一定义参考图支持的格式规则（排除 SVG 和 GIF）。
   - 在 `backend/app/repositories/gallery/collections.py` 中明确提供/复用参考图成员读取能力。
   - 在 `backend/app/repositories/agent/turns.py` 中复用集合仓储层能力或统一判断，移除手写的原生 SQL `lower(trim(...))` 重复查询。

3. **P1-1：收拢并精简 `edits.py` 路由管线**：
   - 在 `backend/app/api/routers/edits.py` 中，将 `/api/edits` 与 `/api/edits/from-gallery/{image_id}` 的重复处理流程抽取为统一编排 helper（如 `_stage_and_queue_edit`）。
   - 保持 HTTP 路由定义、状态码（202）、异常抛出（400/422）和请求体格式完全一致，同时统一多源图片的暂存、掩码校验、数量校验和异常清理流程。

4. **P1-2：拆分 `CollectionsDialog.svelte` 成员排序子视图**：
   - 将 `CollectionsDialog.svelte` 中的成员重排子视图（拉取、缩略图展示、拖拽重排、上下移按钮、不支持格式跳过提示）抽取为独立的子组件 `CollectionMemberOrder.svelte`。
   - `CollectionsDialog.svelte` 聚焦集合的 CRUD、别名设置与集合排序，降低单文件状态复杂度。

## Acceptance Criteria

- [x] 后端：`has_asset_collections` 正确返回是否存在资产包，`gallery_tasks.py` 不再调用全量 `export_gallery_collections()` 做存在性检查。
- [x] 后端：SVG/GIF 过滤逻辑集中且行为一致，`turns.py` 与集合管理对不支持格式的过滤测试全绿。
- [x] 后端：`edits.py` 成功抽取共享暂存与入队 helper，现有编辑相关 Contract 测试（单图、多图、画廊主图、掩码、markup、异常清理）100% 通过。
- [x] 前端：`CollectionMemberOrder.svelte` 成功独立拆分，集合成员排序功能与交互保持一致，前端 unit 测试和 Svelte 检查全绿。
- [x] 回归验证：现有后端测试集和前端测试集通过，无行为破坏。

---

## P2：架构演进（规划中，待评审后实现）

> 与 P0/P1 不同，P2 触及前端核心状态模型与 SQLite 持久化结构，属于"架构演进"而非机械简化。设计细节见 `design.md`，执行清单见 `implement.md`。对外 HTTP/API 契约保持不变。

### P2-1：`EditSourceState` 扁平化为统一数组模型（前端）

现状：编辑源被拆成三套并行字段 —— `files[]`（上传）、`selectedGalleryImageId` + 6 个 `gallery*` 标量（单个画廊主图）、`galleryReferences[]`（画廊参考图）。`editSourceCount` / `primaryEditSourceId` / `remove` / `clearGallerySource` / `Workspace.svelte` 的派生列表都要对三套字段各写一遍分支，`remove()` 与 `clearGallerySource()` 里还存在重复的"清空 6 个标量"代码（且有缩进错位）。

目标：
1. `EditSourceState` 改为 `{ sources: EditSource[]; markup; mask }`，`EditSource` 为 `kind` 区分的联合类型（`upload` / `gallery_primary` / `gallery_reference`），共享 `id/label/previewUrl/previewLabel/width/height`。
2. 主图 = `sources[0]`；store 内单一 `commit()` 负责规范化顺序（画廊主图 → 上传 → 画廊参考图）、掩码/标注失效判断，消除各 mutator 内重复的 `dropStaleMask` 展开。
3. 提供选择器（`primarySource`、`uploadSources`、`galleryPrimary`、`galleryReferences`），调用方不再直接读取分散字段。
4. 保持行为：槽位上限 16、主图变化才使掩码失效、SVG/GIF 参考图过滤、`/api/edits` 与 `/api/edits/from-gallery/{id}` 的端点选择与 multipart 字段顺序不变。

### P2-2：Agent 资产引用结构化持久化（后端）

现状：用户消息的资产包引用以 `asset_refs` 块存放在 `agent_messages.blocks_json` 中，成员以 `round-N-input-K` 标签字符串保存；`create_turn` 里通过 `label.rsplit("-", 1)[1]` 反解序号，并在"新增 / 编辑分支 / 重新生成"三条路径中手工维护 `(alias, indices, collection_id, explicit_indices)` 四元组与索引换算，是脆弱且难测的隐式耦合。

目标：
1. 新增关系表持久化资产包引用（分组 + 成员，成员直接外键到 `agent_message_images` 的 input 行，含 `explicit` 标记），schema 迁移 `43` 并回填历史 `blocks_json` 中的 `asset_refs`。
2. `create_turn` 以 `image_id` 为键处理资产分组，抽出独立的 `_resolve_asset_groups`，移除标签反解与序号换算；编辑保留快照、重新生成复用快照、移除提及只移除 asset-only 图片等既有语义（见 `spec/backend/agent-assets.md`）不变。
3. API 响应中的 `asset_refs` 块形状（`id/type/alias/collection_id/refs/explicit_refs`）保持不变，由关系表合成；`agent_context.py` 与前端无需改动。

### P2 验收标准

- [x] 前端：`EditSourceState` 仅含 `sources/markup/mask`；`editSource.ts` 中不再存在 `selectedGalleryImageId` / `gallery*` 标量 / `galleryReferences` 字段；`frontend:check`、`test:unit`、涉及标注/掩码/资产包的 e2e 全绿。
- [x] 前端：`/api/edits` 提交的 multipart 字段与顺序（`[gallery_primary] → uploads → reference_image_id…`）与重构前逐字段一致（unit 覆盖）。
- [x] 后端：迁移 `43` 幂等，历史 `asset_refs` 回填后 API 返回的块与回填前一致；新增迁移与回填测试。
- [x] 后端：`test_agent_repository.py` 现有资产用例（快照、重新生成、原子回滚、重叠显式附件、SVG/GIF 过滤）不改断言即通过。
- [x] 后端：`create_turn` 中不再出现 `rsplit("-", 1)` 标签反解。
- [x] 全量 `pytest backend/tests -q` 与 `npm run test:contract` 通过；`.trellis/spec` 中 `agent-assets.md` 与前端 state-management 规范同步更新。
