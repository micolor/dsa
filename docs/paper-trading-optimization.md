# 模拟盘（Paper Trading）模块优化分析与计划

> 本文档是**内部优化计划**，不是用户可见的手册。用于先对齐方向，再按确认后的顺序实施。
> 现状依据一次模块全量排查（后端 service/repo/schema、前端 page/components/api、调度与信号消费入口）。

## 0. 实施进度

- ✅ **方向 A（前端加载与 Tab 交互）** 已实施并验证（lint 0 error、`PaperTradingPage.test.tsx` 3/3、build 成功）。见 `apps/dsa-web/src/pages/PaperTradingPage.tsx`。
- ✅ **方向 B（行情 bar 缓存复用）** 已实施并验证：改为进程内共享的模块级缓存，见下方第 4 节「实施结果」。
- 🔶 方向 C（回填/批处理）**部分实施且已部分回退**：改动点 1（`_valuate` 把已读 `positions` 传给 `_record_snapshot`）当初按“省一次查询”落地，但**该次读取在写入前发生，records 已陈旧**——快照因此少标一个交易日、且在平仓日重复计入已平仓市值。已在第 8 节回退为“快照前重新读取”。（本轮同时补充了回填“被跳过信号”的前端提示：`signals_unavailable` 透出。）改动点 2（repo 引入 session-per-batch / 读去 commit / 写批量提交）**未做**——需在确认回填频繁或信号量大的场景单独评审，涉及事务边界与幂等，属高收益但高风险区。
- ✅ **方向 D（成交价与会话语义修正）** 已实施：见第 8 节。
- ✅ **方向 E（交易成本建模）** 已实施：见第 9 节。
- ✅ **方向 F（同日触及止损与止盈的判定细化）** 已实施：见第 10 节。
- ⚠️ **方向 G（前复权覆盖写入对存量持仓的影响）** 已确认影响、**未改代码**：见第 11 节（属新增能力，需独立评审）。
- ✅ **方向 H（账户初始资金配置与重置）** 已实施：见第 12 节。
- ✅ **方向 I（模拟盘实时成交通知）** 已实施：见第 13 节。

## 1. 模块现状

模拟盘模块**完整且可运行**，非半成品：路由 `/api/v1/paper`、侧边栏/路由/页面、API、service、repo、两套表、前后端测试均齐全。

- 后端：`api/v1/endpoints/paper.py`（9 个端点）+ `src/services/paper_service.py`（702 行）+ `src/repositories/paper_repo.py`（330 行）+ `src/storage.py`（表结构）+ `api/v1/schemas/paper.py`。
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
1. ~~开放持仓列表共享：`_valuate` / `_record_snapshot` / `_net_value` 合并为一次 `list_open_positions`，结果在各步间传递，避免重复 session。~~ **已回退（该方向不成立）**：`_record_snapshot` 必须在写入之后重新读持仓——传进来的那批 records 不反映本轮 `upsert_position` / `close_position` 的结果。见第 8 节。`_net_value` 在信号消费路径上仍是独立读取，未合并。
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
| D（成交价与会话语义） | `py_compile` + `tests/test_paper_service.py` + 用真实库副本回放同一条信号核对成交/净值 | 行为契约变更，历史净值会与新语义不一致 |
| E（交易成本建模） | `py_compile` + `tests/test_paper_service.py` + `tests/test_paper_repo.py` + 真实库副本回放同一条信号核对费用与现金对平 | 账户数值口径变更；历史成交流水不会补收费用（存量 `fee` 为 NULL，读回 0） |
| F（同日触发判定细化） | `py_compile` + `tests/test_paper_service.py` | 仅影响同日同时触及止损与止盈的 bar；`ambiguous_stop_loss` 语义收窄 |
| H（初始资金与重置） | `py_compile` + `tests/test_paper_service.py` + `tests/test_paper_api.py` + `tsc` / `lint` / `build` | 新增配置项与端点；归档重启不删数据，但新账户为空、需另行回填 |
| I（实时成交通知） | `py_compile` + `tests/test_paper_notify.py` + `tests/test_paper_service.py` + `tests/test_config_registry.py` + `tsc` / `lint` / `vitest` | 新增配置项（默认关）与通知发送路径；渠道未配置时静默降级为日志，回填不补发 |

