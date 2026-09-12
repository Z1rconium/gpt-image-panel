# 图像任务协调层加固计划（SQLite 写放大 / 租约失效 / 内存-持久层双写）

## 0. 结论摘要

针对提出的三个风险逐条核对了当前代码（`main` @ `29f0330`），结论如下：

| # | 提出的风险 | 核对结论 | 实际缺陷 |
|---|-----------|----------|----------|
| 1 | SQLite 写放大是真实瓶颈；SSE poll、心跳、stage 更新争抢同一 WAL；`busy_timeout=30s`+抖动重试只是缓解 | **部分成立，但描述与代码不符**。WAL 模式下读不阻塞写，SSE poll 与 `COUNT(*)` 是纯读，不参与写锁竞争；真实写入速率很低（每个 unit 生命周期约 10 次写，进度写被限流到 1 次/秒/unit，父任务 running 态持久化限流到 1 次/5 秒）。热路径的 busy 预算并不是 30s，而是 **250ms × 6 次 ≈ 2.1s**（`SQLITE_BUSY_TIMEOUT_MS=250`、`SQLITE_BUSY_RETRY_ATTEMPTS=5`），30s 只用于绕过 `run_db_operation` 的直连路径 | D5：热路径 busy 预算过短且与 `asyncio.to_thread` 直连路径（30s）不对称，终态写失败会触发 D1；D6：空闲时每个 claim 循环每轮都 `BEGIN IMMEDIATE`；D7：缺少写锁等待/持有时长指标，无法验证"是否线性恶化" |
| 2 | 心跳/租约靠时间失效，时钟漂移或 GC 暂停会误判 | **方向对，但真正的问题更严重**。`worker_heartbeats` 只用于指标展示和清理，**不参与任何调度判断**；真正的租约是 `image_job_units.claim_expires_at`（120s），它**只在 progress 持久化时续期**，而上游等待阶段（`waiting_for_api`）不产生 progress，上游超时却是 600s。租约过期后单元会被重新 claim（包括被同一 worker 重复 claim），且终态写没有所有权校验 | **D1：慢上游（>120s）导致同一 unit 被重复执行、重复计费、覆盖写**（已用脚本复现）；**D2：租约过期的 running 单元仍计入 `running_count`，孤儿单元会永久占满并发额度，队列卡死且重启不恢复**（已复现） |
| 3 | `app.state.generate_jobs` 与持久层双写不一致，跨 worker 可能出现"幽灵 running 态" | **成立，但窗口位置与描述不同**。`store_generate_job_async` 内存写在持久写之前，同一协程内顺序执行，不存在"已持久终态但内存仍 running"的原子性窗口。真实问题是：(a) 读路径无条件优先内存副本、且内存副本只在有列表 SSE 订阅者时才与存储对账；(b) 父任务终态写是全列覆盖的 upsert，没有状态/所有权守卫，跨 worker 或同进程内的多 unit 聚合竞争都可能把终态回退成 running | **D3：父任务写无守卫，可被过期基线覆盖（跨 worker 取消覆盖成功态；同进程多 unit 聚合竞争回退终态）**；**D4：非执行 worker 上 `GET /api/generate/{id}`、SSE 首帧、取消前置检查读到过期内存副本** |

优先级：D1 > D2 > D3 > D4 > D5 > D7 > D6。D1/D2 直接影响计费正确性与可用性，必须先做。

---

## 1. 核对依据

### 1.1 写入速率与锁竞争（风险 1）

