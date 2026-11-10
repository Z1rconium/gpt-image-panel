# Implement: P2 架构演进

> 前置：P0/P1 的工作区改动尚未提交。建议在 P2 开工前先把 P0/P1 单独提交一次（`refactor: simplify asset pack checks, edits pipeline and collections dialog`），这样 P2 的 diff 干净、可单独回滚。
> 两个子项互不依赖；建议 **P2-2（后端）先、P2-1（前端）后**，各自独立提交。

## 公共约束

- 后端命令一律用 `.venv/bin/python`；前端 e2e 先手动起 Vite，用隔离 venv，CSS 预算紧（见 memory `frontend-e2e-environment`）。
- 测试用 `-q`，先跑聚焦用例再跑全量。
- 做完每个子项：`trellis-check` → 更新 spec → 提交。不提交 `.env`、sqlite、日志、生成图片。

---

## P2-2 Agent 资产引用结构化（后端）

### 阶段 A：固化现行为（不改实现）
- [x] 跑 `.venv/bin/python -m pytest backend/tests/test_agent_repository.py backend/tests/test_agent_branches_contract.py backend/tests/test_agent_contract.py -q`，记录基线全绿。
- [x] 确认 `test_agent_repository.py` 中 `test_asset_snapshot_and_regenerate`、`test_asset_invalid_and_expansion_limit_are_atomic`、`test_asset_excludes_whitespace_padded_unsupported_formats`、`test_asset_snapshot_tracks_explicit_gallery_overlap` 的断言覆盖：顺序、重叠 `explicit_refs`、编辑保留快照、重新生成、回滚。缺口先补测试（编辑后原图被删除 → 该成员被丢弃）。

### 阶段 B：迁移 + 读取路径
- [x] `backend/app/repositories/db/migrations.py`：新增 `_migration_agent_asset_refs`（两张表 + 两个索引），注册 `(43, "agent_asset_refs", ...)`。
- [x] 回填函数：遍历含 `asset_refs` 的用户消息，`INSERT OR IGNORE`，缺图片行则跳过并 `logger.warning`；`blocks_json` 不改写。
- [x] 新增 `_attach_asset_ref_blocks(conn, messages)`（放 `agent/_common.py` 或 `messages.py`），批量 `IN` 查询合成 `asset_refs` 块。
- [x] 迁移测试（`test_storage_migrations_contract.py`）：旧库含 `asset_refs` 块 → 迁移后两张表内容正确；重复执行迁移幂等；缺失图片行不致命；`_attach_asset_ref_blocks` 对回填数据合成的块与原 `blocks_json` 中的块逐字段一致。
- [x] 注意：本阶段 **不接入** 装载点。读取与写入必须在阶段 C 的同一提交里一起切换，否则"新读取 + 旧写入"期间新建 turn 没有关系表行，块会丢失。

### 阶段 C：写入路径重构
- [x] 接入所有装载点（与下面的写入切换同一提交）：`messages.list_messages`、`messages.get_message`、`branches.py` 路径装载（逐个核对 `_message_from_row` 的调用点）。
- [x] 在 `turns.py` 新增 `_AssetGroup` dataclass 与 `_resolve_asset_groups`，以 `image_id` 为键统一"新增 / 编辑保留快照 / 重新生成"三条路径，删除 `rsplit("-", 1)` 与索引换算。
- [x] 落库：插入 input 行后记录 `image_id → agent_message_images.id`，写 `agent_message_asset_refs` / `_ref_images`；用户消息 `blocks_json` 写 `'[]'`。
- [x] 删除 `create_turn` 中第 188 行的 `blocks=[...]` 一行式构造。
- [x] 回归：`pytest backend/tests/test_agent_repository.py backend/tests/test_agent_branches_contract.py backend/tests/test_agent_contract.py backend/tests/test_storage_migrations_contract.py -q`，已有资产断言 **不修改**即通过。

### 阶段 D：收尾
- [x] 全量 `.venv/bin/python -m pytest backend/tests -q` 与 `npm run test:contract`。
- [x] 确认 `rg 'rsplit\("-", 1\)' backend/app/repositories/agent` 无命中。
- [x] 更新 `.trellis/spec/backend/agent-assets.md`（新增 "Persistence" 小节：两张表、`explicit`、快照语义、API 合成块）与 `database-guidelines.md` 如有迁移约定补充。
- [x] `trellis-check` 通过后提交：`refactor(agent): persist asset pack references as structured rows`。

