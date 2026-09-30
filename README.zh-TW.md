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
    <img alt="版本 v1.7.1" src="https://img.shields.io/badge/release-v1.7.1-0e8dcc" />
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
- 透過 `/v1/images/edits` 編輯圖片，可使用上傳的參考圖或 Gallery 圖片作為來源。
- 內建遮罩編輯器，用於精準定位編輯區域（畫筆/橡皮擦/形狀/套索工具、縮放、羽化、遮罩匯入、預設開啟的自動補洞與邊緣平滑），遮罩隨工作持久化並可在重試時還原，預設會將模型結果貼回未編輯的原圖區域，並具備色彩漂移防護。
- API 預設管理：base URL/path/key、預設模型、response format、健康檢查、SOCKS5 Proxy、webhook 與環境變數參照式密鑰。
- 可由網頁管理的 Overall Config，顯示 env/default/override 來源，以及需要重新啟動或僅影響建置的設定標記。
- 提示詞輔助標籤、可重複使用的提示詞片段、選用的伺服器端提示詞最佳化器，以及 AI Assistant 子系統，可進行提示詞改寫/檢查/變體、參數建議、工作診斷、編輯規劃與 Gallery 圖片分析。
- Agent 對話模式：多輪對話中由模型規劃並透過既有工作佇列產生圖片（批次並行、有相依性的後續輪次、以 `@` 引用並修改歷史圖片），對話歷史儲存在伺服器端，串流回覆可斷線續傳，所有圖片都會進入 Gallery。需要 AI Assistant 端點使用支援 function calling 的模型。
- SQLite 工作佇列：SSE 進度、取消、重試/沿用、持久化歷史、階段耗時資訊，以及生成/編輯共用的並行限制。
- 選用的串流階段性圖片預覽，適用於數量為 1 的 `/v1/images/generations` 與 `/v1/images/edits` 請求，透過 SSE 在上游生成過程中推送。工作歷史會顯示每個工作的 token 用量，並在上游回傳 usage 且該模型已設定費率時顯示估算美元費用。
- 本機 Gallery：游標分頁、搜尋/篩選、收藏、燈箱導覽、selection token 批次操作、ZIP 匯入/匯出、縮圖、位元組大小資訊，以及非同步匯入/匯出工作。
- 選用的 Cloudflare R2 Gallery 備份同步；本機 SQLite 與圖片檔案仍是唯一真實來源。
- 存取密鑰、IP/Host 允許清單、可信任 Proxy Header、CSRF Origin 檢查、CSP nonce、版本檢查，以及選用的 JSON/Prometheus 指標。

## 架構

- 後端：`backend/app/` 下的 FastAPI；ASGI 進入點為 `backend.app.main:app`。
- 前端：`frontend/` 下的 SvelteKit 靜態應用程式；正式環境由後端提供 `frontend/build/`。
- 執行期儲存：圖片預設位於 `images/`、縮圖位於 `images/thumbs/`、SQLite 資料位於 `data/app.sqlite3`、日誌位於 `data/logs/`。
- 多 Worker 協調：排隊工作、背景 Lease、SSE Slot 與排程器所有權使用 SQLite Lease。圖片與縮圖檔案的寫入/刪除僅使用程序內鎖，並透過 UUID 檔名、原子 `Path.replace()` 與孤兒檔案 GC TTL 清理容忍跨程序競爭。
- 公開 API 路由：`backend/app/api/contract_app.py`。
- DTO：`backend/app/schemas/`。
- 持久化：`backend/app/repositories/`。
- 上游 API 整合：`backend/app/integrations/`。
- 執行期設定：`backend/app/core/settings.py`、`backend/app/core/overall_config.py`、`.env.example` 與 `docker-compose.yml`。

## 技術堆疊

- Python 3.11+
- FastAPI
- Granian
- aiohttp
- aiohttp-socks
- httpx
- boto3
- SQLite
- Pydantic v2
- Pillow
- python-multipart
- zipstream-ng
- SvelteKit
- TypeScript
- Tailwind CSS
- Playwright
- pytest