- 连接与事务：`backend/app/repositories/db.py:927-938`（`_open_connection`，默认 `busy_timeout=30000`）、`db.py:1044-1062`（`_transaction`，`BEGIN IMMEDIATE`，仅在超时后 `sqlite.busy +1`）。
- 热路径预算：`backend/app/core/settings.py:114-117`（`DB_EXECUTOR_WORKERS=4`、`SQLITE_BUSY_TIMEOUT_MS=250`、重试 5 次、基数 20ms）；`backend/app/services/blocking.py:128-153`（`run_db_operation` 用 `persistent_connection_scope(250ms)` + 指数抖动重试，总预算 ≈ 6×250ms + 620ms ≈ 2.1s，超出后**抛出 `OperationalError`**）。
- 直连路径：`backend/app`下共 183 处 `asyncio.to_thread(...)` 调用绕过 `run_db_operation`（gallery_jobs 34、gallery_maintenance 26、settings 路由 23、gallery_tasks 23 ……），这些使用 30s busy 超时，和热路径预算不对称。
- 长事务候选：`backend/app/repositories/gallery/mutations.py:510-560`（`sync_gallery_with_image_files` 在**一个** `BEGIN IMMEDIATE` 内分页扫描整张 `gallery_entries`；大图库滚动重启时可持锁数秒，足以耗尽另一 worker 热路径的 2.1s 预算）。
- 写入点清单（每 unit）：claim 1 次；起始 progress 1 次 + 父任务 upsert 1 次；进度写受 `IMAGE_JOB_PROGRESS_PERSIST_INTERVAL_SECONDS=1` 限流（`job_executor.py:331-353`），父任务 running 态受 `GENERATE_JOB_PERSIST_INTERVAL_SECONDS=5` 限流（`job_events.py:318-329`）；完成阶段约 5 次（gallery entry 更新、unit 完成、父任务聚合、`trim_generate_jobs`、edit source 清理）。心跳每 5s/进程 1 次（`job_scheduler.py:20-45`），仅在活跃数变化或到期时写。
- 读路径：SSE 轮询（`backend/app/api/routers/generate.py:78-165`）每 0.35s 执行 1~2 条索引读（`idx_generate_jobs_status_updated_at`），WAL 下不与写锁竞争；真正会排队的是 **4 线程的 DB executor**，指标 `executor.db.queue_wait` 已存在。
- 空闲写锁抖动：`claim_next_image_job_unit`（`image_jobs.py:385-448`）以及 gallery/thumbnail/ai-analyze 的 claim 都是**先 `BEGIN IMMEDIATE` 再查候选**，空闲时每进程每秒约 2 次取写锁（image 2s 退避 + 4 个 gallery 循环 5s 退避 + thumbnail 5s + ai 5s + 心跳）。空事务不写 WAL，但会串行化所有 worker 的写锁获取。

### 1.2 租约与心跳（风险 2）

- 心跳唯一消费者是指标：`backend/app/repositories/coordination.py:1155-1168`（写）、`coordination.py:1232-1256`（读，只算 `workers.active`）、`thumbnail_jobs.py:288`（启动清理）。**没有任何调度逻辑读心跳**。
- 单元租约：claim 时写 `claim_expires_at = now + IMAGE_JOB_UNIT_LEASE_SECONDS(120)`（`job_executor.py:283-284`、`job_scheduler.py:50-58`）；只有 `update_image_job_unit_progress` 会续期（`image_jobs.py:450-481`，通过 `job_executor.py:340-347` 调用）。
- 上游等待期间没有 progress：`backend/app/integrations/upstream/generation.py:335` 发出 `waiting_for_api` 后，直到响应返回才有下一次 progress；上游超时 `total=600s`（`backend/app/integrations/session_pool.py:11-16`）。**慢于 120s 的上游调用必然租约过期。**
- 过期重 claim：`image_jobs.py:403-411` 的 `expired_candidate` 优先级高于 `queued_candidate`，且**不排除 `claimed_by = 本 worker`**；`running_count`（`image_jobs.py:398-402`）统计所有 `status='running'`，**不排除已过期租约**。
- 终态写无所有权守卫：`complete_image_job_unit`（`image_jobs.py:483-533`）、`fail_image_job_unit`（`image_jobs.py:535-590`）、`update_image_job_unit_progress` 的 `WHERE` 只有 `unit_id`（后者多一个 `status='running'`），`claimed_by` 只写不读。
- 启动不再标记 interrupted：`mark_active_generate_jobs_interrupted` 在 commit `88216d7` 被有意从启动流程移除，恢复完全依赖过期重 claim。
- 复现脚本（scratchpad `verify_lease.py`，对当前仓库直接跑 repository 函数）：
  1. `running_limit=1` + 1 个租约已过期的 running 单元 → 任何 worker `claim` 均返回 `None`（**D2：队列卡死**）。
  2. `running_limit=2` + 同一 worker A 再次 claim → 返回**同一个 unit**，`claimed_by` 仍为 A（**D1：同进程重复执行**）。
  3. 原持有者在租约丢失后调用 `complete_image_job_unit` → 仍被接受并覆盖结果（**D1：无 fencing**）。