---

## 8. 方向 D：成交价与会话语义修正（已实施）

**问题（真实库 `data/stock_analysis.db` 可复现）**

模拟盘按“日线 EOD”建模：只用已收盘的日线 bar 定价。但成交价与会话日期此前没有遵守这个前提：

1. **成交日不是交易日**。`_signal_trade_date` 直接取 `created_at`（UTC-naive）的日期，不做市场本地化、不判断是否有 bar。真实案例：`decision_signals.id=2` 创建于 `2026-08-08 08:40:01 UTC`（= 北京时间周六 16:40，收盘后），`paper_trades` 于是在 **周六 2026-08-08** 记账买入，而 001324 当天根本没有 bar。
2. **买入价无视当日区间**。`_entry_price` 直接返回计划价 `entry_high`，从不看 bar。等于给每个计划都成交，哪怕当天最低价远在买点之上。
3. **卖出价无视跳空**。`_daily_exit` 触发后按止损/止盈价原样成交。真实案例：`2026-09-11` 的 bar 是 `open 20.99 / high 20.99 / low 19.80 / close 20.29`，全天未跌破 19.42，账户却按 19.42 卖出——单笔少记 10500 × (20.99 − 19.42) ≈ **16485 元**。
4. **平仓日快照重复计入**。`_record_snapshot` 求和的是 `_valuate` 开头读到的持仓 records；平仓已把市值换成现金，records 仍是旧的，于是现金和已平仓市值一起进净值。真实案例：`2026-09-11` 快照 `cash 1004410 + market_value 194775 = net 1199185`，净值虚增 19.5 个百分点，次日回落到 0.441%。同一原因还让**每个快照都落后一次标记**（真实库里 08-27 的快照用的是 08-26 的收盘价）。

**改动**

- `src/core/trading_calendar.py`：新增 `resolve_fill_session(market, at)`——交易日收盘前→当日；收盘后/非交易日→下一个交易日；未知市场或日历不可用→fail-open 到市场本地自然日（与本模块其余 fail-open 行为一致）。
- `paper_service._signal_trade_date`：把 `created_at`（UTC-naive）换算到标的市场时区后走 `resolve_fill_session`，成交日必须是可下单的会话。
- `paper_service._entry_fill_price`（替换 `_entry_price`）：把 `entry_high` 当限价单——当日 `low > entry_high` 则**不成交**（新 disposition `no_fill`，信号已消费、不重放）；否则按 `min(open, entry_high)` 成交；无计划价时按收盘价（原行为）。bar 缺 `low`/`open` 记 `data_unavailable` 以便重试。
- `paper_service._exit_fill`：跳空开盘越过触发价时按开盘价成交（止损 `min(trigger, open)`、止盈 `max(trigger, open)`），日内触发仍按触发价。
- `paper_service._record_snapshot`：改为在标记/平仓之后重新读取持仓，`positions` 参数随之删除（`_valuate` 是唯一调用方）。
- `_load_bars` 的 bar 字典补 `open` 字段（`_exit_fill` / `_entry_fill_price` 需要）。
- 前端：`PaperRecordsList` 增加 `no_fill` 徽章，`featureText` 补中英 `dispNoFill`（`未成交` / `No fill`），避免新 disposition 在 UI 上露出裸英文串。

**验证**

