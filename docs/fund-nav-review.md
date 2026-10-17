# 场外基金净值复盘

本页说明 `fund_nav_reviews` 台账：每次场外基金净值体检留下一个锚点，之后按净值记录条数评估 1m/3m/6m/1y 的区间表现。它只回答「当时体检之后基金净值怎么走」，不生成买卖建议，也不并入 `decision_signals`。

## 能力边界

- 输入是已有的基金分析链路：`main.py --stocks fund:006229`、单股分析输入 `fund:<6位代码>`，或 Web 端分析场外基金。
- 锚点写入不联网，只取本次分析已经抓到的净值序列最后一条。
- 评估使用**单位净值**（`unit_nav`），不还原分红除权；分红日前后不可与累计净值口径直接比较，这是 v1 的已知限制。
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

## API

路由前缀 `/api/v1/fund-nav-reviews`，复用管理员会话 Cookie 鉴权。错误响应为扁平 `{"error", "message"}`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/v1/fund-nav-reviews` | 分页台账；过滤 `fund_code` / `horizon` / `eval_status` / `anchor_from` / `anchor_to`，`page_size` 最大 100 |
| `GET` | `/api/v1/fund-nav-reviews/stats` | 汇总、`horizon` 与 `month` breakdown；无已评估样本时比率为 `null` |
| `POST` | `/api/v1/fund-nav-reviews/run` | 手动评估；`fund_code` / `horizons` / `force` / `limit<=400`，会联网 |
| `POST` | `/api/v1/fund-nav-reviews/backfill/run` | 从历史基金报告补录锚点 |

统计字段：`total` / `anchor_count`（去重 `(fund_code, anchor_date)`）/ `pending` / `evaluated` / `unable` / `positive_rate_pct` / `avg_nav_return_pct` / `avg_max_drawdown_pct` / `unable_reasons`。月度维度按 `anchor_date` 所在月（`YYYY-MM`）升序。

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
- 「运行评估」会提示该操作联网，完成后刷新台账与统计。
- 面板只读展示，不提供编辑锚点或手工改写结果。

## 回滚

- 关闭 `FUND_NAV_REVIEW_AUTO_EVAL_ENABLED` 可停止自动评估。
- 移除 `src/core/pipeline.py` 中的 `record_anchor` 调用可停止新增锚点。
- `fund_nav_reviews` 表、API 与 Web 面板与现有基金分析、`decision_signals`、`analysis_history` 相互独立；回滚对应代码即可，无数据迁移。
