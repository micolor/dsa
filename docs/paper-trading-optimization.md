# 模拟盘（Paper Trading）模块优化分析与计划

> 本文档是**内部优化计划**，不是用户可见的手册。用于先对齐方向，再按确认后的顺序实施。
> 现状依据一次模块全量排查（后端 service/repo/schema、前端 page/components/api、调度与信号消费入口）。

## 0. 实施进度

- ✅ **方向 A（前端加载与 Tab 交互）** 已实施并验证（lint 0 error、`PaperTradingPage.test.tsx` 3/3、build 成功）。见 `apps/dsa-web/src/pages/PaperTradingPage.tsx`。
- ✅ **方向 B（行情 bar 缓存复用）** 已实施并验证：改为进程内共享的模块级缓存，见下方第 4 节「实施结果」。
- 🔶 方向 C（回填/批处理）**部分实施**：已完成第 5 节的改动点 1（开放持仓读取合并：`_valuate` 把已读 `positions` 传给 `_record_snapshot`，去掉一次重复 `list_open_positions` 查询），并补充回填"被跳过信号"前端提示（`signals_unavailable` 透出）。改动点 2（repo 引入 session-per-batch / 读去 commit / 写批量提交）**未做**——需在确认回填频繁或信号量大的场景单独评审，涉及事务边界与幂等，属高收益但高风险区。

## 1. 模块现状

模拟盘模块**完整且可运行**，非半成品：路由 `/api/v1/paper`、侧边栏/路由/页面、API、service、repo、两套表、前后端测试均齐全。

- 后端：`api/v1/endpoints/paper.py`（8 个端点）+ `src/services/paper_service.py`（702 行）+ `src/repositories/paper_repo.py`（330 行）+ `src/storage.py`（表结构）+ `api/v1/schemas/paper.py`。
- 前端：`apps/dsa-web/src/pages/PaperTradingPage.tsx` + `components/paper/*` + `api/paper.ts` + `types/paper.ts`。
- 集成点：信号消费在 `src/core/pipeline.py:2674` 的 `_try_consume_paper_signal`（每持久化一条 deci Signal 后调 `PaperService.process_signal`）；每日估值由 `src/services/runtime_scheduler.py:352` 每 30 分钟触发（`config.paper_trading_enabled` 控制，默认开）。

主链路：`捕获信号 → process_signal（幂等去重）→ _handle_signal（开/加仓、减仓）→ 每日 _valuate（标到市场价 + 止损止盈 + 快照）→ 净值曲线 / 持仓 / 成交/信号记录`。

## 2. 优化目标总览

| 方向 | 收益 | 风险 | 范围 |
| --- | --- | --- | --- |
| A. 前端加载与 Tab 交互 | 页面更快、请求更省 | 低 | 仅 `PaperTradingPage.tsx` |
| B. 行情 bar 缓存复用 | 后台估值不再每次重拉 365 天行情（最大收益） | **高** | `paper_service.py` + 调度/pipeline 调用方 |
| C. 回填/批处理性能 | 大规模回填更快、commit/会话大幅减少 | 中 | `paper_service.py` + `paper_repo.py` |

建议实施顺序：**A →（单独确认后）B →（视信号量）C**。B 属估值/调度/pipeline 高风险区，必须先单独评审；C 只在信号量大、频繁回填时才有必要。

---

## 3. 方向 A：前端加载与 Tab 交互（低风险）

**问题**
- 页面加载发 6 个并行请求（`PaperTradingPage.tsx:41-66`），其中 `getAccount` 已内嵌 snapshot（服务端 `_account_payload` → `get_snapshot`），又单独 `getSnapshot` → **冗余重复计算一次开放持仓列表**。
- 切换 Tab / 翻页都重发全部 6 个请求（`:68-76`）：`handleTabChange`/`onPageChange` 只改 `page` 状态，未分 Tab 拉取，导致切到"成交"或信号翻页仍重拉持仓/快照/净值/账户。