- `tests/test_paper_service.py` 23 passed（新增 5 个反例：收盘后信号顺延到下一交易日、开盘价优于限价时按开盘价、当日区间没碰到限价时不成交、日内触达止盈仍按目标价、平仓日快照不再重复计入；并把 `test_take_profit_exit` / `test_stop_loss_precedence` 原先钉住的“按触发价原样成交”断言改为跳空成交价）。
- 用真实库副本回放同一条信号：`as_of` 由周六 08-08 变为周一 08-10，买入 10500 @ 19.00（`min(open 19.22, limit 19.00)`），08-11 止盈跳空按开盘价 19.63 成交，账户净值 1006615、收益 0.6615%，平仓日快照 `market_value = 0`；原实现的 09-11 `+19.92%` 虚增消失。
- 单独回放“持仓不动”的 8/11–8/28：每个快照 `market_value` 都等于**当日**收盘价 × 数量（此前落后一次标记）。

**影响与边界**

- 这是行为契约变更：**历史快照与成交流水不会重算**，升级后旧的错误记录仍在库里；如需一致，需要一次显式的重放/重建（本次未做）。
- 同一计划重复出现的 `no_fill` 不再重试（按“当日有效”处理）。若后续要做“挂单持续有效”，需要额外引入订单有效期语义，不在本次范围。
- 已知但**未修**：`data_unavailable` 信号没有后台重试兜底（只有 pipeline 持久化时消费一次）；调度器只看 `latest_snapshot_date`，跳过的交易日不会回补；`_reduce_position`（`sell`/`reduce` 信号）仍取当日收盘价、取不到时回退到 `position.current_price`，即“这次成交价建立在仓位最后已知市价上”，与买入侧新语义并不对称。三者与本次成交价修正独立，建议单独 PR。


---

## 9. 方向 E：交易成本建模（已实施）

**问题**

模拟盘此前按**零成本**成交：买卖都不收佣金、印花税、过户费，也不假设滑点。这会让结论系统性偏乐观——A股一个来回的真实成本约 0.10%（佣金万2.5 双向 + 卖出印花税 0.05% + 过户费 0.001%），高频或短周期策略下会持续累积；港股更重（印花税 0.1% 双向）。同时，现金只按成交额变动，用户在成交流水里看不到费用，也无法把流水对平到账户现金。

**改动**

- `src/config.py` / `.env.example`：新增 3 个配置项——`PAPER_FEE_ENABLED`（默认 true）、`PAPER_FEE_COMMISSION_RATE`（默认 0.00025，覆盖各市场佣金率）、`PAPER_FEE_SLIPPAGE_BPS`（默认 0）。
- `paper_service`：新增 `_MARKET_FEE_PROFILE`（各市场**法定/交易所固定费用**：A股印花税 0.05% 卖出单边 + 过户费 0.001% 双向、单笔最低佣金 5 元；港股印花税 0.1% 双向 + 交易费约 0.0105%；美股 0）与 `_fee_for` / `_apply_slippage` / `_spendable_cash` 三个辅助方法。法定费率写在代码里而非配置里：它们是交易所规则，不随账户变化；券商可谈的佣金率才开放为配置。
- **买入**：`_apply_slippage` 后按限价封顶（限价单不会成交在限价之上），费用 `佣金 + 过户费`（A股买入无印花税）计入现金流出，并**摊入成本价**（`avg_cost = (成交额 + 费用) / 数量`，与券商展示口径一致）。
- **卖出**：`_reduce_position` 与 `_close_by_exit` 都按不利方向加滑点，费用（含印花税）从卖出所得中扣除。
- **不超支**：费用在成交额之外另扣，按全部现金下单会透支。`_spendable_cash` 先按该市场费用率上限预留，再用预留后的金额定仓位。
- `paper_trades` 新增 `fee` 列（`src/storage.py` 模型 + `_ensure_paper_trade_fee_column` 启动迁移），成交流水/API Schema/前端类型同步透出，UI 仅在费用非零时显示。

**验证**

