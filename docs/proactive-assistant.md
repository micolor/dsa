# 主动助手总览

DSA 的主动助手由四层组成，结果统一写入主动提醒收件箱，并按需推送通知：

| 能力 | 会话 | 触发 | 文档 |
| --- | --- | --- | --- |
| 助手日报 | `助手日报` | 盘前/盘后（默认 08:30/15:30） | [proactive-brief.md](proactive-brief.md) |
| 事件提醒 | `事件提醒` | 盘中告警/浮亏/大幅波动/放量/跌破 MA20 | [event-driven-agent.md](event-driven-agent.md) |
| 每周复盘 | `每周复盘` | 每周五 15:30（可配置） | [risk-profile-and-weekly-review.md](risk-profile-and-weekly-review.md) |
| 机会扫描 | `机会扫描` | 默认 08:45/15:45 | [opportunity-scan.md](opportunity-scan.md) |

## 主动收件箱

顶部栏右侧的铃铛把四类主动消息汇总成一个下拉收件箱（不再占用问股会话或独立页面）：

- 下拉只展示未读新消息（最多 8 条），底部「查看全部消息」打开全屏列表；
- 全屏收件箱按时间倒序展示日报 / 事件 / 周复盘 / 机会，整页宽度逐行展示类型、标题、摘要与时间；支持按类型筛选、加载更多、全部已读与标记未读，点击消息仍打开半屏详情抽屉并自动已读；
- 打开任意一条会展示完整 Markdown，并自动标记已读（从新消息列表移除）；
- 事件类消息可一键「加入自选」；
- 已读状态保存在服务端表 `proactive_inbox_reads`，换浏览器/设备保持一致；
- 铃铛未读数每 60 秒刷新一次；
- 推送里的站内深链（`AGENT_PROACTIVE_NOTIFY_BASE_URL`）会带上 `?inbox=<id>`，打开页面后自动展开对应消息。

主动消息不再写入问股会话，问股历史只保留用户与助手的对话。

接口：

```text
GET  /api/v1/inbox/messages?type=&unread_only=&page=&page_size=&id=
POST /api/v1/inbox/read
POST /api/v1/inbox/unread
POST /api/v1/inbox/read-all
```

## 共用能力

- **风险画像**：`AGENT_RISK_PROFILE_*`，问股/事件/周复盘都会遵守；
- **推送策略**：各能力有独立 `*_NOTIFY` 开关；`AGENT_PROACTIVE_NOTIFY_MIN_SEVERITY` 控制最低等级，`AGENT_PROACTIVE_NOTIFY_ROUTE` 指定路由类型，`AGENT_PROACTIVE_NOTIFY_RETRIES` 控制重试，`AGENT_PROACTIVE_NOTIFY_BASE_URL` 附加站内深链；
- **免打扰**：`AGENT_PROACTIVE_QUIET_HOURS=22:00-08:00` 时只写入收件箱、不推送；
- **设置页**：所有主动助手配置已接入「系统设置 → Agent 设置」，可直接开关与调整；
- **机会扫描刷新**：`AGENT_OPPORTUNITY_SCAN_REFRESH=true` 时，快照缺失或过期会先重跑一次选股；
- **按账户画像**：`AGENT_RISK_PROFILE_ACCOUNTS_JSON` 可按 account_id 覆盖周复盘的集中度检查；
- **交易日守卫**：`AGENT_PROACTIVE_MARKET_GUARD=true`（默认）时只在 A 股交易日运行主动任务，事件分析额外限制在盘中与收盘后一小时内；
- **成本护栏**：`AGENT_EVENT_ANALYSIS_MAX_PER_DAY=20` 限制每日事件分析 LLM 调用次数，0 表示不限制，手动触发不受限；
- **状态查询**：`GET /api/v1/agent/proactive/status` 返回每个主动任务的启用状态、上次运行、下次运行；
- **保留策略**：`AGENT_PROACTIVE_RETENTION_DAYS=90` 清理过期的运行记录和已读标记，0 表示永久保留；
- **独立数据刷新**：`AGENT_DATA_REFRESH_ENABLED=true` 时在 08:40/15:40 刷新选股快照与热点缓存，机会扫描可直接使用新数据；
- **并发扫描**：`AGENT_PROACTIVE_SCAN_CONCURRENCY=4` 控制日报/事件扫描读取行情与日线的线程数；
- **事件新闻**：`AGENT_EVENT_ANALYSIS_NEWS_ENABLED=true` 时事件分析附带最多 3 条真实新闻；
- **收件箱动作**：事件详情可直接「加入自选」；
- **去重**：日报/机会扫描按日期 + 时间槽，周复盘按 ISO 周，事件按事件 key；
- **审计**：日报/机会扫描记录在 `proactive_brief_runs`，事件记录在 `event_agent_runs`。

## 运行要求

- 自动任务依赖运行时调度器：使用 `main.py --serve` 或 `--schedule`；
- `--serve-only` 只起 Web 服务、不启动调度器，此时可用各手动接口生成；
- 手动接口统一在 `/api/v1/agent/proactive/*` 下：
  - `brief/preview|run`
  - `events/preview|run`
  - `weekly/preview|run`
  - `opportunity/preview|run`

## 安全边界

- 所有内容都是辅助信息，不构成投资建议；
- 不自动交易，不自动修改持仓、告警或配置；
- 高风险写操作仍然只生成确认提案，必须由用户确认；
- 数据源/模型失败只降级对应区块，不阻断主流程；
- 数据过期会显式标注（选股快照、热点缓存）。
