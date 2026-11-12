<div align="center">
  <br />
  <img src="frontend/static/favicon.svg" alt="GPT Image Panel logo" width="128" height="128" />

  <h1>GPT Image Panel</h1>

  <hr />

  <p>
    <strong>自托管的 GPT 兼容图像生成与编辑面板。</strong>
  </p>

  <p>
    <a href="./README.md">English</a> ·
    <a href="#简体中文">简体中文</a> ·
    <a href="./README.zh-TW.md">繁體中文</a>
  </p>

  <p>
    <img alt="CI passing" src="https://img.shields.io/badge/CI-passing-2cc653?logo=github&logoColor=white" />
    <img alt="Release v1.9.4" src="https://img.shields.io/badge/release-v1.9.4-0e8dcc" />
    <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white" />
    <img alt="Node.js 24" src="https://img.shields.io/badge/Node.js-24-339933?logo=node.js&logoColor=white" />
    <img alt="FastAPI 0.115+" src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white" />
    <img alt="SvelteKit 3" src="https://img.shields.io/badge/SvelteKit-3-FF3E00?logo=svelte&logoColor=white" />
    <img alt="License CC BY-NC 4.0" src="https://img.shields.io/badge/License-CC_BY--NC_4.0-6f42c1" />
    <img alt="Image GHCR" src="https://img.shields.io/badge/GHCR-gpt--image--linux-1f6f8b?logo=github&logoColor=white" />
  </p>
</div>

<a id="简体中文"></a>

## 概述

GPT Image Panel 是一个用于图像生成、编辑与图库管理的 Web 界面，数据保存在你自己的服务器上。它连接外部的 GPT 兼容图像 API，并把图片与元数据存入本地 SQLite。

本项目只是一个控制面板，不提供、不代理、不转售、也不修改任何上游模型服务。生成能力、计费、账号权限与内容策略均来自你配置的服务商。

## 功能

- **生成**：支持 `/v1/images/generations`、`/v1/responses` 以及 OpenAI 兼容的 `/v1/chat/completions`。
- **GPT Image 2.6**（`gpt-image-2.6-flare` / `gpt-image-2.6-sunburst`）：独立复杂度控制、矢量 SVG、动画 GIF 与标注引导编辑。同时支持 **GPT Image 2.5**，默认模型仍为 `gpt-image-2`。
- **编辑**：通过 `/v1/images/edits` 进行，内置蒙版编辑器（画笔/形状/套索、羽化、自动补洞）与网页端标注编辑器（画笔/箭头/形状/文字、100 步撤销）。蒙版编辑结果会贴回未修改的原图区域，并带颜色漂移防护。
- **参考素材包**：有序的图库收藏夹，可作为多图编辑的参考源，也可在 Agent 对话中用 `@别名` 引用。
- **API 预设**：base URL/path/key、健康检查、SOCKS5 代理、webhook、环境变量引用式密钥，以及能力开关（`supports_mask`、`prompt_guard`）。
- **AI Assistant**：提示词改写/检查/变体、参数推荐、任务诊断、编辑规划与图库图片分析。需要端点模型支持 function calling。
- **Agent 模式**：多轮对话中由模型规划并通过任务队列生成图片——支持批量、有依赖的后续轮次，以及用 `@` 引用修改历史图片。历史保存在服务端，流式回复可断线续传。
- **任务队列**：基于 SQLite，支持 SSE 进度、取消、重试、持久化历史与共享并发限制。连续点击生成会把结果追加到同一个预览组，而不是替换之前的结果；每个运行中的任务独立跟踪。
- **流式预览**（可选，1–10 张）：上游返回 `usage` 时，任务历史展示 token 用量与估算美元费用。
- **图库**：游标分页、搜索筛选、收藏、带封面概览的收藏夹、灯箱、批量操作、ZIP 导入导出与缩略图。
- **可选 Cloudflare R2** 备份同步；本地 SQLite 与图片仍是唯一真实来源。
- **安全**：访问密钥、IP/Host 白名单、可信代理头、CSRF 检查、CSP nonce，以及可选的 JSON/Prometheus 指标。

## 快速开始

### Docker Compose

```bash
cp .env.example .env
# 修改 .env：设置 ACCESS_KEY 与上游 API 凭据
docker-compose up -d --force-recreate
```

打开 `http://127.0.0.1:9090`。

Compose 绑定回环地址并使用明文 HTTP，本地使用时需关闭 Secure cookie：