- `tests/test_paper_service.py` 38 passed、`tests/test_paper_repo.py` 8 passed。新增反例：A股卖出收印花税而买入不收、小单触发单笔最低佣金、费用可关闭、滑点按不利方向移动成交价、限价买单不被滑点推过限价、港股印花税双向、按全部现金下单不透支、**成交流水能对平账户现金**。
- 真实库副本回放同一条信号（`decision_signals.id=2`，001324）：

  | 项目 | 数值 |
  | --- | --- |
  | 买入 | 2026-08-10 · 10,500 @ 19.00 · 成交额 199,500 · 费用 **51.87**（佣金 49.88 + 过户费 2.00） |
  | 卖出（止盈跳空） | 2026-08-11 · 10,500 @ 19.63 · 成交额 206,115 · 费用 **156.65**（佣金 51.53 + 印花税 103.06 + 过户费 2.06） |
  | 期末 | 现金 1,006,406.48 · 累计费用 **208.52** · 收益 **+0.6406%** |

  同一条信号在 `PAPER_FEE_ENABLED=false` 下回放得到 1,006,615 / +0.6615%，与费用建模前的数值完全一致——说明费用是纯增量改动，关闭开关等价于旧行为。费用对这一个来回的拖累为 0.0209 个百分点（占成交额约 0.10%）。

**边界与影响**

- 这是**账户数值口径变更**：历史成交不会补收费用（存量行的 `fee` 为 NULL，读回 0，与其真实现金变动一致）；历史净值曲线仍是零成本口径，与新记录不可直接比较。
- 滑点默认 0：滑点是"执行质量假设"而非可观测费用，默认不假装有，需要时用 `PAPER_FEE_SLIPPAGE_BPS` 打开。
- 佣金率是**全局**配置，不区分市场（最低佣金与法定费用仍按各市场规则）；美股默认也会被收万2.5，介意的话可用 `PAPER_FEE_COMMISSION_RATE=0`（美股归零，A股/港股仍收法定费用）或 `PAPER_FEE_ENABLED=false`（全部归零）。
- 仍未建模：涨跌停与停牌（撮合层面无法成交）、盘中撮合、整手之外的碎股规则。

---

## 10. 方向 F：同日触及止损与止盈的判定细化（已实施）

**问题**

日线 bar 只能说明当日**同时**触及了止损与止盈，不能说明先后。此前一律按止损优先记账（`ambiguous_stop_loss`），方向是保守的——不会把好结果当成先发生——但代价是所有这类 bar 都被当成"先亏后赚"，明细数字系统性偏低，且用户从徽章上看不出这是"保守假设"还是"真实止损"。

**改动**

`paper_service._daily_exit`：同日两触发时，先用开盘价判定先后——
- 开盘 ≤ 止损价：止损在开盘即被击穿，先于止盈 → 按 `stop_loss` 记账；
- 开盘 ≥ 止盈价：止盈在开盘即达成，先于止损 → 按 `take_profit` 记账；
- 开盘夹在两者之间：日内顺序无法从 OHLC 判定 → 仍按止损优先，记 `ambiguous_stop_loss`。

**验证**

`tests/test_paper_service.py` 新增 3 例：跳空低开（open 90 / stop 95）→ `stop_loss` @ 90；跳空高开（open 120 / target 115）→ `take_profit` @ 120；开盘夹在止损与止盈之间（open 105 / stop 95 / target 115）→ `ambiguous_stop_loss` @ 95（开盘未越过止损，成交价即止损价）。原 `test_stop_loss_precedence` 用的正是"跳空低开"这一可判定情形，已拆成上面前两例。

**残留偏置（仍然存在，需知情）**

- 只有开盘夹在两者之间的 bar 才走保守分支。这类 bar 仍按止损记账，因此**净值仍然是偏低的**——偏置从"所有同日双触发"收窄到"真正的模糊情形"，但没有消失。日线级模拟无法消除它，除非引入分钟级数据。
- 该保守选择是刻意的：若改为按止盈优先，模拟结果就会依赖"好结果先发生"这一无依据的假设。

---

## 11. 方向 G：前复权覆盖写入对存量持仓的影响（已确认，未改代码）

**结论先行**