- 时钟：所有比较都用 `datetime.now(timezone.utc).isoformat()` 字符串（`core/utils.py:4-5`），格式一致，字典序比较正确；跨容器时钟漂移会等比缩放租约（漂移 Δ 秒 = 租约 ±Δ），在有续期机制后只需 Δ ≪ 续期间隔即可。

### 1.3 内存副本与持久层（风险 3）

- 写顺序：`store_generate_job_async`（`job_events.py:355-398`）先改内存（active 写入 / terminal 弹出）再 `await upsert`，随后 publish。同协程内顺序执行；`await` 期间的窗口是"内存已终态（弹出）而 DB 仍 running"，`GET` 会回退读 DB 得到 running，对单 worker 无感——**与描述方向相反，且无害**。
- 读优先内存且无过期校验：`generate.py:305`（`GET /api/generate/{job_id}`）、`generate.py:336`（单任务 SSE 首帧）、`generate.py:422`（取消前置检查）、`job_events.py:302-315`（`build_job_update`）、`job_events.py:361`（`store_generate_job_async` 的 merge 基线）。
- 对账只在列表 SSE 有订阅者时触发：`reconcile_active_generate_jobs`（`job_events.py:152-163`）由列表 poller（`generate.py:98-99`）和 `stream_generate_jobs` 首帧调用；没有浏览器连到该 worker（纯 API/webhook 客户端）时，非执行 worker 的内存副本永远停留在入队时的 `queued`。
- 父任务写是全列覆盖：`db.py:2663-2687`（`_upsert_generate_job_on_conn`，除 `webhook_url` 外全部 `excluded.*`），无 `status IN (active)` 或 `updated_at` 守卫。
- 由此推演出两条真实路径：
  - **跨 worker 取消覆盖成功态**：worker B 内存为 `queued/running`（过期），DB 已 `success`；`DELETE /api/generate/{id}` 在 B 上通过 409 检查 → `store_generate_job_async(cancelled)` 以过期内存为基线 upsert → DB 成功记录被改写为 `cancelled`（图片字段随基线丢失，图库文件仍在）。
  - **同进程多 unit 聚合回退**：`n=2`，两个 unit 几乎同时完成，各自 `aggregate_parent_image_job`（`job_executor.py:94-281`）先读后写，读写在 4 线程 executor 上不保序；unit1 读到 1/2 → 写 `running`（`force_publish=True` 强制持久化）晚于 unit2 写 `success` 落库 → DB 与内存均为 `running` 且之后没有任何触发点再聚合 → **幽灵 running 态**，webhook 已按 success 发送。窗口窄但结构上成立。

---

## 2. 缺陷清单（按严重度）

