# 主动助手日报

主动助手日报把「持仓、自选、告警、市场」汇总成一条可读的助手消息，不再等用户提问。
它是对既有定时分析 / 告警 / 事件监控的补充，不调用 LLM，结果可重复、可审计。

## 行为

- 按 `AGENT_PROACTIVE_BRIEF_TIMES`（默认 `08:30,15:30`，Asia/Shanghai）生成；
- 写入主动提醒收件箱，类型为 `daily_brief`，标题形如「助手日报 · YYYY-MM-DD HH:MM」；
- 同一日期 + 同一时间槽只生成一次（记录在 `proactive_brief_runs` 表，唯一键为日期 + 时间槽）；
- 手动触发不参与去重，可重复生成；
- 单个数据源或单只标的失败只影响对应区块，不阻断整份日报；
- 生成后在顶部栏右侧铃铛显示未读数，打开收件箱即可查看；
- 开启 `AGENT_PROACTIVE_BRIEF_NOTIFY=true` 时，日报会同时通过已配置的通知渠道推送（受最低等级、免打扰与重试策略约束）。

## 日报内容

- **市场**：上证、深证、创业板、上证 50 等主要指数；
- **持仓**：现价、涨跌、浮盈亏、是否位于 MA20 上方/下方；
- **自选异动**：只列涨跌幅绝对值 ≥5%、跌破 MA20 或量比 ≥2 的标的；
- **告警**：近 24 小时触发的告警（最多 5 条）；
- **今日关注**：根据持仓/自选/告警自动生成的行动项，例如跌破 MA20、单日大跌、浮亏超过 10%、放量。

## 开启

```env
AGENT_PROACTIVE_BRIEF_ENABLED=true
AGENT_PROACTIVE_BRIEF_TIMES=08:30,15:30
# 日报最多检查多少只自选股；持仓不受此限制
AGENT_PROACTIVE_BRIEF_WATCHLIST_LIMIT=20
# 生成后推送到已配置的通知渠道（默认关闭）
AGENT_PROACTIVE_BRIEF_NOTIFY=true
```

> 主动日报依赖运行时调度器。`main.py --serve-only` 只起 Web 服务、不启动调度器；
> 需要 `main.py --serve` 或 `--schedule`，或者通过下面的接口手动生成。
> Docker/后台常驻部署通常使用 `--serve`。

## 手动触发

```bash
# 预览，不写入收件箱
curl http://127.0.0.1:8000/api/v1/agent/proactive/brief/preview

# 生成并写入主动提醒收件箱
curl -X POST http://127.0.0.1:8000/api/v1/agent/proactive/brief/run \
  -H 'Content-Type: application/json' -d '{"slot":"manual"}'
```

## 数据与限制

- 依赖实时行情、日线缓存、持仓快照和告警中心；数据源不可用时对应区块显示「暂不可用」。
- 场外基金目前只展示持仓盈亏，不套用股票 MA20/成交量逻辑。
- 日报是辅助信息，不构成投资建议；不会自动交易，也不会自动修改持仓或告警规则。

## 后续扩展

- 盘前/盘后时段感知与免打扰；
- 事件驱动 Agent 分析（告警触发后自动生成 3-5 句结论 + 动作卡片）；
- 个性化风险画像、决策日志与周复盘。
