# Design: P2 架构演进

> 范围：仅 P2（见 `prd.md` 的 "P2" 小节）。P0/P1 已实现且保持不变。两个子项互不依赖，可分别实现、分别提交。

## 0. 总原则

- 对外契约不变：HTTP 路由、请求/响应字段、multipart 字段顺序、Agent 消息 `asset_refs` 块形状都不动。
- 先立"新模型 + 选择器"，再迁移调用方，最后删除旧字段；每步都应保持 `frontend:check` / pytest 可运行。
- 迁移只前进（`migrations.py` forward-only），旧数据在迁移内回填；不要求回滚兼容旧代码读取新表。

---

## P2-1 `EditSourceState` 扁平化

### 1.1 现状痛点（`frontend/src/lib/stores/editSource.ts`）

| 问题 | 位置 |
|---|---|
| 三套并行字段，每个入口都要写三路分支 | `editSourceCount`、`primaryEditSourceId`、`remove`、`Workspace.svelte` 的 `editSources` 派生、`removeSource`、`Workspace.svelte:1150-1155` |
| "清空画廊主图 6 个标量"逐字重复 | `clearGallerySource` 与 `remove`（后者缩进已错位） |
| 每个 mutator 手写 `update(source => dropStaleMask({...source, X}))` | 8 处 |
| `galleryReferences?.` 可选链仅为兼容测试里缺省字段 | 多处 |

### 1.2 新模型

```ts
type EditSourceBase = {
  id: string;
  label: string;
  previewUrl: string;
  previewLabel: string;
  // 0 = 未知（探测未完成 / 旧画廊行），契约同现状
  width: number;
  height: number;
};

export type EditUploadSource = EditSourceBase & { kind: 'upload'; file: File };

export type EditGallerySource = EditSourceBase & {
  kind: 'gallery_primary' | 'gallery_reference';
  imageId: string;
  filename?: string;
};

export type EditSource = EditUploadSource | EditGallerySource;

export type EditSourceState = {
  sources: EditSource[];        // 规范顺序，见 1.3
  markup?: EditMarkup | null;
  mask: EditMask | null;
};
```

要点：
- `gallery_primary` 的 `id === imageId`（沿用现状：`mask.sourceId` / `markup.sourceId` / `Workspace` 里按画廊 `image.id` 比较都依赖这一点）；`gallery_reference` 的 `id` 仍是 `gallery-ref-…` 唯一前缀。
- `kind` 保留 3 种而不是 `primary: boolean`：端点选择（`/api/edits/from-gallery/{id}` vs `/api/edits`）取决于是否存在 `gallery_primary`，不取决于下标，这样"仅有参考图、无上传、无主图"时仍走 `/api/edits` 与现状一致。

### 1.3 不变量与规范化

单一内部函数 `commit(next: EditSource[])`：

1. 稳定排序为 `[gallery_primary(≤1), ...upload, ...gallery_reference]`。该顺序恰好等于现有的主图判定链 `selectedGallery || files[0] || refs[0]`，所以 **主图 = `sources[0]`**，`primaryEditSourceId` 退化为 `sources[0]?.id ?? ''`。
2. 对 `markup` / `mask` 做"主图变化才失效"检查（即现 `dropStaleMask`），并触发 `editMarkupDiscards` / `editMaskDiscards`。
3. 写入 store。

所有 mutator（`addFiles`、`setGallerySource`、`clearGallerySource`、`addGalleryReferences`、`removeGalleryReference`、`clearGalleryReferences`、`remove`、`updateUploadSize`）只负责计算新的 `sources` 数组并交给 `commit`。`remove(id)` 变成一次 `filter` + URL revoke，三路分支消失。

> `updateUploadSize` 只改 width/height，不改顺序也不改主图，走 `commit` 也安全；若测试显示额外失效检查有副作用，则单独保留纯 `update`。

### 1.4 选择器（供调用方使用，替代直接读字段）

```ts
primaryEditSource(state): EditSource | null           // sources[0]
primaryEditSourceId(state): string                    // 保留导出名，实现变简单
galleryPrimarySource(state): EditGallerySource | null
uploadSources(state): EditUploadSource[]
galleryReferenceSources(state): EditGallerySource[]
editSourceCount(state): number                        // sources.length
```

### 1.5 调用方迁移清单

| 文件 | 变更 |
|---|---|
| `stores/preview.ts` (≈L282-328) | `selectedGalleryImageId` → `galleryPrimarySource(...)?.imageId`；`files.forEach` → `uploadSources`；`galleryReferences` → `galleryReferenceSources`；wire 顺序保持 `[primary?] → uploads → reference_image_id…` |
| `features/workspace/editPrimaryImage.ts` | `fetchPrimaryImageBlob/readPrimaryDimensions/loadPrimaryImage` 改按 `primaryEditSource` 的 `kind` 分派（upload → `file`，gallery_* → 拉取 `imageId`） |
| `features/workspace/markup.ts`、`MarkupEditorDialog.svelte`、`AssetPackPickerDialog.svelte` | 只用 `primaryEditSourceId` / `editSourceCount`，基本无需改 |
| `features/workspace/Workspace.svelte`（≈82 处引用） | `editSources` 派生直接 `state.sources.map(...)` 加 `isPrimary/maskCoverage`；`removeSource`、`preview` 打开、`restoreMaskFromJob`(L1360-1414) 的三路分支改为按 `kind` 取源；清理画廊主图的 3 处 `clearGallerySource` 保留方法名 |
| `tests/unit/editSource.test.ts`(≈139 处)、`editPrimaryImage.test.ts`、`markupInstall.test.ts` | 夹具 `upload()/gallery()` 改造为构造 `sources`；新增"规范化顺序"、"主图变化才失效"、"槽位上限"用例 |
| `tests/e2e/markup-editor*.spec.ts` | 若直接读 store 内部字段则改，否则不动 |