影响真实存在，且此前没有被建模：**日线库的历史 bar 会被后续抓取覆盖成新的前复权序列，而模拟盘的成交价/成本价/风控线是绝对价，从不随复权调整**，两者会在除权除息后落到不同的价格空间。方向是**偏悲观**（低估收益），与方向 D/E 修正的偏乐观方向相反。

**机制（已在代码中确认）**

1. `db.save_daily_data`（`src/storage.py:3088`）按 `(code, date)` 批量 UPSERT，docstring 明写"已存在记录会覆盖更新"。
2. 每次分析都会重新抓取并落库：`pipeline.py:405`（`days=30`）、`pipeline.py:1365`（`_ensure_agent_history`，`days=240`）、`pipeline.py:1837`、`agent/tools/data_tools.py:333`。
3. 抓取一律带 `adjust="qfq"`（`data_provider/akshare_fetcher.py` 多处），即**前复权**：基准是"最新价"，历史上发生过除权除息时，序列会被整体重算。
4. 因此：分析过的股票，其近 30~240 天的历史 bar 每次抓取都可能被改写为**新的**前复权值。

**对存量持仓的影响**

模拟盘里属于"绝对价"、不会被复权改写的字段：

- `paper_trades.price` / `amount` / `fee`（成交时的绝对价）
- `paper_positions.avg_cost`（含费的绝对成本价）
- `decision_signals.stop_loss` / `target_price` → 落到 `paper_positions.stop_loss` / `target_price`

属于"跟着 bar 走"、会被复权改写的字段：`current_price` / `market_value`（来自当次抓到的前复权 bar）。

除权除息后，前复权会把**除权日之前**的 bar 整体下调。于是：

- **虚亏**：持仓的 `avg_cost` 仍是除权前的绝对价，而 `current_price` 已经是复权后的价格 → 立即出现一笔等于分红额（或送转比例）的账面亏损。10 送 10 这类送转会让 `avg_cost` 看起来是高点的近一倍，虚亏接近 50%。而模拟盘**从不发放现金分红、也不调整股数**（`paper_service` / `paper_repo` 中没有任何分红、送转、复权处理）。
- **风控线错位**：`stop_loss` / `target_price` 是信号给出的绝对价，除权后可能落到当前价的错误一侧，触发本不该触发的止损/止盈，并按复权后的 bar 成交。

**仓库里已有可复用能力（但只服务实盘持仓）**

`PortfolioCorporateAction` 表（`src/storage.py:609`）与 `portfolio_service` 的回放已经完整处理这类事件：`action_type` 区分 `cash_dividend` / `split_adjustment`，`cash_dividend_per_share` / `split_ratio` 在回放时分别增加现金与调整股数/成本（`src/services/portfolio_service.py:782+`）。模拟盘完全没有接这套。

**为什么不顺手修**

- 这是**新增能力**，不是小修：需要先把公司行为落进模拟盘的数据模型（或复用 `PortfolioCorporateAction`），再让 `process_signal` / `_valuate` 在跨除权日时调整 `quantity` / `avg_cost` / `stop_loss` / `target_price`，并补回放与幂等测试。
- 还缺**数据来源**：当前没有抓取个股公司行为的链路，`PortfolioCorporateAction` 的表注释也是面向手工录入的。
- 在没有公司行为数据之前做"自动复权对齐"，只能靠猜，风险大于收益。建议作为独立方向单独评审。

**用户需要知道的**

- 只要持仓期内发生除权除息，账户的浮亏/收益率就会失真（虚亏），历史成交与成本价不会自动修正。
- 短期持仓、无分红送转的股票不受影响；这也是本次真实库回放（001324）没有踩到的原因。

---

## 12. 方向 H：账户初始资金配置与重置（已实施）

**问题**

