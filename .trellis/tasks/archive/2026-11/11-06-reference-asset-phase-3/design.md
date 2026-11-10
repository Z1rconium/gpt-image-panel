# Technical Design - Phase 3: 选图器与资产管理

## 1. 范围与边界

本设计覆盖可持久化参考资产库的**阶段 3：选图器与资产管理**：
- 前端 `galleryCollectionsStore` 补充别名配置方法（`setAlias`）与成员读取/重排方法（`loadReferences`、`reorderItems`）
- `CollectionsDialog.svelte` 增强：支持设置与清除 `asset_alias`（捕获 409 冲突与 422 校验失败）、展示 `@alias` 徽章，提供成员改序子面板
- 新增 `AssetPackPickerDialog.svelte`（资产包选图器）：支持在编辑源区弹窗选择资产包、按需多选参考图、槽位容量实时计算与一键注入
- `EditSourcePicker.svelte` 与 `Workspace.svelte` 集成选图器触发入口与弹窗状态控制
- 多语言 `i18n` 扩充

明确不包含（后续阶段处理）：
- 阶段 4：Agent 输入框 `@别名` 解析与展开逻辑
- 阶段 5：归档导入导出支持

## 2. API 映射与 Store 设计 (`galleryCollections.ts`)

### 2.1 契约类型扩展 (`frontend/src/lib/api/types/gallery.ts`)
```typescript
export type GalleryCollection = {
  id: string;
  name: string;
  position: number;
  is_default: boolean;
  image_count: number;
  cover_image_id?: string | null;
  cover_filename?: string | null;
  asset_alias?: string | null; // Phase 1 后端已支持
  created_at: string;
  updated_at: string;
};

export type GalleryCollectionReferencesResponse = {
  collection: GalleryCollection;
  images: GalleryEntry[];
  total_count: number;
  skipped_unsupported: number;
};
```

### 2.2 Store 方法扩展 (`galleryCollectionsStore`)
- `setAlias(id: string, alias: string | null)`:
  发送 `PATCH /api/gallery/collections/{id}`，Body `{ asset_alias: alias ? alias.trim() : null }`。
  调用 `replaceCollection` 更新本地状态。
  捕获 409（别名冲突）与 422（格式错误），向上抛出可读异常供 UI 展示。
- `loadReferences(id: string): Promise<GalleryCollectionReferencesResponse>`:
  发送 `GET /api/gallery/collections/{id}/references`。
  返回有序参考图片列表与计数信息。
- `reorderItems(id: string, ids: string[]): Promise<GalleryCollection>`:
  发送 `PUT /api/gallery/collections/{id}/items/order`，Body `{ ids }`。
  更新对应集合的元数据。

## 3. 组件设计

### 3.1 集合管理弹窗 (`CollectionsDialog.svelte`)
- **别名展示与编辑**：
  - 在集合列表中，若 `collection.asset_alias` 存在，显示 `@${collection.asset_alias}` 标签（Emerald 风格轻量 chip）。
  - 提供别名设置入口（点击别名标签或操作区“设置别名”按钮），支持修改与一键清除。
  - 前端预检别名格式：正则校验 `^[\p{L}\p{N}_-]{1,32}$`（Unicode 字母数字下划线短横线），拒绝 `^round-\d+` 前缀。
- **成员顺序管理**：
  - 点击“管理成员”打开成员改序视图（内嵌面板或子模态）。
  - 加载 `loadReferences(collection.id)`。
  - 列表展示成员卡片，提供“上移”、“下移”按钮（或拖拽）。
  - 调整后调用 `reorderItems(collection.id, ids)` 提交更新。

### 3.2 资产包选图器 (`AssetPackPickerDialog.svelte`)
- **交互流程**：
  1. 用户在编辑源控制栏点击“从资产包添加”按钮打开弹窗。
  2. 弹窗左侧或顶部展示资产包列表（具有 `asset_alias` 的集合置顶标记），显示包名称、别名与图片数。
  3. 选中某个资产包后，加载该包的有效参考图片列表（已排除 SVG/GIF）。
  4. 图片以卡片网格形式展示，支持单张勾选，提供“全选”与“取消全选”。
  5. 底部实时展示：
     - 当前已选：`N` 张
     - 当前编辑源已有：`M` 张
     - 剩余可用槽位：`16 - M` 张
  6. 若选取的数量超出可用槽位，提示截断警告（如：“已超出可用上限，将仅添加前 X 张”）。
  7. 点击“确认添加”，将勾选的图片构造为 `EditGalleryReferenceSource[]`，调用 `editSourceStore.addGalleryReferences(refs)`，关闭弹窗并弹出成功 Toast。

### 3.3 编辑源选择器 (`EditSourcePicker.svelte`)
- 在“上传图片”按钮旁新增“从资产包添加”按钮（`<button type="button" onclick={openAssetPicker}>`，附带 `FolderArchive` 或 `PackagePlus` 图标）。
- 当已达到 16 张上限时置灰禁用，并显示已满提示。

## 4. 异常处理与边界测试

1. **别名冲突与格式校验**：
   - 输入带空格或特殊符号：前端输入框阻断或提示非法。
   - 输入重复别名（服务端 409）：捕获并向用户提示“该别名已被其他集合占用”。
2. **成员改序**：
   - 移动边界（第 0 项无法上移，末项无法下移）。
   - 网络异常时保留上一次有效顺序或提示重试。
3. **容量截断**：
   - 编辑器已有 14 张图，资产包内有 5 张图，用户全选添加时，准确添加 2 张并提示已截断。