```bash
ACCESS_COOKIE_SECURE=false ACCESS_KEY_COOKIE_NAME=gpt_image_access docker-compose up -d --force-recreate
```

通过 HTTPS 提供服务时请保持 `ACCESS_COOKIE_SECURE=true`。若本地测试想跳过访问校验，可留空 `ACCESS_KEY` 并设置 `ALLOW_UNAUTHENTICATED=true`——此时所有非健康检查接口都将公开。

### Docker

```bash
docker build -t gpt-image-panel .
docker run -d --name gpt-image-panel \
  -p 127.0.0.1:9090:9090 \
  -e ACCESS_KEY=change-me \
  -e ACCESS_COOKIE_SECURE=false \
  -e ACCESS_KEY_COOKIE_NAME=gpt_image_access \
  -v $(pwd)/images:/app/images \
  -v $(pwd)/data:/app/data \
  gpt-image-panel
```

镜像使用带哈希的 `requirements.lock` 安装依赖，每次构建版本完全一致。若 Docker Hub 访问缓慢，可替换基础镜像：

```bash
docker build \
  --build-arg PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.12-slim \
  --build-arg NODE_BASE_IMAGE=docker.m.daocloud.io/library/node:24-alpine \
  -t gpt-image-panel .
```

### 反向代理（Caddy）

```caddyfile
panel.example.com {
    reverse_proxy 127.0.0.1:9090
}
```

使用 HTTPS 时需设置匹配的来源与 Host 白名单：

```dotenv
PUBLIC_ORIGIN=https://panel.example.com
ALLOWED_HOSTS=panel.example.com
ACCESS_COOKIE_SECURE=true
```

Caddy 自动 HTTPS 要求域名解析到本机且 `80`/`443` 端口可达。若前面有 CDN 代理，请确认其边缘证书覆盖完整域名，否则浏览器可能在流量到达 Caddy 前就报 `ERR_SSL_VERSION_OR_CIPHER_MISMATCH`。Caddy 若运行在容器内，`127.0.0.1` 指向的是该容器，应改用应用服务名。

### 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| `400 Bad Request: Host is not allowed` | `Host` 头与 `ALLOWED_HOSTS` / `PUBLIC_ORIGIN` 不匹配。修正拼写后执行 `docker-compose up -d --force-recreate`。 |
| 明文 HTTP 下登录循环 | `ACCESS_COOKIE_SECURE=true` 会阻止 HTTP 存储 cookie。直接 HTTP 测试时设为 `false` 并覆盖 cookie 名。 |
| 保存预设失败 | 使用绑定相同用途与来源的密钥引用，或在合适场景下启用明文密钥。 |

## 配置

全部环境变量与默认值见 **[CONFIGURATION.md](CONFIGURATION.md)**；`.env.example` 列出了常用项。

配置按三层解析：

1. **环境变量** —— 进程启动时读取一次。
2. **Overall Config** —— 通过 `/api/settings/overall-config` 持久化到 SQLite 的覆盖值。可热加载的键立即生效，其余需重启。
3. **Web 设置** —— API 预设、提示词优化器、AI Assistant、R2 与 NodeImage 一经保存即从 SQLite 读取，此后对应的环境变量不再生效。

Compose 只转发 `docker-compose.yml` 中列出的变量。若密钥注册表引用了其他环境变量，需在服务环境中显式添加。

常用变量：

| 变量 | 用途 |
| --- | --- |
| `ACCESS_KEY` / `ALLOW_UNAUTHENTICATED` / `TURNSTILE_*` | 访问校验（除非允许未认证，否则必填）；可选 Cloudflare Turnstile。 |
| `DEFAULT_API_URL` / `DEFAULT_API_KEY` / `DEFAULT_API_PATH` | 默认上游预设。建议用 `${OPENAI_API_KEY}` 这类环境变量引用。 |
| `PUBLIC_ORIGIN` / `ALLOWED_HOSTS` | 反向代理 Host/CSRF 加固，必须与实际域名一致。 |
| `MAX_ACTIVE_GENERATE_JOBS` / `MAX_QUEUED_GENERATE_JOBS` | 并发图像单元上限与队列容量（超出返回 `429`）。 |
| `GRANIAN_WORKERS` | Worker 进程数，应与实际进程数一致。 |
| `IMAGES_DIR` / `DATA_DIR` / `DATABASE_FILE` / `LOG_LEVEL` | 存储路径与日志。 |
| `AI_ASSISTANT_*` / `PROMPT_OPTIMIZER_*` | Assistant 与优化器端点；Assistant 默认开启。 |
| `R2_*` / `NODEIMAGE_API_KEY` | 可选图库备份与 NodeImage 上传。 |
| `SECRET_REGISTRY_JSON` | 声明 `${ENV_VAR}` 引用及其用途、来源绑定。 |
| `ENABLE_METRICS` | 启用 JSON/Prometheus 指标。 |