| ID | 缺陷 | 影响 | 触发条件 |
|----|------|------|----------|
| D1 | 单元租约在上游等待期不续期；过期后可被任意 worker（含自身）重新 claim；终态/进度写无 fencing | 同一 unit 重复调用上游 → 重复计费、图库重复图片、后写覆盖前写；两个执行体同时向父任务聚合 | 上游耗时 > 120s（高质量/大尺寸模型、代理链路慢），或终态写因 busy 失败（D5） |
| D2 | `running_count` 含过期租约；无重试次数上限；启动不清理孤儿 | 崩溃/`SIGKILL` 后遗留的 running 单元占满 `MAX_ACTIVE_GENERATE_JOBS`，队列永久卡死，重启无法恢复；反之若额度未满则毒单元被无限次重跑 | 任一进程异常退出时有 in-flight 单元 |
| D3 | 父任务终态写无状态守卫，聚合"读-算-写"不在一个事务内 | 终态回退为 running（幽灵态）、成功态被取消覆盖、webhook 与最终状态不一致 | 跨 worker 取消；`n>1` 且多 unit 近乎同时完成 |
| D4 | 读路径无条件优先 `app.state.generate_jobs`，对账依赖列表 SSE 订阅者 | 非执行 worker 上 `GET`/SSE 首帧/取消检查返回过期状态 | `GRANIAN_WORKERS>1` 且该 worker 没有列表 SSE 订阅者 |
| D5 | 热路径 busy 预算 ≈2.1s，与 30s 直连路径不对称；存在持锁数秒的长事务 | 终态写失败 → 单元滞留 running → 进入 D1/D2 | 大图库启动同步、批量导入/维护任务与生成高峰重叠 |
| D6 | 空闲 claim 每轮 `BEGIN IMMEDIATE` | 多进程下写锁串行化抖动，放大 D5 的 busy 概率 | 常态 |
| D7 | 无写锁等待/持有时长指标，无租约事件指标 | 无法回答"负载下是否线性恶化"，D1/D3 发生时无告警 | — |

---

## 3. 设计原则

1. **不更换存储**。继续以 SQLite WAL 作为唯一协调点；本计划只修正协议正确性与可观测性，不引入 Redis/Postgres。
2. **所有权用 token 而不是 worker_id**。同一 worker 重复 claim 自己的过期单元是已复现路径，`claimed_by` 无法区分两次 claim。
3. **终态只允许从 active 转入，且在单个事务里由 unit 行推导**。父任务状态是 unit 行的纯函数，不再由进程内存推导后覆盖写入。
4. **内存副本降级为缓存**：只用于执行 worker 上的高频进度展示与列表快照；任何决策性读取（GET、SSE 首帧、取消）以 DB 为准，内存只做"更新且更新时间更新"的叠加。
5. **失败要收敛**：租约丢失时执行体必须主动中止上游请求并放弃写入；重跑次数有上限。

---

## 4. 实施计划

### Phase 1 — 单元租约正确性（修 D1、D2）

**Schema（新增 migration 19 `image_job_unit_lease_fencing`）**

```sql
ALTER TABLE image_job_units ADD COLUMN claim_token TEXT;
ALTER TABLE image_job_units ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
DROP INDEX IF EXISTS idx_image_job_units_running_count;
CREATE INDEX IF NOT EXISTS idx_image_job_units_running_count
    ON image_job_units(claim_expires_at) WHERE status = 'running';
```

- 加入 `IMAGE_JOB_UNIT_COLUMNS` 与 `_image_job_unit_from_row`（`db.py`）。
- 旧版本在途单元 `claim_token IS NULL`：升级后其终态写会被 fencing 拒绝，随后由过期重 claim 恢复（单次重跑，可接受；在 release note 里说明"升级前请等待队列排空"）。

**Repository（`backend/app/repositories/image_jobs.py`）**

- `claim_next_image_job_unit(..., claim_token: str, max_attempts: int)`：
  - `running_count` 改为 `WHERE status='running' AND claim_expires_at > :now`。
  - `expired_candidate` 保留，但 `UPDATE` 时 `attempts = attempts + 1`，`claim_token = :token`，`claimed_by = :worker_id`，并且只选 `attempts < :max_attempts` 的过期单元。
  - 新增 `expire_exhausted_image_job_units(now, max_attempts) -> list[unit]`：把 `status='running' AND claim_expires_at <= now AND attempts >= max_attempts` 的单元置为 `status='interrupted', stage='interrupted', error='Unit lease expired after N attempts'`，返回受影响的 `parent_job_id` 供父任务聚合。由 dispatcher 在每次 claim 循环 `before_cycle` 中低频（每 lease/2 秒）调用。
