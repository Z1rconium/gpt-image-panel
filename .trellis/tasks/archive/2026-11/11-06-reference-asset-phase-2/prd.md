# Phase 2: 编辑链路支持多张画廊参考 (Multi-Reference Edit Pipeline)

## Goal

扩充图像编辑链路，支持在一次编辑请求中提交多张画廊参考图（`reference_image_id`）：
1. 后端接口（`/api/edits` 与 `/api/edits/from-gallery/{image_id}`）支持接收多张 `reference_image_id`，与路径主图、上传图合计上限 16 张。
2. 保持严格的组合顺序语义：`[路径画廊主图?, *uploads, *reference_image_id]`，确保掩码与标记（mask / markup）稳定绑定在第一张主图上，不受参考图影响。
3. 后端在暂存前完成总数校验，并拒绝将 SVG/GIF 格式图片作为参考图输入。
4. 前端编辑源状态（`editSourceStore`）与提交逻辑（`preview.ts`）支持画廊参考图列表，保持组合顺序与掩码不失效。

## Requirements

1. **后端接口与表单字段扩充**：
   - 支持通过表单字段 `reference_image_id` 或 `reference_image_id[]` 传入画廊图片 ID（支持单值与多值列表）。
   - 同时支持 `/api/edits`（含上传图 + 画廊参考图，或纯画廊参考图）与 `/api/edits/from-gallery/{image_id}`（路径主图 + 上传图 + 画廊参考图）。
   - 在暂存前执行总数预检：`路径主图(0或1) + 上传图片数 + 参考图ID数 <= 16`；若超过 16 张，立即返回 400（`At most 16 edit source images are supported.`），无需做多余的文件落盘与图片解码。
   - 若总图片数为 0，返回 422（`Upload or reference image is required.`）。

2. **参考图格式校验与 SVG/GIF 拦截**：
   - 引用画廊条目时，校验条目是否存在（不存在返回 404）。
   - 明确拒绝 SVG 和 GIF 作为参考图输入：若引用的画廊条目为 `.svg` 或 `.gif`（或相应 Content-Type），返回 400 拒绝。
   - 所有成功获取的画廊参考图转为 `EditImageSource`，且失败时保证清理所有已暂存文件。

3. **顺序与掩码一致性保障**：
   - 严格保证进入生图队列的图片源顺序：`[路径画廊主图?, *uploads, *reference_image_id]`。
   - 掩码（mask）与标记（markup）仅与 `sources[0]`（主图）进行尺寸与方向校验；附加的参考图不参与主图掩码比对，亦不改变主图地位。

4. **前端状态管理 (`editSource.ts` & `preview.ts`)**：
   - `EditSourceState` 增加 `galleryReferences: EditGalleryReferenceSource[]`。
   - `editSourceCount` 统一计算：`files.length + (selectedGalleryImageId ? 1 : 0) + galleryReferences.length`。
   - 提供 `addGalleryReferences`、`removeGalleryReference`，支持从画廊引用列表添加与移除，容量超出 16 时给出截断或上限提示。
   - 当增删参考图时，由于未改变主图（`primaryEditSourceId` 仍为 `selectedGalleryImageId || files[0].id`），已绘制的掩码和标记必须保持有效，不被误清空。
   - `preview.ts` 提交表单时将 `galleryReferences` 序列化为 `reference_image_id` 键提交至后端。

5. **界面展示联动 (`Workspace.svelte`)**：
   - 在工作区编辑源卡片列表中，按顺序合并展示主图、上传图与画廊参考图。
   - 画廊参考图卡片支持预览展示与一键移除。

## Acceptance Criteria

- [x] 后端 `/api/edits` 与 `/api/edits/from-gallery/{image_id}` 均可正确接收 `reference_image_id` / `reference_image_id[]`。
- [x] 后端在暂存前完成总数校验：总数 > 16 立即返回 400，总数 = 0 返回 422。
- [x] 后端对 `reference_image_id` 校验有效性：不存在返回 404；为 SVG/GIF 格式时拒绝并返回 400。
- [x] 提交队列的 `image_sources` 顺序完全符合 `[路径主图?, *uploads, *reference_image_id]`。
- [x] 掩码针对第一张主图有效，附加画廊参考图不导致掩码尺寸不匹配或被意外剔除。
- [x] 前端 `editSourceStore` 完整支持 `galleryReferences` 的增删查清，容量计算正确，增删参考图不会破坏已有的 mask。
- [x] 前端 `preview.ts` 正确序列化并提交 `reference_image_id`。
- [x] 后端契约测试与前端单元测试全部通过，`npm run check` 零错误。
