# Technical Design - Phase 2: 编辑链路支持多张画廊参考

## 1. 范围与边界

本阶段聚焦于**编辑链路对多张画廊参考图的支撑**：
- 后端编辑路由（`edits.py`）对 `reference_image_id` / `reference_image_id[]` 的解析、预检、格式校验与暂存
- 前端 `editSourceStore` 与 `preview.ts` 对画廊参考图（`galleryReferences`）的数据结构扩展、容量校验与表单提交
- 编辑源展示列表（`Workspace.svelte`）整合画廊参考卡片与移除交互
- 严格保证 `[路径画廊主图?, *uploads, *reference_image_id]` 顺序与第一张图主图掩码语义

明确不包含（后续阶段处理）：
- 阶段 3：资产包选图器弹窗及画廊集合管理弹窗中的上下移重排/别名配置
- 阶段 4：Agent 输入框 `@别名` 解析与展开逻辑
- 阶段 5：归档导入导出

## 2. 后端技术设计

### 2.1 路由层表单解析与预检 (`backend/app/api/routers/edits.py`)

在 `edits.py` 增加辅助解析函数：

```python
def extract_reference_image_ids(form: FormData) -> list[str]:
    ids: list[str] = []
    for field in ("reference_image_id", "reference_image_id[]"):
        for value in form.getlist(field):
            if isinstance(value, str):
                trimmed = value.strip()
                if trimmed:
                    ids.append(trimmed)
    return ids
```

在暂存前进行数量预检：
```python
def pre_validate_edit_source_count(
    *,
    has_gallery_primary: bool,
    uploads_count: int,
    reference_ids_count: int,
):
    total = (1 if has_gallery_primary else 0) + uploads_count + reference_ids_count
    if total > MAX_EDIT_SOURCE_IMAGES:
        raise HTTPException(
            status_code=400,
            detail=f"At most {MAX_EDIT_SOURCE_IMAGES} edit source images are supported.",
        )
    if total == 0:
        raise HTTPException(status_code=422, detail="Upload or reference image is required.")
```

### 2.2 画廊参考图暂存与格式校验 (`backend/app/services/edit_sources.py` & `edits.py`)

实现 `read_gallery_reference_edit_source(image_id: str) -> EditImageSource`：
1. 通过 `get_gallery_entry(image_id)` 获取画廊记录；若记录不存在，抛出 `NotFoundError("Gallery entry not found")`（路由层转 404）。
2. 校验文件扩展名与 Content-Type：
   ```python
   if entry.filename.lower().endswith((".svg", ".gif")):
       raise HTTPException(
           status_code=400,
           detail=f"SVG and GIF images cannot be used as reference images: {image_id}",
       )
   ```
3. 调用现有的 `copy_edit_source_file_to_temp` 暂存至临时目录，角色记为 `role="reference"`。

### 2.3 来源组合与顺序语义

在 `/api/edits/from-gallery/{image_id}`：
- `gallery_source`（主图）由路径参数获取并暂存（携带 `orientation_mask_size` 校验）。
- `upload_sources` 通过 `read_upload_edit_sources` 读取。
- `reference_sources` 逐项或并发读取并暂存。
- 组合列表严格保持：
  `sources = [gallery_source, *upload_sources, *reference_sources]`
- 掩码与 markup 校验均针对 `sources[0]`（即 `gallery_source`）。

在 `/api/edits`：
- 若包含 `uploads`：`upload_sources[0]` 为主图（校验 `orientation_mask_size`），后续为其余 uploads，再后为 `reference_sources`。
- 若无 `uploads` 仅有 `reference_sources`：`reference_sources[0]` 为主图。
- 组合列表严格保持：
  `sources = [*upload_sources, *reference_sources]`
- 掩码与 markup 校验针对 `sources[0]`。

### 2.4 资源释放与异常安全

在两处端点的 `try...except BaseException` 中：
```python
cleanup_edit_sources([
    *sources,
    *([mask] if mask else []),
    *([markup] if markup else []),
])
```
无论在读取上传、读取参考图、校验掩码还是排队生图中的哪一步出错，所有已落盘的临时图片均会被无遗漏清理。

## 3. 前端技术设计

### 3.1 状态扩展 (`frontend/src/lib/stores/editSource.ts`)