- 新增 `renew_image_job_unit_lease(unit_id, claim_token, claim_expires_at) -> bool`：
  `UPDATE image_job_units SET claim_expires_at=? WHERE unit_id=? AND status='running' AND claim_token=?`，返回 `rowcount == 1`。不更新 `updated_at`（避免无意义的 SSE 边沿变化）。
- `update_image_job_unit_progress` / `complete_image_job_unit` / `fail_image_job_unit` 增加 `claim_token` 参数并加入 `WHERE ... AND claim_token = ?`；`rowcount == 0` 时返回 `None`，调用方据此判定租约已丢失。
- `cancel_image_job_units` 不需要 token（取消是全局意图，允许覆盖任何持有者）。

**Executor（`backend/app/services/job_executor.py`）**

- `run_claimed_image_unit` 读取 `unit["claim_token"]`，贯穿所有写入。
- 新增租约续期协程 `renew_lease_loop()`：每 `IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS`（默认 `IMAGE_JOB_UNIT_LEASE_SECONDS / 3` = 40s）调用 `renew_image_job_unit_lease`；返回 `False`（token 已被换或单元已取消）时立即置 `lease_lost.set()` 并 `metrics.increment("image_jobs.lease_lost")`。
  - 续租抛出 DB 异常（busy 预算耗尽等）**不等于**租约丢失：本地维护 `lease_deadline = 上次成功写入 claim_expires_at 的时刻 + lease`，异常时每 5s 重试并计 `image_jobs.lease_renew_retry`，只有当 `deadline - now <= 重试间隔` 仍未成功才 `mark_lease_lost()`。否则一次 2s 的 SQLite 停顿就会中止一个昂贵的上游调用并烧掉一次 attempt（把 D5 放大）。
  - 上游调用用 `asyncio.create_task` 包装后，`asyncio.wait` 不会级联取消：外层取消（优雅停机）、租约丢失、`finally` 三处都必须显式 `cancel()` 并 `await` 上游任务，否则请求会跑完、其迟到的 progress 写被 fencing 拒绝后误报为 `lease_lost`。
- 上游调用用 `asyncio.wait({upstream_task, lease_lost_waiter}, return_when=FIRST_COMPLETED)` 包裹；租约丢失时 `upstream_task.cancel()`，抛出新的 `UnitLeaseLostError`，**不写任何终态**（所有权已转移），只记日志 + 指标，`finally` 中不再聚合父任务。
- 进度持久化保留现有节流；`update_image_job_unit_progress` 返回 `None` 同样视为租约丢失。
- 优雅停机路径（`CancelledError`）行为不变：`fail_image_job_unit(status='cancelled')` 携带 token；若 token 已失效则跳过写。

**Scheduler（`backend/app/services/job_scheduler.py`）**

- `claim_unit()` 生成 `claim_token = uuid4().hex`，传 `max_attempts=config.IMAGE_JOB_UNIT_MAX_ATTEMPTS`。
- `before_cycle` 每 `IMAGE_JOB_UNIT_LEASE_SECONDS / 2` 秒调用一次 `expire_exhausted_image_job_units`，对返回的父任务调用 Phase 2 的 `finalize_parent_job`。

**Settings（`backend/app/core/settings.py`、`overall_config.py`、`.env.example`、README）**

- `IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS`（默认 `max(5, lease/3)`，必须 `< lease/2`，启动校验）。
- `IMAGE_JOB_UNIT_MAX_ATTEMPTS`（默认 `2`，`min_value=1`）。
- 文档写明：租约 120s 只是"崩溃检测延迟"，不再是"最长上游耗时"；上游耗时上限仍由 `session_pool` 的 600s 决定。

### Phase 2 — 父任务写守卫与内存副本降级（修 D3、D4）

**Repository（`image_jobs.py`）**

