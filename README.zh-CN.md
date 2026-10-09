<div align="center">
  <br />
  <img src="frontend/static/favicon.svg" alt="GPT Image Panel 标志" width="128" height="128" />

  <h1>GPT Image Panel</h1>

  <hr />

  <p><strong>自托管 GPT 兼容图像生成和编辑面板。</strong></p>

  <p>
    <a href="./README.md#english">English</a> ·
    简体中文 ·
    <a href="./README.zh-TW.md">繁體中文</a>
  </p>

  <p>
    <img alt="CI 通过" src="https://img.shields.io/badge/CI-passing-2cc653?logo=github&logoColor=white" />
    <img alt="版本 v1.7.12" src="https://img.shields.io/badge/release-v1.7.12-0e8dcc" />
    <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white" />
    <img alt="Node.js 24" src="https://img.shields.io/badge/Node.js-24-339933?logo=node.js&logoColor=white" />
    <img alt="FastAPI 0.115+" src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white" />
    <img alt="SvelteKit 2" src="https://img.shields.io/badge/SvelteKit-2-FF3E00?logo=svelte&logoColor=white" />
    <img alt="许可证 CC BY-NC 4.0" src="https://img.shields.io/badge/License-CC_BY--NC_4.0-6f42c1" />
    <img alt="GHCR 镜像" src="https://img.shields.io/badge/GHCR-gpt--image--linux-1f6f8b?logo=github&logoColor=white" />
  </p>
</div>

## 概述

GPT Image Panel 是一个轻量级 Web UI，用于图像生成、图像编辑、图库管理和本地持久化。它连接用户配置的外部 GPT 兼容图像 API，并把图片与元数据保存到自己的服务器。

本项目只是自托管控制面板，不提供、不代理、不转售、不修改任何上游模型/API 服务。实际生成能力、计费、账号权限、内容政策和模型行为都来自你配置的上游服务商。

## 功能

- 支持 `/v1/images/generations`、`/v1/responses`、OpenAI 兼容 `/v1/chat/completions` 图像生成。
- 支持 `/v1/images/edits` 图像编辑，可用上传参考图或 Gallery 图片作为源图；内置蒙版编辑器（画笔/橡皮擦/形状/套索、缩放、羽化、蒙版导入、自动补洞/边缘平滑）。蒙版随任务持久化并支持重试恢复，结果默认贴回未编辑的原图区域，并带颜色漂移防护。
- API 预设管理：base URL/path/key、默认模型、response format、健康检查、SOCKS5 代理、webhook、环境变量引用式密钥。
- 提示词助手标签、可复用提示词片段、可选服务端提示词优化器，以及 AI Assistant 子系统（提示词改写/检查/变体、参数推荐、任务诊断、编辑规划、Gallery 图片分析）。
- Agent 对话模式：多轮对话中由模型规划并通过现有任务队列生成图片（批量并发、有依赖的后续轮次、用 `@` 引用修改历史图片），对话历史保存在服务端，流式回复可断线续传，所有图片进入 Gallery。需要 AI Assistant 端点使用支持 function calling 的模型。
- SQLite 任务队列：SSE 进度、取消、重试/复用、持久化历史、阶段耗时，以及生成/编辑共享并发限制。
- 可选流式阶段性图片预览，支持 `/v1/images/generations`、`/v1/images/edits`、`/v1/responses`（1–10 张），通过 SSE 在上游生成过程中推送。任务历史展示每个任务的 token 用量，并在上游返回 usage 且该模型已配置费率时显示估算美元费用。
- 本地 Gallery：游标分页、搜索/筛选、收藏、Lightbox、selection token 批量操作、ZIP 导入导出、缩略图、大小统计、异步导出/导入任务。
- 可选 Cloudflare R2 Gallery 备份同步；本地 SQLite 和图片文件仍是唯一源数据。
- 访问密钥、IP/Host 白名单、可信反向代理头、CSRF 检查、CSP nonce、版本检查、可选 JSON/Prometheus metrics。

## 架构

- 后端：`backend/app/` 下的 FastAPI；ASGI 入口是 `backend.app.main:app`。
- 前端：`frontend/` 下的 SvelteKit 静态应用；生产后端服务 `frontend/build/`。
- 运行时存储：图片默认在 `images/`，缩略图在 `images/thumbs/`，SQLite 数据在 `data/app.sqlite3`，日志在 `data/logs/`。
- 多 worker 协调：排队任务、后台 lease、SSE slot 和调度器所有权使用 SQLite lease。图片/缩略图文件写入仅使用进程内锁，并通过 UUID 文件名、原子 `Path.replace()` 和孤儿文件 GC TTL 清理容忍跨进程竞争。
- 关键模块：公共 API 路由在 `backend/app/api/`（契约见 `contract_app.py`），DTO 在 `schemas/`，持久化在 `repositories/`，上游客户端在 `integrations/`，配置在 `core/settings.py` 和 `core/overall_config.py`。

## 技术栈

Python 3.11+、FastAPI、Granian、aiohttp（+aiohttp-socks）、boto3、SQLite、Pydantic v2、Pillow、python-multipart、zipstream-ng · SvelteKit、TypeScript、Tailwind CSS 4 · Playwright、pytest。浏览器最低版本：Chrome 111、Safari 16.4、Firefox 128。

## 项目结构

