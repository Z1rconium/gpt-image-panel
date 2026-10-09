<div align="center">
  <br />
  <img src="frontend/static/favicon.svg" alt="GPT Image Panel 標誌" width="128" height="128" />

  <h1>GPT Image Panel</h1>

  <hr />

  <p><strong>自架式 GPT 相容圖片生成與編輯面板。</strong></p>

  <p>
    <a href="./README.md#english">English</a> ·
    <a href="./README.zh-CN.md">簡體中文</a> ·
    繁體中文
  </p>

  <p>
    <img alt="CI 通過" src="https://img.shields.io/badge/CI-passing-2cc653?logo=github&logoColor=white" />
    <img alt="版本 v1.7.12" src="https://img.shields.io/badge/release-v1.7.12-0e8dcc" />
    <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white" />
    <img alt="Node.js 24" src="https://img.shields.io/badge/Node.js-24-339933?logo=node.js&logoColor=white" />
    <img alt="FastAPI 0.115+" src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white" />
    <img alt="SvelteKit 2" src="https://img.shields.io/badge/SvelteKit-2-FF3E00?logo=svelte&logoColor=white" />
    <img alt="授權 CC BY-NC 4.0" src="https://img.shields.io/badge/License-CC_BY--NC_4.0-6f42c1" />
    <img alt="GHCR 映像" src="https://img.shields.io/badge/GHCR-gpt--image--linux-1f6f8b?logo=github&logoColor=white" />
  </p>
</div>

## 概述

GPT Image Panel 是一套輕量 Web UI，可用於圖片生成、圖片編輯、圖庫管理與本機持久化。它會連線至使用者設定的外部 GPT 相容圖片 API，並將圖片與中繼資料儲存在自己的伺服器。

本專案僅提供自架式控制面板，不提供、代理、轉售或修改任何上游模型/API 服務。實際生成能力、計費、帳號權限、內容政策及模型行為，皆由使用者設定的上游服務商決定。

## 功能

- 透過 `/v1/images/generations`、`/v1/responses` 或 OpenAI 相容的 `/v1/chat/completions` 生成圖片。
- 透過 `/v1/images/edits` 編輯圖片，可使用上傳的參考圖或 Gallery 圖片作為來源；內建遮罩編輯器（畫筆/橡皮擦/形狀/套索、縮放、羽化、遮罩匯入、自動補洞/邊緣平滑）。遮罩隨工作持久化並可在重試時還原，結果預設貼回未編輯的原圖區域，並具備色彩漂移防護。
- API 預設管理：base URL/path/key、預設模型、response format、健康檢查、SOCKS5 Proxy、webhook 與環境變數參照式密鑰。
- 提示詞輔助標籤、可重複使用的提示詞片段、選用的伺服器端提示詞最佳化器，以及 AI Assistant 子系統（提示詞改寫/檢查/變體、參數建議、工作診斷、編輯規劃與 Gallery 圖片分析）。
- Agent 對話模式：多輪對話中由模型規劃並透過既有工作佇列產生圖片（批次並行、有相依性的後續輪次、以 `@` 引用修改歷史圖片），對話歷史儲存在伺服器端，串流回覆可斷線續傳，所有圖片都會進入 Gallery。需要 AI Assistant 端點使用支援 function calling 的模型。
- SQLite 工作佇列：SSE 進度、取消、重試/沿用、持久化歷史、階段耗時，以及生成/編輯共用的並行限制。
- 選用的串流階段性圖片預覽，支援 `/v1/images/generations`、`/v1/images/edits`、`/v1/responses`（1–10 張），透過 SSE 在上游生成過程中推送。工作歷史顯示每個工作的 token 用量，並在上游回傳 usage 且該模型已設定費率時顯示估算美元費用。
- 本機 Gallery：游標分頁、搜尋/篩選、收藏、燈箱導覽、selection token 批次操作、ZIP 匯入/匯出、縮圖、位元組大小資訊，以及非同步匯入/匯出工作。
- 選用的 Cloudflare R2 Gallery 備份同步；本機 SQLite 與圖片檔案仍是唯一真實來源。
- 存取密鑰、IP/Host 允許清單、可信任 Proxy Header、CSRF Origin 檢查、CSP nonce、版本檢查，以及選用的 JSON/Prometheus 指標。

## 架構

- 後端：`backend/app/` 下的 FastAPI；ASGI 進入點為 `backend.app.main:app`。
- 前端：`frontend/` 下的 SvelteKit 靜態應用程式；正式環境由後端提供 `frontend/build/`。
- 執行期儲存：圖片預設位於 `images/`、縮圖位於 `images/thumbs/`、SQLite 資料位於 `data/app.sqlite3`、日誌位於 `data/logs/`。
- 多 Worker 協調：排隊工作、背景 Lease、SSE Slot 與排程器所有權使用 SQLite Lease。圖片/縮圖檔案寫入僅使用程序內鎖，並透過 UUID 檔名、原子 `Path.replace()` 與孤兒檔案 GC TTL 清理容忍跨程序競爭。
- 關鍵模組：公開 API 路由位於 `backend/app/api/`（契約見 `contract_app.py`），DTO 位於 `schemas/`，持久化位於 `repositories/`，上游用戶端位於 `integrations/`，設定位於 `core/settings.py` 與 `core/overall_config.py`。

## 技術堆疊

