<div align="center">
  <br />
  <img src="frontend/static/favicon.svg" alt="GPT Image Panel logo" width="128" height="128" />

  <h1>GPT Image Panel</h1>

  <hr />

  <p>
    <strong>自架的 GPT 相容圖片生成與編輯面板。</strong>
  </p>

  <p>
    <a href="./README.md">English</a> ·
    <a href="./README.zh-CN.md">简体中文</a> ·
    <a href="#繁體中文">繁體中文</a>
  </p>

  <p>
    <img alt="CI passing" src="https://img.shields.io/badge/CI-passing-2cc653?logo=github&logoColor=white" />
    <img alt="Release v1.9.5" src="https://img.shields.io/badge/release-v1.9.5-0e8dcc" />
    <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white" />
    <img alt="Node.js 24" src="https://img.shields.io/badge/Node.js-24-339933?logo=node.js&logoColor=white" />
    <img alt="FastAPI 0.115+" src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white" />
    <img alt="SvelteKit 3" src="https://img.shields.io/badge/SvelteKit-3-FF3E00?logo=svelte&logoColor=white" />
    <img alt="License CC BY-NC 4.0" src="https://img.shields.io/badge/License-CC_BY--NC_4.0-6f42c1" />
    <img alt="Image GHCR" src="https://img.shields.io/badge/GHCR-gpt--image--linux-1f6f8b?logo=github&logoColor=white" />
  </p>
</div>

<a id="繁體中文"></a>

## 概述

GPT Image Panel 是一個用於圖片生成、編輯與圖庫管理的 Web 介面，資料儲存在你自己的伺服器上。它連接外部的 GPT 相容圖片 API，並將圖片與中繼資料存入本機 SQLite。

本專案只是一個控制面板，不提供、不代理、不轉售、也不修改任何上游模型服務。生成能力、計費、帳號權限與內容政策均來自你設定的服務商。

## 功能

- **生成**：支援 `/v1/images/generations`、`/v1/responses` 以及 OpenAI 相容的 `/v1/chat/completions`。
- **GPT Image 2.6**（`gpt-image-2.6-flare` / `gpt-image-2.6-sunburst`）：獨立複雜度控制、向量 SVG、動畫 GIF 與標註引導編輯。同時支援 **GPT Image 2.5**，預設模型仍為 `gpt-image-2`。
- **編輯**：透過 `/v1/images/edits` 進行，內建遮罩編輯器（畫筆/形狀/套索、羽化、自動補洞）與網頁端標註編輯器（畫筆/箭頭/形狀/文字、100 步復原）。遮罩編輯結果會貼回未修改的原圖區域，並具備色彩漂移防護。
- **參考素材包**：有序的圖庫收藏夾，可作為多圖編輯的參考來源，也可在 Agent 對話中以 `@別名` 引用。
- **API 預設**：base URL/path/key、健康檢查、SOCKS5 Proxy、webhook、環境變數參照式密鑰，以及能力開關（`supports_mask`、`prompt_guard`）。
- **AI Assistant**：提示詞改寫/檢查/變體、參數建議、工作診斷、編輯規劃與圖庫圖片分析。需要端點模型支援 function calling。
- **Agent 模式**：多輪對話中由模型規劃並透過工作佇列生成圖片——支援批次、有相依性的後續輪次，以及用 `@` 引用修改歷史圖片。歷史儲存在伺服器端，串流回覆可斷線續傳。
- **工作佇列**：基於 SQLite，支援 SSE 進度、取消、重試、持久化歷史與共用並行限制。連續點擊生成會把結果附加到同一個預覽群組，而不是取代先前的結果；每個執行中的工作獨立追蹤。
- **串流預覽**（選用，1–10 張）：上游回傳 `usage` 時，工作歷史顯示 token 用量與估算美元費用。
- **圖庫**：游標分頁、搜尋篩選、收藏、含封面概覽的收藏夾、燈箱、批次操作、ZIP 匯入匯出與縮圖。
- **選用 Cloudflare R2** 備份同步；本機 SQLite 與圖片仍是唯一真實來源。
- **安全**：存取密鑰、IP/Host 允許清單、可信任 Proxy Header、CSRF 檢查、CSP nonce，以及選用的 JSON/Prometheus 指標。

## 快速開始

### Docker Compose

```bash
cp .env.example .env
# 編輯 .env：設定 ACCESS_KEY 與上游 API 憑證
docker-compose up -d --force-recreate
```