## 專案結構

```text
backend/
  app/
    api/
    core/
    integrations/
    repositories/
    schemas/
    services/
  tests/
frontend/
  src/
    lib/
    routes/
  tests/
deploy/
  nginx.conf
images/
data/
Dockerfile
docker-compose.yml
.env.example
requirements.txt
backend/requirements-dev.txt
package.json
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

1. **容器啟動閃退並報錯 `SecretRegistryError: credentials require a non-empty startup host allowlist`**
   - **原因**：在 `.env` 中設定了 `DEFAULT_API_KEY`，但未設定 `UPSTREAM_HOST_ALLOWLIST`。
   - **解決**：在 `.env` 中將 `DEFAULT_API_URL` 對應的**純網域**填入 `UPSTREAM_HOST_ALLOWLIST`（例如 `UPSTREAM_HOST_ALLOWLIST=api.openai.com` 或 `UPSTREAM_HOST_ALLOWLIST=cf.api.fan`）。
2. **瀏覽器存取顯示 `400 Bad Request: Host is not allowed`**
   - **原因**：請求的 `Host` 標頭未在 `.env` 的 `ALLOWED_HOSTS` 或 `PUBLIC_ORIGIN` 白名單中（常見於網域名稱拼寫錯誤，或修改 `.env` 後未重新建立容器）。
   - **解決**：檢查 `.env` 中的 `PUBLIC_ORIGIN=https://panel.example.com` 與 `ALLOWED_HOSTS=panel.example.com` 拼寫是否完全一致。修改 `.env` 後必須執行 `docker compose up -d --force-recreate` 重新建立容器以套用新變數。
3. **直連 HTTP（無反代/未設定 SSL）環境下無法登入或陷入登入循環**
   - **原因**：預設 `ACCESS_COOKIE_SECURE=true` 要求必須透過 HTTPS，在純 HTTP 下瀏覽器會拒絕儲存/攜帶 Secure Cookie。
   - **解決**：純 HTTP 直連測試時，在 `.env` 中設定 `ACCESS_COOKIE_SECURE=false`；部署 HTTPS 反代後再改回 `true`。