Python 3.11+、FastAPI、Granian、aiohttp（+aiohttp-socks）、boto3、SQLite、Pydantic v2、Pillow、python-multipart、zipstream-ng · SvelteKit、TypeScript、Tailwind CSS 4 · Playwright、pytest。瀏覽器最低版本：Chrome 111、Safari 16.4、Firefox 128。

## 專案結構

```text
backend/
  app/
    api/            # 公開 API 路由（contract_app.py）
    core/           # 設定、Overall Config
    integrations/   # 上游 API 用戶端
    repositories/   # 持久化、SQLite 協調
    runtime/        # 阻塞/並行輔助
    schemas/        # DTO
    services/       # 編排邏輯
  tests/
frontend/
  src/lib/          # 可重複使用的前端程式碼
  src/routes/       # SvelteKit 路由
  static/           # favicon
  tests/
deploy/nginx.conf
Dockerfile  docker-compose.yml  .env.example
requirements.txt  requirements.lock  package.json
images/  data/      # 執行期輸出（產生）
```

## 快速開始

### Docker Compose

```bash
cp .env.example .env
# 編輯 .env：至少設定 ACCESS_KEY，並視需要填入預設上游 API
# 此範例透過迴環位址使用 HTTP，需要停用 Secure cookie
ACCESS_COOKIE_SECURE=false docker-compose up -d --force-recreate
```

開啟 `http://127.0.0.1:9090`。

此本機 HTTP 範例需要設定 `ACCESS_COOKIE_SECURE=false`；透過 HTTPS 提供服務時應保持為 `true`。預設必須設定 `ACCESS_KEY`。僅在本機測試時，清空 `ACCESS_KEY` 並設定 `ALLOW_UNAUTHENTICATED=true`，這會讓所有非 health API 都不需要驗證。若要為解鎖流程額外啟用 Cloudflare Turnstile 人機驗證，請在 `.env` 中設定 `TURNSTILE_ENABLED=true` 以及來自 Cloudflare 儀表板的 `TURNSTILE_SITE_KEY` / `TURNSTILE_SECRET_KEY`；驗證元件會出現在 Unlock 按鈕下方，每次使用存取密鑰登入都需要有效的 token。

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

Docker Hub 速度過慢或無法存取時：

```bash
docker build \
  --build-arg PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.12-slim \
  --build-arg NODE_BASE_IMAGE=docker.m.daocloud.io/library/node:24-alpine \
  -t gpt-image-panel .
```

映像從帶雜湊的 `requirements.lock`（由 `requirements.txt` 以 `uv pip compile` 產生）安裝 Python 相依套件，確保每次建置、兩種架構取得相同版本。

### Caddy Reverse Proxy

Caddy 與應用程式在同一台主機執行時，請使用佔位網域，並繼續將 `9090` 連接埠僅綁定至迴環位址：

```caddyfile
panel.example.com {
    reverse_proxy 127.0.0.1:9090
}
```

透過 HTTPS 部署時，請在 `.env` 設定與網域相符的應用程式來源與 Host Allowlist：

```dotenv
PUBLIC_ORIGIN=https://panel.example.com
ALLOWED_HOSTS=panel.example.com
ACCESS_COOKIE_SECURE=true
```

只有一個上游時，建議使用上面的基本反代設定。如果確實需要主動健康檢查，請先確認 `/health` 在相同 `Host` Request Header 下回傳 `200`，再使用複數形式的 `health_headers` 設定區塊：

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

健康檢查回應狀態碼不在設定範圍內時，Caddy 會將上游標記為不健康。只有一個上游時，這會導致要求失敗，直到健康檢查恢復。修改後請驗證並重新載入 Caddy：

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy 自動 HTTPS 要求網域解析至伺服器，且入站 `80`、`443` 連接埠可存取。使用 Cloudflare 等 CDN Proxy 時，應確認 Edge Certificate 明確涵蓋完整網域，尤其是多層子網域；否則要求尚未到達 Caddy，瀏覽器就可能顯示 `ERR_SSL_VERSION_OR_CIPHER_MISMATCH`。如果 Caddy 在容器內執行，`127.0.0.1` 指向 Caddy 容器本身，此時應改用應用程式服務名稱或其他可存取的容器網路位址。

### 常見部署問題排查

1. **容器啟動閃退並報錯 `SecretRegistryError: credentials require a non-empty startup host allowlist`** — 在 `.env` 中設定了 `DEFAULT_API_KEY`，但未設定 `UPSTREAM_HOST_ALLOWLIST`。在 `.env` 中將 `DEFAULT_API_URL` 對應的純網域填入 `UPSTREAM_HOST_ALLOWLIST`（例如 `UPSTREAM_HOST_ALLOWLIST=api.openai.com` 或 `UPSTREAM_HOST_ALLOWLIST=cf.api.fan`）。
2. **瀏覽器存取顯示 `400 Bad Request: Host is not allowed`** — 請求的 `Host` 標頭未在 `.env` 的 `ALLOWED_HOSTS` 或 `PUBLIC_ORIGIN` 白名單中（常見於網域名稱拼寫錯誤，或修改 `.env` 後未重新建立容器）。確保兩者與網域完全一致，修改 `.env` 後必須執行 `docker compose up -d --force-recreate` 重新建立容器以套用新變數。
3. **直連 HTTP（無反代/未設定 SSL）環境下無法登入或陷入登入循環** — 預設 `ACCESS_COOKIE_SECURE=true` 要求必須透過 HTTPS，在純 HTTP 下瀏覽器會拒絕儲存/攜帶 Secure Cookie。純 HTTP 直連測試時，在 `.env` 中設定 `ACCESS_COOKIE_SECURE=false`；部署 HTTPS 反代後再改回 `true`。
4. **網頁端點擊「儲存預設」無法儲存、側邊欄不關閉** — 預設禁止直接在 Web 介面向資料庫持久化明文 API Key。若要在網頁端直接貼上明文 Key，需在 `.env` 中設定 `ALLOW_PLAINTEXT_SECRETS=true` 並重啟容器。若修改了預設中的 API URL，新網域必須同時加入 `.env` 的 `UPSTREAM_HOST_ALLOWLIST` 白名單。