```text
backend/
  app/
    api/            # 公共 API 路由（contract_app.py）
    core/           # 设置、Overall Config
    integrations/   # 上游 API 客户端
    repositories/   # 持久化、SQLite 协调
    runtime/        # 阻塞/并发辅助
    schemas/        # DTO
    services/       # 编排逻辑
  tests/
frontend/
  src/lib/          # 可复用前端代码
  src/routes/       # SvelteKit 路由
  static/           # favicon
  tests/
deploy/nginx.conf
Dockerfile  docker-compose.yml  .env.example
requirements.txt  requirements.lock  package.json
images/  data/      # 运行时输出（生成）
```

## 快速开始

### Docker Compose

```bash
cp .env.example .env
# 修改 .env：至少设置 ACCESS_KEY，并按需填默认上游 API
# 此示例通过回环地址使用 HTTP，需要禁用 Secure cookie
ACCESS_COOKIE_SECURE=false docker-compose up -d --force-recreate
```

打开 `http://127.0.0.1:9090`。

此本地 HTTP 示例需要设置 `ACCESS_COOKIE_SECURE=false`；通过 HTTPS 提供服务时应保持为 `true`。默认必须设置 `ACCESS_KEY`。仅本地测试时，清空 `ACCESS_KEY` 并设置 `ALLOW_UNAUTHENTICATED=true`，这会让所有非 health API 都不需要鉴权。如需为解锁流程额外启用 Cloudflare Turnstile 人机验证，在 `.env` 中设置 `TURNSTILE_ENABLED=true` 以及来自 Cloudflare 控制台的 `TURNSTILE_SITE_KEY` / `TURNSTILE_SECRET_KEY`；验证组件会出现在 Unlock 按钮下方，每次使用访问密钥登录都需要有效的 token。

### Docker

```bash
docker build -t gpt-image-panel .
docker run -d --name gpt-image-panel \
  -p 127.0.0.1:9090:9090 \
  -e ACCESS_KEY=change-me \
  -e ACCESS_COOKIE_SECURE=false \
  -v $(pwd)/images:/app/images \
  -v $(pwd)/data:/app/data \
  gpt-image-panel
```

Docker Hub 慢或不可访问时：

```bash
docker build \
  --build-arg PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.12-slim \
  --build-arg NODE_BASE_IMAGE=docker.m.daocloud.io/library/node:24-alpine \
  -t gpt-image-panel .
```

镜像从带哈希的 `requirements.lock`（由 `requirements.txt` 用 `uv pip compile` 生成）安装 Python 依赖，保证每次构建、两种架构版本一致。

### Caddy 反向代理

Caddy 与应用运行在同一台主机时，使用占位域名，并继续将 `9090` 端口仅绑定到回环地址：

```caddyfile
panel.example.com {
    reverse_proxy 127.0.0.1:9090
}
```

通过 HTTPS 部署时，在 `.env` 中设置与域名匹配的应用来源和 Host 白名单：

```dotenv
PUBLIC_ORIGIN=https://panel.example.com
ALLOWED_HOSTS=panel.example.com
ACCESS_COOKIE_SECURE=true
```

只有一个上游时，推荐使用上面的基础反代配置。如果确实需要主动健康检查，请先确认 `/health` 在相同 `Host` 请求头下返回 `200`，然后使用复数形式的 `health_headers` 配置块：

```bash
curl -i -H 'Host: panel.example.com' http://127.0.0.1:9090/health
```

```caddyfile
panel.example.com {
    reverse_proxy 127.0.0.1:9090 {
        health_uri /health
        health_interval 15s
        health_timeout 3s
        health_status 200

        health_headers {
            Host panel.example.com
        }
    }
}
```

健康检查响应状态码不在配置范围内时，Caddy 会将上游标记为不健康。只有一个上游时，这会导致请求失败，直到健康检查恢复。修改后验证并重载 Caddy：

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy 自动 HTTPS 要求域名解析到服务器，并且入站 `80`、`443` 端口可访问。使用 Cloudflare 等 CDN 代理时，应确保边缘证书明确覆盖完整域名，特别是多级子域名；否则请求尚未到达 Caddy，浏览器就可能报告 `ERR_SSL_VERSION_OR_CIPHER_MISMATCH`。如果 Caddy 运行在容器内，`127.0.0.1` 指向 Caddy 容器自身，此时应改用应用服务名或其他可访问的容器网络地址。

### 常见部署问题排查