4. **網頁端點擊「儲存預設」無法儲存、側邊欄不關閉**
   - **原因**：
     - 預設禁止直接在 Web 介面向資料庫持久化明文 API Key。若要在網頁端直接貼上明文 Key，需在 `.env` 中設定 `ALLOW_PLAINTEXT_SECRETS=true` 並重啟容器。
     - 若修改了預設中的 API URL，新網域必須同時加入 `.env` 的 `UPSTREAM_HOST_ALLOWLIST` 白名單。

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
| `ACCESS_KEY` | 存取密鑰。除非清空該變數並設定 `ALLOW_UNAUTHENTICATED=true`，否則必填。 |
| `TURNSTILE_ENABLED` / `TURNSTILE_SITE_KEY` / `TURNSTILE_SECRET_KEY` | 選用的 Cloudflare Turnstile 人機驗證；啟用後，解鎖除 `ACCESS_KEY` 外還需要有效的 Turnstile token。 |
| `DEFAULT_API_URL` | 預設上游 API base URL，可包含或省略 `/v1`。 |
| `DEFAULT_API_KEY` | 預設上游 API key。Web Settings 建議使用 `${OPENAI_API_KEY}` 之類的 env ref。 |
| `DEFAULT_API_PATH` | `/v1/images/generations`、`/v1/responses` 或 `/v1/chat/completions`。 |
| `DEFAULT_RESPONSES_MODEL` | `/v1/responses` 在要求/預設未提供模型時使用的備援模型。 |
| `AIOHTTP_CONNECTION_LIMIT` / `AIOHTTP_CONNECTION_LIMIT_PER_HOST` | 上游要求、探測與下載共用的 aiohttp connector 限制。 |
| `APP_VERSION` / `GITHUB_REPO` / `ENABLE_VERSION_CHECK` | UI/API 版本顯示與最新 Release 檢查。 |
| `VERSION_CHECK_CACHE_SECONDS` | 每個程序成功檢查最新版本的快取有效期。 |
| `MAX_UPSTREAM_IMAGE_BYTES_PER_TASK_MB` / `UPSTREAM_MEMORY_BUDGET_MB` | 單一工作的解碼圖片上限，以及程序內上游記憶體加權准入預算。 |
| `DB_EXECUTOR_WORKERS` / `SQLITE_BUSY_*` | SQLite 專用執行器大小，以及短逾時與抖動重試控制。 |
| `IMAGE_CPU_CONCURRENCY` / `FILE_IO_CONCURRENCY` | 每個程序完整圖片解碼與阻塞式檔案 I/O 的有界並行數。 |
| `VISION_PREVIEW_MEMORY_BUDGET_MB` | 獨立的視覺預覽解碼預算，預設每程序 **256 MiB**，範圍 **32–16384**，支援環境變數與 Overall Config。Agent 超出保守記憶體估算預算的圖片跳過視覺預覽，文字參照、原圖及生成／編輯仍可使用；直接 AI Assistant 圖片分析無法容納預覽時回傳 400。 |
| `IMAGE_JOB_PROGRESS_PERSIST_INTERVAL_SECONDS` | 合併寫入圖片單元進度的最短間隔。 |
| `RUNTIME_METRICS_REFRESH_SECONDS` / `EVENT_LOOP_LAG_SAMPLE_SECONDS` | 背景協調快照與事件迴圈延遲取樣間隔。 |
| `MAX_ACTIVE_GENERATE_JOBS` | 全域執行中的生成/編輯 image unit 上限。 |
| `MAX_QUEUED_GENERATE_JOBS` | 佇列容量；超過後的新工作會回傳 `429`。 |
| `MAX_PENDING_EDIT_SOURCE_MB` | 全域待處理編輯來源圖片的位元組保留上限。 |
| `MASK_PASTE_BACK_DEFAULT` | 是否預設將遮罩編輯結果貼回未編輯的原圖區域；可在單次編輯要求中覆寫。 |
| `MAX_SSE_SUBSCRIBERS_GLOBAL` / `MAX_SSE_SUBSCRIBERS_PER_IP` / `SSE_CONNECTION_TTL_SECONDS` | SSE slot 限制與最長連線生命週期。 |
| `IMAGES_DIR` | 圖片儲存目錄。 |
| `MASKS_DIR` | 持久化的編輯遮罩目錄，用於工作重試時還原遮罩；如需調整磁碟區/備份路徑，請保持在 `IMAGES_DIR` 之下。 |
| `THUMBNAILS_DIR` / `THUMBNAIL_*` | Gallery 縮圖儲存與產生控制。 |
| `DATA_DIR` / `DATABASE_FILE` | SQLite 執行期資料。 |
| `PROMPT_OPTIMIZER_*` | 選用的伺服器端提示詞最佳化器設定。 |
| `AI_ASSISTANT_*` | AI Assistant 預設啟用（設 `AI_ASSISTANT_ENABLED=false` 關閉）；API URL、密鑰、模型、逾時、允許清單沿用 `PROMPT_OPTIMIZER_*`。`AI_ASSISTANT_MAX_CONCURRENCY` / `AI_ASSISTANT_BATCH_MAX_IMAGES` 限制並行數與單次 Gallery AI 批次圖片數。 |
| `AGENT_*` | Agent 對話模式的各項上限（對話數、每個對話的輪數、訊息與提示詞長度、附圖數、每批/每輪圖片數、歷史圖片數、工具輪數上限、圖片工作逾時、回合租約、事件保留時間、單一 worker 並行回合數）。Agent 模式本身在「設定 → AI Assistant」中開啟，沿用 `PROMPT_OPTIMIZER_*` 端點。詳見 `.env.example`。 |
| `R2_*` | 選用的 Cloudflare R2 Gallery 備份設定；自訂 Endpoint Host 須設定 `R2_ENDPOINT_HOST_ALLOWLIST`。 |
| `NODEIMAGE_API_KEY` | 選用的 NodeImage API key，用於從伺服器上傳 Gallery 圖片；也可在 Web Settings 中設定 env ref。 |
| `PUBLIC_ORIGIN` / `ALLOWED_HOSTS` | Reverse Proxy Host/CSRF 強化。 |
| `ENABLE_NGINX_ACCEL_REDIRECT` / `PUBLIC_IMAGE_BASE_URL` / `PUBLIC_THUMBNAIL_BASE_URL` | 選用的 nginx/CDN 圖片位元組傳送行為。 |
| `GRANIAN_*` | 正式環境程序、執行緒與靜態資源調校。 |
| `ENABLE_METRICS` | 啟用 JSON/Prometheus 指標端點。 |
| `LOG_DIR` / `LOG_LEVEL` / `LOG_RETENTION_HOURS` | 後端日誌輸出至 stdout 與輪替檔案，預設保留 24 小時。 |