### 本機開發

先使用本機 Python 3.11+ 建立專案專用虛擬環境。`.venv` 屬於本機開發狀態，專案儲存庫不提供此目錄。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt
npm --prefix frontend install
npm run backend:dev
```

另開一個終端機：

```bash
npm run frontend:dev
```

開啟 `http://localhost:5173`。Vite 會將 `/api` 與 `/health` 代理至 `127.0.0.1:9090`。

正式環境形式的本機 Smoke Test：

```bash
npm run frontend:build
ALLOW_UNAUTHENTICATED=true .venv/bin/granian --interface asgi backend.app.main:app --host 127.0.0.1 --port 9090
```

## 設定

大多數執行期選項都位於 `.env.example`。API 預設、提示詞最佳化器、R2 備份與部分應用程式/執行期選項，也可透過 Web Settings / Overall Config 管理。重要變數如下：

| 變數 | 用途 |
| --- | --- |
| `ACCESS_KEY` / `ALLOW_UNAUTHENTICATED` / `TURNSTILE_*` | 存取密鑰（除非清空並設定 `ALLOW_UNAUTHENTICATED=true`，否則必填）；`TURNSTILE_*` 為選用的 Cloudflare Turnstile 人機驗證。 |
| `DEFAULT_API_URL` / `DEFAULT_API_KEY` / `DEFAULT_API_PATH` / `DEFAULT_RESPONSES_MODEL` | 預設上游預設值。密鑰建議使用 `${OPENAI_API_KEY}` 之類的 env ref。 |
| `UPSTREAM_HOST_ALLOWLIST` | 設定上游 API key 時必填；列出允許的上游主機名稱。 |
| `PUBLIC_ORIGIN` / `ALLOWED_HOSTS` | Reverse Proxy Host/CSRF 強化；必須與部署網域一致。 |
| `MAX_ACTIVE_GENERATE_JOBS` / `MAX_QUEUED_GENERATE_JOBS` | 全域執行中的 image unit 上限與佇列容量（超過後新工作回傳 `429`）。 |
| `IMAGE_JOB_UNIT_LEASE_SECONDS` / `IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS` / `IMAGE_JOB_UNIT_MAX_ATTEMPTS` | 執行中 image unit 的 SQLite 租約（當機偵測延遲）、續租週期與最大領取次數。 |
| `GRANIAN_WORKERS` / `GRANIAN_*` | Worker 程序數與正式環境執行期調校；應等於實際程序數。 |
| `VISION_PREVIEW_MEMORY_BUDGET_MB` | 視覺預覽解碼預算：預設每程序 **256 MiB**，範圍 **32–16384**。Agent 超出保守記憶體估算預算的圖片跳過視覺預覽，文字參照、原圖及生成/編輯仍可使用。 |
| `IMAGE_CPU_CONCURRENCY` / `FILE_IO_CONCURRENCY` / `UPSTREAM_MEMORY_BUDGET_MB` / `MAX_UPSTREAM_IMAGE_BYTES_PER_TASK_MB` | 解碼、檔案 I/O 並行與上游圖片記憶體的有界預算。 |
| `DB_EXECUTOR_WORKERS` / `SQLITE_BUSY_*` / `SQLITE_CRITICAL_BUSY_*` / `SQLITE_SLOW_TXN_WARN_MS` | SQLite 執行器大小與 Busy 重試預算；關鍵的 claim/lease/finalize 寫入使用較大預算。 |
| `IMAGES_DIR` / `MASKS_DIR` / `THUMBNAILS_DIR` / `THUMBNAIL_*` / `DATA_DIR` / `DATABASE_FILE` / `LOG_DIR` / `LOG_LEVEL` / `LOG_RETENTION_HOURS` | 儲存路徑、縮圖控制與日誌（輪替檔案預設保留 24 小時）。 |
| `MASK_PASTE_BACK_DEFAULT` | 是否預設將遮罩編輯結果貼回未編輯的原圖區域；可在單次編輯要求中覆寫。 |
| `MAX_SSE_SUBSCRIBERS_GLOBAL` / `MAX_SSE_SUBSCRIBERS_PER_IP` / `SSE_CONNECTION_TTL_SECONDS` | SSE slot 限制與最長連線生命週期。 |
| `PROMPT_OPTIMIZER_*` | 選用的伺服器端提示詞最佳化器設定。 |
| `AI_ASSISTANT_*` | AI Assistant 預設啟用（設 `AI_ASSISTANT_ENABLED=false` 關閉）；API URL、密鑰、模型、逾時、允許清單沿用 `PROMPT_OPTIMIZER_*`。 |
| `AGENT_*` | Agent 對話模式的各項上限；模式本身在「設定 → AI Assistant」中開啟，沿用 `PROMPT_OPTIMIZER_*` 端點。 |
| `R2_*` | 選用的 Cloudflare R2 Gallery 備份同步；自訂 Endpoint Host 須設定 `R2_ENDPOINT_HOST_ALLOWLIST`。 |
| `NODEIMAGE_API_KEY` | 選用的 NodeImage API key，用於從伺服器上傳 Gallery 圖片。 |
| `IMAGE_COST_RATES_JSON` | 覆寫/新增費用估算使用的內建單模型美元費率。 |
| `APP_VERSION` / `GITHUB_REPO` / `ENABLE_VERSION_CHECK` / `VERSION_CHECK_CACHE_SECONDS` | 版本顯示與最新 Release 檢查。 |
| `ENABLE_NGINX_ACCEL_REDIRECT` / `PUBLIC_IMAGE_BASE_URL` / `PUBLIC_THUMBNAIL_BASE_URL` | 選用的 nginx/CDN 圖片位元組傳送行為。 |
| `ENABLE_METRICS` | 啟用 JSON/Prometheus 指標端點。 |
| `SECRET_REGISTRY_JSON` / `ALLOW_PLAINTEXT_SECRETS` / `ALLOW_LEGACY_ENV_REFS` | 密鑰處理：`${ENV_VAR}` 參照必須在 `SECRET_REGISTRY_JSON` 中宣告；明文 Secret 儲存至 SQLite 需 `ALLOW_PLAINTEXT_SECRETS=true`；`ALLOW_LEGACY_ENV_REFS=true` 是棄用的遷移開關。 |