开启 `ENABLE_METRICS=true` 后，`/api/metrics` 会暴露 SQLite 协调诊断：`sqlite.write_txn`、`sqlite.write_lock_wait_ms`、`sqlite.write_txn_hold_ms`（随 `MAX_ACTIVE_GENERATE_JOBS × GRANIAN_WORKERS` 增长时关注 p95）、`sqlite.busy_retries`，以及图像单元租约健康度（`lease_renewed`、`lease_lost`、`unit_reclaimed`）。`lease_lost` 应保持为 0。

## 使用

1. 打开面板，用 `ACCESS_KEY` 解锁。顶部按钮可在英文与简体中文间切换，按浏览器记忆。
2. 在**设置**中创建或选择 API 预设：base URL、路径、模型、响应格式，以及 API 密钥或环境变量引用。
3. 按需配置 SOCKS5 代理、webhook、提示词优化器、AI Assistant、R2 备份或 NodeImage 上传，保存后运行健康检查。
4. 输入提示词生成图片，或上传/选择源图进行编辑。
5. 使用**图库**复用、筛选、收藏、批量操作、导入导出与 R2 同步。

**预设快捷链接**：`/?apiUrl=https://api.example.com&apiModel=gpt-image-2` 会提示用该 URL 与模型创建预设。确认前不会保存任何内容，API 密钥保持为空，仅接受 `https`，参数会从地址栏移除。（`?model=` 是图库筛选参数，故此处用 `apiModel`。）

**预设分享**：**导出**下载不含密钥的版本化 JSON 包，**分享链接**复制 `?preset=` 链接，**导入**会先预览文件、剪贴板 JSON 或分享链接再应用。

**Agent 模式**：在设置 → AI Assistant 中启用并选择支持 function calling 的模型，然后在顶部切换到 **Agent**。用 `@round-1-image-1` 引用之前的图片，可附加图库图片，按 Ctrl/Cmd+Enter 发送。用 Stop 取消正在进行的回复。

### Agent 分支与联网搜索

**编辑消息 → 作为新分支发送**或**重新生成**会创建历史轮次的同级分支；**对话路径**可在刷新后恢复任意路径。切换路径不会发起请求也不会中止任务——编辑或重新生成前请先停止当前轮次。只有执行路径上的祖先会进入模型历史。图片 ID 保持稳定，但显示的轮次编号跟随当前路径。删除图库图片会使所有路径上的引用失效；删除对话则保留其图库图片。

联网搜索**默认关闭**。确认端点/模型支持 Responses 联网搜索后再启用**允许联网搜索**，该功能要求走 `/v1/responses` 路径。不兼容的配置会阻止提交，而不是静默地以关闭搜索的方式重试。上游标注提供的引用会随轮次保存，并在刷新后恢复。

### GPT Image 2.6

`gpt-image-2.6-flare` 适合快速迭代，`gpt-image-2.6-sunburst` 适合精细构图与精确编辑。两者均支持 `auto/low/medium/high/xhigh/max` 质量档位，**复杂度**独立于质量控制规划。

- **标注编辑**：在浏览器中绘制（**绘制标注**）或上传透明 PNG 图层。标注作为 `markup` 单独发送，绝不与原因合并。它必须与正向主图匹配、包含透明像素，且小于 50 MiB。标注与蒙版互斥，标注编辑不使用贴回。
- **SVG**：单个矢量结果；预览与下载保留矢量原件，图库使用栅格缩略图。含活动内容、内嵌图片或外部资源的 SVG 会被拒绝。
- **GIF**：2–64 帧，每帧 20–2000 ms（默认 9 帧 × 200 ms）。灯箱播放动画，缩略图显示首帧。
- SVG/GIF 仅用于生成——不支持参考图、蒙版、标注、流式预览或去色。SVG 限一个结果。
- 内置 2.6 费率：文本输入 $5/M、图像输入 $8/M、图像输出 $30/M tokens。SVG 另有创作者模型费用；无法获取时估算会标记为不完整。