1. **容器启动闪退并报错 `SecretRegistryError: credentials require a non-empty startup host allowlist`** — 在 `.env` 中配置了 `DEFAULT_API_KEY`，但未配置 `UPSTREAM_HOST_ALLOWLIST`。在 `.env` 中将 `DEFAULT_API_URL` 对应的纯域名填入 `UPSTREAM_HOST_ALLOWLIST`（例如 `UPSTREAM_HOST_ALLOWLIST=api.openai.com` 或 `UPSTREAM_HOST_ALLOWLIST=cf.api.fan`）。
2. **浏览器访问提示 `400 Bad Request: Host is not allowed`** — 请求的 `Host` 头未在 `.env` 的 `ALLOWED_HOSTS` 或 `PUBLIC_ORIGIN` 白名单中（常见于域名拼写错误，或修改 `.env` 后未重新创建容器）。确保两者与域名完全一致，修改 `.env` 后必须执行 `docker compose up -d --force-recreate` 重新创建容器生效。
3. **直连 HTTP（无反代/未配 SSL）环境下无法登录或陷入登录循环** — 默认 `ACCESS_COOKIE_SECURE=true` 要求必须走 HTTPS，纯 HTTP 下浏览器会拒绝保存/携带 Secure Cookie。纯 HTTP 直连测试时，在 `.env` 中设置 `ACCESS_COOKIE_SECURE=false`；部署 HTTPS 反代后再改为 `true`。
4. **网页端点击「保存预设」无法保存、侧边栏不关闭** — 默认禁止直接在 Web 界面向数据库持久化明文 API Key。若要在网页端直接粘贴明文 Key，需在 `.env` 中设置 `ALLOW_PLAINTEXT_SECRETS=true` 并重启容器。若修改了预设中的 API URL，新域名必须同时加入 `.env` 的 `UPSTREAM_HOST_ALLOWLIST` 白名单。

### 本地开发

先使用本机的 Python 3.11+ 创建项目专用虚拟环境。`.venv` 属于本地开发环境，仓库不提供该目录。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt
npm --prefix frontend install
npm run backend:dev
```

另开终端：

```bash
npm run frontend:dev
```

打开 `http://localhost:5173`。Vite 会把 `/api` 和 `/health` 代理到 `127.0.0.1:9090`。

生产风格 smoke test：

```bash
npm run frontend:build
ALLOW_UNAUTHENTICATED=true .venv/bin/granian --interface asgi backend.app.main:app --host 127.0.0.1 --port 9090
```

## 配置

大多数运行时配置在 `.env.example`。API 预设、提示词优化器、R2 备份和部分应用/运行时配置也可通过 Web Settings / Overall Config 管理。关键变量：

| 变量 | 用途 |
| --- | --- |
| `ACCESS_KEY` / `ALLOW_UNAUTHENTICATED` / `TURNSTILE_*` | 访问密钥（除非清空并设置 `ALLOW_UNAUTHENTICATED=true`，否则必填）；`TURNSTILE_*` 可选 Cloudflare Turnstile 人机验证。 |
| `DEFAULT_API_URL` / `DEFAULT_API_KEY` / `DEFAULT_API_PATH` / `DEFAULT_RESPONSES_MODEL` | 默认上游预设。密钥建议用 `${OPENAI_API_KEY}` 这类 env ref。 |
| `UPSTREAM_HOST_ALLOWLIST` | 配置上游 API key 时必填；列出允许的上游域名。 |
| `PUBLIC_ORIGIN` / `ALLOWED_HOSTS` | 反向代理 Host/CSRF 加固；必须与部署域名一致。 |
| `MAX_ACTIVE_GENERATE_JOBS` / `MAX_QUEUED_GENERATE_JOBS` | 全局运行中 image unit 上限与队列容量（超出后新任务返回 `429`）。 |
| `IMAGE_JOB_UNIT_LEASE_SECONDS` / `IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS` / `IMAGE_JOB_UNIT_MAX_ATTEMPTS` | 运行中 image unit 的 SQLite 租约（崩溃检测延迟）、续租周期与最大领取次数。 |
| `GRANIAN_WORKERS` / `GRANIAN_*` | Worker 进程数与生产运行时调优；应等于实际进程数。 |
| `VISION_PREVIEW_MEMORY_BUDGET_MB` | 视觉预览解码预算：默认每进程 **256 MiB**，范围 **32–16384**。Agent 超出保守内存估算预算的图片跳过视觉预览，文字引用、原图及生成/编辑仍可用。 |
| `IMAGE_CPU_CONCURRENCY` / `FILE_IO_CONCURRENCY` / `UPSTREAM_MEMORY_BUDGET_MB` / `MAX_UPSTREAM_IMAGE_BYTES_PER_TASK_MB` | 解码、文件 I/O 并发与上游图片内存的有界预算。 |
| `DB_EXECUTOR_WORKERS` / `SQLITE_BUSY_*` / `SQLITE_CRITICAL_BUSY_*` / `SQLITE_SLOW_TXN_WARN_MS` | SQLite 执行器大小与 busy 重试预算；关键的 claim/lease/finalize 写入使用更大预算。 |
| `IMAGES_DIR` / `MASKS_DIR` / `THUMBNAILS_DIR` / `THUMBNAIL_*` / `DATA_DIR` / `DATABASE_FILE` / `LOG_DIR` / `LOG_LEVEL` / `LOG_RETENTION_HOURS` | 存储路径、缩略图控制与日志（轮转文件默认保留 24 小时）。 |
| `MASK_PASTE_BACK_DEFAULT` | 是否默认把蒙版编辑结果贴回未编辑的原图区域；可在单次编辑请求中覆盖。 |
| `MAX_SSE_SUBSCRIBERS_GLOBAL` / `MAX_SSE_SUBSCRIBERS_PER_IP` / `SSE_CONNECTION_TTL_SECONDS` | SSE slot 限制和最大连接生命周期。 |
| `PROMPT_OPTIMIZER_*` | 可选服务端提示词优化器配置。 |
| `AI_ASSISTANT_*` | AI Assistant 默认启用（设置 `AI_ASSISTANT_ENABLED=false` 关闭）；API URL、密钥、模型、超时、allowlist 复用 `PROMPT_OPTIMIZER_*`。 |
| `AGENT_*` | Agent 对话模式的各项上限；模式本身在“设置 → AI 助手”中开启，复用 `PROMPT_OPTIMIZER_*` 端点。 |
| `R2_*` | 可选 Cloudflare R2 Gallery 备份同步；自定义 endpoint host 需要配置 `R2_ENDPOINT_HOST_ALLOWLIST`。 |
| `NODEIMAGE_API_KEY` | 可选 NodeImage API key，用于服务端 Gallery 图片上传。 |
| `IMAGE_COST_RATES_JSON` | 覆盖/新增费用估算使用的内置单模型美元费率。 |
| `APP_VERSION` / `GITHUB_REPO` / `ENABLE_VERSION_CHECK` / `VERSION_CHECK_CACHE_SECONDS` | 版本显示与最新 release 检查。 |
| `ENABLE_NGINX_ACCEL_REDIRECT` / `PUBLIC_IMAGE_BASE_URL` / `PUBLIC_THUMBNAIL_BASE_URL` | 可选 nginx/CDN 图片字节服务行为。 |
| `ENABLE_METRICS` | 启用 JSON/Prometheus metrics 接口。 |
| `SECRET_REGISTRY_JSON` / `ALLOW_PLAINTEXT_SECRETS` / `ALLOW_LEGACY_ENV_REFS` | 密钥处理：`${ENV_VAR}` 引用必须在 `SECRET_REGISTRY_JSON` 中声明；明文 secret 写入 SQLite 需 `ALLOW_PLAINTEXT_SECRETS=true`；`ALLOW_LEGACY_ENV_REFS=true` 是弃用的迁移开关。 |