- 新增 `finalize_parent_job_from_units(parent_job_id, derive: Callable[[parent, aggregate], dict | None]) -> tuple[dict | None, bool]`：
  在**一个** `BEGIN IMMEDIATE` 内：读父任务行 + `aggregate_image_job_units`（复用现有逻辑，改成 `_on_conn` 版本）→ 调 `derive` 得到 update → 若父任务已是终态则**不写**，返回 `(current_row, False)` → 否则写入并 `RETURNING`，返回 `(row, True)`。
  写入 SQL 用 `UPDATE ... WHERE job_id=? AND status IN (active)`，而不是全列 upsert。
- 新增 `transition_generate_job_to_terminal(job_id, updates) -> tuple[dict | None, bool]`：同样的守卫，供取消路径使用；取消时把父任务终态写与 `cancel_image_job_units` 放进同一事务（`cancel_generate_job_tx(job_id, message)`）。
- `upsert_generate_job` 保留给入队与执行 worker 的 running 态节流持久化；running 态写也加 `WHERE status IN (active)` 守卫（用 `INSERT ... ON CONFLICT DO UPDATE ... WHERE generate_jobs.status IN (...)`）。

**Service（`job_executor.py`、`job_events.py`）**

- `aggregate_parent_image_job` 拆成纯函数 `derive_parent_update(parent, aggregate, *, operation) -> dict | None`（现有分支逻辑原样搬入，便于单测）+ 调 `finalize_parent_job_from_units`。返回 `written=False` 时，用 DB 行刷新内存并 publish，但**不再派发 webhook**（webhook 由第一次真正写终态的调用派发；`pop_generate_job_webhook` 已是一次性消费，双保险）。
- `store_generate_job_async`：终态写改走 `transition_generate_job_to_terminal`；merge 基线在终态时改为 `{**db_row, **memory_if_newer, **updates}`。
- 新增 `resolve_generate_job_view(job_id) -> dict | None`：读 DB 行；若内存副本存在、状态 active 且 `updated_at >= db.updated_at`，叠加内存副本（保留执行 worker 的高频 stage/message），否则以 DB 为准并顺手修正/弹出内存副本。用于 `GET /api/generate/{id}`、单任务 SSE 首帧、取消前置检查。
- `reconcile_active_generate_jobs` 保留，但内存副本额外记录 `_seen_at`（monotonic）；`snapshot_active_generate_jobs_from_memory` 对超过 `2 × GENERATE_JOB_PERSIST_INTERVAL_SECONDS` 未被本进程更新且本进程不持有其 unit 的条目视为过期，触发一次存储对账（无订阅者时也生效，解决"没有列表订阅者就永不对账"）。

**Router（`generate.py`）**

- `cancel_generate_job`：先 `resolve_generate_job_view`（DB 优先），再 `cancel_generate_job_tx`；事务返回"已是终态"时回 409，不再覆盖。
- `GET /api/generate/{job_id}` 与 `stream_generate_job` 首帧改用 `resolve_generate_job_view`。

### Phase 3 — 写锁预算、空闲抖动与可观测性（修 D5、D6、D7）

**Observability（`db.py::_transaction`、`blocking.py`）**

- `_transaction` 计时：`sqlite.write_lock_wait_ms`（`BEGIN IMMEDIATE` 耗时）、`sqlite.write_txn_hold_ms`（BEGIN→COMMIT）；持有超过 `SQLITE_SLOW_TXN_WARN_MS`（默认 500）记 warning，携带 `run_db_operation` 通过 contextvar 注入的 `metric_name`。
- 新增计数器：`sqlite.write_txn`（每次成功 COMMIT +1，用于核对空闲写入量）、`image_jobs.lease_renewed`、`image_jobs.lease_lost`、`image_jobs.unit_reclaimed`、`image_jobs.unit_exhausted`、`image_jobs.parent_write_skipped_terminal`、`image_jobs.claim_precheck_skipped`。
- `/api/metrics` 已聚合 counters/timings，无需新端点；README 的"运维指标"段补充上述指标及判读方法（`sqlite.busy_retries` 与 `write_lock_wait_ms p95` 随 `MAX_ACTIVE_GENERATE_JOBS × GRANIAN_WORKERS` 的变化曲线）。