Docker 镜像已包含 SVG 缩略图所需的 Cairo 运行时。本地开发需先安装 Cairo（`brew install cairo` 或 `apt-get install libcairo2`）。Apple Silicon 上若加载器找不到 Homebrew 的 Cairo，请以 `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` 启动后端。

### 蒙版编辑与贴回

编辑流程内置蒙版编辑器（画笔/橡皮擦/形状/套索、缩放、羽化、磁吸吸附），也可导入 PNG 蒙版。自动补洞与边缘平滑只会增加可编辑像素，不会删除已绘制区域。保存的蒙版写入 `MASKS_DIR`，重试时在确认源图仍匹配后恢复。

默认（`MASK_PASTE_BACK_DEFAULT=true`）只有蒙版内的像素来自模型，其余部分以羽化边界还原为未修改的原图。若模型改动了蒙版外的像素，颜色漂移防护会跳过贴回。可在单次编辑中关闭，或在预设中设 `supports_mask=false` 以适配拒绝蒙版上传的上游。

### 支持的上游路径

| 路径 | 说明 |
| --- | --- |
| `/v1/images/generations` | 标准生成；从 `data[]` 读取图像数据。 |
| `/v1/responses` | 从 `image_generation_call` 输出项读取 base64 图片。 |
| `/v1/chat/completions` | 从消息或 SSE 分块中提取图片 URL/base64。 |
| `/v1/images/edits` | 以 multipart 发送源图与支持的编辑参数。 |

`/v1/responses` 与 `/v1/chat/completions` 会禁用尺寸/质量/格式/压缩/数量/背景控制，因为这些路径的参数约定不同。

### 自定义异步供应商

接受任务后异步完成的网关可以用声明式 JSON 映射替代 OpenAI 路径。在设置中选择**自定义异步供应商**并粘贴网关的映射。

- **流程**：提交 → 轮询至终态 → 读取图片。路径仅支持 `$.a.b[0]` 与 `[*]`，不会作为代码执行。
- **版本**：v1 从响应中读取状态/结果 URL。v2 新增任务 ID 映射（`poll.task_id_path` + `poll.url_template`），适用于只返回 ID 的供应商；若提交响应已含图片，可设 `"mode": "sync"`。
- **编辑**：添加 `edit_submit` 段即声明支持编辑；已配置的蒙版或标注一定会被发送，无法承载它们的映射会在提交前被拒绝。
- **安全**：状态/结果/取消 URL 必须与预设同源并通过相同的 SSRF 检查。图片下载不携带凭据。
- **恢复**：单元会持久化远端任务 ID 与截止时间，Worker 崩溃后由其他 Worker 继续轮询而非重新提交。未记录响应的提交会标记为 `interrupted`，不会自动重提。
- **诊断**：失败的单元会保留脱敏记录——打开**任务历史 → 诊断**查看阶段、HTTP 状态与失败的映射路径。

### 流式预览与费用

流式预览对 Images 生成/编辑与 `/v1/responses` 可选开启，支持 1–10 张、1–3 帧中间图。每张图片仍是独立队列单元，受并发限制约束。若兼容的上游拒绝 `stream`/`partial_images`，任务会以明确错误失败，而不是静默地改用非流式重试——后者可能导致重复计费。

中间帧仅存于服务端内存（`PREVIEW_CACHE_MAX_ENTRY_MB` / `PREVIEW_CACHE_MAX_ENTRIES`，默认 8 MiB / 500 条）。重启或换到其他 Worker 后没有可重放的帧。

**估算费用不是账单。** 它仅根据上游实际返回的 `usage` 计算；缺少用量或模型价格时，界面会说明原因而不是显示 `$0.00`。可用 `IMAGE_COST_RATES_JSON` 覆盖费率。

### 图库与通知

- 桌面端可拖拽框选，或使用 Ctrl/⌘/Shift 多选。触屏上刻意横向滑动可切换卡片，纵向移动则滚动。
- 图库 ZIP 导出包含收藏夹名称、素材包别名与成员顺序。导入会创建新收藏夹、为名称/别名冲突添加后缀，并只恢复成功导入的成员。单次导入上限 10,000 张图片。
- 灯箱支持双指缩放、双击缩放，长按可下载/收藏/编辑。Escape 先关闭操作菜单再关闭查看器。
- **工作区偏好 → 任务完成时通知**默认关闭，启用时会请求权限。页面在后台时，每个图像任务或 Agent 轮次通知一次；点击可打开结果。需要在页面关闭后仍能送达的场景请使用 Webhook。
- 排队中的任务会冻结非密钥的服务商设置。修改预设的 API 来源会中止排队任务，而不是把更新后的凭据发往旧来源。

