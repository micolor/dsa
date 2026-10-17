# 基金净值复盘二期设计：分红还原、区间走势与组合入口

- 日期：2026-10-17
- 状态：已定稿，待实施
- 关联：`docs/fund-nav-review.md`、`src/services/fund_nav_review_service.py`、`data_provider/fund_fetcher.py`

## 1. 背景与目标

基金净值复盘 v1 已上线：每次 `fund:<6位代码>` 体检把最新一条净值记为锚点，之后按 1m/3m/6m/1y（锚点后第 22/66/126/252 条净值）评估区间收益和最大回撤，落在独立的 `fund_nav_reviews` 台账，并在组合页展示。

v1 只用 `unit_nav`，分红除权会被算成亏损。二期解决四类问题：

1. **真复权口径**：用东财 F10 分红送配数据做分红再投资复权，得到 `dividend_reinvested` 口径的区间收益与回撤；不可用时依次回退累计净值、单位净值。
2. **双口径展示**：台账与统计同时保留单位口径，Web 可切换并逐行显示实际口径。
3. **存量回填**：为 v1 已评估行补算复权指标，提供手动 API 与默认关闭的每日自动回填。
4. **区间走势与入口**：新增区间净值查询 API 与走势图，并在单只基金报告页展示复盘摘要。

同类基准对比（C）与组合层盈亏归因（D）仍不在本期范围，另立项目。

## 2. 数据源：东财 F10 分红送配

新增 `FundFetcher.get_distribution_events(code) -> FundDistributionEvents`，抓取
`https://fundf10.eastmoney.com/fhsp_<code>.html`（Referer/UA 复用 `_HEADERS`）。

页面包含两张表：

- `table.cfxq`：分红明细，列为 年份 / 权益登记日 / 除息日 / 每10份分红 / 分红发放日。
- `table.fhxq`：拆分折算，列为 年份 / 拆分折算日 / 拆分类型 / 拆分折算比例。

解析约定：

- 分红事件取除息日（缺失时退回权益登记日），现金额用正则提取「每10份派现金 X 元」中的 X，再除以 10 得到每份现金。
- 拆分事件取拆分折算日，比例解析 `1:1.4982` 的右值作为份额乘数（已用 161725 实测）。
- 无法解析的行跳过并计数，不猜测。
- 「暂无拆分信息!」等空表返回空列表。
- HTTP/网络异常向上抛，由调用方记为可重试的抓取失败；空列表是合法的「无事件」。

数据结构：

```python
@dataclass
class FundDividendEvent:
    date: str          # 除息日 YYYY-MM-DD
    cash_per_share: float

@dataclass
class FundSplitEvent:
    date: str          # 拆分折算日 YYYY-MM-DD
    ratio: float       # 份额乘数

@dataclass
class FundDistributionEvents:
    dividends: List[FundDividendEvent]
    splits: List[FundSplitEvent]
    skipped_rows: int = 0
```

## 3. 复权算法

对一条复盘记录，锚点索引 `anchor_idx`、评估点索引 `target_idx`，取 `(anchor_date, eval_date]` 内的事件按日期升序处理：

- 分红：`factor *= 1 + cash_per_share / nav_at_event`，`nav_at_event` 为除息日当天（或之后最近一条）的 `unit_nav`。
- 拆分：`factor *= ratio`。
- 事件日不是净值日时，用之后最近一条净值近似再投资价格。
- 比例/金额非法 → 复权不可用，原因 `invalid_distribution_event`。

结果：

```text
restored_return_pct = (eval_unit_nav * factor / anchor_nav - 1) * 100
```

最大回撤在复权序列 `adjusted_value_t = unit_nav_t * factor_t` 上计算：`factor_t` 只累计事件日期 `<= t` 的事件，锚点处因子为 1。除息日当天净值已扣除分红，因子同步上调，序列在事件日连续。

已知边界：

- 分红现金按除息日净值再投资，不涉及申购费、税费。
- 拆分折算比例按「份额乘数」解释；若数据源给出其他语义，解析失败即降级，不做推断。
- 货币基金等没有常规净值/分红事件的品种，复权不可用时回退累计净值或单位净值。

## 4. 回退链与口径标识

每条已评估记录必须有且仅有以下一种 `restored_basis`：

| 优先级 | `restored_basis` | 条件 |
| --- | --- | --- |
| 1 | `dividend_reinvested` | 事件抓取成功且窗口内事件都能解析、事件日净值齐全 |
| 2 | `acc_nav` | 复权不可用，但锚点、评估点及窗口内 `acc_nav` 全部 > 0 |
| 3 | `unit_nav` | 前两者都不可用 |

同时写入 `restored_reason`（如 `distribution_fetch_error`、`invalid_distribution_event`、`acc_nav_unavailable`、`window_mismatch`），供 API 和 UI 解释降级原因。任何降级都不改变 `eval_status`。

单位净值口径的 `nav_return_pct` / `max_drawdown_pct` 保持 v1 语义，不重算、不改写。

## 5. 存储与迁移

