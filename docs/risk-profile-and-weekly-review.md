# 风险画像与每周复盘

这是主动助手第三层的两个能力：把「你的风险边界」写进建议，并把一周的判断结果收口成复盘。

## 风险画像

配置（环境变量）：

```env
AGENT_RISK_PROFILE_HORIZON=medium        # short / medium / long
AGENT_RISK_PROFILE_MAX_DRAWDOWN_PCT=15
AGENT_RISK_PROFILE_MAX_POSITION_PCT=35
AGENT_RISK_PROFILE_NOTES=
# 可选：按账户覆盖全局画像（JSON，键为 account_id；目前用于周复盘集中度检查）
AGENT_RISK_PROFILE_ACCOUNTS_JSON={"2":{"max_position_pct":60,"horizon":"short"}}
```

生效位置：

- 问股单 Agent 系统提示词：建议必须遵守单票上限与回撤容忍，超限时优先提示减仓/分散；
- 事件驱动 Agent 分析：事件数据中携带画像，结论按画像约束；
- 每周复盘：检查单票集中度与组合回撤是否超过画像上限；
- 按账户覆盖：`AGENT_RISK_PROFILE_ACCOUNTS_JSON` 可按 `account_id` 覆盖单票上限/期限/回撤，周复盘会分别检查每个账户的集中度（问股与事件分析仍使用全局画像）。

画像只约束建议，不会自动调仓或自动交易。

## 每周复盘

默认每周五 15:30 生成一份「每周复盘」，写入主动提醒收件箱（类型 `weekly`）：

- **账户**：总权益、浮动盈亏、已实现盈亏；
- **决策复盘**：本周到期的决策信号、命中/未命中/中性、方向命中率、平均个股收益与超额收益；
- **风险画像**：单票集中度、组合回撤是否超限；
- **自选周涨跌**：领涨/领跌各 3 只（按最近 5 个交易日）；
- **告警**：本周告警数量与最近 3 条；
- **下周关注**：由画像风险、自选领涨/领跌自动生成。

配置：

```env
AGENT_WEEKLY_REVIEW_ENABLED=true
AGENT_WEEKLY_REVIEW_DAY=4          # 0=周一 ... 6=周日
AGENT_WEEKLY_REVIEW_TIME=15:30
AGENT_WEEKLY_REVIEW_NOTIFY=true
```

- 同一 ISO 周只生成一次（记录在 `proactive_brief_runs`，slot=`weekly`）；
- 与日报/事件提醒共用顶部铃铛收件箱与未读数；
- `--serve-only` 不启动调度器；需要 `--serve` / `--schedule` 或手动接口。

## 手动触发

```bash
curl http://127.0.0.1:8010/api/v1/agent/proactive/weekly/preview
curl -X POST http://127.0.0.1:8010/api/v1/agent/proactive/weekly/run
```

## 限制

- 决策复盘只统计已有后验结果的信号；没有到期信号时显示「本周暂无到期」；
- 周涨跌依赖日线缓存，数据不足时跳过该标的；
- 全局画像仍是一份配置；按账户覆盖目前只支持周复盘的集中度检查，问股与事件分析仍使用全局画像；
- 复盘是辅助信息，不构成投资建议。

## 相关能力

机会扫描（基于选股快照与热点生成候选与理由）见 [opportunity-scan.md](opportunity-scan.md)。
