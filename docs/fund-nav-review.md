# 场外基金净值复盘

本页说明 `fund_nav_reviews` 台账：每次场外基金净值体检留下一个锚点，之后按净值记录条数评估 1m/3m/6m/1y 的区间表现。它只回答「当时体检之后基金净值怎么走」，不生成买卖建议，也不并入 `decision_signals`。

## 能力边界

- 输入是已有的基金分析链路：`main.py --stocks fund:006229`、单股分析输入 `fund:<6位代码>`，或 Web 端分析场外基金。
- 锚点写入不联网，只取本次分析已经抓到的净值序列最后一条。
- 评估同时保留**单位净值**（`unit_nav`）与**复权口径**：复权优先按分红再投资计算，不可用时回退累计净值，再回退单位净值，并在每行记录实际口径。
- 不产生 hit/miss、不做同类基准对比、不做交易成本或组合盈亏归因。
- 基金分析本身（确定性报告、LLM 增强、通知、历史记录）不因本台账变化；锚点写入失败只记录 warning。

## 锚点口径

- 锚点日期与净值：`profile.nav_history[-1]` 的 `(date, unit_nav)`，即分析当次看到的最新净值。
- 每次体检为 4 个 horizon 各写一行，`eval_status=pending`。
- 唯一键 `(fund_code, anchor_date, horizon, engine_version)`；同一天重复分析只补缺失 horizon，不覆盖首次 `analysis_history_id`。
- `fund_code` 落库为 6 位数字，入参接受 `fund:003095` 或 `003095`（大小写不敏感）。
- `due_date = anchor_date + {1m:35, 3m:100, 6m:190, 1y:380} 天`，只用于筛选候选，不决定最终评估位置。

## 评估口径

| horizon | 目标记录 | 说明 |
| --- | --- | --- |
| `1m` | 锚点之后第 22 条净值 | 与 `compute_metrics` 的 `return_1m` 同口径 |
| `3m` | 第 66 条 | 与 `return_3m` 同口径 |
| `6m` | 第 126 条 | 与 `return_6m` 同口径 |
| `1y` | 第 252 条 | 与 `return_1y` 同口径 |

- 位置按**净值记录条数**计算，不受自然日、节假日和停牌影响；自然日只用于 `due_date`。
- 锚点日期必须在抓取序列中精确存在；缺失记为可重试的 `anchor_not_found`，窗口不会下移。
- 区间收益 `nav_return_pct = (eval_nav / anchor_nav - 1) * 100`。
- 最大回撤 `max_drawdown_pct`：从锚点遍历到评估点，以期间峰值为基准的负百分比。
- 每轮按基金分组，每只基金只调用一次区间净值接口；单轮最多 50 只基金、400 行。

## 复权口径与回退链

评估完成后，每行额外写入 `restored_*` 字段。原 `nav_return_pct` / `max_drawdown_pct` 始终是单位净值口径，语义不变。

回退顺序：

| `restored_basis` | 含义 |
| --- | --- |
| `dividend_reinvested` | 用东财 F10 分红送配数据，把现金分红按除息日（或其后第一条）净值再投资，份额折算按比例放大；区间收益与最大回撤都基于复权序列 |
| `acc_nav` | 分红数据不可用，但窗口内累计净值（`acc_nav`）完整有效；累计净值把分红现金加回，不假设再投资 |
| `unit_nav` | 前两者都不可用，回退到单位净值 |

- 分红与拆分来自 `https://fundf10.eastmoney.com/fhsp_<code>.html`；抓取失败只记录 warning，不阻断评估。
- 复权因子从锚点日起算 1.0，只应用 `(锚点日期, 评估日期]` 区间内的事件；除息日落在非交易日时，按其后第一条净值近似。
- 回退时 `restored_reason` 记录原因：`distribution_fetch_error` / `invalid_distribution_event` / `acc_nav_unavailable`。
- `restored_events` 是窗口内实际应用的分红与拆分事件数。
- `anchor_acc_nav` / `eval_acc_nav` / `acc_nav_return_pct` / `acc_max_drawdown_pct` 始终按累计净值单独记录，便于对比。
- 已知边界：货币基金、净值恒为 1 的品种没有有意义的回撤；累计净值不等于复权净值；东财 F10 缺失或格式变化时按上述回退链降级，不伪造复权结果。