`fund_nav_reviews` 追加可空列：

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `anchor_acc_nav` | FLOAT | 锚点累计净值，锚点写入或评估时补齐 |
| `eval_acc_nav` | FLOAT | 评估点累计净值 |
| `acc_nav_return_pct` | FLOAT | 累计净值区间收益 |
| `acc_max_drawdown_pct` | FLOAT | 累计净值区间最大回撤 |
| `restored_return_pct` | FLOAT | 当前最佳口径区间收益 |
| `restored_max_drawdown_pct` | FLOAT | 当前最佳口径最大回撤 |
| `restored_basis` | VARCHAR(24) | 实际口径 |
| `restored_reason` | VARCHAR(48) | 降级原因，成功时为 NULL |
| `restored_events` | INTEGER | 窗口内参与复权的事件数 |
| `restored_checked_at` | DATETIME | 最近一次复权计算时间 |

新增索引：`ix_fund_nav_review_restore_backlog (engine_version, eval_status, restored_checked_at)`。

迁移：新增 `_ensure_fund_nav_review_restore_columns()`，复用 `_ensure_decision_signal_outcome_benchmark_columns` 的 SQLite 幂等 `ALTER TABLE` 与锁重试模式，并在 `_init_db` 中调用。`create_all` 只建新表，不能补列；非 SQLite 沿用仓库现状，由全新表结构承载。

不修改唯一键 `(fund_code, anchor_date, horizon, engine_version)`，不 bump `engine_version`。

`record_anchor` 在能取到 `acc_nav > 0` 时写入 `anchor_acc_nav`。

## 6. 评估链路

`_evaluate_row` 在原有单位净值计算和状态更新之后追加：

1. 用本次已拉取的净值区间定位锚点和评估点（原逻辑不变）。
2. 拉取（或复用本轮缓存）该基金的分红送配事件。
3. 按第 3、4 节计算 `restored_*` 与 acc 指标，写入同一行。
4. 复权失败只影响 `restored_basis/restored_reason`，不影响 `eval_status`、`last_error`、`checked_at`。

每次 `run_reviews` 内按 `fund_code` 缓存净值区间与事件，单只基金最多一次事件请求。事件请求失败计入该基金本轮全部待评估行的 `restored_reason=distribution_fetch_error`，不重试、不阻塞主评估。

## 7. 存量回填

新增服务方法 `backfill_restored_metrics(...)`：

- 选择条件：`eval_status='evaluated'` 且 `restored_checked_at IS NULL`；`force=true` 时忽略后者，按 `fund_code`、`anchor_from`、`anchor_to` 过滤。
- 按基金分组，每只基金只拉一次净值区间和分红事件；单轮上限沿用 50 只基金 / 400 行。
- 逐行用存储的 `horizon` 计算目标索引，校验目标记录日期等于存储的 `eval_date`；不一致记 `window_mismatch`，不覆盖原单位指标。
- 更新复权/acc 列与 `restored_checked_at`，不改 `eval_status`、`checked_at`、单位指标。
- 返回 `scanned / updated / unavailable / skipped / funds / dry_run / engine_version`。
- `dry_run=true` 时只返回将处理的行数与分类，不落库；默认 true。
- 自动回填只处理 `restored_basis IS NULL` 的行；已经降级的行不会自动重试，需要显式传 `force=true` 手动重跑。

调度：新增 `FUND_NAV_REVIEW_AUTO_RESTORE_ENABLED`，默认 `false`。开启后注册每日后台任务 `fund_nav_review_restore_backfill`，处理积压（无积压时直接返回），异常只 warning；关闭后仍可走手动 API。

环境变量与文档：`.env.example`、`docs/full-guide.md`、`docs/full-guide_EN.md` 同步说明「默认关闭、开启后会联网」。

## 8. API

### 8.1 阶段 1：字段与回填

`GET /api/v1/fund-nav-reviews` item 追加：

```text
anchor_acc_nav, eval_acc_nav,
acc_nav_return_pct, acc_max_drawdown_pct,
restored_return_pct, restored_max_drawdown_pct,
restored_basis, restored_reason, restored_events
```

`GET /api/v1/fund-nav-reviews/stats` 与各 breakdown bucket 追加：

```text
restored_evaluated, restored_positive_rate_pct,
avg_restored_return_pct, avg_restored_max_drawdown_pct,
restored_bases: {dividend_reinvested, acc_nav, unit_nav}
```

旧字段全部保留，语义不变。

新增 `POST /api/v1/fund-nav-reviews/backfill/restore-metrics`：

```json
{
  "fund_code": "003095",
  "anchor_from": "2025-01-01",
  "anchor_to": "2025-12-31",
  "limit": 200,
  "dry_run": true,
  "force": false
}
```

- 路由级 `Security(APIKeyCookie(COOKIE_NAME))`，与现有路由一致。
- `fund_code` 同时接受 `fund:123456` 与 `123456`，非法返回 400。
- `limit` 上限 400；其他异常走 `api_error`，返回扁平 `{"error","message"}`。

### 8.2 阶段 2：区间净值

新增 `GET /api/v1/fund-nav-reviews/nav-range`：