## 开发

### 项目结构

```text
backend/
  app/
    api/            # 公共 API 路由（contract_app.py）
    core/           # 设置、Overall Config
    integrations/   # 上游 API 客户端
    repositories/   # 持久化、SQLite 协调
    runtime/        # 阻塞/并发辅助
    schemas/        # DTO
    services/       # 编排
  tests/
frontend/
  src/lib/          # 可复用前端代码
  src/routes/       # SvelteKit 路由
  static/           # favicon
  tests/
deploy/nginx.conf
Dockerfile  docker-compose.yml  .env.example
```

### 架构

- **后端**：`backend/app/` 下的 FastAPI；ASGI 入口 `backend.app.main:app`，由 Granian 提供服务。
- **前端**：SvelteKit 静态应用；生产环境由后端提供 `frontend/build/`。
- **存储**：图片在 `images/`，缩略图在 `images/thumbs/`，SQLite 在 `data/app.sqlite3`，日志在 `data/logs/`。
- **多 Worker 协调**：任务、租约、SSE 槽位与调度器归属均使用 SQLite 租约。文件写入使用 UUID 文件名、原子 `Path.replace()` 与孤儿文件 GC 的 TTL 清理，而不依赖进程内锁。
- **技术栈**：Python 3.11+、FastAPI、Granian、aiohttp、boto3、SQLite、Pydantic v2、Pillow、CairoSVG · SvelteKit 3、TypeScript、Tailwind CSS 4 · Playwright、pytest。浏览器基线：Chrome 111+、Safari 16.4+、Firefox 128+。

### 本地开发

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt
npm --prefix frontend install
npm run backend:dev
```

另开一个终端：

```bash
npm run frontend:dev
```

打开 `http://localhost:5173`。Vite 会把 `/api` 与 `/health` 代理到 `127.0.0.1:9090` 的 FastAPI。

生产模式冒烟测试：

```bash
npm run frontend:build
ALLOW_UNAUTHENTICATED=true .venv/bin/granian --interface asgi backend.app.main:app --host 127.0.0.1 --port 9090
```

### 测试

```bash
npm run frontend:check
npm --prefix frontend run test:unit
.venv/bin/python -m pytest backend/tests -q
npm run test:contract
npm run test:e2e
```

`frontend:check`、`test:unit` 与后端测试套件无需构建即可运行。打包与 Worker 检查针对生产构建，需先执行 `npm run frontend:build`：

```bash
npm --prefix frontend run check:bundle
npm --prefix frontend run check:worker-build
```

可选的性能测试：

```bash
npm run test:perf
npm run test:e2e:perf
```

若缺少 Playwright 浏览器：`npm --prefix frontend exec playwright install chromium`。按改动范围运行相关子集；发布相关改动请全部运行。

### 贡献者约定

- 浏览器请求一律经 `/api/*` 同源访问——前端不得直接调用上游 API、R2、webhook 目标或任意图片 URL。
- 保持职责边界：路由在 `backend/app/api/routers/`，DTO 在 `schemas/`，持久化在 `repositories/`，上游客户端在 `integrations/`，前端镜像类型在 `frontend/src/lib/api/types/`。
- 除非有意做破坏性变更，否则保持公共契约稳定：路径、方法、状态码、cookie、SSE 事件名、响应结构、队列生命周期与取消语义。
- 保持校验集中：图片字节校验、安全路径、SSRF 相关 URL 处理与密钥脱敏。
- 新增环境变量时，需同步更新 `core/settings.py`、用户可见时更新 `core/overall_config.py`、`.env.example`、可在 Compose 配置时更新 `docker-compose.yml`，以及 `CONFIGURATION.md`。
- 切勿提交运行时产物：`images/`、`data/`、`frontend/build/`、`.svelte-kit/`、Playwright 报告、依赖目录、数据库或日志。

## 许可证

本项目采用 [CC BY-NC 4.0](./LICENSE)（Creative Commons Attribution-NonCommercial 4.0 International）许可。