### 1.6 取舍

- **保留 store 方法名**（`setGallerySource` 等）而不是改成通用 `add/set`：Workspace 调用点保持语义化，diff 集中在 store 与读取处。
- **不引入 Map/Record 按 id 索引**：数量 ≤16，数组足够，且顺序即语义。
- **不改后端契约**：后端仍分别接收 `files`、`reference_image_id`；扁平化只发生在浏览器状态层。

### 1.7 风险

- Workspace.svelte 体量大、引用多 → 用 TS 编译错误驱动迁移，先删旧字段类型再逐处修。
- 规范化排序可能改变"先加画廊参考图、后加上传"的**显示**顺序：现状是 `Workspace` 派生列表里本来就按"主图→上传→参考图"拼接（design phase-2 §3.3），故显示顺序不变；wire 顺序同。需单测固定。

---

## P2-2 Agent 资产引用结构化持久化

### 2.1 现状痛点

- `asset_refs` 块存于 `agent_messages.blocks_json`，成员为标签 `round-N-input-K`；`create_turn` 用 `int(label.rsplit("-", 1)[1]) - 1` 反解下标，再对 `source_images` / `attachment_ids` 做两次下标换算（`turns.py` L113-124，一行 ~300 字符的列表推导）。
- 三条路径（新增资产、编辑分支保留快照、重新生成）各自构造 `(alias, indices, collection_id, explicit_indices)` 四元组，位置语义靠元组下标。
- `create_turn` 单函数 ≈230 行，混合了分支校验、附件展开、快照解析、落库。
- 标签 ↔ 图片行的关系只靠字符串约定，无外键、无法按 `image_id` 反查"哪些资产包引用了该图"。

### 2.2 新表（迁移 `43 agent_asset_refs`）

```sql
CREATE TABLE IF NOT EXISTS agent_message_asset_refs (
    id TEXT PRIMARY KEY,                -- 作为 API 块 id
    conversation_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    message_id TEXT NOT NULL,           -- 用户消息
    position INTEGER NOT NULL,          -- 同一消息内的分组顺序
    alias TEXT NOT NULL,                -- 快照：当时的 asset_alias
    collection_id TEXT,                 -- 无外键：集合之后可能被删除，快照仍需保留
    FOREIGN KEY(conversation_id) REFERENCES agent_conversations(id) ON DELETE CASCADE,
    FOREIGN KEY(turn_id)         REFERENCES agent_turns(id)         ON DELETE CASCADE,
    FOREIGN KEY(message_id)      REFERENCES agent_messages(id)      ON DELETE CASCADE,
    UNIQUE(message_id, position)
);
CREATE TABLE IF NOT EXISTS agent_message_asset_ref_images (
    asset_ref_id TEXT NOT NULL,
    input_image_row_id TEXT NOT NULL,   -- agent_message_images.id (role='input')
    position INTEGER NOT NULL,          -- 集合内顺序（即快照时的集合顺序）
    explicit INTEGER NOT NULL DEFAULT 0,-- 该图同时被用户显式附加
    PRIMARY KEY (asset_ref_id, input_image_row_id),
    FOREIGN KEY(asset_ref_id)       REFERENCES agent_message_asset_refs(id) ON DELETE CASCADE,
    FOREIGN KEY(input_image_row_id) REFERENCES agent_message_images(id)     ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_agent_asset_refs_message ON agent_message_asset_refs(message_id);
CREATE INDEX IF NOT EXISTS idx_agent_asset_ref_images_row ON agent_message_asset_ref_images(input_image_row_id);
```

- 成员外键指向 `agent_message_images.id`（而不是 `gallery_entries.id`）：画廊图被删时该行 `image_id` 置 NULL 但行仍在，快照成员与标签 `refs` 仍可渲染（与现状"图片已删除"表现一致）。
- 不为 `collection_id` 建外键：规范明确"集合之后改变/删除，重新生成与保留编辑仍用原快照"。

### 2.3 回填（迁移内，幂等）