Secret 欄位優先使用 `${ENV_VAR_NAME}` 參照。若要將純文字 Secret 儲存在 SQLite，必須明確設定 `ALLOW_PLAINTEXT_SECRETS=true`。

Overall Config 會將 Override 持久化至 SQLite。部分設定可熱更新；需要重新啟動或僅影響建置的設定會在 UI 中標示。為了可重現部署，仍建議透過 `.env`/Compose 管理。

## 使用方式

1. 開啟面板。
2. 若已啟用存取密鑰，先使用 `ACCESS_KEY` 解鎖。
3. 開啟 Settings。
4. 建立或選取 API 預設。
5. 設定 API base URL、API path、模型、response format 與 API key/env ref。
6. 視需要設定 SOCKS5 Proxy、webhook、提示詞最佳化器、AI Assistant、R2 備份、NodeImage 上傳或 Overall Config Override。
7. 儲存預設，必要時執行健康檢查。
8. 輸入 Prompt 生成圖片，或上傳/選取來源圖片進行編輯。
9. 在 Gallery 中沿用參數、篩選、收藏、批次操作、匯入/匯出、執行 R2 同步，或上傳至 NodeImage。
10. 可以把 `/?apiUrl=https://api.example.com&apiModel=gpt-image-2` 存成書籤。開啟後會提示以該 URL 與模型建立新預設；確認前不會儲存任何內容，API 金鑰保持空白，且這些參數會立即從網址列移除。只接受 `https` URL，也不會從 URL 讀取任何憑證。（`?model=` 已被 Gallery 篩選占用，因此參數名稱是 `apiModel`。）
11. 視需要在「設定 → AI Assistant」中開啟 Agent 模式（選擇支援 function calling 的模型），然後在頁首切換到 **Agent**。描述需求，用 `@`（例如 `@round-1-image-1` 或 `@第1輪圖1`）引用先前的圖片，也可從 Gallery 加入圖片，按 Ctrl/Cmd+Enter 送出。執行中可按「停止」取消；產生的圖片會出現在 Gallery。

## GPT Image 2.5

生成表單和預設設定提供 `gpt-image-2.5-flare`（快速日常生成）與 `gpt-image-2.5-sunburst`（精細編輯）。預設模型仍為 `gpt-image-2`，保留已儲存預設和自訂模型；透過自訂模型欄位可輸入兩個模型的 `-2026-09-08` 日期快照名稱。

文生圖使用 `/v1/images/generations`，上傳參考圖或選取圖庫圖片後使用 multipart `/v1/images/edits`。兩個變體均支援 `auto/low/medium/high/xhigh/max` 品質；舊模型和自訂模型沿用原有品質選項，切換至不支援目前品質的模型時恢復為 `auto`。