設定解析分三層：環境變數（程序啟動時讀取一次）；透過 `/api/settings/overall-config` 持久化至 SQLite 的 Overall Config Override（可熱更新的鍵立即生效，`restart_required` 的鍵於下次啟動生效）；標記為 `exposed_in_settings` 的名稱（API 預設、提示詞最佳化器、AI Assistant、R2、NodeImage），一旦透過 Web Settings 儲存即從 SQLite 讀取——此後同名環境變數不再生效。少數設定（如 `DB_EXECUTOR_WORKERS`、`AI_ASSISTANT_MAX_CONCURRENCY`、`IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS`）由其他值衍生並自動重算。已知限制：執行期修改 `DB_EXECUTOR_WORKERS`、`IMAGE_CPU_CONCURRENCY` 或 `FILE_IO_CONCURRENCY` 不會調整已建立的執行緒池。

開啟 `ENABLE_METRICS=true` 後，`/api/metrics` 提供 SQLite 協調調校診斷：`sqlite.write_txn`、`sqlite.write_lock_wait_ms`、`sqlite.write_txn_hold_ms`（寫入交易速率與鎖定等待/持有時間，p50–p99/max——隨 `MAX_ACTIVE_GENERATE_JOBS x GRANIAN_WORKERS` 成長應留意 p95）；`sqlite.busy`、`sqlite.busy_retries`（競爭下失敗/重試的鎖定次數）；`image_jobs.lease_renewed`、`lease_lost`、`unit_reclaimed`、`unit_exhausted`（image unit 租約健康度；`lease_lost` 應保持為零）。

## 使用方式

1. 開啟面板。
2. 若已啟用存取密鑰，先使用 `ACCESS_KEY` 解鎖。
3. 開啟 Settings，建立或選取 API 預設。
4. 設定 API base URL、API path、模型、response format 與 API key/env ref。
5. 視需要設定 SOCKS5 Proxy、webhook、提示詞最佳化器、AI Assistant、R2 備份、NodeImage 上傳或 Overall Config Override。
6. 儲存預設，必要時執行健康檢查。
7. 輸入 Prompt 生成圖片，或上傳/選取來源圖片進行編輯。
8. 在 Gallery 中沿用參數、篩選、收藏、批次操作、匯入/匯出、執行 R2 同步。
9. 可以把 `/?apiUrl=https://api.example.com&apiModel=gpt-image-2` 存成書籤。開啟後會提示以該 URL 與模型建立新預設；確認橫幅會醒目顯示目標主機，並提醒僅為可信主機輸入 API 金鑰。確認前不會儲存任何內容，API 金鑰保持空白，且這些參數會立即從網址列移除。只接受 `https` URL，也不會從 URL 讀取任何憑證。（`?model=` 已被 Gallery 篩選占用，因此參數名稱是 `apiModel`。）
10. 用「匯出」把所選預設下載成帶版本、不含金鑰的 JSON 套件；用「分享連結」複製 `?preset=` 連結；用「匯入」先預覽檔案、剪貼簿 JSON 或分享連結，再確認套用。預設可透過拖曳手柄或上下箭頭排序，順序儲存在 SQLite。
11. 視需要在「設定 → AI Assistant」中開啟 Agent 模式（選擇支援 function calling 的模型），然後在頁首切換到 **Agent**。描述需求，用 `@`（例如 `@round-1-image-1`）引用先前的圖片，也可從 Gallery 加入圖片，按 Ctrl/Cmd+Enter 送出。執行中可按「停止」取消；產生的圖片會出現在 Gallery。

### Agent 分支、Markdown 與可選聯網搜尋

助手回覆支援標題、清單、表格、程式碼與連結。原始 HTML 顯示為文字，外部 Markdown 圖片不自動載入，連結僅允許不含憑據的 HTTP(S) 位址。