- 参数：`fund_code`（必填）、`start_date`、`end_date`（必填，`YYYY-MM-DD`）。
- 约束：`start_date <= end_date`，区间不超过 `MAX_NAV_RANGE_DAYS = 1100` 天；日期越界返回 400。净值记录最多返回 1000 条，超出时截断并返回 `truncated=true`。
- 响应：

```json
{
  "fund_code": "003095",
  "start_date": "2024-01-01",
  "end_date": "2026-10-17",
  "adjustment_status": "available",
  "truncated": false,
  "events": {"dividends": 3, "splits": 0},
  "items": [
    {"date": "2024-01-02", "unit_nav": 1.234, "acc_nav": 2.345, "adjusted_nav": 1.234, "change_pct": 0.12}
  ]
}
```

- `adjusted_nav` 复用阶段 1 的事件与因子算法，基准因子在区间首条记录处为 1；事件数据不可用时为 `null`，`adjustment_status=unavailable`。
- 该接口只读，不写台账、不触发评估。

## 9. Web

### 9.1 阶段 1：双口径

- 组合页 `FundNavReviewPanel` 增加口径切换：`复权`（默认）/ `单位`。
- 统计卡随口径切换：复权使用 `avg_restored_*`、`restored_positive_rate_pct`；单位使用原有字段。
- 台账「区间收益」「最大回撤」按所选口径展示；每行显示实际口径徽标：`复权` / `累计` / `单位`，并可通过可见文案说明降级原因（不使用原生 `title`）。
- 口径说明文案：累计净值按分红现金加回、不做再投资；复权按除息日净值再投资。
- 中英 i18n key 同步补齐；空态、错误、分页、竞态保护沿用现有实现。

### 9.2 阶段 2：走势与入口

- 新增 `getNavRange` API 客户端与类型（snake_case 查询、camelCase 响应）。
- 使用已有 `recharts` 画净值走势，支持单位 / 累计 / 复权切换；区间默认近 1 年，最长 1100 天。
- 图表放在组合页复盘面板内，并按需加载，避免把图表依赖带进首屏。
- 基金报告页（`ReportSummary` 的 fund 分支 / `FundMetricsCard`）增加紧凑复盘摘要：最近若干条锚点与区间收益，数据来自现有 list 接口，不改变报告主体结构。
- 新增组件需覆盖默认不请求、加载、空态、错误重试与口径切换测试。

## 10. 配置

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `FUND_NAV_REVIEW_AUTO_RESTORE_ENABLED` | `false` | 每日自动回填存量复权指标；关闭后仅手动 API |

不新增其他开关；阶段 2 区间接口限制为常量，不做配置。

## 11. 兼容性与回滚

- 所有新增数据库列可空，旧行保持 NULL；旧 API 字段与语义不变。
- Web 默认口径从单位切到复权属于用户可见变化，文档与 CHANGELOG 明确记录；单行徽标与降级原因保证可解释。
- 回填默认 dry-run，自动回填默认关闭；失败只告警，不影响主评估。
- 回滚：关闭 `FUND_NAV_REVIEW_AUTO_RESTORE_ENABLED` → 移除回填任务注册 → Web 默认口径改回单位；新增列可保留，直接 revert 代码不影响 v1 台账、基金分析、`decision_signals` 与 `analysis_history`。

## 12. 测试

后端：

- `tests/test_fund_fetcher.py`：fhsp 分红/拆分解析、空表、异常行跳过、抓取异常上抛。
- `tests/test_fund_nav_review_service.py`：分红再投资、拆分乘数、事件日净值缺失回退、acc 回退、单位回退、复权回撤、单位口径不变。
- `tests/test_fund_nav_restore_backfill.py`：存量回填、幂等、dry-run、force、`window_mismatch`、按基金只拉一次、事件失败降级。
- `tests/test_fund_nav_review_api.py`：新字段、stats 兼容、回填接口鉴权/校验、nav-range 参数校验与 adjusted_nav。
- `tests/test_runtime_scheduler_fund_review_tasks.py`：自动回填开关、注册与异常不外抛。
- 迁移测试：旧表缺列时启动可幂等补列。

前端：

- `fundNavReviews` API 类型与参数序列化。
- `FundNavReviewPanel`：口径切换、徽标、统计卡、单位回退、竞态。
- 走势图组件：加载/空态/错误/口径切换。
- 基金报告页摘要：fund 报告渲染、非 fund 报告不请求。
- `tests/ui_governance.test.ts` 与 i18n 完整性检查。

文档：`docs/fund-nav-review.md` 增补复权口径、回退链、回填接口与区间 API；`docs/CHANGELOG.md` 两条扁平条目（阶段 1、阶段 2）；重新生成 `docs/architecture/api_spec.json`；INDEX 描述同步。

## 13. 交付拆分

- **阶段 1**：数据源、算法、存储迁移、评估、回填（手动 + 可配置自动）、API 字段、组合页双口径、测试与文档。
- **阶段 2**：区间净值 API、走势图、基金报告页摘要、测试与文档。

两个阶段各自独立可交付、可回滚；阶段 2 依赖阶段 1 的复权算法与字段。
