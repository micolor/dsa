# 定时任务与任务日历

定时任务用于把「每天/每周固定时间要做的分析」交给系统自动执行，并用日历查看每一天会跑哪些任务。

入口：左侧导航「定时任务」（`/scheduled-tasks`），或直接让问股助手创建。

## 支持的任务类型

| 类型 | 说明 | 主要参数 |
| --- | --- | --- |
| `stock_analysis` | 个股 / 自选 / 场外基金分析 | `stocks`（留空使用当前自选；场外基金写 `fund:005827`）、`report_type`、`force_refresh`、`mode`（standard/quick）、`notify` |
| `market_review` | 大盘复盘 | `region`（cn/hk/us）、`notify` |
| `screening` | 选股 | `strategy`、`market`、`max_results` |
| `backtest` | 回测评估 | `code`（留空全量）、`force`、`limit` |
| `agent_prompt` | 自定义助手指令 | `prompt`（5-2000 字），由问股 Agent 在后台执行并把结果写入运行记录 |

除固定类型外，`agent_prompt` 可以记录任意自然语言指令，例如「每周一分析当前持仓风险并列出三个观察点」。

## 调度语义

- 时间统一按 `Asia/Shanghai`；`weekdays` 使用 `0=周一 ... 6=周日`。
- `trading_day_only=true` 时，非交易日不执行，日历显示「已跳过」。
- 调度轮询每 60 秒检查一次当前时间槽；同一任务同一时间槽只会执行一次（`scheduled_task_runs.slot_key` 唯一）。
- 服务重启不会补跑历史时间槽；错过的时间槽在日历中标记「未执行」。
- 任务提交到现有后台任务队列执行（`stock_analysis` / `market_review` / `screening` / `backtest`），`agent_prompt` 在调度线程内同步调用**通用单 Agent**（不进入多智能体股票流水线，避免后台任务先跑数分钟分析）。
- 默认只在 `main.py --serve` / `--schedule` 启动调度器时执行；`--serve-only` 只提供页面与手动「立即执行」。

## 快速模式

`stock_analysis` 的 `payload.mode="quick"` 只拉实时行情与最近 30 个日线，计算 MA5/10/20、5 日涨跌、量比和相对 MA20 位置，生成每个标的一行摘要（场外基金用最新净值与区间收益/回撤），不运行完整分析流水线、也不进入多 Agent。适合「每天开盘前给我看一眼自选」。

- 更快、可重复、不消耗多阶段 LLM；
- 结果写入定时任务运行记录，勾选 `notify` 时同时推送通知；
- 不生成完整分析报告/历史报告；
- 数据源失败只影响对应标的。

## 页面

- 月历按天展示任务；点击某天查看当天任务、状态与执行结果摘要。
- 任务列表支持启用/停用、编辑、删除、立即执行。
- 新建/编辑表单按任务类型展示对应参数。

## 助手能力

问股助手新增工具：

- `get_scheduled_tasks_view`：读取任务与未来若干天日历；
- `create_scheduled_task`：创建任务（必须先征得用户明确同意）；
- `update_scheduled_task`：修改名称、时间、重复日或参数；
- `set_scheduled_task_enabled`：启用/停用；
- `run_scheduled_task_now`：立即在后台执行一次。

删除任务仍需在页面操作。

## 在问股里直接创建

可以直接对问股助手说：

- 「每天早上 8:30 分析 600519，创建一个定时任务」
- 「每周一、周三 15:30 帮我复盘大盘」
- 「每天收盘后提醒我检查持仓风险」（会创建 `agent_prompt` 自定义任务）

包含「定时任务 / 每天 / 每周 / 工作日 / 提醒我」等调度措辞的消息会走通用单 Agent，从而能调用定时任务工具；纯个股分析问题仍走多智能体流水线。助手在创建前会先确认任务类型、时间和重复日，创建后用一句话确认；删除仍需在定时任务页面操作。

## 配置

```env
# 总开关；关闭后不再注册 60 秒轮询任务
SCHEDULED_TASKS_ENABLED=true
```

## 接口

```text
GET    /api/v1/scheduled-tasks
POST   /api/v1/scheduled-tasks
PATCH  /api/v1/scheduled-tasks/{id}
DELETE /api/v1/scheduled-tasks/{id}
POST   /api/v1/scheduled-tasks/{id}/enabled
POST   /api/v1/scheduled-tasks/{id}/run
GET    /api/v1/scheduled-tasks/calendar?start=YYYY-MM-DD&end=YYYY-MM-DD
GET    /api/v1/scheduled-tasks/{id}/runs
```

## 限制与安全边界

- 日历范围最多 92 天。
- `agent_prompt` 会消耗 LLM 调用；成本与问股对话相同。
- 任务不会自动交易、不会修改持仓或告警配置。
- 任务执行失败只记录在运行记录与任务列表，不影响其他任务。
- 多进程部署时依赖数据库唯一键去重；同一时间槽仍只执行一次。
- `agent_prompt` 任务会在内部 `scheduled:` 会话中保留上下文，不会出现在问股历史列表。
