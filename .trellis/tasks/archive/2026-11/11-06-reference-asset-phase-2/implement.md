# Implementation Plan - Phase 2: 编辑链路支持多张画廊参考

## Execution Steps

### 1. 后端编辑接口扩展 (`backend/app/api/routers/edits.py` & `backend/app/services/edit_sources.py`)
- [x] 在 `backend/app/api/routers/edits.py` 实现 `extract_reference_image_ids(form: FormData) -> list[str]`。
- [x] 实现暂存前数量预检逻辑 `pre_validate_edit_source_count`，总数 > 16 返回 400，总数 == 0 返回 422。
- [x] 在 `backend/app/services/edit_sources.py`（或 `edits.py`）实现 `read_gallery_reference_edit_source(image_id: str) -> EditImageSource`：
  - 查询画廊条目，不存在抛出 `NotFoundError` (API 404)。
  - 拦截 `.svg` 与 `.gif`，若命中抛出 `HTTPException(400, "SVG and GIF images cannot be used as reference images")`。
  - 拷贝至临时路径并返回 `EditImageSource(..., role="reference")`。
- [x] 更新 `POST /api/edits`：
  - 提取 `uploads` 与 `reference_image_ids`。
  - 执行 `pre_validate_edit_source_count`。
  - 读取并暂存 `uploads` 与 `reference_sources`。
  - 组合并确定顺序：`sources = [*upload_sources, *reference_sources]`。
  - 掩码/标记针对 `sources[0]` 校验。
  - 异常时通过 `cleanup_edit_sources` 完整释放资源。
- [x] 更新 `POST /api/edits/from-gallery/{image_id}`：
  - 提取 `uploads` 与 `reference_image_ids`。
  - 执行 `pre_validate_edit_source_count(has_gallery_primary=True)`。
  - 读取主图、上传图与参考图。
  - 组合并确定顺序：`sources = [gallery_source, *upload_sources, *reference_sources]`。
  - 掩码/标记针对 `gallery_source` 校验。
  - 异常时清理所有已暂存文件。

### 2. 前端编辑源 Store 扩展 (`frontend/src/lib/stores/editSource.ts`)
- [x] 定义 `EditGalleryReferenceSource` 接口类型。
- [x] 在 `EditSourceState` 增加 `galleryReferences: EditGalleryReferenceSource[]`。
- [x] 更新 `initialEditSourceState`。
- [x] 更新 `editSourceCount(source)` 计算，包含 `galleryReferences.length`。
- [x] 更新 `primaryEditSourceId(source)`，确保优先级 `selectedGalleryImageId || files[0]?.id || galleryReferences?.[0]?.id || ''`。
- [x] 在 store 中实现 `addGalleryReferences(refs, setError)` 方法（容量计算与截断提示）。
- [x] 更新 `remove(sourceId)` 支持移除画廊参考图。
- [x] 更新 `clear()` 支持重置 `galleryReferences: []`。
- [x] 确保 `dropStaleMask` 行为：追加或移除参考图时不改变主图 ID，保留已有掩码和 markup。

### 3. 前端提交与界面展示联动 (`preview.ts` & `Workspace.svelte`)
- [x] 在 `frontend/src/lib/stores/preview.ts` 的 `editImage` 中将 `galleryReferences` 序列化为 `reference_image_id` 键附加到 `formData`。
- [x] 在 `frontend/src/lib/features/workspace/Workspace.svelte` 的 `editSources` 派生列表中包含 `galleryReferences` 项（类型为 `gallery_reference`），确保移除按钮和信息展示正常工作。

### 4. 自动化测试与契约验证
- [x] 编写后端测试 `backend/tests/test_edits_multi_reference_contract.py`：
  - 验证 `/api/edits` 多参考图与顺序 `[*uploads, *reference_image_ids]`。
  - 验证 `/api/edits/from-gallery/{id}` 多参考图与顺序 `[primary, *uploads, *reference_image_ids]`。
  - 验证超限（> 16）在暂存前拦截并返回 400。
  - 验证空请求返回 422。
  - 验证 SVG / GIF 参考图被拒绝返回 400。
  - 验证主图掩码不受参考图影响。
- [x] 编写前端单测 `frontend/tests/unit/editSourceReferences.test.ts`（或扩充 `editSource.test.ts`）：
  - 验证 `addGalleryReferences` 容量检查、上限截断。
  - 验证 `editSourceCount` 准确性。
  - 验证添加参考图不清除已有 mask。
  - 验证移除参考图。
- [x] 运行类型检查与测试：
  - `PYTHONPATH=. ./.venv/bin/pytest backend/tests/test_edits_multi_reference_contract.py`
  - `cd frontend && npm run check`
  - `cd frontend && npm run test:unit`