**改动（仅在 `PaperTradingPage.tsx`，低风险）**
1. 去掉冗余的第 6 个 `getSnapshot`：从 `getAccount` 响应里取内嵌 snapshot，或干脆让页面只用 `getSnapshot`（账户卡若只需 id/name/cash，可由 snapshot/账户别名提供）——具体以页面实际用到哪些 account 字段为准，避免删过头。
2. 按 Tab 懒加载：持仓 Tab 用 snapshot/positions；信号与成交 Tab 各自独立 `getSignals` / `getTrades`，只在切到对应 Tab 或翻页时拉。加载态、占位、空态沿用现有组件。

**验证**：`cd apps/dsa-web && npm run lint && npm run build`；`PaperTradingPage.test.tsx` 跑通；浏览器确认切 Tab/翻页只拉对应列表（Network 面板只出现目标请求）。

**回滚**：改动未提交前 `git checkout apps/dsa-web/src/pages/PaperTradingPage.tsx`；已提交则 revert 该 commit。

---

## 4. 方向 B：行情 bar 缓存复用（高风险，需单独评审）

**问题**
- `_bar_cache` 是 `paper_service.py:80` 的**实例级** dict；但 `runtime_scheduler.py:367` 每轮估值都 `new PaperService()`，`pipeline.py:2694` 又各自实例化 → **缓存从不跨次运行生效**。
- 每 30 分钟、对每只持仓，`_load_bars`（`:649`）经 `db.get_data_range`（`:666`）重拉该股全窗（`DEFAULT_LOOKBACK_DAYS=365` 天，取自 `stock_daily`），构建后整份丢弃。后台估值线程与主分析线程各拉一遍。

**方案（三选一，评审后定）**
1. **模块级（进程内）单例缓存**：把 `_bar_cache` 从实例级移到模块级（`__init__` 之外）或一个共享 holder，带 TTL 与容量上限，后台估值与 pipeline 共享。改动最小、无需持久化，但进程重启即丢、多进程下不共享。**推荐先做这个。**
2. **复用 `DataFetcherManager`/数据层已有的 bar 缓存**：若 `db.get_data_range` 底层已有按数据源的缓存/内存缓存，则改为走那条路径并延长其 TTL，避免在 service 层再造一层。需先确认数据层现状。
3. **持久化/DB 级**：把每日行情 bar 落一张表或延长现缓存，跨进程共享。最彻底但改动面最大、风险最高。

**统一注意事项**：无论哪种，都要保证 `_load_bars` 在窗口内只拉一次、且 `stop_loss`/`target_price`/当日高低价判断读的是同一份数据；锁粒度沿用现有 `_account_locks`（模块级已共享），避免并发估值与分析在写入时相互覆盖。

**影响面（必须自审）**：每日估值、信号消费（`process_signal` → `_handle_signal` → `_load_bars`）、某窗口内的净值曲线、及依赖当日 bar 的任何判断。缓存 TTL 的取舍：太长则行情滞后，太短则优化失效。

**验证**：先离线/确定性检查；单测 `tests/test_paper_service.py` 全绿；跑一次后台估值确认 bar 只拉一次（日志/探针）；`run_diagnostics` 相关路径、数据源 fallback 行为不被破坏。

**回滚**：改动未提交 `git checkout`；已提交则 revert；因属进程内缓存，重启即回旧行为，无损数据。

**实施结果（采用方案 1：模块级共享缓存）**