- **初始资金不可配置**：`PaperRepository.ensure_account` 的默认参数硬编码 `1000000.0`（`src/repositories/paper_repo.py:37`），只在账户**首次创建**时写入；账户一旦存在就按 `status == 'active'` 复用，传入的 `initial_capital` 被静默忽略（`tests/test_paper_repo.py` 已把"忽略"钉为既有契约）。生产代码里所有调用者都用默认值，没有配置项。
- **没有重置入口**：`/api/v1/paper` 原先只有 6 个 GET 与 `refresh` / `backfill` 两个 POST，无法换资金、也无法重新开始；前端把初始资金只读地展示出来（`PaperTradingPage.tsx`），改不了。

**设计选择**

| 决策 | 取值 | 理由 |
| --- | --- | --- |
| 重置语义 | **归档重启**（把 active 账户置为 `archived`，按新资金开新账户） | 非破坏性：旧的持仓/成交/快照按原 `account_id` 留在库里，可查、可复核。复用现成的 `status` 字段与 `ensure_account` 的"取 id 最小的 active"逻辑，查询侧零改动。 |
| 初始资金来源 | env `PAPER_INITIAL_CAPITAL`（默认 1000000）作为新建账户的默认值；`POST /paper/reset` 的 body 可覆盖**本次**重置的金额 | 不配置即维持现状（"不配置也可运行"）；金额不需要改 `.env` + 重启就能换，前端才有得填。 |
| 重置后是否自动回放历史 | **不自动**，新账户为空，由用户另行发起「历史回填」 | 起始日期该由用户选；自动回放要逐条信号拉日线，多账户 × 全历史会拖长请求且不可控。 |
| 重置入口位置 | 设置页「系统设置」区的「模拟盘」卡片，模拟盘页**不再**放入口 | 重置是低频破坏性操作，和刷新 / 回填挤在同一排工具栏容易误点；设置页是与「配置备份」等同级的一次性操作区。左侧导航的分类来自 `src/core/config_registry.py` 的固定白名单，单为一个动作新开分类不划算，故并入既有 `system` 区。 |

**改动**

- `src/config.py`：新增 `paper_initial_capital`（默认 1000000.0）+ env `PAPER_INITIAL_CAPITAL`（`parse_env_float`，minimum 1.0）。`.env.example` 同步。
- `src/services/paper_service.py`：`INITIAL_CAPITAL = 1000000.0` 常量；`_default_initial_capital()`（照 `_default_position_weight()` 的"读配置 / 异常兜底"写法）；`get_or_create_account(initial_capital=None)` 为 `None` 时取配置值；新增 `reset_account(initial_capital=None)`。
- `src/repositories/paper_repo.py`：**未改动**——归档用现成的 `update_account(id, {"status": "archived"})`，建账户用现成的 `ensure_account(initial_capital=...)`。
- `api/v1/schemas/paper.py`：新增 `PaperResetRequest`（`initial_capital: Optional[float]`，`gt=0`）。
- `api/v1/endpoints/paper.py`：新增 `POST /api/v1/paper/reset`，返回 `PaperAccountResponse`。
- 前端：`api/paper.ts` 加 `reset(initialCapital?)`；新增 `components/settings/PaperAccountCard.tsx`（账户摘要 + 「重置账户」按钮 + `ConfirmDialog`，可填初始资金、留空走配置），挂在**设置页「系统设置」区**；`ConfirmDialog` 新增可选 `children` 插槽（纯追加，不影响既有调用方）；`locales/featureText.ts` 补中英文案。`PaperTradingPage.tsx` 只保留刷新 / 回填这类高频操作，不再有重置入口。

**`reset_account` 的执行顺序**

1. 解析金额（省略则取配置值），校验必须是有限且 > 0 的数值——**校验在任何写入之前**，非法金额不会留下半成品账户；
2. 取当前 active 账户，以其 id 拿 `_account_lock`（并发重置不会各自归档同一个账户再各建一个）；
3. 把所有 active 账户置为 `archived`（正常情况下只有一个，多 active 的脏状态也一并收敛）；
4. 按新金额 `ensure_account` 建新账户并返回其 payload。

