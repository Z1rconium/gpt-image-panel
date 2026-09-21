# 蒙版编辑（Mask Inpainting）实施计划

> 状态：已实施（Phase 1 + Phase 2 + Phase 3；`has_mask` 与预设级 `supports_mask` 已落地）· 2026-09-21
> 实施结果：Phase 1/2 基线 后端 539 passed / 前端 e2e 118 passed；Phase 3 落地后 后端 546 passed / 前端单测 37 passed / 前端 e2e 121 passed（其中蒙版 14）/ `frontend:check` 0 错误
> 未完成项：无（Phase 0 上游 `mask` 验证已于 2026-09-21 完成；Phase 3 增强已实施，仅"导出/导入与 R2 同步"按范围决策未纳入）
> 范围：为 `/api/edits` 与 `/api/edits/from-gallery/{image_id}` 增加可选的 `mask` 蒙版，前端提供画笔式蒙版编辑器，后端做严格校验并透传到上游 `/v1/images/edits`。
> 参考实现：[Houtx/gpt-image-playground](https://github.com/Houtx/gpt-image-playground)（`src/components/editing-form.tsx`、`src/app/api/images/route.ts`、`src/app/page.tsx`）。

---

## 0. 上游 API 硬约束（设计基线）

来自 `openai-python` `types/image_edit_params.py` 的官方 docstring（逐字）：

> `mask`: An additional image whose fully transparent areas (e.g. where alpha is zero) indicate where `image` should be edited. If there are multiple images provided, the mask will be applied on the first image. Must be a valid PNG file, less than 4MB, and have the same dimensions as `image`.

由此推出所有不可妥协的规则：

| 规则 | 含义 | 本计划落点 |
| --- | --- | --- |
| 仅 PNG | 蒙版必须是带 alpha 通道的 PNG | 前后端都校验；前端始终以 `image/png` 导出 |
| alpha == 0 才算"要编辑" | 半透明像素**不会**被编辑；抗锯齿边缘会比画的略小 | 导出时把 alpha 二值化为 0/255 |
| 尺寸必须等于 `image` | 指**第一张** `image` | 后端按"主图"（gallery 源优先，否则首个上传）的真实像素尺寸校验 |
| < 4 MB | 上游硬上限，与 `MAX_FILE_SIZE_MB` 无关 | 新增 `MAX_EDIT_MASK_BYTES = 4 MiB` |
| 作用于第一张图 | 多参考图时只有主图被 inpaint | 前端把蒙版**绑定到主图的 sourceId**，主图变了蒙版即失效 |

---

## 1. 参考实现分析

### 1.1 它做了什么

- **画布叠层**：`<img>` 上叠一个 `<canvas>`，`width/height` 设为原图自然尺寸，CSS 拉伸到容器宽度；`getMousePos` 用 `getBoundingClientRect` 反算比例。
- **笔触模型**：`editDrawnPoints: {x,y,size}[]` 存在 React state；`drawLine` 在两点之间按 `size/4` 步长插值，每个插值点都 `setState` 一次。
- **渲染**：每次 `editDrawnPoints` 变化，`useEffect` 清空反馈画布、**重画全部点**（红色实心圆），再以 50% alpha 绘制到显示画布。
- **导出**：`generateAndSaveMask` 新建离屏画布 → 填黑 → `globalCompositeOperation='destination-out'` 画所有点 → `toDataURL`（预览）+ `toBlob`（上传文件）。
- **上传蒙版**：只校验 `file.type === 'image/png'` 与宽高是否等于原图。
- **提交**：`page.tsx` 把 `editGeneratedMaskFile` 以 `mask` 字段 append 到 FormData；`route.ts` 直接 `...(maskFile ? { mask: maskFile } : {})` 透传给 SDK。
- **生命周期**：任何 `imageFiles`/`sourceImagePreviewUrls` 变化都把蒙版相关 state 全部清零。

### 1.2 不足之处（按严重程度）

**A. 正确性 / 语义**

1. **添加第二张参考图会把蒙版清空**。`useEffect` 依赖 `imageFiles`，只要数组变化（哪怕只是追加）就 reset 全部蒙版 state，用户辛苦画的蒙版无故消失；但反过来，**删掉首图后其它图前移**时蒙版反而应该失效，它却只是同样粗暴清零，没有任何提示。
2. **服务端零校验**。`route.ts` 不检查蒙版是 PNG、是否含 alpha、尺寸是否与首图一致、是否 < 4 MB，也不检查是否"至少有一个透明像素"。所有错误都要等上游返回 4xx 才暴露；在本项目的**排队 + 多 worker** 架构下这意味着错误在任务入队之后才出现，必须在入队前（admission）就拦下。
3. **上传的蒙版不检查 alpha 通道**。`RGB` 模式的 PNG 会通过前端校验，然后上游要么报错、要么什么都不改。
4. **无 4 MB 限制**（前后端都没有）。
5. **抗锯齿边缘未二值化**。`arc()` 填充的边缘是半透明像素，按官方语义**不会被编辑**，实际 inpaint 区域比看到的红色略小，用户难以察觉。

**B. 性能**

6. **O(n²) 重绘**：每个新点都触发一次全量重绘（所有点 `arc+fill`），画布是**原图自然分辨率**（可到 3840×3840）。长笔触几千个点后每次 mousemove 都要重画几千个圆，明显卡顿。
7. **每个插值点一次 `setState`**：一次 `mousemove` 可能产生几十次 state 更新/re-render。
8. **`toDataURL` 双份内存**：预览用 base64 字符串（4K PNG 可达数 MB 字符串）+ Blob 各存一份，并且放在 React state 里随组件 re-render。
9. **显示画布 = 自然分辨率**：没有 DPR 处理，也没有把"显示层"与"数据层"分开。

**C. 交互 / 可用性**

10. **两步式"保存蒙版"**：画完必须点"保存蒙版"，否则提交时报"请先保存已绘制的蒙版"。这是把实现细节（何时导出 PNG）暴露给用户；蒙版应在提交时按需导出。
11. **无撤销/重做，无橡皮擦**，只有"Clear"全清。
12. **无缩放/平移**，画布被压到容器宽度；大图上精细涂抹几乎不可能。
13. **笔刷大小以图像像素为单位**：同一个 30px 在 1024 图上很粗、在 4096 图上很细，且没有跟随光标的笔刷轮廓预览。
14. **Mouse + Touch 分别处理，没有 Pointer Events**：无 `setPointerCapture`，`onMouseLeave` 直接停笔，鼠标划出画布再回来笔触断掉；无 `getCoalescedEvents`，快速移动时笔触呈锯齿状。
15. **预览语义混乱**：蒙版预览放在白底上，透明区显示为白色，用户看到"黑 = 保留、白 = 编辑"，与画布上"红 = 编辑"的心智模型不一致。
16. **无可访问性**：canvas 无 `role`/`aria-label`，无键盘替代，无实时朗读。
17. **无法回看历史任务用了什么蒙版**，重试也无法带回蒙版。
18. **硬编码红色反馈色 + 白底预览**，与本项目"Quiet Control Room / 单一信号色（Operational Emerald）"的设计系统冲突。

**D. 架构**

19. 所有状态（12 个 useState）由 `page.tsx` 持有并通过 props 下钻到 `editing-form.tsx`，蒙版逻辑与表单耦合，无法复用/单测。
20. 无任何自动化测试覆盖蒙版路径。

---

## 2. 本项目现状（编辑管线梳理）

```
浏览器 preview.ts:editImage()
  └─ FormData{prompt,size,...,image|image[]}  POST /api/edits[/from-gallery/{id}]
       └─ api/routers/edits.py
            ├─ read_upload_edit_sources()  → 逐个流式落盘 data/edit-sources/edit-source-*.ext
            │      └─ validate_edit_source_header() + validate_edit_source_file()（Pillow 解码校验）
            ├─ read_gallery_edit_source()  → 复制 gallery 文件到同目录
            ├─ sources = [gallery_source, *upload_sources]      ← gallery 源永远排第一
            └─ services/job_queue.py:queue_edit_job()
                 └─ enqueue_image_job(edit_sources=[{temp_path,byte_size,filename,content_type}])
                      └─ SQLite image_job_units.edit_sources_json；edit_source_reservations 记账字节
                            └─ services/job_executor.py  (任意 worker 领取 unit)
                                 ├─ edit_source_from_payload() 重建 EditImageSource
                                 └─ integrations/upstream/generation.py:call_image_edit_api()
                                      └─ aiohttp.FormData: image|image[] + _build_image_params() (+stream)
                                           └─ 成功/失败后 cleanup_parent_edit_sources() 删临时文件
启动：services/startup_maintenance.py 清理 data/edit-sources/edit-source-* 残留
```

可复用 / 需注意的点：

- `EditImageSource`（`services/job_queue.py:63`）是 frozen dataclass，序列化为 dict 落库；**扩一个 `role` 字段即可承载蒙版**，无需改表结构。
- `cleanup_parent_edit_sources()` 遍历 `edit_sources` 全部条目删文件 → 蒙版临时文件自动纳入清理。
- `queue_edit_job()` 用 `sum(byte_size)` 做 pending 字节预留 → 蒙版字节自动计入。
- `startup_maintenance.py:36` 只 glob `edit-source-*` → 蒙版临时文件**必须沿用 `edit-source-` 前缀**（或扩展 glob）。
- `validate_edit_source_count()` 与 `MAX_EDIT_SOURCE_IMAGES=16` 只应统计 `role == "image"` 的条目。
- `queue_image_job()` 对 `is_image_25` 的 content_type/50 MB 检查会遍历所有 `edit_sources_payload`，蒙版是 PNG，无害。
- `validate_image_file_details()`（`repositories/image_files.py:41`）已能返回 `(format, width, height)`，可直接用于"主图尺寸"与"蒙版尺寸"比对。
- 前端 `EditSourcePicker.svelte` 已按卡片展示每个源（gallery 源排最前），是"主图"最自然的锚点；`EditPreviewModal.svelte` + `panels.ts` 的 lazy dialog 模式可直接照搬给蒙版编辑器。
- `retryJob()`（`Workspace.svelte:978`）在 edit 重试时只要求源存在，需要补充"该任务曾使用蒙版"的提示。

---

## 3. 设计决策

| # | 决策 | 备选 | 取舍理由 |
| --- | --- | --- | --- |
| D1 | 蒙版作为 `edit_sources` 的一个条目持久化，`role: "mask"`；已有条目缺省 `role: "image"` | 新增 `edit_mask_json` 列 + migration | 零 migration；清理、字节记账、多 worker 重建全部复用；executor 只需按 role 分组 |
| D2 | 后端在 **admission**（路由层）完成全部蒙版校验：PNG、含 alpha、alpha==0 像素占比 > 0、尺寸 == 主图、< 4 MiB、只允许一个 `mask` 字段 | 只在 executor 校验 / 只信前端 | 排队架构下越早失败越好；前端校验只是体验优化，不是安全边界 |
| D3 | "主图" = gallery 源（若有）否则首个上传；与现有 `sources = [gallery_source, *upload_sources]` 一致 | 让前端显式指定主图 index | 不改现有顺序语义；前端 UI 用"主图"标签把这个规则可视化 |
| D4 | 前端蒙版**绑定 `primarySourceId`**：追加/删除非主图不影响蒙版；主图被移除/替换才使蒙版失效（toast 提示并清除） | 参考实现的"任何变动全清" | 修复缺陷 #1 |
| D5 | 蒙版在"应用"时导出一次 PNG Blob 存入 store；提交时直接 append，不再有"保存蒙版"步骤 | 参考实现的两步保存 | 修复缺陷 #10 |
| D6 | 数据层与显示层分离：自然分辨率的 `marks` 离屏画布（增量 `lineTo`）+ 显示分辨率（CSS px × DPR）的叠加画布 | 单一自然分辨率画布全量重绘 | 修复缺陷 #6/#7/#9 |
| D7 | 导出格式：黑色不透明底 + `destination-out` 打孔，再把 alpha 二值化为 0/255 | 用原图像素做底 | 官方语义只看 alpha；黑底 PNG 压缩后极小（远小于 4 MB）；二值化修复缺陷 #5 |
| D8 | 笔刷大小以**屏幕像素**定义，映射到图像像素 | 图像像素 | 修复缺陷 #13，跨分辨率手感一致 |
| D9 | 输入统一走 Pointer Events + `setPointerCapture` + `getCoalescedEvents` + `touch-action:none` | mouse/touch 双写 | 修复缺陷 #14 |
| D10 | 上传蒙版 = 把 PNG 的 alpha==0 区域导入为 `marks` 初始层，之后可继续涂抹/擦除 | 上传即终态 | 一条导出管线，同时获得校验与可编辑性 |
| D11 | 反馈色使用 Operational Emerald（`#059669` / dark `#10B981`）约 45% alpha；"仅看蒙版"模式用棋盘格表示透明 | 红色 + 白底 | 遵守 `DESIGN.md`；修复缺陷 #15/#18 |
| D12 | Phase 1 只在 job 上记录 `mask_applied: true`（history/preview 显示"Masked"徽标）；蒙版文件本身不持久化到 gallery | 把蒙版存进 `images/masks/` | 先交付主链路；持久化蒙版留作 Phase 3 |
| D13 | 蒙版启用时若 `size ≠ auto` 且 ≠ 主图尺寸，仅给**软提示**（建议 auto），不硬拦 | 硬拦 | 上游行为不明确，交由用户决定 |

---

## 4. 实施计划

### Phase 0 · 准备（0.5 天）

- [x] 用当前预设跑一次手工 curl：`/v1/images/edits` + `mask` 字段，确认用户所用网关/上游真的支持 `mask`（部分 OpenAI 兼容网关会静默忽略）。**已验证 2026-09-21** · 网关 `https://www.nexotoken.net/v1`、模型 `gpt-image-2`：无蒙版基线整图变绿；左半透明蒙版仅左半变绿、右半保留原色；右半透明蒙版结果对称。该网关真正应用 `mask`，对应预设应保持 `supports_mask=true`（观察：网关返回值把 1024×1024 重采样为 1254×1254，`data[0]` 为 `url` 而非 `b64_json`）。
- [x] 确认 `generate_jobs` 行的存储方式（`repositories/db/schema.py:334`、`db/rows.py` 的 normalize 白名单），决定 `mask_applied` 是走 JSON 列还是需要一条 `schema_migrations`。
- [x] 与 `DESIGN.md`/`PRODUCT.md` 对齐蒙版编辑器的视觉：沿用 `EditPreviewModal` 的深色画布容器、`overlay-panel`、`rounded-2xl`、`control-focus`。

> 实施说明：`mask_applied` 选择真实 SQLite 列 + 第 21 号 `schema_migrations`（`generate_job_mask_column`），与 `streaming`/`partial_images` 同模式；`db/rows.py` 读取时做 `bool()` 归一。

### Phase 1 · 后端：蒙版接入 + 校验 + 透传（1.5 天）

**1.1 常量与数据模型**

- `backend/app/api/edit_limits.py`
  - `MAX_EDIT_MASK_BYTES = 4 * 1024 * 1024`
  - `EDIT_MASK_FIELD_NAME = "mask"`
- `backend/app/services/job_queue.py`
  - `EditImageSource` 增加 `role: Literal["image", "mask"] = "image"`
  - `edit_source_to_payload()` 写入 `role`；`edit_source_from_payload()` 读取 `role`，缺省 `"image"`（兼容旧 unit 行）
  - `queue_edit_job(req, image_sources, mask_source: EditImageSource | None = None)`：payload 拼接 `[*images, mask]`；`pending_edit_source_bytes` 计入蒙版
  - `build_pending_job()` / `queue_image_job()` 增加 `mask_applied: bool`（落到 job 行；SSE/`GenerateJobStatus` 一并带出）

**1.2 校验（新文件 `backend/app/services/edit_masks.py`）**

```python
@dataclass(frozen=True)
class EditMaskInfo:
    width: int
    height: int
    transparent_ratio: float   # alpha == 0 像素占比

def validate_edit_mask_file(path: Path, *, expected_width: int, expected_height: int) -> EditMaskInfo:
    # 1. validate_image_file(path, filename="mask.png", content_type="image/png") → 必须 detected_format == "png"
    # 2. Pillow 打开：mode 必须含 alpha（RGBA/LA/PA）或 info 含 "transparency"；否则 ValueError("mask PNG must have an alpha channel")
    # 3. image.size 必须 == (expected_width, expected_height)，错误信息带上两组尺寸
    # 4. alpha = image.convert("RGBA").getchannel("A"); zeros = alpha.histogram()[0]
    #    ratio = zeros / (w*h)；ratio == 0 → ValueError("mask has no fully transparent region to edit")
    # 5. 走 configure_pillow_image_limits()/decompression-bomb 保护（复用 verify_pillow_image 的 opener 模式）
```

- 在线程池执行（`run_image_operation(..., metric_name="validate_edit_mask")`），与现有源图校验一致。

**1.3 路由 `backend/app/api/routers/edits.py`**

- 把 `await request.form()` 提到一处，`read_upload_edit_sources(form)` 与新增 `read_upload_edit_mask(form)` 共用同一个 form 对象。
- `read_upload_edit_mask(form) -> EditImageSource | None`
  - `form.getlist("mask")` 长度 > 1 → 400 `"Only one mask is supported."`
  - 非 UploadFile / 空文件 → 400
  - 后缀/Content-Type 必须是 PNG（`is_image_upload` + `resolve_upload_content_type() == "image/png"`）→ 否则 400 `"Mask must be a PNG file."`
  - 复用 `copy_edit_source_stream_to_temp()` 落盘，但 `too_large_detail` 使用 4 MiB 上限（需要给该函数加 `max_bytes` 参数，默认 `max_upload_bytes()`）；临时文件前缀保持 `edit-source-`（满足 `startup_maintenance` 的 glob），后缀 `.png`
  - 返回的 `EditImageSource(role="mask")`
- `validate_edit_mask_against_primary(mask, primary: EditImageSource)`
  - `validate_image_file_details(primary.temp_path, ...)` 取主图 `(w, h)`
  - `validate_edit_mask_file(mask.temp_path, expected_width=w, expected_height=h)`
  - `ValueError` → 422（尺寸/alpha 语义错误属于"请求不可处理"）
- `edit_image()`：`sources` 读完后读 mask → 主图 = `sources[0]` → 校验 → `queue_edit_job(req, sources, mask)`；所有失败路径 `cleanup_edit_sources([*sources, mask])`
- `edit_image_from_gallery()`：主图 = `gallery_source`；其余同上
- `validate_edit_source_count()` 只统计 `role == "image"`

**1.4 上游 `backend/app/integrations/upstream/generation.py:call_image_edit_api()`**

- 新增参数 `mask_source: ImageEditSource | None = None`
- `form.add_field("mask", mask_file, filename=mask_source.filename or "mask.png", content_type="image/png")`，文件句柄纳入 `image_files` 统一关闭
- 进度文案：有蒙版时 `"Uploading source image, mask and edit parameters"`

**1.5 执行器 `backend/app/services/job_executor.py:~670`**

```python
sources = [edit_source_from_payload(s) for s in unit.get("edit_sources") or []]
image_sources = [s for s in sources if s.role == "image"]
mask_source = next((s for s in sources if s.role == "mask"), None)
```

- 传 `mask_source=mask_source` 给 `call_image_edit_api`；`image_sources` 为空仍抛 `UpstreamApiError`。

**1.6 Schema / 类型**

- `schemas/generation.py:GenerateJobStatus` 增加 `mask_applied: Optional[bool] = None`
- `frontend/src/lib/api/types/jobs.ts:GenerateJobStatus` 同步

**1.7 后端测试（`backend/tests/test_generation_jobs_contract.py` 或新文件 `test_edit_mask_contract.py`）**

| 用例 | 断言 |
| --- | --- |
| 上传源 + 合法 RGBA 蒙版（含透明像素、尺寸一致） | 202；job `mask_applied == true`；fake `call_image_edit_api` 收到 `mask_source.role == "mask"` |
| from-gallery + 蒙版尺寸 == gallery 图 | 202；蒙版按 gallery 图校验（而非上传图） |
| 蒙版尺寸 ≠ 主图 | 422，detail 含 `"mask"`、两组尺寸 |
| 蒙版无 alpha 通道（RGB PNG） | 422 |
| 蒙版全不透明（无编辑区） | 422 |
| 蒙版是 JPEG（改名 .png / 或 Content-Type 伪装） | 400（header sniff 拦下） |
| 蒙版 > 4 MiB | 400，detail 提到 4 MB |
| 两个 `mask` 字段 | 400 |
| 有蒙版时 `image[]` 仍可 16 张；蒙版不计入 16 | 202 |
| 上游表单字段（仿 `test_upstream_edit_api_sends_multiple_sources_as_image_array`） | `fields` 含 `("mask", "mask.png")`，且 image 字段命名规则不受影响 |
| 成功/失败后蒙版临时文件被清理（仿 `test_edit_source_temp_path_is_cleaned_after_success`） | 文件不存在 |
| 旧 unit 行（无 `role`）重建 | 全部视为 image，行为不变 |
| `edit_source_reservations` 计入蒙版字节 | 预留字节 == images + mask |

### Phase 2 · 前端：蒙版编辑器 + 提交 + 徽标（3–4 天）

**2.1 Store `frontend/src/lib/stores/editSource.ts`**

```ts
export type EditMask = {
  sourceId: string;        // 绑定的主图 id（gallery id 或 upload id）
  blob: Blob;              // 导出的 PNG（alpha==0 = 编辑区）
  width: number; height: number;
  coverage: number;        // 0..1，alpha==0 像素占比
  previewUrl: string;      // URL.createObjectURL(blob)，仅用于卡片缩略/预览
  origin: 'painted' | 'uploaded';
};
// EditSourceState 新增 mask: EditMask | null
```

- `primaryEditSourceId(state)`：`selectedGalleryImageId || files[0]?.id || ''`
- `isMaskValid(state)`：`mask?.sourceId === primaryEditSourceId(state)`
- `setMask(mask)`（先 revoke 旧 previewUrl）、`clearMask()`；`remove()`/`clearGallerySource()`/`setGallerySource()`/`clear()` 内部：若操作导致 `primaryEditSourceId` 变化且 mask 存在 → `clearMask()` 并返回 `{ maskDiscarded: true }` 供 Workspace toast（`messages.editMaskDiscarded`）
- `cleanup()` 一并 revoke mask previewUrl

**2.2 纯逻辑模块 `frontend/src/lib/features/mask/maskDocument.ts`（无 Svelte 依赖，可 vitest 单测）**

```ts
type StrokeCommand = { kind: 'stroke'; tool: 'brush' | 'erase'; size: number; points: {x:number;y:number}[] };
type Command = StrokeCommand | { kind: 'clear' } | { kind: 'invert' } | { kind: 'import'; bitmap: ImageBitmap };

createMaskDocument(width, height) → {
  marks: HTMLCanvasElement | OffscreenCanvas   // 自然分辨率，画上 = 待编辑
  beginStroke(tool, sizeImagePx, p) / extendStroke(points[]) / endStroke()   // 增量 lineTo，round cap/join
  undo() / redo()          // 命令栈；undo = 清空 marks 后重放剩余命令（重放成本 O(总点数)，仅在 undo 时发生）
  clear() / invert()
  importFromPng(file): Promise<void>   // 解码 → getImageData → alpha==0 → 画进 marks（作为 'import' 命令）
  coverage(): number       // getImageData 统计，节流调用（结束一笔后再算）
  exportPng(): Promise<{ blob: Blob; coverage: number }>   // 黑底 + destination-out(marks) → 二值化 alpha → toBlob('image/png')
  dispose()
}
```

- 二值化：一次 `getImageData` 遍历，`a < 128 ? 0 : 255`；同时统计 coverage，避免两次遍历。
- 导出与 `importFromPng` 在 `requestIdleCallback`/微任务中执行并展示 busy 状态；≥ 8 MP 图像上 `getImageData` 会有几十 ms，接受。

**2.3 组件 `frontend/src/lib/components/MaskEditorDialog.svelte`**

- 布局：沿用 `EditPreviewModal` 骨架（`mobile-dialog-root`、`overlay-panel`、`use:dialog`、`dialogIn/Out` 动效）；标题"蒙版编辑 · {主图名}"；主体 = 深色画布容器（`bg-zinc-950`），底部工具条。
- 画布层：
  - `<img>` 主图（`object-contain` 适配容器，记录 `fitScale`）
  - 叠加 `<canvas>`（CSS 尺寸 = 图片显示尺寸，backing = × `devicePixelRatio`），每帧 `drawImage(doc.marks)` 后用 `source-in` 填充 emerald 45% 做着色；`requestAnimationFrame` 合并多次 pointermove 的重绘
  - 光标层：跟随指针的圆形描边（半径 = 笔刷屏幕像素 / 2）
- 输入：`pointerdown`（`setPointerCapture`）→ `pointermove`（`getCoalescedEvents()`，屏幕坐标 → 图像坐标 = `(clientX - rect.left) / fitScale`）→ `pointerup/cancel`；`touch-action: none`；Shift + 点击 = 直线到上一点（可选）
- 工具条（全部有 `aria-label` / `aria-pressed` / `title`，`mobile-touch-target`）：
  - 画笔 / 橡皮（`B` / `E`）
  - 笔刷大小 slider（屏幕 px，4–160，`[` / `]` 调整；label 显示 "笔刷 32px"）
  - 撤销 / 重做（`Ctrl/Cmd+Z`、`Ctrl/Cmd+Shift+Z`）
  - 反选、清空
  - 上传蒙版 PNG（`accept="image/png"`）→ `importFromPng`，错误（非 PNG / 无 alpha / 尺寸不符）走 `showToast(..., 'error')`
  - 视图切换：`叠加` / `仅蒙版`（棋盘格底 + 黑色不透明区）
  - 右侧状态：`编辑区域 12.4%`（`aria-live="polite"`）
  - `应用` 主按钮（emerald；coverage == 0 时禁用并提示"尚未标记任何区域"）、`取消`
- 打开时若 store 已有蒙版且 `sourceId` 匹配 → `importFromPng(mask.blob)` 复原（撤销栈从"import"开始，可接受）
- 关闭：未应用的改动直接丢弃（有改动时先 `confirm` store 二次确认）
- 尺寸提示：若 `promptForm.size !== 'auto'` 且 ≠ `${w}x${h}` → 顶部一行中性提示（D13）

**2.4 入口与联动**

- `panels.ts`：`LazyPanel` 增加 `'maskEditor'`，`lazyPanels.maskEditor = createLazyComponent(() => import('$lib/components/MaskEditorDialog.svelte'))`
- `stores/ui.ts`：`maskEditorOpen: boolean`
- `EditSourcePicker.svelte`
  - `sources` 项增加 `isPrimary: boolean`、`hasMask: boolean`
  - 主图卡片显示"主图"小标签；卡片操作区新增"蒙版"按钮（仅主图可用；非主图 hover/焦点显示 tooltip "蒙版只作用于主图"）
  - 已有蒙版：卡片右下角 emerald 小徽标 + 覆盖率；点击徽标 = 重新编辑；新增"移除蒙版"次级操作
  - `props`: `onEditMask(sourceId)`, `onRemoveMask()`
- `Workspace.svelte`
  - `openMaskEditor()`：`rememberPanelFocus('maskEditor')` → `ensurePanel('maskEditor')` → `setUi('maskEditorOpen', true)`；关闭对称
  - `applyMask(mask)` → `editSourceStore.setMask(mask)` → toast `messages.editMaskApplied(coverage)`
  - 现有 `removeEditSource/clearEditSource/applyGalleryEditChoice/gallery 删除回调` 处理 `maskDiscarded` 返回值 → toast
  - `retryJob(job)`：`job.operation === 'edit' && job.mask_applied && !isMaskValid($editSourceStore)` → toast `messages.editRetryMaskMissing`（继续提交，不阻塞）
- `stores/preview.ts:editImage()`
  - `if (editSource.mask && !isMaskValid(editSource))` → `setError(messages.editMaskStale)`，return
  - `formData.append('mask', editSource.mask.blob, 'mask.png')`
- `PreviewPanel` / `JobHistoryList` / `RunningJobsList`：`job.mask_applied` → "Masked" 芯片（复用现有 `streaming` 芯片样式）
- `AiAssistantPanel` / `planEdit()`（可选，低成本）：`AssistantEditPlanRequest` 增加 `has_mask: bool`，系统提示里说明"仅描述蒙版区域内的变化"

**2.5 i18n（`locales/en.ts`、`locales/zh-CN.ts`，`i18n/types.ts` 同步）**

`promptForm.*`：`primarySourceBadge`、`editMask`、`maskOnlyPrimaryHint`、`maskBadge(coverage)`、`removeMask`
`maskEditor.*`：`title(label)`、`brush`、`eraser`、`brushSize(px)`、`undo`、`redo`、`invert`、`clear`、`uploadMask`、`viewOverlay`、`viewMaskOnly`、`coverage(pct)`、`apply`、`cancel`、`noAreaMarked`、`sizeHint(size)`、`discardChanges`
`messages.*`：`editMaskApplied(pct)`、`editMaskDiscarded`、`editMaskStale`、`editMaskUploadNotPng`、`editMaskUploadNoAlpha`、`editMaskUploadSizeMismatch(w,h,ew,eh)`、`editRetryMaskMissing`

**2.6 前端测试**

- vitest（若项目尚未配置则用 Playwright component 或在 e2e 内通过 `page.evaluate` 覆盖）：`maskDocument` 的 `exportPng` 二值化、`coverage`、`undo/redo`、`importFromPng` 对无 alpha PNG 抛错
- Playwright `frontend/tests/e2e/edit-mask.spec.ts`
  1. 上传一张 PNG → 主图卡片出现"蒙版"按钮 → 打开编辑器 → `page.mouse` 画一笔 → 覆盖率 > 0% → 应用 → 卡片显示徽标
  2. 再拖入第二张图 → 徽标仍在（回归缺陷 #1）
  3. 删除主图 → 徽标消失 + toast
  4. 提交编辑 → `route('/api/edits')` 的 `postDataBuffer()` 包含 `name="mask"; filename="mask.png"`
  5. 上传尺寸不符的蒙版 PNG → 错误 toast
  6. 键盘：`Tab` 可达全部工具，`Escape` 关闭，`Ctrl+Z` 撤销后覆盖率回落
  7. 移动端视口（375px）：编辑器全屏，工具条可用，画布可涂抹
- `npm run frontend:check` 无新增诊断

### Phase 3 · 增强（已实施）

- [x] **缩放/平移**：滚轮以光标为锚缩放（1–8×）、空格拖拽/中键拖拽平移、工具条放大/缩小/重置、`0` 重置。实现方式为外层 stage（不参与变换，作为坐标基准）+ 内层缩放层（`translate` + `scale`），屏幕坐标经逆向变换映射回图像坐标；`marks` 保持自然分辨率，缩放不重采样数据。
- [x] **矩形 / 套索工具**（拖动填充，按住 Alt 擦除；未提交前实时预览，抬起时入命令栈），**笔刷羽化**（导出前对打孔 alpha 高斯模糊再二值化）。注意：上游只认 `alpha == 0`，故羽化实现为**边界平滑**（消除指针锯齿），无法表达真正的渐变软边。
- [x] **蒙版持久化（仅本地）**：入队时把校验过的蒙版复制到 `images/masks/<job_id>.png`（`MASKS_DIR`）；`GET /api/generate/{job_id}/mask` 取回。清理覆盖三条路径：入队失败回滚、`trim_generate_jobs`/`clear_generate_job_history` 删除、启动孤儿清扫。**不含** gallery 导出/导入与 R2 同步（未纳入本次范围）。
- [x] **history/重试带回蒙版**：`job.mask_applied` 且当前蒙版无效时，重试自动按 `job_id` 拉取持久化蒙版载入 store（`origin: 'restored'`）；失败才回退到 `editRetryMaskMissing` 提示。
- [x] **gallery 元数据**：`mask_coverage` 写入 `gallery_entries`（migration 23），来源为 admission 的 `EditMaskInfo.transparent_ratio`；gallery 新增"仅蒙版编辑"筛选（`mask_only`，含 URL 状态与 i18n）。
- [x] **预设级能力开关** `supports_mask`，对已知不支持 `mask` 的网关在 UI 隐藏入口：`api_presets.supports_mask`（migration 22，默认 1），设置抽屉内可开关；关闭后主图卡片不再显示蒙版入口，已应用的蒙版会被清除并提示，切换预设时同样生效。（实测 `https://688.qzz.io` 会丢弃 `mask` 字段，属于该开关的目标场景。）
- [x] **补遗**：`EditMask.origin`（painted/uploaded/restored）在主图卡片展示来源；`editSource` 蒙版绑定逻辑补齐独立单测（`frontend/tests/unit/editSource.test.ts`）。

---

## 5. 关键时序（提交一次带蒙版的编辑）

```
用户在主图卡片点"蒙版" → MaskEditorDialog(lazy) → 涂抹(增量 lineTo, rAF 合并渲染)
 → 应用: exportPng() → {blob, coverage} → editSourceStore.setMask({sourceId: primary, blob, ...})
 → 点"编辑": preview.editImage()
     isMaskValid? ── 否 → setError(editMaskStale)
     是 → FormData + mask=mask.png → POST /api/edits[/from-gallery/{id}]
 → edits.py: 读 image(s) → 读 mask(≤4MiB, PNG) → 主图尺寸 → validate_edit_mask_file → queue_edit_job
 → SQLite unit.edit_sources_json = [{role:image,...}, {role:mask,...}]；reservation += mask bytes
 → worker: call_image_edit_api(image_sources, mask_source) → aiohttp FormData(image|image[], mask, params)
 → 成功/失败 → cleanup_parent_edit_sources() 删全部临时文件（含蒙版）
 → job.mask_applied=true → SSE → history/preview 显示 "Masked"
```

---

## 6. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 上游网关不支持 `mask`（静默忽略或 400） | Phase 0 手工验证；错误信息原样透传到 job.error；Phase 3 预设级开关 |
| 大图（≥ 8 MP）导出/导入 `getImageData` 卡主线程 | 仅在应用/导入时执行一次；busy 状态；后续可迁到 Web Worker + `OffscreenCanvas` |
| 用户选择的 `size` 与主图不一致导致上游结果异常 | D13 软提示；文档说明 |
| 旧 `image_job_units` 行没有 `role` | `edit_source_from_payload` 缺省 `"image"`，并有回归测试 |
| 蒙版临时文件泄漏 | 沿用 `edit-source-` 前缀纳入启动清理；`cleanup_parent_edit_sources` 遍历全部条目；测试覆盖 |
| 半透明边缘不被编辑 | 导出二值化（D7） |
| 移动端 Safari 对 `OffscreenCanvas`/`getCoalescedEvents` 支持不全 | 特性检测降级：普通 `<canvas>` + 单事件 |
| `request.form()` 在两个 reader 间重复解析 | 统一在路由入口解析一次并传递 form 对象 |

---

## 7. 验收标准（Definition of Done）

- [x] `POST /api/edits` / `/from-gallery/{id}` 接受可选 `mask`；所有非法蒙版在入队前被 400/422 拒绝，错误信息可读（`backend/tests/test_edit_mask_contract.py` 14 项）
- [x] 上游请求包含 `mask` 字段且文件名 `mask.png`；蒙版不计入 16 张上限；字节计入 pending 预留
- [x] 蒙版临时文件在任务成功/失败/启动清理三条路径都被删除（沿用 `edit-source-` 前缀，`cleanup_parent_edit_sources` 遍历全部条目）
- [x] 前端：主图卡片可打开蒙版编辑器；画笔/橡皮/撤销/重做/反选/清空/上传/视图切换/覆盖率均可用；键盘可完成全部操作；375px 视口可用
- [x] 追加非主图不丢蒙版；主图变更清蒙版并提示；蒙版失效时提交被拦截并提示
- [x] job 与 history/preview 显示 "Masked" 徽标；重试缺蒙版时提示
- [x] en / zh-CN 文案齐全，`frontend:check` 通过
- [x] 后端全量测试 + `npm run test:contract` + 新增 e2e 通过（539 passed / 418 passed / e2e 118 passed）
- [x] 视觉符合 `DESIGN.md`（单一信号色、扁平层级、WCAG 2.2 AA 对比度；焦点环沿用 `control-focus`）
- [x] 追加：`/api/edits` 请求体上限计入 `MAX_EDIT_MASK_BYTES`（否则 16 张满额图 + 蒙版会被 413 误拦）
- [x] 追加：`planEdit()` 请求带 `has_mask`，系统提示词约束"只描述蒙版区域内变化"
- [x] 追加：预设级 `supports_mask` 开关（migration 22、设置 UI、入口隐藏、切换预设清理 + 提示）

---

## 8. 工作量估算

| 阶段 | 估算 |
| --- | --- |
| Phase 0 准备 | 0.5 天 |
| Phase 1 后端 | 1.5 天 |
| Phase 2 前端 | 3–4 天（其中 `maskDocument` 1 天、Dialog 1.5 天、联动/徽标/i18n 0.5 天、测试 1 天） |
| 合计 | ≈ 5–6 天 |

---

## 附：与参考实现的逐项对照

| 参考实现缺陷 | 本计划对应措施 |
| --- | --- |
| #1 加图清蒙版 | D4 绑定主图 sourceId |
| #2/#3/#4 服务端零校验 | Phase 1.2/1.3 admission 校验（PNG/alpha/尺寸/4 MiB/单字段/有透明区） |
| #5 抗锯齿边缘不被编辑 | D7 导出二值化 |
| #6/#7/#9 O(n²) 重绘、逐点 setState、无 DPR | D6 双层画布 + 增量 lineTo + rAF |
| #8 base64 预览双份内存 | store 只存 Blob + objectURL |
| #10 两步保存 | D5 应用即导出 |
| #11 无撤销/橡皮 | 命令栈 undo/redo + eraser |
| #12 无缩放 | Phase 3 |
| #13 笔刷单位 | D8 屏幕像素 |
| #14 鼠标/触摸分写 | D9 Pointer Events |
| #15/#18 预览语义与配色 | D11 emerald 叠加 + 棋盘格 |
| #16 无 a11y | 2.3 aria/键盘/aria-live |
| #17 无历史记录 | `mask_applied` 徽标 + 重试提示；持久化留 Phase 3 |
| #19 状态下钻耦合 | store + 纯逻辑模块 + lazy dialog |
| #20 无测试 | Phase 1.7 / 2.6 |