- 现状确认：`db.get_data_range`（storage）底层为直接 DB SELECT、无数据层缓存 → 排除方案 2，落地方案 1。
- `paper_service` 模块级新增 `_BAR_CACHE`（`Dict[code, (start, end, bars)]`）、`_BAR_CACHE_LOCK`、`_BAR_CACHE_MAX_ENTRIES=1024`、`clear_bar_cache_for_tests()`；删除 `__init__` 里的实例级 `_bar_cache`/`_bar_cache_lock`。
- `_load_bars` 缓存命中改用**覆盖窗口检查** `start <= as_of <= cached_end`：既修掉旧实例级 `start <= as_of` 检查的跨日陈旧（bar 只拉到 fetch 时的 `today`，跨日请求不命中即重拉），也保证回填早于窗口时重拉更宽窗口。
- LRU 上限 1024 界定长驻进程内存；空结果**不写缓存**——防止晚到的行情被旧空窗挡住、把可重试信号误判为 `data_unavailable`（回归 `test_buy_without_price_is_data_unavailable_and_retryable`）。
- 测试隔离：`tests/test_paper_service.py` 新增 `setup_function` → `clear_bar_cache_for_tests()`，避免共享缓存按 code 键在用例间泄漏（同属早期 belong_boards 一次类问题）。
- **覆盖路径（自审）**：信号消费 `process_signal`→`_handle_signal`→`_load_bars`（`pipeline.py:2694` 每次新建实例，现共享缓存）；每日估值 `run_daily_valuation`（`runtime_scheduler.py:367` 每 30 分钟新建实例，现共享缓存）。净值曲线、当日止损止盈判断读同一份 bar。
- **验证**：`py_compile`、`test_paper_service.py`+`test_paper_repo.py` 23 passed、调度/流水线/paper 相关离线用例 478 passed、完整离线套件（`-m "not network"`）全绿。`flake8` 未在本地 PATH（CI 环境安装），故未跑；改动仅移动缓存作用域，无新导入/无未用变量。
- 收益：后台估值与主分析线程（含 pipeline）不再各自重拉每只持仓 365 天行情，改为窗口内共享一次；进程重启即共享缓存清空，无持久化、无损数据。

---

## 5. 方向 C：回填 / 批处理性能（中风险，视信号量）

**问题**
- `backfill_history`（`:188`）逐信号循环，每信号约 8-12 次独立 session/commit/refresh（`has_signal_record` → `_handle_signal` → `get_open_position` + `_net_value` + `upsert_position` + `_apply_cash` + `add_trade` → `add_signal_record` → 变更则整次 `_valuate` + `_record_snapshot`）。一年信号 = 上千次 commit。
- `_valuate`（`:470`）列开放持仓，`_record_snapshot`（`:557`）再列一次求和，`_net_value`（`:589`）第三列 → 同一批开放持仓重复读取。
- `paper_repo.py` 每个方法自开 session 且 commit（读也 commit）。

**改动**
1. 开放持仓列表共享：`_valuate` / `_record_snapshot` / `_net_value` 合并为一次 `list_open_positions`，结果在各步间传递，避免重复 session。
2. repo 引入 session-per-batch：回填/估值期间复用同一 session 批次，读方法去掉无效 commit，写方法改为批量提交（需保证回填失败时可回滚/幂等，可用现有 `has_signal_record` 去重兜底）。
3. 若信号量小，此项收益有限——**建议在确认回填频繁或信号量大后再做**，否则属于"顺手优化"，与仓库"最小改动"原则不符。

**验证**：`tests/test_paper_repo.py` + `tests/test_paper_service.py` 全绿；构造一段跨日信号回填，核对持仓/快照/流水与逐条执行一致；确认失败重入仍幂等。

**回滚**：未提交 `git checkout`；已提交则 revert；因批量提交改动事务边界，回滚前确认无残留部分提交（幂等价保证这一致）。

---

## 6. 建议顺序与"不做"清单

- 顺序：A（低风险、快见效）→ B（单独评审后）→ C（仅有明确需求）。
- 不与本次并行夹带：不改接口/Schema/字段语义、不改报告结构、不改数据源 fallback 语义；B/C 仅在确认后单独成 PR，避免与其它改动混在一起难 review。
- 若做前需先确认：`db.get_data_range` 底层是否已有可复用的 bar 缓存（决定 B 走方案 1 还是 2）；页面 `getAccount` 响应用到的具体字段（决定 A 第 1 步怎么删最稳）。

## 7. 验证矩阵

| 改动 | 本地验证 | 备注 |
| --- | --- | --- |
| A（前端） | `npm run lint` + `npm run build` + 前端单测 + 浏览器 Network 确认请求收敛 | 纯前端，无后端/接口/Schema 改动 |
| B（bar 缓存） | `./scripts/ci_gate.sh` + `py_compile` + `tests/test_paper_service.py` + 后台估值探针确认只拉一次 | 涉及估值/调度/pipeline，需写明覆盖路径 |
| C（回填批处理） | `tests/test_paper_repo.py` + `tests/test_paper_service.py` + 跨日回填一致性核对 | 事务边界变更，需确认幂等 |
