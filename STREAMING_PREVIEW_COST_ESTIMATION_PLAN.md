# 流式阶段性图片预览与费用估算实施计划

## 目标

为生成和编辑任务加入两项完整能力：

1. 对支持的 OpenAI 兼容图像接口启用流式响应，在任务完成前把阶段性图片预览显示在 `PreviewPanel` 中。
2. 解析上游 usage，计算可解释的美元费用估算，并在运行任务、任务详情和任务历史中展示 token、耗时和成本。

实现必须保留现有 SQLite 任务队列、SSE 重连、批量 `n`、图库落盘和非流式接口的兼容行为。

## 当前实现与约束

- `POST /api/generate` 只接收 `GenerateRequest`，后台 worker 在 `backend/app/services/job_executor.py` 中调用 `call_image_generation_api` 或 `call_image_edit_api`。
- 当前任务 SSE 只发送 `job`/`jobs` 事件；`frontend/src/lib/stores/jobs.ts` 将 job 状态写入 `previewStore`，`PreviewPanel.svelte` 展示最终图片和阶段文字。
- `generate_jobs` 和 `image_job_units` 已有 `duration`、`stage_timings_json`、多图计数和结果 JSON，但没有 usage/cost 字段。
- `backend/app/integrations/upstream/transport.py` 当前会把普通 JSON/SSE 响应整体解析后再提取图片，不能在响应过程中转发 partial image。
- 一个父任务的每个图片 unit 当前单独执行，父任务最终通过 `aggregate_image_job_units` 汇总。因此 usage 和 cost 应先记录到 unit，再由父任务求和。
- 阶段性 base64 图片可能很大，不能写入 SQLite，也不能放入普通 job 状态轮询结果。它们应作为有大小上限的短生命周期 SSE 事件传输。
- 不同 OpenAI 兼容服务的 usage 字段可能不同，不能把缺失或未知费率显示为 `$0`。

## 设计决定

### 1. 流式请求边界

- 在 `GenerateRequest` 增加可选字段：
  - `stream: bool = False`
  - `partial_images: int = 2`，限制为 `1..3`
- 只有 `/v1/images/generations` 和 `/v1/images/edits` 允许 `stream=true`；`/v1/responses` 与 `/v1/chat/completions` 保持现有非流式逻辑。
- 当 `n > 1` 时禁止流式，原因是当前队列将每个 unit 作为独立请求执行；后端返回明确的 422，而不是静默降级。
- 兼容服务拒绝流式参数时，将任务标记为 `upstream_error`，错误中说明可关闭流式；不自动发起第二次请求，避免重复计费。
- 流式事件只发送给订阅该 job 的 SSE 客户端，不进入 `/api/generate/jobs` 列表广播。

### 2. SSE 事件协议

新增事件：`preview`。

```json
{
  "job_id": "<parent-job-id>",
  "unit_index": 0,
  "partial_image_index": 0,
  "sequence": 3,
  "mime_type": "image/png",
  "data_url": "data:image/png;base64,..."
}
```

- `data_url` 只保存在内存队列中；单张事件设置最大字节数，超过限制时丢弃该预览并继续等待最终结果。
- `sequence` 用于丢弃旧事件，避免网络抖动导致预览倒退。
- 连接重建后，服务端可发送每个 unit 最近一次的内存预览；没有缓存时直接等待下一张。缓存随任务结束或进程重启清理。
- 保留现有 `job` 事件，并在终态 job 中发送最终 usage/cost；最终图片到达后前端清除 partial preview。

### 3. usage 与费用数据模型

新增一个可扩展的标准化结构，同时保留原始 usage：

```json
{
  "raw": {"...": "upstream response usage"},
  "input_tokens": 120,
  "output_tokens": 1024,
  "text_input_tokens": 120,
  "image_input_tokens": 0,
  "image_output_tokens": 1024,
  "total_tokens": 1144,
  "available": true
}
```

费用结构：

```json
{
  "currency": "USD",
  "estimated_cost_usd": 0.03072,
  "rate_source": "builtin|env|unknown",
  "pricing_model": "gpt-image-2",
  "complete": true,
  "reason": null
}
```

