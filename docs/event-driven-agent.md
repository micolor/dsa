# 事件驱动 Agent 分析

事件驱动分析是主动助手的第二层：不再等用户开口，也不只在盘前/盘后汇总，而是在
盘中出现值得关注的变化时，自动做一次短分析并留下「事件提醒」。

它复用现有告警与行情数据，但只调用 **一次 LLM**，不开放工具调用，避免把盘中提醒
变成另一条完整的多智能体分析流水线。

## 触发事件

- 告警中心最近触发的规则（按 `triggered_at` 过滤）；
- 持仓浮亏达到 `-10%`；
- 自选/持仓单日涨跌幅绝对值 `>= 5%`；
- 现价跌破 MA20；
- 量比 `>= 2`。

同一标的同一类事件每天只处理一次；告警事件按 trigger id 去重。每次扫描默认最多处理
3 条，按「告警 → 持仓浮亏 → 大幅波动 → 放量 → 跌破 MA20」排序。

## 输出

每条事件生成一段 3-5 句中文分析：

1. 事件是什么、是否值得重视；
2. 对趋势/持仓的主要风险或机会；
3. 接下来应关注的一个动作或观察点。

结果写入主动提醒收件箱（类型 `event`，标题形如「事件提醒 · 时间 标的」），铃铛显示未读数。
开启推送后同时发送到已配置的通知渠道（当前为飞书等）；开启
`AGENT_EVENT_ANALYSIS_DIGEST_NOTIFY=true` 时，本轮多条事件合并成一条摘要推送。

## 开启

```env
AGENT_EVENT_ANALYSIS_ENABLED=true
AGENT_EVENT_ANALYSIS_INTERVAL_MINUTES=10
AGENT_EVENT_ANALYSIS_MAX_PER_RUN=3
AGENT_EVENT_ANALYSIS_NOTIFY=true
AGENT_EVENT_ANALYSIS_TIMEOUT_S=60
# 本轮多条事件合并为一条摘要推送（默认逐条）
AGENT_EVENT_ANALYSIS_DIGEST_NOTIFY=false
# 每日 LLM 调用上限（0=不限制；手动触发不受限）
AGENT_EVENT_ANALYSIS_MAX_PER_DAY=20
# 事件分析附带真实新闻（没有可用新闻源时自动跳过）
AGENT_EVENT_ANALYSIS_NEWS_ENABLED=true
```

- `AGENT_EVENT_ANALYSIS_ENABLED` 默认关闭；
- 运行时调度器每 `INTERVAL_MINUTES` 扫描一次，启动后先等一个间隔，不在启动瞬间调用 LLM；
- `AGENT_EVENT_ANALYSIS_NOTIFY` 默认关闭，开启后会真实推送；
- `AGENT_PROACTIVE_MARKET_GUARD=true`（默认）时，事件分析只在 A 股交易日、盘中与收盘后一小时内运行。

## 手动触发

```bash
# 只检测事件，不调用 LLM、不推送
curl http://127.0.0.1:8010/api/v1/agent/proactive/events/preview

# 立即分析未处理事件（会调用 LLM，按配置推送）
curl -X POST http://127.0.0.1:8010/api/v1/agent/proactive/events/run \
  -H 'Content-Type: application/json' -d '{"max_events":1}'
```

## 去重与限制

- 去重记录在 `event_agent_runs` 表，`event_key` 唯一；
- LLM 返回空内容或调用失败时不落库、不标记已处理，下一轮可重试；
- 单条分析上限 500 tokens，超时默认 60 秒；
- 事件分析是辅助信息，不构成投资建议；不会自动交易、不会自动修改持仓或告警规则；
- 场外基金没有 K 线/成交量，当前默认监控只对股票/ETF 有意义；
- `--serve-only` 不启动调度器；自动事件扫描需要 `--serve` / `--schedule`，手动接口不受限。

## 与主动日报的关系

- 主动日报：盘前/盘后汇总，偏「今天整体怎么样」；
- 事件驱动分析：盘中触发，偏「这件事现在意味着什么」；
- 两者都写入顶部铃铛收件箱的不同类型，互不覆盖，也不进入问股会话。