`ensure_account` 取"id 最小的 active"，所以归档后 `process_signal`、`runtime_scheduler` 等所有入口自动认到新账户，无需改动调用方。

**验证**

- `tests/test_paper_service.py`：归档后旧账户的持仓/成交按旧 `account_id` 原样可查、旧账户状态为 `archived`、新账户为空且金额生效、新账户成为 `get_or_create_account` 的返回值、重置后新信号能重新开仓（信号消费记录按账户隔离，旧账户的"已消费"标记不挡新账户）、省略金额时取 `PAPER_INITIAL_CAPITAL`、非法金额（0 / 负数 / NaN / inf）抛错且不改动账户。
- `tests/test_paper_api.py`（新增）：`POST /paper/reset` 传金额、省略 body 走配置、金额 ≤ 0 返回 422 且无副作用。
- 前端：`components/settings/__tests__/PaperAccountCard.test.tsx`（新增）覆盖账户摘要渲染、二次确认后按填入金额调用 reset 并刷新摘要、非法金额在本地拦截、加载失败给出错误提示；`PaperTradingPage.test.tsx` 相应移除重置用例。`tsc --noEmit` 干净、`npm run lint` 0 error、`npm run build` 通过。

**边界与已知限制**

- **归档 ≠ 删除**：老账户的数据都在，但**目前没有任何界面能查看归档账户**（只能通过 `PaperRepo.list_accounts()` 查）。要做账户切换/历史账户入口属另一个话题。
- 新账户为空是设计行为，不是缺陷；净值曲线与收益率都从零开始。
- 重置**不影响** `decision_signals`、`stock_daily` 等分析侧数据，只动 `paper_*` 的账户归属。

---

## 13. 方向 I：模拟盘实时成交通知（已实施）

**问题**

模拟盘成交此前只落库 + Web 页面展示，**任何通知渠道都收不到推送**：`src/services/paper_service.py` 不 import 任何通知模块，`pipeline.py` / `runtime_scheduler.py` 消费信号后也没有通知步骤，`PAPER_*` 下没有任何通知开关。用户在模拟盘成交、尤其盘后估值触发的止损/止盈时，只能主动打开页面才发现。

**设计选择**

| 决策 | 取值 | 理由 |
| --- | --- | --- |
| 触发范围 | **只发实时**：信号消费产生的开仓/加仓/减仓/清仓 + 盘后估值触发的止损/止盈 | 回填是**重放**，一次可能落几百笔成交，逐笔推送会把渠道刷屏。实时成交才是用户需要及时知道的事件。 |
| 回填如何静音 | 显式 `notify: bool = True` 参数贯穿 `_handle_signal` / `_open_or_add` / `_reduce_position` / `_valuate` / `_close_by_exit`，`backfill_history` 全程传 `False` | 比隐藏的实例属性 / 上下文管理器 suppress 标记更显式、更好读；flag 必须到达 5 个方法，因为三个入口都经 `_valuate` 触发 `_close_by_exit`。 |
| 通知路由 | **复用 `event`**（`NOTIFICATION_EVENT_CHANNELS`） | 该路由本就服务「龙虎榜 / 主力资金 / 重要公告」这类事件型通知，模拟成交同属事件。新增 `trade` 路由要同时改路由表、配置 schema、前端渠道勾选与文档，收益不抵成本。 |
| 开关形态 | env `PAPER_NOTIFY_ENABLED`（默认 false）+ 注册进 `src/core/config_registry.py`，在设置页「系统设置」区渲染成开关 | 默认关闭 = 不配置即维持现状；注册后自动获得标题/说明/示例/文档链接与统一的保存链路，且不会出现「同一个键两个控件」。配置保存走既有热重载，无需重启。 |
| 进程内去重 | **不做**（仍把 `dedup_key` 传给 `send_with_results`） | 模拟成交天然不会重复：`process_signal` 按信号落已消费记录，`_valuate` 只对仍 `open` 的持仓触发离场。而按 trade id 攒去重集合会在长期运行的进程里单调增长（内存泄漏），收益为零。 |
| 失败处理 | 只记 `warning` 日志并返回 `False`，绝不抛回交易路径 | 与 `src/services/system_alert.py` 同款约定：一条通知发不出去不能影响模拟盘记账，也不能触发自己的告警形成环路。 |