**热路径预算（`blocking.py`、`settings.py`）**

- `run_db_operation(..., critical: bool = False)`：`critical=True` 使用 `SQLITE_CRITICAL_BUSY_TIMEOUT_MS`（默认 2000）与 `SQLITE_CRITICAL_BUSY_RETRY_ATTEMPTS`（默认 6），总预算约 15s。应用于：claim、`renew_image_job_unit_lease`、`complete/fail_image_job_unit`、`finalize_parent_job_from_units`、`cancel_generate_job_tx`。轮询类读操作保持 250ms（超时只是跳过一轮）。
- 这些调用运行在 executor 线程，不阻塞事件循环；代价仅是占用一个 DB 线程，因此同时把 `DB_EXECUTOR_WORKERS` 的默认值改为 `max(4, MAX_ACTIVE_GENERATE_JOBS // 2 + 2)` 并在启动日志提示。

**空闲 claim 预检（`claim_loop.py` 及各 claim 函数）**

- `run_claim_loop` 新增可选 `claim_precheck_fn: Callable[[], Awaitable[bool]]`；为 `False` 时跳过本轮 `claim_fn`（kick 事件到来时仍强制 claim 一次）。
- 实现只读预检：`has_claimable_image_job_unit(now)`（利用 `idx_image_job_units_claim_queued` / `idx_image_job_units_claim_running_expired` 的 `SELECT 1 ... LIMIT 1`）、`has_claimable_gallery_job(kind, now)`、`has_claimable_thumbnail_job(now)`、`has_claimable_ai_analyze_job(now)`。
- `trim_generate_jobs` 改为先只读 `COUNT(*)`，超限才进写事务。

**长事务拆分（`gallery/mutations.py::sync_gallery_with_image_files`）**

- 扫描阶段改为只读事务收集 `stale_ids`；删除阶段按 `GALLERY_SYNC_BATCH_SIZE` 分批，每批一个短写事务；filter option 增量与 gallery version bump 放在最后一批。行为不变（幂等），只是不再长时间持锁。
- 顺手 grep 其它在 `_transaction` 内做分页循环的函数（`backfill_missing_gallery_bytes` 等），同样拆批。

### Phase 4 — 测试

Python（`backend/tests/test_generation_jobs_contract.py` 或新建 `test_image_job_leases_contract.py`，纳入 `npm run test:contract`）：

1. `test_claim_excludes_expired_leases_from_running_count`：`running_limit=1`、1 个过期 running 单元 → 新 claim 返回该单元且 `attempts == 1`、`claim_token` 变化。
2. `test_claim_caps_attempts_and_marks_exhausted`：`attempts >= max` 的过期单元不再被 claim，`expire_exhausted_image_job_units` 置 `interrupted` 并返回父任务 id，父任务被聚合为 `error/interrupted`。
3. `test_stale_owner_cannot_complete_or_progress_unit`：旧 token 调用 `complete/fail/update_progress/renew` 均返回 `None/False`，行内容不变。
4. `test_lease_is_renewed_during_slow_upstream`：monkeypatch `config.IMAGE_JOB_UNIT_LEASE_SECONDS=2`、renew=0.5s（绕过 settings 的 30s 下限，测试已有此模式），上游 mock sleep 3s；断言只调用一次上游、单元未被重新 claim、`claim_token` 不变、`image_jobs.lease_renewed >= 2`。
5. `test_lease_loss_aborts_inflight_upstream_without_terminal_write`：执行中由另一 token 重新 claim；断言上游任务被取消、失败方未写终态、`image_jobs.lease_lost == 1`、最终结果来自新持有者。
6. `test_parent_terminal_state_never_regresses`：直接调用 `finalize_parent_job_from_units` 先写终态，再用返回 running 的 `derive` 调用 → `written=False`，DB 仍终态；同理对 `upsert_generate_job(running)`。
7. `test_multi_unit_concurrent_completion_keeps_success`：`n=2`，用 `threading.Event` 让两个 unit 同时释放；断言最终 DB/内存/SSE 均为 `success`，webhook 只发一次。
8. `test_cancel_after_success_returns_409_and_keeps_result`：DB 为 success、内存塞入过期 `running` 副本 → `DELETE` 返回 409，DB 不变。
9. `test_get_job_prefers_storage_over_stale_memory`：内存 `queued` 副本 + DB `success` → `GET` 与 SSE 首帧返回 `success`，内存副本被弹出。
10. `test_idle_claim_loop_does_not_take_write_lock`：monkeypatch `db._transaction` 计数；空闲运行 3 个周期，写事务数为 0，kick 后为 1。
11. `test_sync_gallery_with_image_files_uses_bounded_transactions`：注入 5×batch 条目，断言 `_transaction` 进入次数 ≥ ceil(n/batch)，且单次 hold 指标存在。
12. 迁移测试（`test_storage_migrations_contract.py`）：旧 schema 升级后新列存在、默认值正确、旧行可被正常 claim/expire。