- 在 `backend/app/services/image_cost.py` 实现 usage 归一化和费用计算；使用 `Decimal`，最后统一保留 6 位小数。
- 支持 `input_tokens_details.text_tokens`、`input_tokens_details.image_tokens`、`output_tokens` 等已知形态，并保留未知字段到 `raw`。
- 费率放在后端配置的版本化映射中，并允许 `IMAGE_COST_RATES_JSON` 覆盖；每条 cost 记录 `rate_source` 和 `pricing_model`。
- 未知模型或未返回 usage 时，展示 usage（若存在），但 `estimated_cost_usd=null`、`complete=false` 并给出原因。
- 不根据图片数量或质量自行推算 token；只有上游返回 usage 时才报告实际估算。

## 实施步骤

### 阶段一：后端数据和费用服务

1. 新建 `backend/app/services/image_cost.py`：
   - 定义 `NormalizedUsage`、`CostEstimate` 的 TypedDict/dataclass。
   - 实现 `normalize_usage(raw_usage)`、`estimate_image_cost(model, usage)`、`sum_usage()`、`sum_costs()`。
   - 为空、负数、非数字和超大数字提供安全处理；不让异常 usage 影响任务完成。
2. 扩展 `backend/app/core/settings.py` 或独立配置模块：
   - 读取 `IMAGE_COST_RATES_JSON`。
   - 提供费率配置校验和当前 pricing version；启动时记录“已配置模型数”，不记录密钥或完整配置。
3. 在 `backend/app/schemas/generation.py` 增加：
   - `stream`、`partial_images` 请求字段及 `n=1`、API path 校验。
   - `UsageSummary`、`CostEstimate` 响应模型。
   - `GenerateJobStatus.usage`、`GenerateJobStatus.cost`、`GenerateJobStatus.streaming`、`GenerateJobStatus.partial_images`。
4. 在 `backend/app/repositories/db.py` 增加 nullable 列：
   - `generate_jobs.usage_json`、`generate_jobs.cost_json`、`generate_jobs.streaming`、`generate_jobs.partial_images`。
   - `image_job_units.usage_json`、`image_job_units.cost_json`。
   - 在现有 migration 函数中使用 `ALTER TABLE ... ADD COLUMN`，旧数据库不重建、不丢数据。
   - 更新列常量、整数列常量、序列化/反序列化和 `_normalize_generate_job`；旧行返回 `usage=null`、`cost=null`。

### 阶段二：上游流式解析与 worker 传递

1. 在 `backend/app/integrations/upstream/transport.py` 增加有界 SSE frame parser：
   - 按 `data:` 和空行切分，不把整个响应读入内存。
   - 校验 content type、单 frame 大小、总流大小、JSON 格式。
   - 识别 `image_generation.partial_image`、`image_edit.partial_image`、completed/done 事件和 usage。
2. 在 `backend/app/integrations/upstream/generation.py`：
   - 增加 `UpstreamImageResult`，包含 `entries`、`raw_usage`、`streamed`。
   - 为 generation/edit 增加 `preview` 回调，收到 partial image 时先解码并验证 MIME/大小，再回调 worker。
   - 流式请求发送 `stream=true` 和 `partial_images`；非流式路径完全复用当前解析。
   - usage 提取失败时不阻塞最终图片保存，但要记录 `available=false`。
3. 在 `backend/app/services/job_events.py`：
   - 增加 `publish_generate_job_preview(job_id, payload)`，只投递给该 job 的 subscribers。
   - 增加每 job、每 unit 的最近预览缓存，限制总内存、单图大小和条目数。
   - 任务终态、取消、异常和进程清理时删除缓存。
4. 在 `backend/app/services/job_executor.py`：
   - 为每个 unit 创建 preview callback，调用 `publish_generate_job_preview`。
   - 调用新的 `UpstreamImageResult`，把 usage 标准化并计算 unit cost。
   - 在 `complete_image_job_unit`/`fail_image_job_unit` 前写入 usage/cost；失败任务仍保留已收到的 usage。
   - 将 `duration` 与已有 `stage_timings` 保持现有格式，不以流式事件时间替代 worker 的真实耗时。