配置解析分三层：环境变量（进程启动时读取一次）；通过 `/api/settings/overall-config` 持久化到 SQLite 的 Overall Config override（可热更新的键立即生效，`restart_required` 的键下次启动生效）；标记为 `exposed_in_settings` 的名称（API 预设、提示词优化器、AI Assistant、R2、NodeImage），一旦通过 Web Settings 保存就从 SQLite 读取——此后同名环境变量不再生效。少数设置（如 `DB_EXECUTOR_WORKERS`、`AI_ASSISTANT_MAX_CONCURRENCY`、`IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS`）由其他值派生并自动重算。已知限制：运行时修改 `DB_EXECUTOR_WORKERS`、`IMAGE_CPU_CONCURRENCY` 或 `FILE_IO_CONCURRENCY` 不会调整已创建的线程池。

开启 `ENABLE_METRICS=true` 后，`/api/metrics` 提供 SQLite 协调调优诊断：`sqlite.write_txn`、`sqlite.write_lock_wait_ms`、`sqlite.write_txn_hold_ms`（写事务速率与锁等待/持有时间，p50–p99/max——随着 `MAX_ACTIVE_GENERATE_JOBS x GRANIAN_WORKERS` 增长应关注 p95）；`sqlite.busy`、`sqlite.busy_retries`（锁竞争下失败/重试的加锁次数）；`image_jobs.lease_renewed`、`lease_lost`、`unit_reclaimed`、`unit_exhausted`（image unit 租约健康度；`lease_lost` 应保持为零）。

## 使用

1. 打开面板。
2. 如启用访问密钥，先用 `ACCESS_KEY` 解锁。
3. 打开 Settings，创建或选择 API 预设。
4. 设置 API base URL、API path、模型、response format 和 API key/env ref。
5. 按需配置 SOCKS5 代理、webhook、提示词优化器、AI Assistant、R2 备份、NodeImage 上传或 Overall Config override。
6. 保存预设，必要时执行健康检查。
7. 输入 prompt 生成图片，或上传/选择源图执行编辑。
8. 在 Gallery 中复用参数、筛选、收藏、批量操作、导入导出、执行 R2 同步。
9. 可以把 `/?apiUrl=https://api.example.com&apiModel=gpt-image-2` 存为书签。打开后会提示用该 URL 和模型新建预设；确认横幅会突出显示目标主机，并提醒仅为可信主机输入 API 密钥。确认前不会保存任何内容，API 密钥保持为空，并且这些参数会立即从地址栏移除。只接受 `https` URL，也不会从 URL 读取任何凭据。（`?model=` 已被 Gallery 筛选占用，所以参数名是 `apiModel`。）
10. 用「导出」把所选预设下载为带版本号、不含密钥的 JSON 包；用「分享链接」复制 `?preset=` 链接；用「导入」先预览文件、剪贴板 JSON 或分享链接，再确认应用。预设可通过拖拽手柄或上下箭头排序，顺序保存在 SQLite 中。
11. 按需在「设置 → AI 助手」中开启 Agent 模式（选择支持 function calling 的模型），然后在页眉切换到 **Agent**。描述需求，用 `@`（如 `@round-1-image-1`）引用之前的图片，也可从 Gallery 添加图片，按 Ctrl/Cmd+Enter 发送。运行中可点「停止」取消；生成的图片会出现在 Gallery。

### Agent 分支、Markdown 与可选联网搜索

助手回复支持标题、列表、表格、代码和链接。原始 HTML 显示为文本，外部 Markdown 图片不自动加载，链接只允许不含凭据的 HTTP(S) 地址。