## 状态机

| `eval_status` | 含义 |
| --- | --- |
| `pending` | 尚未到期、净值记录不足或抓取失败可重试 |
| `evaluated` | 已写入 `eval_date` / `eval_nav` / `nav_return_pct` / `max_drawdown_pct` |
| `unable` | 终态：锚点净值非法、净值长期不可用、基金疑似停牌/清盘、评估净值非法 |

- 可恢复失败写入 `last_error`（`fetch_error` / `anchor_not_found` / `waiting_for_records` / `nav_range_empty`）并保持 `pending`。
- `checked_at` 提供 6 小时冷却，避免同一锚点被高频重试；手动 `force=true` 可忽略冷却。
- 净值最新日期早于 `as_of - 30 天` 视为长期停更，到期仍无足够记录时转 `unable/fund_delisted_or_suspended`。
- 区间为空：30 天宽限期内保持 `pending`，超过后转 `unable/fund_nav_unavailable`。
- `evaluated` 或终态 `unable` 会清空 `last_error` / `unable_reason` 中已不再适用的值。

## 自动评估

- `FUND_NAV_REVIEW_AUTO_EVAL_ENABLED` 默认 `true`；关闭后不再注册 `fund_nav_review_evaluation` 后台任务，但手动 `POST /run` 仍可用。
- 调度器每 6 小时执行一次 `run_reviews(limit=100)`，异常只记录 warning，不影响其他后台任务。
- 该任务会访问外部净值数据源（东财 `lsjz`），单只基金失败不会拖垮整轮。

## 复权指标回填

存量行在复权字段引入前已经评估，只包含单位口径。回填会为这些行补算累计净值与复权指标，不修改原有评估字段。

- 手动：`POST /api/v1/fund-nav-reviews/backfill/restore-metrics`，默认 `dry_run=true`，只统计不落库；确认后传 `dry_run=false`。
- 自动：`FUND_NAV_REVIEW_AUTO_RESTORE_ENABLED` 默认 `false`；开启后调度器每天执行一次 `backfill_restored_metrics(dry_run=false, limit=400)`。
- 只处理 `eval_status=evaluated` 且 `restored_checked_at` 为空的行；`force=true` 可重算已回填行。
- 回填前用存储的 `eval_date` 校验目标记录位置；不一致记为 `skipped`，不猜测窗口。
- 净值抓取失败向上抛错，当前基金分组不写入任何行；分红抓取失败只降级到累计/单位口径，不阻断回填。
- 返回 `scanned` / `updated` / `unavailable` / `skipped` / `funds` / `dry_run` / `engine_version`。

## API

