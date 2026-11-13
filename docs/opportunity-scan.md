# 机会扫描

机会扫描把最近一次选股快照和热点题材整理成一份候选清单，写入主动提醒收件箱
（类型 `opportunity`），并标注候选是否已经在自选或持仓中。

它不是实时推荐，也不会自动运行选股；数据来源是 DSA 已有的选股历史与热点缓存。

## 触发时间

默认 `08:45,15:45`（Asia/Shanghai），由运行时调度器每 5 分钟检查一次；同一日期 +
同一时间槽只生成一次。

## 输入

- 最近一次选股运行及其候选（评分、主题、理由、风险、关注项）；
- 最近一次热点题材缓存；
- 当前自选与持仓，用于标记「已在自选 / 已在持仓」；
- 用户风险画像（候选页仅作提示，不在此处自动筛选）。

## 输出

- 候选清单：名称/代码、评分、主题/行业、理由、风险、关注项、持有状态；
- 热点题材：最近 3 个热点的涨跌、热度与阶段；
- 数据时效提示：选股快照或热点缓存超过阈值时会明确标注「已过期」；
- 使用说明与风险提示。

## 配置

```env
AGENT_OPPORTUNITY_SCAN_ENABLED=true
AGENT_OPPORTUNITY_SCAN_TIMES=08:45,15:45
AGENT_OPPORTUNITY_SCAN_MAX_CANDIDATES=5
AGENT_OPPORTUNITY_SCAN_MAX_AGE_HOURS=72
AGENT_OPPORTUNITY_SCAN_NOTIFY=true
# 快照缺失或过期时先重跑一次选股（默认关闭；会联网且耗时）
AGENT_OPPORTUNITY_SCAN_REFRESH=false
```

- 默认关闭；本地开启后按时间自动生成；
- `MAX_AGE_HOURS` 用于判断选股快照是否过期；
- `NOTIFY` 开启后会推送到已配置的通知渠道。

## 手动触发

```bash
curl http://127.0.0.1:8010/api/v1/agent/proactive/opportunity/preview
curl -X POST http://127.0.0.1:8010/api/v1/agent/proactive/opportunity/run
```

## 限制

- 不会自动运行选股；没有选股历史时只提示「请先运行选股」；
- 选股快照过期时仍会展示，但会明确标注，不应据此直接下单；
- 热点缓存过期时同样标注；
- 候选只是研究起点，不是买入建议；不会自动交易；
- `--serve-only` 不启动调度器；自动扫描需要 `--serve` / `--schedule`，手动接口不受限。