对每条 `agent_messages` 中 `role='user'` 且 `blocks_json` 含 `"asset_refs"` 的行：
1. 解析 JSON；对每个 `asset_refs` 块，`INSERT OR IGNORE` 一行 `agent_message_asset_refs`（沿用块的 `id`，`position` = 块序号）。
2. 对 `refs`/`explicit_refs` 的每个标签，用 `UNIQUE(conversation_id, ref_label)` 在 `agent_message_images` 查到 `id`；查不到（异常脏数据）则跳过该成员并记 warning，不中断迁移。
3. `blocks_json` **不改写**（保留作回滚/排障依据），运行期不再读取其中的 `asset_refs`。
4. 迁移幂等：以 `INSERT OR IGNORE` + 主键保证重复执行安全；`schema_migrations` 记账沿用现有机制。

### 2.4 读取路径（保持 API 形状）

`_message_from_row` 是纯行函数，不改。新增 `_attach_asset_ref_blocks(conn, messages)`：对一批用户消息用 `message_id IN (...)` 一次性查两张表，合成

```json
{"id": "<ref id>", "type": "asset_refs", "alias": "...", "collection_id": "...",
 "refs": ["round-1-input-1", ...], "explicit_refs": ["round-1-input-1"]}
```

放在 `blocks` 最前（与现状一致：用户消息的 blocks 本就只含 asset_refs）。调用点：`messages.list_messages`、`messages.get_message`、`branches` 的路径装载（`branches.py:99`）。这样 `agent_context.py`、前端 `agent.ts` / `agentBlocks.ts` / 类型定义 **零改动**；`refs` 标签由成员行关联的 `ref_label` 生成，顺序按成员 `position`。

> 助手消息的 `blocks_json` 仍由 `turns.py:367-378` 等路径写入，且不含 asset_refs，不受影响。

### 2.5 写入路径：`create_turn` 重构

引入内部数据类并抽出函数（同文件，非新模块，避免过度拆分）：

```python
@dataclass
class _AssetGroup:
    alias: str
    collection_id: str | None
    image_ids: list[str]          # 按集合/快照顺序，以画廊 image_id 为键
    explicit_image_ids: set[str]

def _resolve_asset_groups(conn, *, action, source_turn_id, text, attachment_ids,
                          explicit_ids, asset_collection_ids) -> list[_AssetGroup]
```

三条路径统一产出 `_AssetGroup[]`：

- **新增（continue / edit 中新选择的集合）**：沿用现有查询 `list_collection_reference_image_ids_on_conn`，并把 image_id 追加进 `attachment_ids`（含上限检查，位置不变）。
- **编辑保留快照**：从 **源 turn 的关系表** 读取分组（`JOIN agent_message_images` 取 `image_id`），仅保留 alias 仍被文本提及（沿用现有正则）且 `image_id ∈ attachment_ids` 的成员；不再 `rsplit` 标签、不再下标换算。
- **重新生成**：直接复制源 turn 的分组（`attachment_image_ids` 即源 input 行的 image_id，本就一致）。

落库顺序：先插入 `agent_message_images` input 行（已有逻辑）并记录 `image_id → row_id`，再插入 `agent_message_asset_refs` / `_ref_images`。`blocks_json` 对用户消息写 `'[]'`。

`create_turn` 其余职责（分支冲突校验、轮次/seq、标题）不动；仅把 "asset 分组解析" 抽出，预计 `create_turn` 缩短约 40 行并移除 4 元组索引逻辑。

### 2.6 `explicit` 语义保持

现状 `explicit_refs` = 该资产包成员中、同时在 `explicit_ids`（用户另行显式附加）里的图。关系表里同样存 `explicit`；编辑场景下"保留的旧分组"其 `explicit` 取自源行，再与当前 `explicit_ids` 取并集的规则以现有测试 `test_asset_snapshot_tracks_explicit_gallery_overlap` 为准，**先用测试固化现行为再重构**。

### 2.7 取舍

- **选关系表而非"在 JSON 里改存 image_ids"**：JSON 方案仍无外键/索引，无法 `image_id` 反查，也无法表达图片行删除；关系表代价是一次迁移 + 一个批量读取 helper，收益是消除隐式字符串耦合，符合 `database-guidelines.md`。
- **不删旧 `blocks_json` 内容**：迁移不做破坏性改写。
- **API 保持合成块**：避免前后端同时改；前端 fork 逻辑（`agent.ts` 的 `explicit_refs/refs` 标签计算）留待后续独立演进，不在本任务内。

### 2.8 风险与对策

| 风险 | 对策 |
|---|---|
| 回填遇到脏标签/缺失图片行 | 跳过成员 + warning，不中断；迁移测试覆盖 |
| 批量读取漏掉某个消息装载入口导致块缺失 | 以 `rg "_message_from_row"` 列出的全部调用点逐一接入，并用 `test_agent_branches_contract` / `test_agent_contract` 回归 |
| 重构改变 `explicit_refs` 细节 | 先补/确认现有 4 个资产用例断言（不改断言），再动实现 |
| 大会话下的额外查询开销 | 单次 `IN` 批量查询；两个索引覆盖 `message_id` 与 `input_image_row_id` |

---

## 3. 范围外

- 前端 `agent.ts#fork` 的标签集合运算改为结构化引用（后续）。
- 后端 `/api/edits` multipart 字段重命名或合并（契约变更，不做）。
- P0/P1 已完成项的再次调整。