- 生成、編輯、助手和提示詞收藏最多支援 32,000 個 Unicode 字元（提示詞最佳化器共用同一上限，除非明確設定了 `PROMPT_OPTIMIZER_MAX_OUTPUT_CHARS`）。
- 尺寸為 `auto` 或 `寬x高`：邊長須是 16 的倍數且不超過 3840，寬高比不超過 3:1，總像素為 655,360–8,294,400；超過 2560×1440 屬於實驗性支援。
- 輸出支援 PNG/JPEG/WebP；壓縮參數（0–100，預設 100）僅用於 JPEG/WebP。透明背景需要 PNG/WebP，JPEG 搭配透明背景會自動切換為 PNG。
- 2.5 一律回傳 Base64：面板會儲存圖片並提供本地 URL，忽略舊預設繼承的 `response_format`；其他模型維持原有回傳格式行為。
- 編輯最多接受 16 張 PNG/JPEG/WebP 輸入（單張小於 50 MB，受應用程式上傳限制約束，其他格式請先轉換）；1–10 張輸出沿用現有佇列，每個執行單元請求一張圖片。

本次僅透過 Images API 整合——不包含官方 Responses 閘道格式與 Chat Completions，但遮罩編輯與貼回（見下文）與其他模型一致。上游帳號需要具備模型存取權限；更高品質可能消耗更多 token，兩個變體相同的 token 單價不代表每張圖片費用相同。