開啟 `http://127.0.0.1:9090`。

Compose 綁定迴環位址並使用明文 HTTP，本機使用時需停用 Secure cookie：

```bash
ACCESS_COOKIE_SECURE=false ACCESS_KEY_COOKIE_NAME=gpt_image_access docker-compose up -d --force-recreate
```

透過 HTTPS 提供服務時請保持 `ACCESS_COOKIE_SECURE=true`。若本機測試想略過存取驗證，可留空 `ACCESS_KEY` 並設定 `ALLOW_UNAUTHENTICATED=true`——此時所有非健康檢查介面都將公開。

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

映像使用含雜湊的 `requirements.lock` 安裝相依套件，每次建置版本完全一致。若 Docker Hub 存取緩慢，可替換基礎映像：

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

使用 HTTPS 時需設定相符的來源與 Host 允許清單：

```dotenv
PUBLIC_ORIGIN=https://panel.example.com
ALLOWED_HOSTS=panel.example.com
ACCESS_COOKIE_SECURE=true
```

Caddy 自動 HTTPS 要求網域名稱解析到本機且 `80`/`443` 連接埠可達。若前面有 CDN 代理，請確認其邊緣憑證涵蓋完整網域名稱，否則瀏覽器可能在流量到達 Caddy 前就報 `ERR_SSL_VERSION_OR_CIPHER_MISMATCH`。Caddy 若執行於容器內，`127.0.0.1` 指向的是該容器，應改用應用程式服務名稱。

### 常見問題

| 現象 | 原因與處理 |
| --- | --- |
| `400 Bad Request: Host is not allowed` | `Host` 標頭與 `ALLOWED_HOSTS` / `PUBLIC_ORIGIN` 不符。修正拼字後執行 `docker-compose up -d --force-recreate`。 |
| 明文 HTTP 下登入迴圈 | `ACCESS_COOKIE_SECURE=true` 會阻止 HTTP 儲存 cookie。直接 HTTP 測試時設為 `false` 並覆寫 cookie 名稱。 |
| 儲存預設失敗 | 使用綁定相同用途與來源的密鑰參照，或在合適情境下啟用明文密鑰。 |

## 設定

全部環境變數與預設值見 **[CONFIGURATION.md](CONFIGURATION.md)**；`.env.example` 列出了常用項目。

設定依三層解析：

1. **環境變數** —— 行程啟動時讀取一次。
2. **Overall Config** —— 透過 `/api/settings/overall-config` 持久化到 SQLite 的覆寫值。可熱載入的鍵立即生效，其餘需重新啟動。
3. **Web 設定** —— API 預設、提示詞最佳化器、AI Assistant、R2 與 NodeImage 一經儲存即從 SQLite 讀取，此後對應的環境變數不再生效。

Compose 只轉送 `docker-compose.yml` 中列出的變數。若密鑰登錄檔參照了其他環境變數，需在服務環境中明確加入。

常用變數：

| 變數 | 用途 |
| --- | --- |
| `ACCESS_KEY` / `ALLOW_UNAUTHENTICATED` / `TURNSTILE_*` | 存取驗證（除非允許未認證，否則必填）；選用 Cloudflare Turnstile。 |
| `DEFAULT_API_URL` / `DEFAULT_API_KEY` / `DEFAULT_API_PATH` | 預設上游預設。建議用 `${OPENAI_API_KEY}` 這類環境變數參照。 |
| `PUBLIC_ORIGIN` / `ALLOWED_HOSTS` | 反向代理 Host/CSRF 強化，必須與實際網域名稱一致。 |
| `MAX_ACTIVE_GENERATE_JOBS` / `MAX_QUEUED_GENERATE_JOBS` | 並行圖片單元上限與佇列容量（超出回傳 `429`）。 |
| `GRANIAN_WORKERS` | Worker 行程數，應與實際行程數一致。 |
| `IMAGES_DIR` / `DATA_DIR` / `DATABASE_FILE` / `LOG_LEVEL` | 儲存路徑與日誌。 |
| `AI_ASSISTANT_*` / `PROMPT_OPTIMIZER_*` | Assistant 與最佳化器端點；Assistant 預設開啟。 |
| `R2_*` / `NODEIMAGE_API_KEY` | 選用圖庫備份與 NodeImage 上傳。 |
| `SECRET_REGISTRY_JSON` | 宣告 `${ENV_VAR}` 參照及其用途、來源綁定。 |
| `ENABLE_METRICS` | 啟用 JSON/Prometheus 指標。 |