路由前缀 `/api/v1/fund-nav-reviews`，复用管理员会话 Cookie 鉴权。错误响应为扁平 `{"error", "message"}`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/v1/fund-nav-reviews` | 分页台账；过滤 `fund_code` / `horizon` / `eval_status` / `anchor_from` / `anchor_to`，`page_size` 最大 100 |
| `GET` | `/api/v1/fund-nav-reviews/stats` | 汇总、`horizon` 与 `month` breakdown；无已评估样本时比率为 `null` |
| `POST` | `/api/v1/fund-nav-reviews/run` | 手动评估；`fund_code` / `horizons` / `force` / `limit<=400`，会联网 |
| `POST` | `/api/v1/fund-nav-reviews/backfill/run` | 从历史基金报告补录锚点 |
| `POST` | `/api/v1/fund-nav-reviews/backfill/restore-metrics` | 为已评估行回填复权指标；默认 `dry_run=true`，会联网 |

### 区间净值走势

`GET /api/v1/fund-nav-reviews/nav-range` 为走势图提供只读数据：

- 参数：`fund_code`（接受 `fund:123456` 或 `123456`）、`start_date`、`end_date`（`YYYY-MM-DD`，含端点）。
- `start_date > end_date` 或区间超过 `1100` 天返回 400；记录超过 `1000` 条时截断并返回 `truncated=true`。
- 响应 `items` 按日期正序，逐条包含 `date` / `unit_nav` / `acc_nav` / `adjusted_nav` / `change_pct`。
- `adjusted_nav` 复用复权算法，以区间首条记录因子为 1；分红数据不可用时为 `null`，`adjustment_status=unavailable`，事件计数归零。
- 只读接口：不写台账、不触发评估；净值抓取异常按 500 返回。

台账 item 在原有字段之外追加 `anchor_acc_nav` / `eval_acc_nav` / `acc_nav_return_pct` / `acc_max_drawdown_pct` / `restored_return_pct` / `restored_max_drawdown_pct` / `restored_basis` / `restored_reason` / `restored_events` / `restored_checked_at`。

统计字段：`total` / `anchor_count`（去重 `(fund_code, anchor_date)`）/ `pending` / `evaluated` / `unable` / `positive_rate_pct` / `avg_nav_return_pct` / `avg_max_drawdown_pct` / `unable_reasons`；复权口径追加 `restored_evaluated` / `restored_positive_rate_pct` / `avg_restored_return_pct` / `avg_restored_max_drawdown_pct` / `restored_bases`。月度维度按 `anchor_date` 所在月（`YYYY-MM`）升序。

## 历史报告补录

`POST /api/v1/fund-nav-reviews/backfill/run` 默认 `dry_run=true`、`resolve_missing=false`：

- 新报告：读取 `analysis_history.raw_result.dashboard.latest_nav_date` 与 `latest_nav`，离线生成锚点，不联网。
- 旧报告（没有 `latest_nav_date`）：只有显式 `resolve_missing=true` 才联网，按报告 `created_at` 前 14 天到后 2 天抓取区间净值，用 `latest_nav` 浮点容差 `0.00005` 匹配，取 `date <= created_at.date()` 的最新一条。
- 同一基金按 `fund_code` 分组，单次最多 50 只基金、200 份报告；同一 `(fund_code, anchor_date)` 去重。
- 返回 `scanned` / `created` / `skipped` / `unmatched` / `funds` / `dry_run` / `engine_version`。
- 补录不会自动执行，也不进入启动流程或调度器。

## Web

组合页在持仓明细之后提供「基金净值复盘」折叠面板：

- 首次展开才请求 list + stats；收起时不发请求。
- 支持周期与状态过滤；展示已评估样本、正收益率、平均区间收益、平均最大回撤、月度表和分页台账。
- 「复权 / 单位」口径切换：默认复权，统计卡、月度表与台账同步切换；每行标注实际 `restored_basis`。
- 「运行评估」会提示该操作联网，完成后刷新台账与统计。
- 点击台账中的基金代码，在面板内按需加载该基金近一年净值走势（默认复权，可切单位 / 累计）；图表组件懒加载，不进首屏 bundle。
- 单只基金报告页在净值指标卡之后提供「净值复盘」摘要：最近 5 条锚点的区间收益、回撤与实际口径；无记录时显示空态。
- 面板与报告页摘要均只读展示，不提供编辑锚点或手工改写结果。

## 回滚

- 关闭 `FUND_NAV_REVIEW_AUTO_EVAL_ENABLED` 可停止自动评估。
- 关闭 `FUND_NAV_REVIEW_AUTO_RESTORE_ENABLED`（默认即关闭）可停止每日复权回填；手动接口仍可用。
- 移除 `src/core/pipeline.py` 中的 `record_anchor` 调用可停止新增锚点。
- `fund_nav_reviews` 表、API 与 Web 面板与现有基金分析、`decision_signals`、`analysis_history` 相互独立；回滚对应代码即可，无数据迁移。