定义类型：
```typescript
export type EditGalleryReferenceSource = {
  id: string; // 唯一前缀，如 `gallery-ref-${imageId}-${seq}`
  imageId: string;
  filename?: string;
  label: string;
  previewUrl: string;
  previewLabel: string;
  width: number;
  height: number;
};
```

在 `EditSourceState` 增加：
```typescript
export type EditSourceState = {
  // ... 现有字段保持
  galleryReferences: EditGalleryReferenceSource[];
};
```

更新容量计算函数：
```typescript
export function editSourceCount(source: EditSourceState) {
  return (
    source.files.length +
    (source.selectedGalleryImageId ? 1 : 0) +
    (source.galleryReferences ? source.galleryReferences.length : 0)
  );
}
```

更新主图 ID 计算：
```typescript
export function primaryEditSourceId(source: EditSourceState) {
  return (
    source.selectedGalleryImageId ||
    source.files[0]?.id ||
    source.galleryReferences?.[0]?.id ||
    ''
  );
}
```

新增与修改 store 操作：
- `addGalleryReferences(refs: EditGalleryReferenceSource[], setError?: (msg: string) => void)`：
  计算当前可用槽位 `MAX_EDIT_SOURCE_IMAGES - editSourceCount(state)`。
  截取可用数量追加到 `galleryReferences`。若超出槽位则调用 `setError` 提示截断。
  应用 `dropStaleMask(next)`，由于主图 ID 未变，已绘制掩码不会丢失。
- `remove(sourceId: string)`：
  新增分支：若在 `galleryReferences` 中找到对应项，将其从数组中滤除，调用 `dropStaleMask`。
- `clear()`：清空 `galleryReferences: []`。

### 3.2 提交链路扩展 (`frontend/src/lib/stores/preview.ts`)

在 `editImage` 函数中：
```typescript
// 序列化画廊参考图
if (editSource.galleryReferences?.length) {
  editSource.galleryReferences.forEach((ref) => {
    formData.append('reference_image_id', ref.imageId);
  });
}
```
端点选择保持：
- 若设置了 `editSource.selectedGalleryImageId`，向 `/api/edits/from-gallery/{id}` 发送请求。
- 否则向 `/api/edits` 发送请求。

### 3.3 UI 联动 (`frontend/src/lib/features/workspace/Workspace.svelte`)

在 `editSources` 派生列表中包含画廊参考图：
```typescript
const editSources = $derived.by(() => {
  const state = $editSourceStore;
  const primaryId = primaryEditSourceId(state);
  const mask = state.mask;
  return [
    ...(state.selectedGalleryImageId ? [...] : []),
    ...state.files.map((source) => ({ ...source, kind: 'upload' as const })),
    ...(state.galleryReferences || []).map((ref) => ({
      id: ref.id,
      label: ref.label,
      previewUrl: ref.previewUrl,
      kind: 'gallery_reference' as const
    }))
  ].map((source) => ({
    ...source,
    isPrimary: source.id === primaryId,
    maskCoverage: mask && mask.sourceId === source.id ? mask.coverage : null,
    maskOrigin: mask && mask.sourceId === source.id ? mask.origin : null
  }));
});
```

移除按钮调用 `editSourceStore.remove(source.id)` 即可无缝支持删除参考卡片。

## 4. 契约与测试设计

1. **后端集成测试** (`backend/tests/test_edits_multi_reference_contract.py`):
   - `/api/edits` 支持多张 `reference_image_id` + 上传图组合，验证 `image_sources` 顺序为 `[*uploads, *reference_image_ids]`。
   - `/api/edits/from-gallery/{id}` 支持 `reference_image_id` + 上传图，验证顺序为 `[gallery_source, *uploads, *reference_image_ids]`。
   - 超过 16 张时在暂存前拦截并返回 400。
   - 引用不存在图片返回 404。
   - 引用 SVG / GIF 返回 400。
   - 针对第一张图的主图掩码依然有效且通过比对。

2. **前端单元测试** (`frontend/tests/unit/editSource.test.ts`):
   - 测试追加与移除 `galleryReferences`。
   - 测试添加画廊参考图不破坏已有的主图掩码和 markup。
   - 测试容量上限截断与 `editSourceCount` 计数。