開啟 `ENABLE_METRICS=true` 後，`/api/metrics` 會揭露 SQLite 協調診斷：`sqlite.write_txn`、`sqlite.write_lock_wait_ms`、`sqlite.write_txn_hold_ms`（隨 `MAX_ACTIVE_GENERATE_JOBS × GRANIAN_WORKERS` 成長時關注 p95）、`sqlite.busy_retries`，以及圖片單元租約健康度（`lease_renewed`、`lease_lost`、`unit_reclaimed`）。`lease_lost` 應保持為 0。

## 使用方式

1. 開啟面板，用 `ACCESS_KEY` 解鎖。頂部按鈕可在英文與繁體中文間切換，依瀏覽器記憶。
2. 在**設定**中建立或選擇 API 預設：base URL、路徑、模型、回應格式，以及 API 密鑰或環境變數參照。
3. 視需要設定 SOCKS5 Proxy、webhook、提示詞最佳化器、AI Assistant、R2 備份或 NodeImage 上傳，儲存後執行健康檢查。
4. 輸入提示詞生成圖片，或上傳/選擇來源圖進行編輯。
5. 使用**圖庫**重複使用、篩選、收藏、批次操作、匯入匯出與 R2 同步。

**預設快捷連結**：`/?apiUrl=https://api.example.com&apiModel=gpt-image-2` 會提示以該 URL 與模型建立預設。確認前不會儲存任何內容，API 密鑰保持為空，僅接受 `https`，參數會從網址列移除。（`?model=` 是圖庫篩選參數，故此處用 `apiModel`。）

**預設分享**：**匯出**下載不含密鑰的版本化 JSON 封裝，**分享連結**複製 `?preset=` 連結，**匯入**會先預覽檔案、剪貼簿 JSON 或分享連結再套用。

**Agent 模式**：在設定 → AI Assistant 中啟用並選擇支援 function calling 的模型，然後在頂部切換到 **Agent**。用 `@round-1-image-1` 引用先前的圖片，可附加圖庫圖片，按 Ctrl/Cmd+Enter 傳送。用 Stop 取消進行中的回覆。

### Agent 分支與聯網搜尋

**編輯訊息 → 作為新分支傳送**或**重新生成**會建立歷史輪次的同級分支；**對話路徑**可在重新整理後還原任意路徑。切換路徑不會發起請求也不會中止工作——編輯或重新生成前請先停止目前輪次。只有執行路徑上的祖先會進入模型歷史。圖片 ID 保持穩定，但顯示的輪次編號跟隨目前路徑。刪除圖庫圖片會使所有路徑上的參照失效；刪除對話則保留其圖庫圖片。

聯網搜尋**預設關閉**。確認端點/模型支援 Responses 聯網搜尋後再啟用**允許聯網搜尋**，此功能要求走 `/v1/responses` 路徑。不相容的設定會阻止送出，而不是靜默地以關閉搜尋的方式重試。上游標註提供的引用會隨輪次儲存，並在重新整理後還原。

### GPT Image 2.6

`gpt-image-2.6-flare` 適合快速迭代，`gpt-image-2.6-sunburst` 適合精細構圖與精確編輯。兩者均支援 `auto/low/medium/high/xhigh/max` 品質等級，**複雜度**獨立於品質控制規劃。

- **標註編輯**：在瀏覽器中繪製（**繪製標註**）或上傳透明 PNG 圖層。標註作為 `markup` 單獨傳送，絕不與原圖合併。它必須與正向主圖相符、包含透明像素，且小於 50 MiB。標註與遮罩互斥，標註編輯不使用貼回。
- **SVG**：單一向量結果；預覽與下載保留向量原件，圖庫使用點陣縮圖。含活動內容、內嵌圖片或外部資源的 SVG 會被拒絕。
- **GIF**：2–64 影格，每格 20–2000 ms（預設 9 格 × 200 ms）。燈箱播放動畫，縮圖顯示首格。
- SVG/GIF 僅用於生成——不支援參考圖、遮罩、標註、串流預覽或去色。SVG 限一個結果。
- 內建 2.6 費率：文字輸入 $5/M、圖片輸入 $8/M、圖片輸出 $30/M tokens。SVG 另有創作者模型費用；無法取得時估算會標記為不完整。