回滚点：阶段 B 之后可停（仅多了两张表与未接入的 helper，运行行为不变）；阶段 C 出问题可 `git revert` 该提交，但回滚后在 C 之后新建的 turn 其 `asset_refs` 只存在于关系表、旧代码读不到——因此阶段 C 合入前完成全量回归，并在 spec 记录这一单向性。

---

## P2-1 EditSourceState 扁平化（前端）

### 阶段 A：用测试固化现行为
- [x] `npm --prefix frontend run test:unit -- editSource editPrimaryImage markupInstall` 记录基线。
- [x] 补齐/确认以下用例（对旧模型先写断言，随后迁移到新夹具）：槽位上限 16（上传+主图+参考图合计）；仅主图变化才失效 mask/markup；添加参考图不丢掩码；SVG/GIF 参考图被过滤；`remove` 对三类来源均生效；`clear` 重置；提交 multipart 顺序（`preview.ts`）。

### 阶段 B：新模型与选择器（`stores/editSource.ts`）
- [x] 定义 `EditSource` 联合类型、新 `EditSourceState` / `initialEditSourceState`。
- [x] 实现 `commit(next)`（规范排序 + 掩码/标注失效 + 写 store），所有 mutator 改走 `commit`；删除 `dropStaleMask` 的逐处展开与重复的"清空 6 标量"。
- [x] 导出选择器：`primaryEditSource`、`primaryEditSourceId`、`galleryPrimarySource`、`uploadSources`、`galleryReferenceSources`、`editSourceCount`。
- [x] 保留 store 方法名（`addFiles/setGallerySource/clearGallerySource/addGalleryReferences/removeGalleryReference/clearGalleryReferences/remove/setMarkup/…`）。

### 阶段 C：迁移调用方（由 `npm run frontend:check` 的类型错误驱动）
- [x] `stores/preview.ts`：端点选择与 FormData 组装改用选择器；字段与顺序不变。
- [x] `features/workspace/editPrimaryImage.ts`：按 `primaryEditSource.kind` 分派。
- [x] `features/workspace/Workspace.svelte`：`editSources` 派生、`removeSource`、预览打开、`restoreMaskFromJob`、画廊主图清理三处。
- [x] `markup.ts`、`MarkupEditorDialog.svelte`、`AssetPackPickerDialog.svelte`：确认仅用导出函数，必要时微调。
- [x] 测试夹具迁移到新结构（`editSource.test.ts`、`editPrimaryImage.test.ts`、`markupInstall.test.ts`）；e2e 若直接读取旧字段则同步。

### 阶段 D：验证与收尾
- [x] `npm run frontend:check`、`npm --prefix frontend run test:unit`。
- [x] 手动起 Vite（5173）后跑 `npm run test:e2e -- markup-editor` 及资产包相关 spec；完成后停掉 Vite。
- [x] `rg 'selectedGalleryImageId|galleryReferences|galleryPreviewUrl' frontend/src` 仅剩 API/表单字段名（非 store 状态）。
- [x] 更新 `.trellis/spec/frontend/state-management.md`：编辑源扁平模型、规范顺序不变量、选择器约定。
- [x] `trellis-check` 后提交：`refactor(frontend): flatten edit source state into a unified sources array`。

回滚点：阶段 B 完成时旧字段已删，必须与阶段 C 同一提交；若 Workspace 迁移受阻，可 `git revert` 整个子项，不影响后端。

---

## 最终验收（两个子项都完成后）

- [x] `.venv/bin/python -m pytest backend/tests -q`
- [x] `npm run test:contract`
- [x] `npm run frontend:check` + `npm --prefix frontend run test:unit`
- [x] 手动 e2e：标注/掩码/资产包相关 spec
- [x] 更新 `prd.md` 中 P2 验收勾选；`trellis-update-spec`；记录 journal；`task.py finish`

---

## 验证记录（2026-11-10）

| 检查 | 结果 |
|---|---|
| `pytest backend/tests -q` | 1303 passed / 23 skipped / 1 failed |
| `test_*_contract.py` | 732 passed |
| P0/P1/P2 聚焦 8 个测试文件 | 148 passed |
| `frontend run test:unit` | 27 files / 449 tests passed |
| `frontend run check` | 0 errors, 0 warnings |
| `test:e2e -- markup-editor gallery-collections agent-asset-mentions` | 56 passed |

唯一失败项 `test_generated_media.py::test_svg_atomic_save_thumbnail_and_download` 为本地环境问题：CairoSVG 找不到 Homebrew 的 `libcairo`。设置 `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` 后该文件 16 项全过；CI 使用系统 cairo，不受影响。

提交：`b0890eb`（P0-1/P0-2/P1-1/P1-2）、`40a3a3f`（P2-2）、`82b7e09`（P2-1）。