5. 在 `backend/app/repositories/image_jobs.py`：
   - 扩展 unit 完成、失败、读取和聚合逻辑。
   - `aggregate_image_job_units` 对 token 字段求和，对 cost 使用 Decimal 求和；按 unit 顺序保留 raw usage 列表或明确的 aggregate raw 标记。
   - 父任务状态响应包含聚合后的 usage/cost，保证历史分页和单 job 查询结果一致。

### 阶段三：前端请求和实时预览

1. 更新 `frontend/src/lib/api/types/generation.ts`、`jobs.ts`：
   - 添加请求开关和 `UsageSummary`、`CostEstimate`、`preview` SSE 类型。
2. 更新 `frontend/src/lib/stores/preview.ts`：
   - 在 `PreviewState` 增加 `streamingPreviewImages`、`activePreviewSequence`、`usage`、`cost`。
   - 任务切换、清除、终态和错误时正确重置，避免上一任务预览残留。
3. 更新 `frontend/src/lib/stores/jobs.ts` 和 `frontend/src/lib/api/events.ts`：
   - 允许 job SSE 监听 `preview` 事件。
   - 丢弃错误 job、错误 unit 或较旧 sequence 的事件。
   - SSE 断线时保留最后预览，重连后以最终 job 状态覆盖；沿用现有轮询 fallback。
4. 更新 `frontend/src/lib/components/PromptForm.svelte`：
   - 添加“流式预览”开关和 1/2/3 张预览数量。
   - `n > 1`、非 images generation/edit path 或 loading 时禁用，并展示原因。
   - 提示预览会增加上游输出 token，费用以最终 usage 为准。
5. 更新 `frontend/src/lib/components/PreviewPanel.svelte`：
   - 运行中优先显示最新 partial image，显示“阶段性预览”和 unit/index。
   - 显示 queued/running 阶段、已完成图片数和“费用待上游 usage 返回”。
   - 终态最终图片替换 partial image；无预览时继续显示现有 spinner/空状态。
   - 加入 `aria-live` 状态文本，不依赖颜色表达状态。

### 阶段四：任务历史和可解释费用

1. 更新 `frontend/src/lib/components/JobHistoryList.svelte`：
   - 每条历史任务显示完成时间、现有 duration、模型、图片数量、token 总量和估算费用。
   - 增加“usage / cost details”可展开区域，分别展示文本输入、图片输入、图片输出 token、费率来源和未提供原因。
   - cost 缺失显示“上游未提供 usage”或“未配置该模型费率”，禁止显示 `$0.000000`。
2. 更新 `frontend/src/lib/components/JobHistoryDrawer.svelte`：
   - 增加总图片数、总 token 和总估算费用；只对 `complete=true` 的 cost 求和。
   - 保留失败筛选、分页、虚拟列表和清空历史行为。
3. 更新 `frontend/src/lib/i18n/locales/en.ts`、`zh-CN.ts`：
   - 添加流式预览、partial image、usage、cost、pricing unavailable、unknown provider 等文案。
4. 如需要，在 `Lightbox.svelte` 的任务元数据区域补充该图片所属任务的 cost/usage 摘要；不要把完整 raw usage 默认展开。

### 阶段五：测试、观测和文档

1. 后端单元测试：
   - usage 各种字段形态、缺失值、未知字段、Decimal 舍入和费率覆盖。
   - SSE frame 解析、partial image 大小上限、畸形事件和流终止。
   - migration 在旧数据库上执行一次后可重复执行。
   - unit usage/cost 写入、父任务聚合、失败/取消和 `n>1` 求和。
2. 后端 contract 测试：
   - 默认 `stream=false` 的旧请求响应不变。
   - 允许的流式请求返回 `202`，job SSE 收到 `preview` 后收到终态 `job`。
   - 不允许的 path、`n>1`、`partial_images` 越界返回 422。
   - usage 缺失时历史 API 字段为 null，而不是 0。
3. 前端单元测试：
   - preview sequence 去重、任务切换清理、终态覆盖和 cost 格式化。