Docker 映像已包含 SVG 縮圖所需的 Cairo 執行環境。本機開發需先安裝 Cairo（`brew install cairo` 或 `apt-get install libcairo2`）。Apple Silicon 上若載入器找不到 Homebrew 的 Cairo，請以 `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` 啟動後端。

### 遮罩編輯與貼回

編輯流程內建遮罩編輯器（畫筆/橡皮擦/形狀/套索、縮放、羽化、磁吸吸附），也可匯入 PNG 遮罩。自動補洞與邊緣平滑只會增加可編輯像素，不會刪除已繪製區域。儲存的遮罩寫入 `MASKS_DIR`，重試時在確認來源圖仍相符後還原。

預設（`MASK_PASTE_BACK_DEFAULT=true`）只有遮罩內的像素來自模型，其餘部分以羽化邊界還原為未修改的原圖。若模型改動了遮罩外的像素，色彩漂移防護會略過貼回。可在單次編輯中關閉，或在預設中設 `supports_mask=false` 以配合拒絕遮罩上傳的上游。

### 支援的上游路徑

| 路徑 | 說明 |
| --- | --- |
| `/v1/images/generations` | 標準生成；從 `data[]` 讀取圖片資料。 |
| `/v1/responses` | 從 `image_generation_call` 輸出項讀取 base64 圖片。 |
| `/v1/chat/completions` | 從訊息或 SSE 分塊中擷取圖片 URL/base64。 |
| `/v1/images/edits` | 以 multipart 傳送來源圖與支援的編輯參數。 |

`/v1/responses` 與 `/v1/chat/completions` 會停用尺寸/品質/格式/壓縮/數量/背景控制，因為這些路徑的參數約定不同。

### 自訂非同步供應商

接受任務後非同步完成的閘道可以用宣告式 JSON 對映取代 OpenAI 路徑。在設定中選擇**自訂非同步供應商**並貼上閘道的對映。

- **流程**：提交 → 輪詢至終態 → 讀取圖片。路徑僅支援 `$.a.b[0]` 與 `[*]`，不會作為程式碼執行。
- **版本**：v1 從回應中讀取狀態/結果 URL。v2 新增任務 ID 對映（`poll.task_id_path` + `poll.url_template`），適用於只回傳 ID 的供應商；若提交回應已含圖片，可設 `"mode": "sync"`。
- **編輯**：加入 `edit_submit` 段即宣告支援編輯；已設定的遮罩或標註一定會被傳送，無法承載它們的對映會在提交前被拒絕。
- **安全**：狀態/結果/取消 URL 必須與預設同源並通過相同的 SSRF 檢查。圖片下載不攜帶憑證。
- **復原**：單元會持久化遠端任務 ID 與截止時間，Worker 意外結束後由其他 Worker 繼續輪詢而非重新提交。未記錄回應的提交會標記為 `interrupted`，不會自動重提。
- **診斷**：失敗的單元會保留去識別化記錄——開啟**工作歷史 → 診斷**查看階段、HTTP 狀態與失敗的對映路徑。

### 串流預覽與費用

串流預覽對 Images 生成/編輯與 `/v1/responses` 選用開啟，支援 1–10 張、1–3 格中間圖。每張圖片仍是獨立佇列單元，受並行限制約束。若相容的上游拒絕 `stream`/`partial_images`，工作會以明確錯誤失敗，而不是靜默地改用非串流重試——後者可能導致重複計費。

中間影格僅存於伺服器端記憶體（`PREVIEW_CACHE_MAX_ENTRY_MB` / `PREVIEW_CACHE_MAX_ENTRIES`，預設 8 MiB / 500 筆）。重新啟動或換到其他 Worker 後沒有可重播的影格。

**估算費用不是帳單。** 它僅根據上游實際回傳的 `usage` 計算；缺少用量或模型價格時，介面會說明原因而不是顯示 `$0.00`。可用 `IMAGE_COST_RATES_JSON` 覆寫費率。

### 圖庫與通知

- 桌面端可拖曳框選，或使用 Ctrl/⌘/Shift 多選。觸控螢幕上刻意橫向滑動可切換卡片，縱向移動則捲動。
- 圖庫 ZIP 匯出包含收藏夾名稱、素材包別名與成員順序。匯入會建立新收藏夾、為名稱/別名衝突加上後綴，並只還原成功匯入的成員。單次匯入上限 10,000 張圖片。
- 燈箱支援雙指縮放、雙擊縮放，長按可下載/收藏/編輯。Escape 先關閉操作選單再關閉檢視器。
- **工作區偏好 → 工作完成時通知**預設關閉，啟用時會請求權限。頁面在背景時，每個圖片工作或 Agent 輪次通知一次；點擊可開啟結果。需要在頁面關閉後仍能送達的情境請使用 Webhook。
- 佇列中的工作會凍結非密鑰的供應商設定。修改預設的 API 來源會中止佇列工作，而不是把更新後的憑證送往舊來源。

