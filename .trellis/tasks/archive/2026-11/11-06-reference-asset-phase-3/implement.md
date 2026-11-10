# Implementation Plan - Phase 3: 选图器与资产管理

## Execution Steps

### 1. 契约类型与 Store 扩展 (`types/gallery.ts` & `galleryCollections.ts`)
- [x] 在 `frontend/src/lib/api/types/gallery.ts` 的 `GalleryCollection` 中添加 `asset_alias?: string | null`。
- [x] 在 `frontend/src/lib/api/types/gallery.ts` 中新增 `GalleryCollectionReferencesResponse` 类型定义。
- [x] 在 `frontend/src/lib/stores/galleryCollections.ts` 中实现：
  - `setAlias(id: string, alias: string | null): Promise<GalleryCollection>`：调用 `PATCH /api/gallery/collections/{id}`，捕获 409（别名已被占用）与 422（格式错误）。
  - `loadReferences(id: string): Promise<GalleryCollectionReferencesResponse>`：调用 `GET /api/gallery/collections/{id}/references`。
  - `reorderItems(id: string, ids: string[]): Promise<GalleryCollection>`：调用 `PUT /api/gallery/collections/{id}/items/order`。

### 2. 国际化翻译扩展 (`locales/zh-CN.ts` & `locales/en.ts`)
- [x] 在 `frontend/src/lib/i18n/locales/zh-CN.ts` 与 `en.ts` 中添加词条：
  - 资产包别名展示与提示（`assetAlias`, `setAlias`, `clearAlias`, `aliasPlaceholder`, `aliasConflict`, `aliasInvalid`）。
  - 成员改序与管理（`manageItems`, `itemOrderUpdated`）。
  - 选图器弹窗（`addFromAssetPack`, `selectAssetPack`, `assetPackEmpty`, `addSelectedReferences`, `slotsAvailable`, `slotsFull`）。

### 3. 集合管理弹窗增强 (`CollectionsDialog.svelte`)
- [x] 在集合列表中展示 `asset_alias` 徽章。
- [x] 增加别名设置/清除弹层或行内表单，前端校验格式与拦截非法字符，支持清空。
- [x] 增加集合成员改序面板：加载参考图片列表，提供上移/下移按钮，调用 `reorderItems` 保存。

### 4. 资产包选图器组件与集成 (`AssetPackPickerDialog.svelte` & `EditSourcePicker.svelte`)
- [x] 新建 `frontend/src/lib/components/gallery/AssetPackPickerDialog.svelte`：
  - 接收 `open`、`onClose`、`onAdd(refs)` 参数。
  - 集合列表筛选与选中：优先列出资产包。
  - 选中集合后调用 `galleryCollectionsStore.loadReferences` 加载图片网格。
  - 支持按张勾选及“全选/取消全选”。
  - 实时显示当前容量与剩余槽位，超额给出截断提示。
- [x] 在 `EditSourcePicker.svelte` 增加“从资产包添加”触发按钮，点击打开选图弹窗。
- [x] 在 `Workspace.svelte` 挂载 `AssetPackPickerDialog` 并处理选中的参考图，调用 `editSourceStore.addGalleryReferences` 批量追加。

### 5. 自动化测试与验证
- [x] 编写前端单测 `frontend/tests/unit/galleryCollectionsAsset.test.ts`：
  - 测试 `setAlias` 成功、清空与 409 冲突处理。
  - 测试 `loadReferences` 与 `reorderItems`。
- [x] 编写选图器与集成测试或扩展 `editSource.test.ts`。
- [x] 运行代码检查与测试：
  - `cd frontend && npm run check`
  - `cd frontend && npm run test:unit`