4. Playwright 测试（扩展 `frontend/tests/e2e/jobs-edit.spec.ts`、新建 `streaming-cost.spec.ts`）：
   - mock `preview` SSE 事件，验证预览图在最终图前出现。
   - 验证断线/轮询后最终结果不被旧 preview 覆盖。
   - 验证历史 token、耗时、成本详情和 unknown cost 文案。
   - 验证手机端开关、无横向溢出、触控尺寸和 `aria-live`。
5. 观测：
   - 增加 `image_job.streaming_requested`、`streaming_preview_received`、`streaming_preview_dropped`、`usage_missing`、`cost_unknown_rate` 计数器。
   - 指标和日志只记录 job id、model、事件计数和大小，不记录 prompt、base64、API key 或 raw 响应。
6. 文档：
   - 在 README 和 `.env.example` 说明流式支持范围、费用估算不是账单、费率覆盖方式、未知模型行为和内存限制。

## 推荐提交顺序

1. `feat: add usage and cost models for image jobs`
2. `feat: parse streamed image previews from upstream`
3. `feat: display streamed previews in workspace`
4. `feat: show usage and cost in job history`
5. `test: cover streaming preview reconnect and cost aggregation`
6. `docs: document streaming and cost estimation settings`

每一步都应保持可构建、可回滚；前端在后端尚未部署新字段时必须继续正常显示旧任务。

## 验收标准

- 默认关闭流式时，现有 generation/edit、SSE、图库和任务历史行为不变。
- 开启流式且上游支持时，用户能在最终图片前看到至少一张 partial preview；最终图片到达后 partial preview 被替换。
- `n=1` 流式约束、非支持 API path 和参数越界均有可理解的前端禁用状态或 422 错误。
- 历史任务能显示耗时；上游提供 usage 且模型有费率时显示分项 token 和估算美元成本。
- usage 缺失、模型未知或费率未配置时不会伪造零成本，并能解释原因。
- `n>1` 任务的 usage、cost、成功/失败计数按 unit 正确聚合。
- SSE 断线、页面刷新、worker 重启和旧数据库升级不会泄露 base64、破坏任务历史或产生重复计费请求。
- `.venv/bin/python -m pytest backend/tests -q`、`npm run frontend:check`、相关 contract 测试和新增 Playwright 测试全部通过；测试期间启动的服务在结束后停止。

## 风险与处理

| 风险 | 处理 |
|---|---|
| 兼容服务声称支持 OpenAI 但不支持 partial image | 返回结构化 upstream error，提供关闭流式的操作提示，不自动重试 |
| partial base64 占用过多内存 | 单图/单 job/全局上限，丢弃预览事件但继续保存最终图 |
| usage 字段格式不统一 | 归一化器保留 raw，未知字段不参与计算并显示原因 |
| 费率过时或第三方费率不同 | 记录 pricing version 和 rate source，支持环境变量覆盖，并明确标注“估算” |
| SSE 重连收到旧 preview | 使用 `sequence` 和 job/unit 校验，终态 job 优先级最高 |
| 多 unit 聚合重复计算 | 仅在 unit 完成时写入 usage/cost，父任务只汇总 unit 持久化值 |

## 文件级执行清单

### 后端