## 開發

### 專案結構

```text
backend/
  app/
    api/            # 公共 API 路由（contract_app.py）
    core/           # 設定、Overall Config
    integrations/   # 上游 API 用戶端
    repositories/   # 持久化、SQLite 協調
    runtime/        # 阻塞/並行輔助
    schemas/        # DTO
    services/       # 編排
  tests/
frontend/
  src/lib/          # 可重用前端程式碼
  src/routes/       # SvelteKit 路由
  static/           # favicon
  tests/
deploy/nginx.conf
Dockerfile  docker-compose.yml  .env.example
```

### 架構

- **後端**：`backend/app/` 下的 FastAPI；ASGI 入口 `backend.app.main:app`，由 Granian 提供服務。
- **前端**：SvelteKit 靜態應用程式；生產環境由後端提供 `frontend/build/`。
- **儲存**：圖片在 `images/`，縮圖在 `images/thumbs/`，SQLite 在 `data/app.sqlite3`，日誌在 `data/logs/`。
- **多 Worker 協調**：工作、租約、SSE 槽位與排程器歸屬均使用 SQLite 租約。檔案寫入使用 UUID 檔名、原子 `Path.replace()` 與孤兒檔案 GC 的 TTL 清理，而不依賴行程內鎖。
- **技術堆疊**：Python 3.11+、FastAPI、Granian、aiohttp、boto3、SQLite、Pydantic v2、Pillow、CairoSVG · SvelteKit 3、TypeScript、Tailwind CSS 4 · Playwright、pytest。瀏覽器基準：Chrome 111+、Safari 16.4+、Firefox 128+。

### 本機開發

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

開啟 `http://localhost:5173`。Vite 會把 `/api` 與 `/health` 代理到 `127.0.0.1:9090` 的 FastAPI。

生產模式快速驗證：

```bash
npm run frontend:build
ALLOW_UNAUTHENTICATED=true .venv/bin/granian --interface asgi backend.app.main:app --host 127.0.0.1 --port 9090
```

### 測試

```bash
npm run frontend:check
npm --prefix frontend run test:unit
.venv/bin/python -m pytest backend/tests -q
npm run test:contract
npm run test:e2e
```

`frontend:check`、`test:unit` 與後端測試套件無需建置即可執行。打包與 Worker 檢查針對生產建置，需先執行 `npm run frontend:build`：

```bash
npm --prefix frontend run check:bundle
npm --prefix frontend run check:worker-build
```

選用的效能測試：

```bash
npm run test:perf
npm run test:e2e:perf
```

若缺少 Playwright 瀏覽器：`npm --prefix frontend exec playwright install chromium`。依改動範圍執行相關子集；發布相關改動請全部執行。

### 貢獻者約定

- 瀏覽器請求一律經 `/api/*` 同源存取——前端不得直接呼叫上游 API、R2、webhook 目標或任意圖片 URL。
- 保持職責邊界：路由在 `backend/app/api/routers/`，DTO 在 `schemas/`，持久化在 `repositories/`，上游用戶端在 `integrations/`，前端鏡像型別在 `frontend/src/lib/api/types/`。
- 除非有意做破壞性變更，否則保持公共契約穩定：路徑、方法、狀態碼、cookie、SSE 事件名稱、回應結構、佇列生命週期與取消語意。
- 保持驗證集中：圖片位元組驗證、安全路徑、SSRF 相關 URL 處理與密鑰去識別化。
- 新增環境變數時，需同步更新 `core/settings.py`、使用者可見時更新 `core/overall_config.py`、`.env.example`、可在 Compose 設定時更新 `docker-compose.yml`，以及 `CONFIGURATION.md`。
- 切勿提交執行階段產物：`images/`、`data/`、`frontend/build/`、`.svelte-kit/`、Playwright 報告、相依套件目錄、資料庫或日誌。

## 授權條款

本專案採用 [CC BY-NC 4.0](./LICENSE)（Creative Commons Attribution-NonCommercial 4.0 International）授權。