可选（`RUN_PERFORMANCE_TESTS=true`，`test_performance.py`）：`multiprocessing.spawn` 两个进程共享同一 `DATABASE_FILE`，各自跑 dispatcher，`MAX_ACTIVE=8`、上游 mock 0.2s、200 个 unit；断言无重复上游调用、`sqlite.busy_retries` 与 `write_lock_wait_ms p95` 在阈值内，输出曲线供 README 引用。

前端：无接口变更；`npm run frontend:check` 通过即可。e2e 现有 `jobs` 相关 spec 回归。

### Phase 5 — 发布

- 迁移是纯新增列/索引，可回滚（旧版本忽略新列）。
- 升级顺序：先排空队列（或接受在途单元被重跑一次），再滚动重启；多进程部署（`GRANIAN_WORKERS>1`）时全部进程升级到同一版本后再恢复流量。
- `VERSION`/`docker-compose.yml`/GHCR 发布按 `AGENTS.md` 流程。

---

## 5. 验收标准

- D1：`IMAGE_JOB_UNIT_LEASE_SECONDS=30`、上游 mock 90s 的场景下，上游调用次数 = 1，图库无重复图片，计费 usage 只计一次。
- D2：kill -9 一个持有 `MAX_ACTIVE` 个单元的进程，另一进程在 ≤ lease + renew 时间内接管；attempts 用尽的单元 ≤ `max_attempts × lease` 内转为 `interrupted`，父任务不再显示 running。
- D3/D4：上述测试 6–9 全绿；跨 worker 手工验证：浏览器只连 worker A，用 curl 打 worker B 的 `GET`/`DELETE`，状态与 A 一致。
- D5/D6/D7：`/api/metrics` 暴露 `sqlite.write_lock_wait_ms`、`sqlite.write_txn_hold_ms`、`image_jobs.lease_*`；空闲 5 分钟内 `sqlite.write_txn` 计数 ≈ 心跳 + runtime metrics 次数（每进程 ≈ 60 + 20），不再有每秒级的 claim 事务；perf 测试中 `MAX_ACTIVE` 从 2 → 8 → 16 时 `write_lock_wait_ms p95` 增长 < 线性。
- 全量 `.venv/bin/python -m pytest backend/tests -q`、`npm run test:contract`、`npm run frontend:check` 通过。

---

## 6. 明确不做

- 不迁移到外部数据库或消息队列；SQLite 仍是单一协调点。
- 不把取消做成"中止在途上游请求"（现为协作式取消，单元结束时才标记 cancelled；跨 worker 亦然）。这与三个风险无关，另开计划。
- 不持久化流式预览帧（现有设计已明确为进程内缓存）。
- 不改变 `GENERATE_JOB_PERSIST_INTERVAL_SECONDS=5` 的 running 态节流：跨 worker SSE 最多滞后 5s 是有意取舍，且 D3/D4 修复后滞后不再导致错误状态。