使用「编辑消息 → 发送为新分支」或「重新生成」创建历史回合的同级分支。「对话路径」可切换并在刷新后恢复原路径或新路径；切换不会发起模型请求，也不会停止运行中的任务。编辑或重新生成前需要先停止活动回合。模型历史只包含执行路径的祖先；图片 ID 和 `@round-N-image-M` 引用保持稳定，显示轮次及 `@第N轮图M` 按当前路径解释。跨分支复用图片时，请从 Gallery 显式添加附件。图库删除会使所有路径上的引用失效；删除对话仍保留图库图片。

联网搜索**默认关闭**。在「设置 → AI 助手」中声明当前端点及模型支持 Responses 联网搜索，再开启「允许联网搜索」；继承的提示词优化接口必须采用 `/v1/responses` 且模型兼容，请先查阅供应商文档确认支持。搜索关闭时，chat/completions 继续使用既有函数工具；启用搜索但配置不兼容时会在提交前提示并阻止发送（API 返回 422）。搜索失败会显示错误，不会自动关闭搜索并重试。

搜索进度及上游 annotations 提供的来源会随回合保存，行内引用编号对应「来源」列表及引用片段，刷新、SSE 重放和切换路径后可恢复。普通 Markdown 链接不会变成来源。搜索遵守既有工具轮次、超时、取消及分支历史预算；缺少 usage 时不编造费用。数据库迁移 37/38 在启动时自动应用。

## GPT Image 2.5

生成表单和预设设置提供 `gpt-image-2.5-flare`（快速日常生成）与 `gpt-image-2.5-sunburst`（精细编辑）。默认模型仍为 `gpt-image-2`，保留已保存预设和自定义模型；通过「自定义模型 / 快照」可输入两个模型的 `-2026-09-08` 快照名称。

文生图使用 `/v1/images/generations`，上传参考图或选择图库图片后使用 multipart `/v1/images/edits`。两个变体均支持 `auto/low/medium/high/xhigh/max` 质量；旧模型和自定义模型沿用原有质量选项，切换至不支持当前质量的模型时恢复为 `auto`。

- 生成、编辑、助手和提示词收藏最多支持 32,000 个 Unicode 字符（提示词优化器共用同一上限，除非明确配置了 `PROMPT_OPTIMIZER_MAX_OUTPUT_CHARS`）。
- 尺寸为 `auto` 或 `宽x高`：边长须是 16 的倍数且不超过 3840，宽高比不超过 3:1，总像素为 655,360–8,294,400；超过 2560×1440 属于实验性支持。
- 输出支持 PNG/JPEG/WebP；压缩参数（0–100，默认 100）仅用于 JPEG/WebP。透明背景需要 PNG/WebP，JPEG 配合透明背景会自动切换为 PNG。
- 2.5 始终返回 Base64：面板保存图片并提供本地 URL，忽略旧预设继承的 `response_format`；其他模型沿用原有返回格式行为。
- 编辑最多接受 16 张 PNG/JPEG/WebP 输入（单张小于 50 MB，受应用上传限制约束，其他格式请先转换）；1–10 张输出沿用现有队列，每个执行单元请求一张图片。

本次仅通过 Images API 接入——不包含官方 Responses 网关格式和 Chat Completions，但蒙版编辑与贴回（见下文）与其他模型一致。上游账号需要具备模型访问权限；更高质量可能消耗更多 token，两个变体相同的 token 单价不代表每张图片费用相同。