使用「編輯訊息 → 作為新分支傳送」或「重新產生」建立歷史回合的同級分支。「對話路徑」可切換並在重新整理後還原原路徑或新路徑；切換不會發起模型請求，也不會停止執行中的工作。編輯或重新產生前需先停止活動回合。模型歷史僅包含執行路徑的祖先；圖片 ID 與 `@round-N-image-M` 參照保持穩定，顯示輪次及 `@第N轮图M` 依目前路徑解讀。跨分支重用圖片時，請從 Gallery 明確加入附件。圖庫刪除會使所有路徑上的參照失效；刪除對話仍保留圖庫圖片。

聯網搜尋**預設關閉**。在「設定 → AI Assistant」中聲明目前端點及模型支援 Responses 聯網搜尋，再啟用搜尋；繼承的提示詞最佳化端點必須採用 `/v1/responses` 且模型相容，請先查閱供應商文件確認支援。搜尋關閉時，chat/completions 繼續使用既有函式工具；啟用搜尋但設定不相容時，會在提交前說明並阻止傳送（API 回傳 422）。搜尋失敗會顯示錯誤，不會自動關閉搜尋並重試。

搜尋進度與上游 annotations 提供的來源會隨回合儲存，行內引用編號對應「來源」清單及引用片段，重新整理、SSE 重播及切換路徑後可還原。一般 Markdown 連結不會變成來源。搜尋遵守既有工具輪次、逾時、取消及分支歷史預算；缺少 usage 時不編造費用。資料庫遷移 37/38 在啟動時自動套用。

## GPT Image 2.5

生成表單和預設設定提供 `gpt-image-2.5-flare`（快速日常生成）與 `gpt-image-2.5-sunburst`（精細編輯）。預設模型仍為 `gpt-image-2`，保留已儲存預設和自訂模型；透過「自訂模型 / 快照」可輸入兩個模型的 `-2026-09-08` 日期快照名稱。

文生圖使用 `/v1/images/generations`，上傳參考圖或選取圖庫圖片後使用 multipart `/v1/images/edits`。兩個變體均支援 `auto/low/medium/high/xhigh/max` 品質；舊模型和自訂模型沿用原有品質選項，切換至不支援目前品質的模型時恢復為 `auto`。

- 生成、編輯、助手和提示詞收藏最多支援 32,000 個 Unicode 字元（提示詞最佳化器共用同一上限，除非明確設定了 `PROMPT_OPTIMIZER_MAX_OUTPUT_CHARS`）。
- 尺寸為 `auto` 或 `寬x高`：邊長須是 16 的倍數且不超過 3840，寬高比不超過 3:1，總像素為 655,360–8,294,400；超過 2560×1440 屬於實驗性支援。
- 輸出支援 PNG/JPEG/WebP；壓縮參數（0–100，預設 100）僅用於 JPEG/WebP。透明背景需要 PNG/WebP，JPEG 搭配透明背景會自動切換為 PNG。
- 2.5 一律回傳 Base64：面板會儲存圖片並提供本地 URL，忽略舊預設繼承的 `response_format`；其他模型維持原有回傳格式行為。
- 編輯最多接受 16 張 PNG/JPEG/WebP 輸入（單張小於 50 MB，受應用程式上傳限制約束，其他格式請先轉換）；1–10 張輸出沿用現有佇列，每個執行單元請求一張圖片。

本次僅透過 Images API 整合——不包含官方 Responses 閘道格式與 Chat Completions，但遮罩編輯與貼回（見下文）與其他模型一致。上游帳號需要具備模型存取權限；更高品質可能消耗更多 token，兩個變體相同的 token 單價不代表每張圖片費用相同。