| 文件 | 需要完成的改动 | 验收点 |
|---|---|---|
| backend/app/schemas/generation.py | 增加 stream、partial_images、UsageSummary、CostEstimate，以及 GenerateJobStatus 的 usage/cost/streaming 字段 | 旧请求仍可通过；非法 stream 参数返回 422 |
| backend/app/core/settings.py | 增加 IMAGE_COST_RATES_JSON、preview 单事件大小、preview cache 总大小等配置 | 配置缺失时使用安全默认值 |
| backend/app/services/image_cost.py | 新增 usage 归一化、费用估算、usage 汇总、cost 汇总 | 不因畸形 usage 抛出未处理异常 |
| backend/app/repositories/db.py | 为 generate_jobs 和 image_job_units 添加 usage_json、cost_json、streaming、partial_images 相关列和 migration | 旧数据库重复启动不报错，旧行字段为空 |
| backend/app/repositories/image_jobs.py | complete/fail unit 写入 usage/cost；aggregate_image_job_units 汇总父任务 usage/cost | n 大于 1 的父任务统计正确 |
| backend/app/integrations/upstream/transport.py | 新增有界 SSE frame parser，按 data 行解析 JSON 事件 | malformed、超限、错误事件都有受控错误 |
| backend/app/integrations/upstream/generation.py | generation/edit 支持 stream=true 和 partial_images；提取 partial image、最终 data、usage | 非流式路径保持现有逻辑 |
| backend/app/services/job_events.py | 新增 preview event 发布、短期内存缓存和终态清理 | preview 只发给单 job 订阅者 |
| backend/app/api/routers/generate.py | 单 job SSE 支持 preview event，并在重连时补发最近 preview | jobs 列表 SSE 不发送 base64 preview |
| backend/app/services/job_executor.py | 串接 preview callback、usage/cost 计算、unit 写入和父任务聚合 | 终态 job 包含最终 images、usage、cost |
| .env.example | 记录费率覆盖和 preview 限制配置 | 部署者知道费用只是估算 |
| README.md / README.zh-CN.md / README.zh-TW.md | 更新功能、限制、费用估算说明 | 文档与 UI 行为一致 |

### 前端

| 文件 | 需要完成的改动 | 验收点 |
|---|---|---|
| frontend/src/lib/api/types/generation.ts | GenerateRequestBody 增加 stream、partial_images | TypeScript 调用点无类型错误 |
| frontend/src/lib/api/types/jobs.ts | 增加 UsageSummary、CostEstimate、GeneratePreviewEvent | job 和 preview SSE 类型清晰 |
| frontend/src/lib/api/events.ts | 支持 job stream 的 preview event name | 不影响 gallery job SSE |
| frontend/src/lib/stores/preview.ts | PreviewState 增加 streaming preview、usage、cost；提交/清除/终态时重置 | 旧任务 preview 不残留 |
| frontend/src/lib/stores/jobs.ts | trackJob 同时监听 job 和 preview，处理 sequence 去重和轮询 fallback | 断线后最终 job 仍覆盖 preview |
| frontend/src/lib/components/PromptForm.svelte | 添加流式预览开关和 1/2/3 预览数量选择 | n 大于 1 或 unsupported path 时禁用并解释 |
| frontend/src/lib/components/PreviewPanel.svelte | 运行中展示最新 partial image、阶段、完成数量、usage 等待状态 | 最终图出现后替换 preview |
| frontend/src/lib/components/JobHistoryDrawer.svelte | 添加总图片、总 token、总估算费用摘要 | 只统计已知费用并标注 estimated |
| frontend/src/lib/components/JobHistoryList.svelte | 每条历史展示 duration、token、estimated cost 和详情展开 | unknown cost 不显示为 0 |
| frontend/src/lib/utils/format.ts | 增加 token、美元费用、unknown reason 格式化 helper | 中英文显示稳定 |
| frontend/src/lib/i18n/types.ts | 增加新增文案类型 | locale 类型同步 |
| frontend/src/lib/i18n/locales/en.ts | 增加英文文案 | 英文 UI 完整 |
| frontend/src/lib/i18n/locales/zh-CN.ts | 增加中文文案 | 中文 UI 完整 |
| frontend/tests/e2e/fixtures/mockApi.ts | EventSource mock 支持 preview event；job fixture 支持 usage/cost | e2e 可覆盖 preview 到 final 的过程 |

## 推荐开发顺序

### Step 1：usage/cost 后端基础

先完成 schema、DB columns、image_cost.py、非流式 usage 提取、unit 写入和父任务聚合。这个阶段不做流式 UI。完成后，普通生成任务的历史应能显示 usage/cost；没有 usage 时显示 unavailable。

### Step 2：任务历史 UI

接入前端类型、JobHistoryList 和 JobHistoryDrawer 的 token/cost 展示。这个阶段仍不要求上游 stream，只使用已有 job history API。完成后，历史列表应能展示 duration、token、estimated cost 和详情展开。

### Step 3：后端上游流式和 preview SSE