**改动**

- `src/services/paper_notify.py`（新增）：`build_paper_fill_message(trade, *, cash_after, disposition=None)` 渲染正文——标题 `模拟盘成交 | <code> <name>`，正文含动作、成交价量、金额、手续费、**成交后现金**、日期；止损/同日先触止损用 `warning`，止盈用 `success`，其余 `info`（`NotificationBuilder.build_simple_alert`）。`send_paper_fill_notification(trade, *, cash_after, disposition=None, enabled=None)` 读 `Config.get_instance().paper_notify_enabled` 门控，走 `route_type="event"`，`dedup_key=paper-fill:<account_id>:<trade_id>`，`except Exception` 兜底。
- `src/services/paper_service.py`：新增 `_notify_fill(account, trade, disposition, notify)`（`notify=False` 直接返回，否则把 `account.cash` 作为成交后现金传下去）；三处 `add_trade` 的返回值（`PaperTradeRecord`）接住并回调，`_close_by_exit` 亦同；`_handle_signal` / `_open_or_add` / `_reduce_position` / `_valuate` / `_close_by_exit` 增加 `notify: bool = True`；`backfill_history` 传入 `notify=False`。
- `src/config.py`：新增 `paper_notify_enabled`（默认 `False`）+ env `PAPER_NOTIFY_ENABLED`。
- `src/core/config_registry.py`：注册 `PAPER_NOTIFY_ENABLED`（`system` / boolean / switch / `default_value: "false"` / 带 `help_key`、`examples`、`docs`）。`apps/dsa-web/src/locales/settingsHelp.ts` 补中英帮助文案。`.env.example` 在 `PAPER_FEE_SLIPPAGE_BPS` 之后补注释条目。

**验证**

- `tests/test_paper_notify.py`（新增）：消息渲染按止损/止盈/开仓/加仓/减仓/清仓区分语气与文案（加仓与开仓同为 `side=buy`，靠 `disposition` 区分）；未开启时不构造 `NotificationService`；开启时走 `event` 路由且带 `paper-fill:1:7` 去重键；发送抛异常与返回 `success=False` 都只返回 `False`，不向调用方抛出。
- `tests/test_paper_service.py`：实时开仓调用了通知且 `cash_after` 是扣款后的值（`1000000 - 200000 - 52`）；盘后止盈平仓按 `closed` 发通知；回填确实落了成交流水但通知一次都没被调用；把 `NotificationService` 替换成抛异常的实现后 `process_signal` 仍返回 `opened`、成交与已消费标记照常落库。
- `tests/test_config_registry.py` 58 例通过（新增键的 `help_key` / `examples` / `docs` 与 locale 一致性均由既有守卫覆盖）。

**边界与已知限制**

- **回填不发历史通知**：重置账户后执行「历史回填」重建历史时，不会补发任何成交通知。这是刻意的——重放是离线重建，不是当时发生的事件。
- 通知渠道需自行在 `NOTIFICATION_EVENT_CHANNELS` 配好；路由与已配置渠道的交集为空时下游返回 `no_channel`，本模块只记 `warning` 日志，界面上不会有显式报错。
- 成交后现金取 `account.cash`（成交记账后的值），不含未成交持仓市值；标题固定中文，未做中英双语（通知渠道面向用户自身，非 Web UI 文案）。
- 通知在 `_account_lock` 内发出，与既有的行情取数（`_valuate` → `_bar_for` → `_load_bars`）同处临界区，渠道超时会拖慢同账户的并发消费。没有把发送挪到锁外：那需要把「本轮产生的成交」暂存起来在释放锁后再发，改动面远大于收益，而锁内做网络请求已是该模块既有形态。