規範核對日期：2026-09-10。[官方指南](https://developers.openai.com/api/docs/guides/image-generation) · [生成介面](https://developers.openai.com/api/reference/resources/images/methods/generate) · [編輯介面](https://developers.openai.com/api/reference/resources/images/methods/edit)。

## 遮罩編輯與貼回

- 編輯流程內建遮罩編輯器（畫筆、橡皮擦、形狀/套索工具、縮放、羽化、磁性吸附），用於精準繪製編輯區域；也可匯入既有 PNG 遮罩（黑白遮罩以白色為可編輯區域，柔和透明遮罩以透明度低於 50% 為可編輯區域，同比例匯入會自動縮放符合原圖）。自動補洞與邊緣平滑預設開啟，且只會增加可編輯像素、不會移除已繪製區域，兩者都即時反映在預覽與覆蓋率讀數中。
- 儲存的遮罩（PNG，最大 4 MB）隨工作持久化到磁碟（`MASKS_DIR`）；重試時會先驗證來源圖仍相符才還原遮罩，來源圖已變更則拒絕重試。
- 預設情況下，遮罩編輯結果會貼回原圖（`MASK_PASTE_BACK_DEFAULT=true`）：只保留模型輸出中遮罩範圍內的像素，其餘部分還原為未編輯的原圖並做羽化融合，若模型改動了遮罩外的像素則由色彩漂移防護略過貼回。可依編輯關閉貼回，預覽面板也可在結果與原圖間切換以檢查邊界。
- API 預設攜帶 `supports_mask` 開關（預設開啟），關閉後可為不支援遮罩上傳的上游隱藏遮罩編輯功能。
- Gallery 圖片會記錄 `mask_coverage`、`paste_back`、`paste_back_scale`，可透過 `mask_only` 篩選遮罩編輯圖片。

## 支援的上游路徑

| 路徑 | 說明 |
| --- | --- |
| `/v1/images/generations` | 標準圖片生成端點，從 `data[]` 讀取圖片資料。 |
| `/v1/responses` | 傳送 `prompt` 與 `model`，從 `image_generation_call` 輸出項目讀取 Base64 圖片。 |
| `/v1/chat/completions` | 傳送 OpenAI 相容的 Chat Completions 要求，從訊息或 SSE Chunk 擷取圖片 URL/Base64。 |
| `/v1/images/edits` | 供 Edits 流程使用，傳送 Multipart 來源圖片與支援的編輯參數。 |

使用 `/v1/responses` 與 `/v1/chat/completions` 時，尺寸、品質、格式、壓縮率與數量控制項會停用，因為這些路徑的參數契約不同。

## 自訂非同步供應商

部分閘道會先接收任務、回傳狀態/結果 URL，稍後才完成。預設可以用宣告式 JSON 對映取代 OpenAI 路徑：在設定中選擇**自訂非同步供應商**，再貼上你的閘道對應的對映（下方範例列出全部欄位；輸入框為空時會以灰色顯示相同的骨架）。

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

- **流程**：把轉譯後的請求本文 `POST` 到 `API URL + submit.path`，從回應取得狀態 URL，輪詢直到 `status_path` 的值屬於 `done`（或 `failed`），視需要再請求 `result.url_path`，最後從 `images_path` 讀取圖片（URL 或 base64）。不會把任何內容當作程式碼執行；路徑只支援 `$.a.b[0]` 與 `[*]`。
- **變數**：`prompt`、`model`、`n`、`width`、`height`、`size`、`quality`、`output_format`、`background`。值只是一個沒有內容的變數（例如尺寸為 `auto` 時的 `width`）時，該欄位會被省略。`submit.path` 只能使用 `{{model}}`。
- **只回傳任務 ID 的對映（version 2）**：若供應商只回傳任務 ID，可設定 `"version": 2`，使用 `poll.task_id_path` + `poll.url_template`（`result`/`cancel` 同理），例如 `{"task_id_path": "$.data.id", "url_template": "/v1/jobs/{{task_id}}"}`。範本必須是純路徑，任務 ID 會以路徑片段進行 URL 編碼，絕對 URL 會被拒絕。可選 `submit.idempotency_header`（例如 `Idempotency-Key`）讓中斷的提交以同一穩定鍵重試。**同步模式**：提交回應已包含圖片時，v2 對映可設定 `"mode": "sync"`——省略 `poll` 與 `cancel`，以 `result.images_path` 從提交回應讀取圖片。
- **方法與查詢對映**：`submit.method` 與 `poll.method` 接受 `GET` 或 `POST`（GET 提交必須為空請求本文；POST 輪詢傳送空 JSON 物件）。`submit.query`、`edit_submit.query` 與 `poll.query` 以範本對映 URL 查詢參數——提交用 submit 變數，輪詢用 `{{task_id}}`；字面值僅允許字母、數字與 `- _ . ~`。
- **圖片編輯（edit_submit）**：新增 `edit_submit` 區段即宣告編輯支援。Multipart 編輯將驗證後的參考圖與遮罩作為檔案分片（`files.images` 每張圖片一個分片、`files.mask`）；JSON 編輯在 `edit_submit.body` 內嵌 `{{reference_images}}`（有界 data URL 清單）與選用的 `{{mask}}`（一個 data URL）。編輯沿用面板的上傳驗證、大小上限、遮罩前置處理、取消與結果貼回；設定遮罩就一定會送出——無法攜帶遮罩的對映會在提交前被拒絕。
- **能力宣告**：`capabilities.transparent_background` 控制 `background: transparent`（省略時面板會從 `{{background}}` 用法自動偵測），`capabilities.formats` 收窄可接受的輸出格式；未宣告的要求會在提交前被明確拒絕。`stream` 保持保留位。色鍵背景仍可用於生成——上游收到不透明背景與鍵控提示詞，面板於本機移除顏色。
- **憑證**：對映中不放任何金鑰。預設的 API 金鑰（環境變數參照或 Secret Registry ID）以 `header: scheme key` 傳送；`header` 為 `Authorization` 或 `X-API-Key`，`scheme` 為 `Bearer`、`Key`、`Token` 或留空。
- **安全**：狀態、結果與取消 URL 來自上游回應，因此必須與預設 API URL 同源（金鑰會隨請求傳送），並通過與提交 URL 相同的 SSRF 與對端 IP 檢查。圖片下載不帶憑證，錯誤訊息會遮蔽敏感內容。
- **復原**：提交成功後，工作單元會持久化遠端任務 ID、後續位址、絕對輪詢截止時間、冪等鍵與非密鑰對映快照。Worker 意外結束後，由其他 Worker 繼續輪詢同一任務，而不是重新提交；截止時間與接管次數會跨重啟保留（預設最多接管 5 次）。提交已送出但結果未記錄時，工作標記為 `interrupted` 並帶 `submit_unknown` 診斷，不會自動重新提交。失去租約不會取消遠端任務；使用者主動取消會盡力送出一次 `cancel`。輪詢遇到限流、5xx 或斷網會在截止時間內有上限地退避重試。
- 健康檢查只驗證對映並探測提交 URL，不會真的提交任務。設定中可複製「對映生成提示詞」交給 LLM，並對貼上的對映做即時驗證；貼上模擬回應還能在本機驗證任務 ID、狀態與圖片擷取（不會請求上游）；驗證結果同時列出解析出的能力。
- **診斷**：失敗的工作單元會保留一份受限、遮蔽的提交/輪詢/結果階段記錄。在「工作歷史 → 診斷」中可查看階段、HTTP 狀態、失敗的對映路徑與回應快照，並複製診斷資訊用於排查。

## 串流預覽與費用估算

- 串流預覽需主動開啟，支援 Images 生成/編輯端點及 `/v1/responses`，數量為 1–10。每張圖片仍作為獨立單元排隊，遵守既有並行限制。可選 1–3 個中間畫面，實際畫面數與用量取決於上游；Responses 圖片按輸出呼叫歸屬，文字 delta 不會成為圖片預覽。宣告式自訂供應商對應仍要求 `capabilities.stream: false`。
- 若相容服務拒絕 `stream`/`partial_images` 參數，工作會以明確錯誤結束，而非自動降級為非串流重試，以免重複計費。
- 中間圖片僅存在伺服器記憶體中（每個執行單元/輸出呼叫保留最新畫面，受 `PREVIEW_CACHE_MAX_ENTRY_MB`/`PREVIEW_CACHE_MAX_ENTRIES` 限制，預設 8 MiB / 500 項）；慢速 SSE 用戶端的畫面會合併。最終圖片只取代自己的槽位，失敗、取消及完成會清理預覽。重新連線先恢復工作快照再重播快取，重啟或切換 worker 後無預覽可重播。同一次請求回傳有效 JSON 最終結果時會直接接受，不重新提交。
- **費用估算並非帳單。** 僅在上游回傳 `usage` 時才會計算；缺少 usage 或未設定費率時會顯示原因而非 `$0.00`。內建費率涵蓋 `gpt-image-1` 與 GPT Image 2 / 2.5，取自 OpenAI 官方定價——第三方上游通常並不相同。可透過 `IMAGE_COST_RATES_JSON` 覆寫或新增費率。

## 圖庫、觸控操作與完成通知

- 桌面可拖曳框選或按 Ctrl/⌘/Shift 選擇卡片；觸控螢幕上的明確橫向側滑每次切換一張圖片，縱向移動保留捲動。全篩選範圍選擇保留伺服器 selection token，直到主動選擇「退出全選」。
- 收藏夾概覽從列表取得封面、數量及預設標記，不必逐一請求圖片詳情；進入收藏夾保留其他篩選。空收藏夾與封面缺失仍可管理及匯出 ZIP。
- Lightbox 支援雙指縮放、放大後拖曳、雙擊縮放及長按下載/收藏/編輯選單；Escape 先關閉操作選單，再關閉檢視器。
- 「工作區偏好 → 工作完成通知」預設關閉，僅主動啟用時請求權限。安全環境中的支援瀏覽器在頁面位於背景時按圖片父工作或 Agent 回合彙總通知，並說明部分失敗數量；點擊開啟結果或對話。Web Locks 與有界 localStorage 歷史協調多分頁及重播，舊瀏覽器為盡力協調。頁面必須持續執行，關閉後的整合通知使用 Webhooks。
- 排隊工作凍結非密鑰供應商設定，憑據仍從目前預設讀取；變更 API origin 後工作會明確失敗，避免把新憑據送到已儲存的舊位址。

## API 概覽

主要後端路由（依領域分組）：

| 方法 | 路徑 | 用途 |
| --- | --- | --- |
| `GET` | `/health`、`/api/access/status`、`/api/version`、`/api/version/latest` | 健康檢查、存取狀態、目前/最新版本。 |
| `POST` | `/api/access` | 使用存取密鑰解鎖面板。 |
| `GET/PUT` | `/api/settings/overall-config` | 讀取/儲存 Overall Config Override。 |
| `GET/POST` | `/api/settings` | 讀取/儲存目前預設、提示詞最佳化器、R2 備份、Proxy 與 webhook 設定。 |
| `POST` | `/api/settings/presets`（+ `/{preset_id}/activate`、`/health`、`DELETE /api/settings/presets/{preset_id}`） | 建立、啟用、驗證、刪除 API 預設；`POST /api/settings/r2/health` 驗證草稿 R2 備份設定。 |
| `GET/POST`、`PATCH/DELETE` | `/api/prompt-snippets`（+ `/search`、`/{snippet_id}`） | 列出/建立、搜尋、更新/刪除可重複使用的提示詞片段。 |
| `GET/POST` | `/api/prompt/optimizer-system-prompt`；`POST /api/prompt/optimize`、`/api/prompt/optimizer-health` | 讀取/儲存最佳化器 System Prompt；最佳化提示詞或探測最佳化器連線狀態。 |
| `POST` | `/api/assistant/health`、`/api/assistant/prompt/rewrite\|check\|variants`、`/api/assistant/generate/recommend-params`、`/api/assistant/jobs/{job_id}/diagnose`、`/api/assistant/edit/plan`、`/api/assistant/image/prompt`、`/api/assistant/image/prompt/optimize`；`POST/GET /api/assistant/gallery/*` | 探測 Assistant 連線；提示詞改寫/檢查/變體；參數建議；工作診斷與編輯規劃；本機圖片反推 Prompt；Gallery 描述/分析/批次操作。 |
| `GET/POST` | `/api/agent/conversations`；`GET/PATCH/DELETE /api/agent/conversations/{conversation_id}`；`POST .../turns`；`PATCH .../branch` | 列出/建立、讀取/重新命名/刪除對話（刪除對話保留 Gallery 圖片）；送出訊息（`client_turn_id` 讓重試具冪等性，回傳 `202`）；選取持久化路徑。 |
| `GET` | `/api/agent/turns/{turn_id}`（+ `/events`）；`POST /api/agent/turns/{turn_id}/cancel` | 讀取回合或訂閱可重播的 SSE 事件；停止執行中的回合并取消其排隊圖片工作。 |
| `POST` | `/api/generate`、`/api/edits`、`/api/edits/from-gallery/{image_id}` | 建立生成/編輯工作（編輯支援選用的 PNG 遮罩與貼回偏好）。 |
| `GET`、`GET/DELETE` | `/api/generate/jobs`（+ `/events`）、`/api/generate/{job_id}`（+ `/events`）；`DELETE /api/generate/jobs/history` | 列出即時工作/歷史、工作清單或單一工作 SSE、讀取/取消單一工作、清除已終止的工作歷史。 |
| `GET`、`POST` | `/api/gallery`、`/api/gallery/search`；`GET/DELETE /api/gallery/{image_id}`；`PATCH /api/gallery/{image_id}/favorite` | 列出/搜尋/篩選 Gallery（含 `mask_only`）、讀取/刪除/收藏圖片。 |
| `POST/PATCH` | `/api/gallery/batch/*`；`POST /api/gallery/thumbnails/status` | Selection Token、收藏、刪除與下載等批次操作；縮圖存在/狀態檢查。 |
| `POST`、`GET` | `/api/gallery/nodeimage-upload-jobs`、`/api/gallery/export-jobs`、`/api/gallery/direct-export-jobs`、`/api/gallery/sync-jobs`、`/api/gallery/import-jobs`（各帶 `/{job_id}`、`/events`，以及適用的 `DELETE`/`cancel` 或 `/download`） | NodeImage 非同步上傳、Gallery ZIP 匯出、R2 同步、匯入工作的狀態/SSE 追蹤。 |
| `GET` | `/api/image/{filename}`、`/api/thumb/{filename}`、`/api/download/{filename}`、`/api/download-all?export_job_id=` | 提供通過授權的圖片位元組、縮圖、單張下載與串流 ZIP 匯出。 |
| `POST` | `/api/import` | 匯入 Gallery ZIP；`async_job=true` 會建立匯入工作。 |
| `GET` | `/api/metrics`、`/api/metrics/prometheus` | 設定 `ENABLE_METRICS=true` 時可用。 |

公開 API 已有契約測試。除非刻意進行破壞性變更，否則請維持路徑、方法、狀態碼、SSE 事件名稱、Cookie 與回應結構穩定。

## 貢獻者界線

- 瀏覽器要求應維持同源 `/api/*`；請勿從前端直接呼叫上游模型 API、R2、webhook 目標或任意圖片 URL。
- 維持現有程式碼分層：路由與要求協調位於 `backend/app/api/routers/`，DTO 位於 `backend/app/schemas/`，持久化與 SQLite 協調位於 `backend/app/repositories/`，上游整合位於 `backend/app/integrations/`，前端鏡像 API 型別位於 `frontend/src/lib/api/types.ts`。
- 除非刻意進行破壞性變更，否則請維持下列公開契約：API 路徑、方法、狀態碼、Cookie、SSE 事件名稱與回應結構；生成/編輯佇列生命週期、取消語意與多 Worker SQLite 協調。檔案系統競爭透過 UUID 檔名、原子替換與孤兒檔案 GC 來容忍；請勿依賴程序內鎖實作跨 Worker 互斥。
- 集中管理驗證與安全邏輯：圖片位元組驗證、安全路徑、縮圖/封存輔助函式；SSRF 敏感 URL 處理應位於 Validator、Safe Connector 與整合 Client；前端只能看到遮罩後的 Secret 或環境變數參照中繼資料。
- 保留目前的執行期限制：編輯工作最多接受 16 張點陣來源圖片，外加最多 4 MB 的單張 PNG 遮罩；Gallery ZIP 匯入/匯出沿用既有安全限制；SSE 使用具有全域/單一 IP 上限與 TTL 的 SQLite Slot Lease；R2 同步僅作備份——本機 SQLite 記錄與本機圖片檔案仍是唯一真實來源。
- 新增或修改環境變數時，請同步更新 `backend/app/core/settings.py`、必要時的 `backend/app/core/overall_config.py`、`.env.example`、`docker-compose.yml` 與 README。
- 請勿提交 `images/`、`data/`、`frontend/build/`、`.svelte-kit/`、Playwright 報告、測試結果、相依套件目錄、本機資料庫或日誌等執行期/產生檔案。

## 測試

執行後端或契約測試前，請先啟用專案本機 `.venv`。npm 的契約/效能測試指令會使用 `.venv/bin/python`。

```bash
npm run frontend:check
npm run frontend:build
.venv/bin/python -m pytest backend/tests -q
npm run test:contract
npm run test:e2e
npm run test:perf
npm run test:e2e:perf
```

一般變更只需執行相關子集；大範圍變更或發行前則應執行完整測試。

若缺少 Playwright 瀏覽器：

```bash
npm --prefix frontend exec playwright install chromium
```

## 貢獻

實作界線及貢獻者應遵守的不變條件，請以上方「貢獻者界線」為準。執行與變更範圍相符的最小驗證集合；若變更行為或環境變數定義，請在同一修補中同步更新 README 與設定檔。

## 授權條款

本專案採用 `CC BY-NC 4.0`（`Creative Commons Attribution-NonCommercial 4.0 International`）授權條款。

請參閱 [LICENSE](./LICENSE)。