实现 transport.py 的 SSE parser、generation.py 的 stream=true 请求、job_events.py 的 preview 发布，以及 generate.py 单 job SSE 的 preview 转发。完成后，后端 contract 测试应能收到 preview event，然后收到终态 job event。

### Step 4：前端流式预览 UI

在 PromptForm 增加开关，在 jobs store 接收 preview event，在 preview store 保存最新 preview，在 PreviewPanel 展示运行中的 partial image。完成后，Playwright mock 场景应验证 partial image 先出现，最终图到达后替换。

### Step 5：文档、观测和完整回归

补齐 README、.env.example、metrics 和日志策略，最后跑完整后端、前端类型检查、unit、contract 和 Playwright 测试。

## 关键实现细节

- preview event 不进入 generate_jobs 表，也不进入 image_job_units.result_json，避免 SQLite 膨胀。
- preview event 不进入 jobs 列表 SSE，只进入 /api/generate/{job_id}/events。
- preview cache 使用 job_id 加 unit_index，只保存最近一次 preview；终态、取消、异常后清理。
- 上游流式失败不自动重试非流式，因为重试可能造成二次计费。
- 费用估算只基于上游 usage；缺 usage、未知模型或缺费率时显示原因。
- 父任务 cost 汇总应能表达 partial estimate，不能把未知部分当 0。
- 前端终态 job 的优先级高于 preview；收到最终 images 后应清空 streamingPreviewImages。
- EventSource 断线后继续沿用现有 polling fallback；polling 不提供 preview，但必须能拿到最终 job。
- raw usage 可通过 API 返回给 UI 详情，但日志、metrics、toast 不记录 raw usage。
- 新字段全部 nullable，保证旧任务历史和旧数据库兼容。

## 测试矩阵

| 层级 | 场景 | 预期 |
|---|---|---|
| Backend unit | normalize_usage 处理标准 OpenAI usage | token 字段正确归一化 |
| Backend unit | usage 缺失或字段异常 | 返回 unavailable，不抛未处理异常 |
| Backend unit | unknown model cost | cost.complete 为 false，reason 解释缺费率 |
| Backend unit | IMAGE_COST_RATES_JSON 覆盖 | 使用 env rate_source |
| Backend unit | SSE parser 收到 partial + completed | yield preview 和 final usage/data |
| Backend unit | SSE parser malformed event | 抛受控 UpstreamApiError |
| Backend contract | stream=false 旧请求 | 响应与旧逻辑兼容 |
| Backend contract | stream=true 且 n=1 | enqueue 成功，SSE 可发 preview |
| Backend contract | stream=true 且 n>1 | 422 |
| Backend contract | stream=true 且 /v1/responses | 422 |
| Backend persistence | unit 写入 usage/cost | get_generate_job 返回父任务聚合值 |
| Frontend unit | preview sequence 旧事件 | 被丢弃 |
| Frontend unit | 收到终态 job | partial preview 被清空 |
| Playwright | preview event 后 final job | UI 先显示预览再显示最终图 |
| Playwright | 历史 cost unknown | 显示 unknown reason，不显示 0 美元 |
| Playwright | mobile viewport | 无横向溢出，控件可点击 |

## 验收命令

按仓库约定，Python 命令使用项目虚拟环境：

~~~sh
.venv/bin/python -m pytest backend/tests/test_image_cost.py -q
.venv/bin/python -m pytest backend/tests/test_upstream_streaming.py -q
.venv/bin/python -m pytest backend/tests/test_generate_contract.py -q
.venv/bin/python -m pytest backend/tests -q
npm run frontend:check
npm --prefix frontend run test:unit
npm run test:e2e -- streaming-cost.spec.ts jobs-edit.spec.ts
~~~

测试完成后，停止为测试启动的本地服务，包括 Vite 5173 和后端 9090。

## 回滚策略

- 新增 DB 列全部 nullable，回滚应用代码后可保留列，不需要迁移回退。
- 流式开关默认关闭；如有问题，可先隐藏前端开关并让后端拒绝 stream=true。
- 若费用费率不确定，可先只展示 usage，将 cost 标记为 incomplete。
- 若 preview 内存压力偏高，可把 preview cache limit 降到 0，让任务继续非预览式运行。