规范核对日期：2026-09-10。[官方指南](https://developers.openai.com/api/docs/guides/image-generation) · [生成接口](https://developers.openai.com/api/reference/resources/images/methods/generate) · [编辑接口](https://developers.openai.com/api/reference/resources/images/methods/edit)。

## 蒙版编辑与贴回

- 编辑流程内置蒙版编辑器（画笔、橡皮擦、形状/套索工具、缩放、羽化、磁性吸附），用于精确绘制编辑区域；也可导入已有 PNG 蒙版（黑白蒙版以白色为可编辑区域，软透明蒙版以透明度低于 50% 为可编辑区域，同比例导入会自动缩放匹配）。自动补洞与边缘平滑默认开启，且只会增加可编辑像素、不会移除已绘制区域，两者都实时反映在预览与覆盖率读数中。
- 保存的蒙版（PNG，最大 4 MB）随任务持久化到磁盘（`MASKS_DIR`）；重试时会先校验源图仍然匹配再恢复蒙版，源图变化则拒绝重试。
- 默认情况下，蒙版编辑结果会贴回原图（`MASK_PASTE_BACK_DEFAULT=true`）：只保留模型输出中蒙版范围内的像素，其余部分还原为未编辑的原图并做羽化融合，若模型改动了蒙版外的像素则由颜色漂移防护跳过贴回。可按次关闭贴回，预览面板也可在结果与原图间切换以检查边界。
- API 预设携带 `supports_mask` 开关（默认开启），关闭后为不支持蒙版上传的上游隐藏蒙版编辑功能。
- Gallery 图片记录 `mask_coverage`、`paste_back` 状态、`paste_back_scale`，可通过 `mask_only` 筛选蒙版编辑图片。

## 支持的上游路径

| 路径 | 说明 |
| --- | --- |
| `/v1/images/generations` | 标准图片生成接口，从 `data[]` 读取图片数据。 |
| `/v1/responses` | 发送 `prompt` 和 `model`，从 `image_generation_call` 输出项读取 base64 图片。 |
| `/v1/chat/completions` | 发送 OpenAI 兼容 chat completions 请求，从消息或 SSE chunk 中提取图片 URL/base64。 |
| `/v1/images/edits` | Edits 流程使用，发送 multipart 源图和支持的编辑参数。 |

使用 `/v1/responses` 和 `/v1/chat/completions` 时，尺寸、质量、格式、压缩率、数量控件会禁用，因为这些路径的参数契约不同。

## 自定义异步供应商

有些网关会先接收任务、返回状态/结果 URL，稍后才完成。预设可以用声明式 JSON 映射代替 OpenAI 路径：在设置中选择**自定义异步供应商**，再粘贴你的网关对应的映射（下面的示例列出了全部字段；输入框为空时会以灰色显示同样的骨架）。

```json
{
  "version": 1,
  "auth": {"header": "Authorization", "scheme": "Key"},
  "submit": {"path": "/{{model}}", "body": {"prompt": "{{prompt}}", "num_images": "{{n}}", "image_size": {"width": "{{width}}", "height": "{{height}}"}}},
  "poll": {"url_path": "$.status_url", "status_path": "$.status", "done": ["COMPLETED"], "failed": ["FAILED"], "interval_seconds": 2, "timeout_seconds": 600},
  "result": {"url_path": "$.response_url", "images_path": "$.images[*].url", "image_kind": "url"},
  "cancel": {"url_path": "$.cancel_url", "method": "PUT"}
}
```

- **流程**：把渲染后的请求体 `POST` 到 `API URL + submit.path`，从响应里取状态 URL，轮询直到 `status_path` 的值属于 `done`（或 `failed`），按需再请求 `result.url_path`，最后从 `images_path` 读取图片（URL 或 base64）。不会把任何内容当作代码执行；路径只支持 `$.a.b[0]` 和 `[*]`。
- **变量**：`prompt`、`model`、`n`、`width`、`height`、`size`、`quality`、`output_format`、`background`。值只是一个没有内容的变量（例如尺寸为 `auto` 时的 `width`）时，该字段会被省略。`submit.path` 只能使用 `{{model}}`。
- **仅返回任务 ID 的映射（version 2）**：若供应商只返回任务 ID，可设置 `"version": 2`，用 `poll.task_id_path` + `poll.url_template`（`result`/`cancel` 同理），例如 `{"task_id_path": "$.data.id", "url_template": "/v1/jobs/{{task_id}}"}`。模板必须是纯路径，任务 ID 会按路径段进行 URL 编码，绝对 URL 会被拒绝。可选 `submit.idempotency_header`（如 `Idempotency-Key`）让中断的提交用同一稳定键重试。**同步模式**：提交响应里已包含图片时，v2 映射可设置 `"mode": "sync"`——省略 `poll` 和 `cancel`，用 `result.images_path` 从提交响应读取图片。
- **方法与查询映射**：`submit.method` 和 `poll.method` 接受 `GET` 或 `POST`（GET 提交必须为空请求体；POST 轮询发送空 JSON 对象）。`submit.query`、`edit_submit.query` 和 `poll.query` 用模板映射 URL 查询参数——提交用 submit 变量，轮询用 `{{task_id}}`；字面值只允许字母、数字和 `- _ . ~`。
- **图片编辑（edit_submit）**：添加 `edit_submit` 段即声明编辑支持。Multipart 编辑将校验后的参考图和蒙版作为文件分片上传（`files.images` 每张图片一个分片、`files.mask`）；JSON 编辑在 `edit_submit.body` 内联 `{{reference_images}}`（有界 data URL 列表）和可选的 `{{mask}}`（一个 data URL）。编辑复用面板的上传校验、大小上限、蒙版预处理、取消和结果贴回；配置了蒙版就一定会发送——无法携带蒙版的映射会在提交前被拒绝。
- **能力声明**：`capabilities.transparent_background` 控制 `background: transparent`（省略时面板会从 `{{background}}` 用法自动检测），`capabilities.formats` 收窄可接受的输出格式；未声明的请求会在提交前被明确拒绝。`stream` 保持保留位。色键背景仍可用于生成——上游收到不透明背景和键控提示词，面板在本地移除颜色。
- **凭据**：映射里不放任何密钥。预设的 API 密钥（环境变量引用或 Secret Registry ID）按 `header: scheme key` 发送；`header` 为 `Authorization` 或 `X-API-Key`，`scheme` 为 `Bearer`、`Key`、`Token` 或留空。
- **安全**：状态、结果和取消 URL 来自上游响应，因此必须与预设 API URL 同源（密钥会随请求发送），并通过与提交 URL 相同的 SSRF 和对端 IP 检查。图片下载不带凭据，错误信息会脱敏。
- **恢复**：提交成功后，任务单元会持久化远端任务 ID、后续地址、绝对轮询截止时间、幂等键和非密钥映射快照。Worker 意外退出后，由其他 Worker 继续轮询同一任务，而不是重新提交；截止时间和接管次数跨重启保留（默认最多接管 5 次）。提交已发出但结果未记录时，任务标记为 `interrupted` 并带 `submit_unknown` 诊断，不会自动重新提交。丢失租约不会取消远端任务；用户主动取消会尽力发送一次 `cancel`。轮询遇到限流、5xx 或断网会在截止时间内有上限地退避重试。
- 健康检查只校验映射并探测提交 URL，不会真正提交任务。设置中可复制「映射生成提示词」交给 LLM，并对粘贴的映射做实时校验；粘贴模拟响应还能在本地验证任务 ID、状态和图片提取（不会请求上游）；校验结果同时列出解析出的能力。
- **诊断**：失败的任务单元会保留一份受限、脱敏的提交/轮询/结果阶段记录。在「任务历史 → 诊断」中可查看阶段、HTTP 状态、失败的映射路径和响应快照，并复制诊断信息用于排查。

## 流式预览与费用估算

- 流式预览需主动开启，支持 Images 生成/编辑端点和 `/v1/responses`，数量为 1–10。每张图片仍作为独立单元排队，遵守现有并发限制。可选 1–3 个中间帧，实际帧数与用量取决于上游；Responses 图片按输出调用归属，文字 delta 不会成为图片预览。声明式自定义供应商映射仍要求 `capabilities.stream: false`。
- 如果兼容服务拒绝 `stream`/`partial_images` 参数，任务会以明确错误结束，而不是自动降级为非流式重试，以免重复计费。
- 中间图片只存在服务端内存中（每个执行单元/输出调用保留最新一帧，受 `PREVIEW_CACHE_MAX_ENTRY_MB`/`PREVIEW_CACHE_MAX_ENTRIES` 限制，默认 8 MiB / 500 项）；慢 SSE 客户端的帧会合并。最终图只替换自己的槽位，失败、取消与完成会清理预览。重连先恢复任务快照再回放缓存帧，重启或切换 worker 后无预览可回放。同一次请求返回有效 JSON 最终结果时会直接接受，不重新提交。
- **费用估算不是账单。** 仅当上游返回 `usage` 时才会计算；缺少 usage 或未配置费率时会显示原因而非 `$0.00`。内置费率覆盖 `gpt-image-1` 与 GPT Image 2 / 2.5，取自 OpenAI 官方定价——第三方上游通常并不相同。可通过 `IMAGE_COST_RATES_JSON` 覆盖或新增费率。

## 图库、触摸操作与完成通知

- 桌面可拖动框选或按 Ctrl/⌘/Shift 选择卡片；触屏上的明确横向侧滑每次切换一张图片，纵向移动保留滚动。全筛选范围选择会保留服务端 selection token，直至主动选择「退出全选」。
- 收藏夹概览从列表响应取得封面、数量和默认标记，无需逐个请求图片详情；进入收藏夹保留其他筛选。空收藏夹和封面缺失仍可管理与导出 ZIP。
- Lightbox 支持双指缩放、放大后拖动、双击缩放及长按下载/收藏/编辑菜单；Escape 先关闭操作菜单，再关闭查看器。
- 「工作区偏好 → 任务完成通知」默认关闭，仅主动启用时请求权限。安全上下文中的支持浏览器在页面处于后台时按图片父任务或 Agent 轮次汇总通知，并说明部分失败数量；点击打开结果或对话。Web Locks 与有界 localStorage 历史协调多标签页及重放，旧浏览器为尽力协调。页面须持续运行，关闭后的集成通知使用 Webhooks。
- 排队任务冻结非密钥供应商配置，凭据仍从当前预设读取；修改 API origin 后任务会明确失败，避免将新凭据发送给保存的旧地址。

## API 概览

核心后端路由（按域分组）：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/health`、`/api/access/status`、`/api/version`、`/api/version/latest` | 健康检查、访问状态、当前/最新版本。 |
| `POST` | `/api/access` | 使用访问密钥解锁面板。 |
| `GET/PUT` | `/api/settings/overall-config` | 读取/保存 Overall Config override。 |
| `GET/POST` | `/api/settings` | 读取/保存当前预设、提示词优化器、R2 备份、代理和 webhook 设置。 |
| `POST` | `/api/settings/presets`（+ `/{preset_id}/activate`、`/health`、`DELETE /api/settings/presets/{preset_id}`） | 创建、激活、校验、删除 API 预设；`POST /api/settings/r2/health` 校验草稿 R2 备份设置。 |
| `GET/POST`、`PATCH/DELETE` | `/api/prompt-snippets`（+ `/search`、`/{snippet_id}`） | 列出/创建、搜索、更新/删除可复用提示词片段。 |
| `GET/POST` | `/api/prompt/optimizer-system-prompt`；`POST /api/prompt/optimize`、`/api/prompt/optimizer-health` | 读取/保存优化器 system prompt；优化提示词或探测优化器连通性。 |
| `POST` | `/api/assistant/health`、`/api/assistant/prompt/rewrite\|check\|variants`、`/api/assistant/generate/recommend-params`、`/api/assistant/jobs/{job_id}/diagnose`、`/api/assistant/edit/plan`、`/api/assistant/image/prompt`、`/api/assistant/image/prompt/optimize`；`POST/GET /api/assistant/gallery/*` | 探测 Assistant 连通性；提示词改写/检查/变体；参数推荐；任务诊断与编辑规划；本地图片反推 prompt；Gallery 描述/分析/批量操作。 |
| `GET/POST` | `/api/agent/conversations`；`GET/PATCH/DELETE /api/agent/conversations/{conversation_id}`；`POST .../turns`；`PATCH .../branch` | 列出/创建、读取/重命名/删除对话（删除对话保留 Gallery 图片）；发送消息（`client_turn_id` 保证幂等，返回 `202`）；选择持久化路径。 |
| `GET` | `/api/agent/turns/{turn_id}`（+ `/events`）；`POST /api/agent/turns/{turn_id}/cancel` | 读取回合或订阅可回放的 SSE 事件；停止运行中的回合并取消其排队图片任务。 |
| `POST` | `/api/generate`、`/api/edits`、`/api/edits/from-gallery/{image_id}` | 创建生成/编辑任务（编辑支持可选 PNG 蒙版和贴回偏好）。 |
| `GET`、`GET/DELETE` | `/api/generate/jobs`（+ `/events`）、`/api/generate/{job_id}`（+ `/events`）；`DELETE /api/generate/jobs/history` | 查询实时任务/历史、任务列表或单任务 SSE、读取/取消单个任务、清理终态历史。 |
| `GET`、`POST` | `/api/gallery`、`/api/gallery/search`；`GET/DELETE /api/gallery/{image_id}`；`PATCH /api/gallery/{image_id}/favorite` | 列出/搜索/筛选 Gallery（含 `mask_only`）、读取/删除/收藏图片。 |
| `POST/PATCH` | `/api/gallery/batch/*`；`POST /api/gallery/thumbnails/status` | selection token、收藏、删除、下载等批量操作；缩略图存在/状态检查。 |
| `POST`、`GET` | `/api/gallery/nodeimage-upload-jobs`、`/api/gallery/export-jobs`、`/api/gallery/direct-export-jobs`、`/api/gallery/sync-jobs`、`/api/gallery/import-jobs`（各带 `/{job_id}`、`/events`，以及适用的 `DELETE`/`cancel` 或 `/download`） | NodeImage 异步上传、Gallery ZIP 导出、R2 同步、导入任务的状态/SSE 跟踪。 |
| `GET` | `/api/image/{filename}`、`/api/thumb/{filename}`、`/api/download/{filename}`、`/api/download-all?export_job_id=` | 返回鉴权图片字节、缩略图、单张下载与流式 ZIP 导出。 |
| `POST` | `/api/import` | 导入 Gallery ZIP；`async_job=true` 会创建导入任务。 |
| `GET` | `/api/metrics`、`/api/metrics/prometheus` | `ENABLE_METRICS=true` 时可用。 |

公共 API 已有契约测试；除非明确做 breaking change，否则保持路径、方法、状态码、SSE 事件名、cookie 和响应结构稳定。

## 贡献者边界

- 浏览器请求保持同源 `/api/*`；不要在前端直接调用上游模型 API、R2、webhook 目标或任意图片 URL。
- 保持现有代码分层：路由与请求编排在 `backend/app/api/routers/`，DTO 在 `backend/app/schemas/`，持久化与 SQLite 协调在 `backend/app/repositories/`，上游集成在 `backend/app/integrations/`，前端镜像 API 类型在 `frontend/src/lib/api/types.ts`。
- 除非明确要做 breaking change，否则保持这些公共契约稳定：API 路径、方法、状态码、cookie、SSE 事件名、响应结构；生成/编辑队列生命周期、取消语义、多 worker 下的 SQLite 协调。文件系统竞争通过 UUID 文件名、原子替换和孤儿文件 GC 来容忍；不要依赖进程内锁实现跨 worker 互斥。
- 校验与安全逻辑保持集中：图片字节校验、安全路径、缩略图/归档 helper；SSRF 敏感 URL 处理继续放在 validators、safe connector、integration client 中；前端可见 secret 只能是打码值或 env-ref 元数据。
- 保持现有运行时约束：编辑任务最多接受 16 张 raster 源图，外加最多 4 MB 的单张 PNG 蒙版；Gallery ZIP 导入导出继续沿用现有安全限制；SSE 使用 SQLite slot lease、全局/单 IP 限制和连接 TTL；R2 同步只是备份——本地 SQLite 记录和本地图片文件始终是源数据。
- 新增或修改环境变量时，同步更新 `backend/app/core/settings.py`、用户可见时的 `backend/app/core/overall_config.py`、`.env.example`、可由 Compose 配置时的 `docker-compose.yml`，以及本 README。
- 不要提交运行时/生成产物，例如 `images/`、`data/`、`frontend/build/`、`.svelte-kit/`、Playwright 报告、测试结果、依赖目录、本地 DB 文件或日志。

## 测试

运行后端或契约测试前，请先激活项目本地 `.venv`。npm 的契约/性能测试脚本会使用 `.venv/bin/python`。

```bash
npm run frontend:check
npm run frontend:build
.venv/bin/python -m pytest backend/tests -q
npm run test:contract
npm run test:e2e
npm run test:perf
npm run test:e2e:perf
```

普通改动跑相关子集即可；大范围或发布前改动跑全套。

如果缺少 Playwright 浏览器：

```bash
npm --prefix frontend exec playwright install chromium
```

## 贡献

实现边界和贡献者需要遵守的不变量，以上面的「贡献者边界」为准。提交改动时只跑与你改动范围匹配的最小验证集合；如果改了行为或环境变量定义，README 和配置文件要一起更新。

## 许可证

本项目采用 `CC BY-NC 4.0`（`Creative Commons Attribution-NonCommercial 4.0 International`）许可证。

见 [LICENSE](./LICENSE)。