規範核對日期：2026-09-10。[官方指南](https://developers.openai.com/api/docs/guides/image-generation) · [生成介面](https://developers.openai.com/api/reference/resources/images/methods/generate) · [編輯介面](https://developers.openai.com/api/reference/resources/images/methods/edit)。

## 遮罩編輯與貼回

- 編輯流程內建遮罩編輯器（畫筆、橡皮擦、形狀/套索工具、縮放、羽化），用於精準繪製編輯區域；也可匯入既有 PNG 遮罩（黑白遮罩以白色為可編輯區域，柔和透明遮罩以透明度低於 50% 為可編輯區域，同比例匯入會自動縮放符合原圖）。自動補洞與邊緣平滑預設開啟，且只會增加可編輯像素、不會移除已繪製區域，兩者都即時反映在預覽與覆蓋率讀數中。
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

部分閘道會先接收任務、回傳狀態/結果 URL，稍後才完成。預設可以用宣告式 JSON 對映取代 OpenAI 路徑：在設定中選擇 **自訂非同步供應商**，再貼上你的閘道對應的對映（下方範例列出全部欄位；輸入框為空時會以灰色顯示相同的骨架）。

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

- **流程**：把轉譯後的請求本文 `POST` 到 `API URL + submit.path`，從回應取得狀態 URL，輪詢直到 `status_path` 的值屬於 `done`（或 `failed`），視需要再請求 `result.url_path`，最後從 `images_path` 讀取圖片（URL 或 base64）。
- **變數**：`prompt`、`model`、`n`、`width`、`height`、`size`、`quality`、`output_format`、`background`。值只是一個沒有內容的變數（例如尺寸為 `auto` 時的 `width`）時，該欄位會被省略。`submit.path` 只能使用 `{{model}}`。不會把任何內容當作程式碼執行；路徑只支援 `$.a.b[0]` 與 `[*]`。
- **憑證**：對映中不放任何金鑰。預設的 API 金鑰（環境變數參照或 Secret Registry ID）以 `header: scheme key` 傳送；`header` 為 `Authorization` 或 `X-API-Key`，`scheme` 為 `Bearer`、`Key`、`Token` 或留空。
- **安全**：狀態、結果與取消 URL 來自上游回應，因此必須與預設 API URL 同源（金鑰會隨請求傳送），並通過與提交 URL 相同的 SSRF 與對端 IP 檢查。圖片下載不帶憑證，錯誤訊息會遮蔽敏感內容。
- **限制**：不支援圖片編輯、串流預覽與本機色鍵去背，遮罩會被強制關閉。逾時或取消時會盡力送出一次 `cancel` 請求。Worker 租約到期會重新提交整個任務，因為遠端任務 ID 不會持久化。
- 健康檢查只驗證對映並探測提交 URL，不會真的提交任務。引擎目前僅有模擬連線測試涵蓋，未內建任何特定供應商。

## 串流預覽與費用估算

- 串流預覽需在生成表單中主動開啟，僅適用於數量為 1 的 `/v1/images/generations` 與 `/v1/images/edits`——每張請求圖片都作為獨立執行單元排隊，串流與批次無法組合。可選 1–3 個預覽畫面，畫面數越多上游輸出 token 越多，費用也略高。
- 若相容服務拒絕 `stream`/`partial_images` 參數，工作會以明確錯誤結束，而非自動降級為非串流重試，以免重複計費。
- 階段性預覽圖片僅存在伺服器記憶體中（每個執行單元僅一幀，受 `PREVIEW_CACHE_MAX_ENTRY_MB`/`PREVIEW_CACHE_MAX_ENTRIES` 限制），不會持久化；重新連線的客戶端會拿到仍快取的畫面，重啟或切換 worker 後則無幀可重播。
- **費用估算並非帳單。** 僅在上游回傳 `usage` 時才會計算；缺少 usage 或未設定費率時會顯示原因而非 `$0.00`。內建費率涵蓋 `gpt-image-1` 與 GPT Image 2 / 2.5，取自 OpenAI 官方定價——第三方上游通常並不相同。可透過 `IMAGE_COST_RATES_JSON` 覆寫或新增費率。

## API 概覽

主要後端路由：

| 方法 | 路徑 | 用途 |
| --- | --- | --- |
| `GET` | `/health` | 健康檢查。 |
| `GET` | `/api/access/status` | 讀取存取狀態。 |
| `POST` | `/api/access` | 使用存取密鑰解鎖面板。 |
| `GET` | `/api/version`, `/api/version/latest` | 讀取目前版本與選用的最新 Release 資訊。 |
| `GET/PUT` | `/api/settings/overall-config` | 讀取/儲存 Overall Config Override。 |
| `GET/POST` | `/api/settings` | 讀取/儲存目前預設、提示詞最佳化器、R2 備份、Proxy 與 webhook 設定。 |
| `POST` | `/api/settings/presets` | 建立 API 預設。 |
| `POST` | `/api/settings/presets/{preset_id}/activate` | 啟用已儲存的 API 預設。 |
| `DELETE` | `/api/settings/presets/{preset_id}` | 刪除 API 預設。 |
| `POST` | `/api/settings/presets/{preset_id}/health` | 驗證已儲存的上游預設。 |
| `POST` | `/api/settings/r2/health` | 驗證草稿 R2 備份設定。 |
| `GET/POST` | `/api/prompt-snippets` | 列出/建立可重複使用的提示詞片段。 |
| `POST` | `/api/prompt-snippets/search` | 搜尋提示詞片段。 |
| `PATCH/DELETE` | `/api/prompt-snippets/{snippet_id}` | 更新/刪除提示詞片段。 |
| `GET/POST` | `/api/prompt/optimizer-system-prompt` | 讀取/儲存提示詞最佳化器 System Prompt。 |
| `POST` | `/api/prompt/optimize`, `/api/prompt/optimizer-health` | 最佳化提示詞或探測最佳化器連線狀態。 |
| `POST` | `/api/assistant/health` | 探測 AI Assistant 連線狀態。 |
| `POST` | `/api/assistant/prompt/rewrite`, `/api/assistant/prompt/check`, `/api/assistant/prompt/variants` | Prompt Copilot 改寫、檢查與變體工具。 |
| `POST` | `/api/assistant/generate/recommend-params` | 僅建議目前 API path 支援的生成參數。 |
| `POST` | `/api/assistant/jobs/{job_id}/diagnose`, `/api/assistant/edit/plan` | 診斷工作或規劃編輯，不會自動提交。 |
| `POST` | `/api/assistant/image/prompt` | 在記憶體中驗證並反推一張本機點陣圖的提示詞，不建立 Gallery 記錄。 |
| `POST` | `/api/assistant/image/prompt/optimize` | 結合上傳的來源圖片，最佳化反推提示詞結果。 |
| `POST/GET` | `/api/assistant/gallery/*` | 描述、反推 Prompt、分析、批次分析及讀取本機 Gallery AI 中繼資料。 |
| `GET/POST` | `/api/agent/conversations` | 列出或建立 Agent 對話。 |
| `GET/PATCH/DELETE` | `/api/agent/conversations/{conversation_id}` | 讀取（訊息、圖片引用、進行中的回合）、重新命名或刪除對話；刪除對話不會刪除其產生的 Gallery 圖片。 |
| `POST` | `/api/agent/conversations/{conversation_id}/turns` | 送出訊息（`client_turn_id` 讓重試具冪等性）；以背景回合執行，回傳 `202`。 |
| `GET` | `/api/agent/turns/{turn_id}`, `/api/agent/turns/{turn_id}/events` | 讀取回合狀態，或訂閱可重播的 SSE 事件（`?after=` 或 `Last-Event-ID` 續傳）。 |
| `POST` | `/api/agent/turns/{turn_id}/cancel` | 停止執行中的回合，並取消其排隊中的圖片工作。 |
| `POST` | `/api/generate` | 建立生成工作。 |
| `POST` | `/api/edits` | 以上傳的來源圖片建立編輯工作，支援選用的 PNG 遮罩與貼回偏好設定。 |
| `POST` | `/api/edits/from-gallery/{image_id}` | 以現有 Gallery 圖片建立編輯工作，同樣支援選用的遮罩與貼回偏好設定。 |
| `GET` | `/api/generate/jobs` | 列出即時工作與選用的持久化歷史。 |
| `GET` | `/api/generate/jobs/events` | 工作清單更新的 SSE 串流。 |
| `GET/DELETE` | `/api/generate/{job_id}` | 讀取或取消單一生成/編輯工作。 |
| `GET` | `/api/generate/{job_id}/events` | 單一工作 SSE。 |
| `DELETE` | `/api/generate/jobs/history` | 清除已終止的工作歷史。 |
| `GET` | `/api/gallery` | 列出/搜尋/篩選 Gallery 圖片，支援 `mask_only` 篩選遮罩編輯圖片。 |
| `POST` | `/api/gallery/search` | 使用 JSON 要求本文搜尋/篩選 Gallery，支援 `mask_only` 篩選遮罩編輯圖片。 |
| `GET/DELETE` | `/api/gallery/{image_id}` | 讀取或刪除 Gallery 圖片。 |
| `PATCH` | `/api/gallery/{image_id}/favorite` | 收藏/取消收藏單張 Gallery 圖片。 |
| `POST` | `/api/gallery/{image_id}/nodeimage-upload` | 上傳單張 Gallery 圖片至 NodeImage。 |
| `POST` | `/api/gallery/batch/nodeimage-upload` | 將 Selection Token 選取的 Gallery 圖片批次上傳至 NodeImage，作為非同步工作。 |
| `GET` | `/api/gallery/nodeimage-upload-jobs/{job_id}`, `/api/gallery/nodeimage-upload-jobs/{job_id}/events` | 讀取或訂閱 NodeImage 批次上傳工作。 |
| `DELETE` | `/api/gallery/nodeimage-upload-jobs/{job_id}`, `POST` `/api/gallery/nodeimage-upload-jobs/{job_id}/cancel` | 刪除或取消 NodeImage 批次上傳工作。 |
| `POST/PATCH` | `/api/gallery/batch/*` | Selection Token、收藏、刪除與下載等批次操作。 |
| `POST` | `/api/gallery/thumbnails/status` | 檢查一組 Gallery 圖片的縮圖存在/狀態。 |
| `POST` | `/api/gallery/export-jobs`, `/api/gallery/direct-export-jobs` | 建立非同步 Gallery 匯出工作。 |
| `GET` | `/api/gallery/export-jobs/{job_id}`, `/api/gallery/direct-export-jobs/{job_id}` | 讀取非同步 Gallery 匯出工作狀態。 |
| `GET` | `/api/gallery/export-jobs/{job_id}/events`, `/api/gallery/direct-export-jobs/{job_id}/events` | Gallery 匯出工作 SSE。 |
| `GET` | `/api/gallery/export-jobs/{job_id}/download` | 下載已完成的受追蹤匯出 ZIP。 |
| `POST` | `/api/gallery/sync-jobs` | 建立 R2 備份同步工作。 |
| `GET` | `/api/gallery/sync-jobs/{job_id}`, `/api/gallery/sync-jobs/{job_id}/events` | 讀取或訂閱 R2 備份同步工作狀態。 |
| `GET` | `/api/gallery/import-jobs/{job_id}` | 讀取非同步匯入工作狀態。 |
| `GET` | `/api/gallery/import-jobs/{job_id}/events` | 非同步匯入工作狀態的 SSE 串流。 |
| `GET` | `/api/image/{filename}` | 提供通過授權的圖片位元組。 |
| `GET` | `/api/thumb/{filename}` | 提供已生成的 Gallery 縮圖。 |
| `GET` | `/api/download/{filename}` | 下載單張 Gallery 圖片。 |
| `GET` | `/api/download-all?export_job_id=` | 通過直接匯出任務串流匯出 Gallery ZIP。 |
| `POST` | `/api/import` | 匯入 Gallery ZIP；`async_job=true` 會建立匯入工作。 |
| `GET` | `/api/metrics`, `/api/metrics/prometheus` | 設定 `ENABLE_METRICS=true` 時可用。 |

公開 API 已有契約測試。除非刻意進行破壞性變更，否則請維持路徑、方法、狀態碼、SSE 事件名稱、Cookie 與回應結構穩定。

## 貢獻者界線

- 瀏覽器要求應維持同源 `/api/*`；請勿從前端直接呼叫上游模型 API、R2、webhook 目標或任意圖片 URL。
- 維持現有程式碼分層：
  - 路由與要求協調位於 `backend/app/api/routers/`
  - DTO 位於 `backend/app/schemas/`
  - 持久化與 SQLite 協調位於 `backend/app/repositories/`
  - 上游整合位於 `backend/app/integrations/`
  - 前端鏡像 API 型別位於 `frontend/src/lib/api/types.ts`
- 除非刻意進行破壞性變更，否則請維持下列公開契約：
  - API 路徑、方法、狀態碼、Cookie、SSE 事件名稱與回應結構
  - 生成/編輯佇列生命週期、取消語意與多 Worker SQLite 協調
  - 檔案系統競爭透過 UUID 檔名、原子替換與孤兒檔案 GC 來容忍；請勿依賴程序內鎖實作跨 Worker 互斥
- 集中管理驗證與安全邏輯：
  - 圖片位元組驗證、安全路徑、縮圖/封存輔助函式
  - SSRF 敏感 URL 處理應位於 Validator、Safe Connector 與整合 Client
  - 前端只能看到遮罩後的 Secret 或環境變數參照中繼資料
- 保留目前的執行期限制：
  - 編輯工作最多接受 16 張點陣來源圖片，外加最多 4 MB 的單張 PNG 遮罩
  - Gallery ZIP 匯入/匯出沿用既有安全限制
  - SSE 使用具有全域/單一 IP 上限與 TTL 的 SQLite Slot Lease
  - R2 同步僅作備份；本機 SQLite 記錄與本機圖片檔案仍是唯一真實來源
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
