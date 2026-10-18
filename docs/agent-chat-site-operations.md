# 问股全站操作助手

问股页面（`/chat`）在原有问答与分析能力之上，可以读取全站数据、直接执行低风险可逆操作、
提交长任务，并对高风险写操作生成确认卡片。开关：`AGENT_CHAT_SITE_OPERATIONS_ENABLED`
（默认 `true`）。关闭后回到功能上线前的工具集（含既有持仓录入与告警规则提案卡片），
本功能新增的只读视图、直执行、后台任务与提案工具全部移除；同时对话系统提示词会追加
一段停用说明，明确告知模型这些工具不可调用、不得声称操作已执行。工具注册表带缓存，
需重启服务后生效。

运行前提：`AGENT_ARCH=single`（默认值）。`AGENT_ARCH=multi` 时问股走多智能体分析流水线，
该流水线按个股分析阶段固定编排，不读取本页的对话规则，也不会调度这些站点操作工具；
需要全站操作助手时请改用单 Agent 架构。

## 执行语义

| 模式 | 含义 | 用户看到 |
| --- | --- | --- |
| `none` | 只读 | 无卡片 |
| `direct` | 立即执行 | 「已执行」卡片 + 撤销方式 |
| `task` | 提交后台任务 | 任务卡片 + 状态轮询 |
| `proposal` | 生成确认卡片 | 确认/取消，确认后由浏览器调用既有 REST 写接口 |

## 只读视图

`get_*_view` 系列工具只读数据库或进程内状态，不产生卡片。主要视图：

- `get_history_view`：历史列表、单条详情；传 `record_id` 时可再用 `part` 选择
  `detail`（默认）/`news`（原始情报）/`markdown`（报告原文）。
- `get_alert_view`：规则、触发记录、通知记录；通知记录按 `trigger_id` 过滤。
- `get_config_view`：非敏感配置项（`config`）、LLM 用量（`usage`，支持
  `period=today|month|all`）、数据质量差异（`data_quality`）、通知投递记录
  （`notifications`）。密钥、Token、密码类字段永远不会返回。
- `get_screening_view`：策略、运行状态、历史、热点、数据源历史（`source_history`）。
- `get_signal_view`：信号、当前有效信号、后验结果、统计，以及技能历史胜率
  （`skill_stats`，可按 `skill_id`/`horizon` 过滤）。
- `get_fund_review_view`：基金净值复盘台账、统计，以及净值区间序列
  （`nav_range`，需 `fund_code`、`start_date`、`end_date`）。
- 其余：`get_analysis_task_view`、`get_backtest_view`、`get_paper_view`、
  `get_portfolio_ledger_view`、`get_intelligence_view`、`get_scheduler_view`。

以下前端能力刻意不暴露为 agent 读取工具：历史页的诊断/流程视图，以及选股任务详情
`screen/tasks/{id}`；助手只能读到已有的任务列表/详情与历史运行结果。

## 直接执行（低风险）

`manage_watchlist`、`set_alert_rule_enabled`、`dry_run_alert_rule`、`refresh_fx_rates`、
`refresh_paper_valuation`、`test_intelligence_source`、`test_llm_channel`。这些操作可逆、
不涉及资金、不修改密钥；其中 `test_llm_channel` 的密钥只在服务端读取，不会进入对话或事件。

## 后台任务

个股分析、大盘复盘、回测、选股、情报抓取、基金复盘补录/评估、信号后验、模拟盘回填。
基金复盘评估与信号后验评估不设条数上限：任务按 400/500 条分批循环，直到没有可处理行。
任务状态统一查询 `GET /api/v1/analysis/status/{task_id}`。

## 提案（高风险）

告警规则增删改、持仓交易/资金/公司行动/账户变更、决策信号、模拟盘重置、历史删除、
情报源创建、系统配置修改、立即调度、通知渠道测试。提案不会直接写入；用户确认后由浏览器
以当前会话调用既有 REST 接口，AI 不持有写权限。删除类操作使用普通确认卡片，不要求输入文本。

## 明确排除

- 登录、登出、会话、API Key 等认证与密钥操作。
- 配置 schema 中 `is_sensitive=true` 的字段（`propose_config_update` 会拒绝并提示到设置页修改）。
- 配置导入/导出、CSV 文件导入（需要本地文件选择，属于纯 UI 操作）。
- 情报源更新/删除：当前后端没有对应 REST 端点，助手只支持创建情报源与创建默认源。

## 审计与回滚

- 每次直接执行、提案、任务提交都会写入一条 `[AgentAction]` 结构化日志
  （`agent_action=<...> tool=<...> kind=<...> success=<true|false>`）；
  载荷缺失或非法时记录 `agent_action=action_rejected` 且 `success=false`。
  无进度回调的后台执行同样写日志。
- 直接执行在卡片上展示撤销方式；提案在用户确认前不产生任何副作用。
- 关闭 `AGENT_CHAT_SITE_OPERATIONS_ENABLED` 并重启服务后，本功能新增的全部站点读写工具会被收回，工具集回到功能上线前（含既有持仓录入与告警规则提案卡片），原有问答不受影响。

## 前端入口

- `/chat` 是完整形态：历史会话、导出、发送到通知渠道、策略选择。
- 其余页面右下角常驻悬浮助手（`apps/dsa-web/src/components/layout/AssistantDock.tsx`）。
  展开后复用同一个 `ChatPage` 的 `dock` 形态，即同一份消息渲染、同一个 `agentChatStore`
  会话：悬浮层里的提问会出现在 `/chat` 的历史里，反之亦然。悬浮层隐藏历史侧栏、
  导出/发送工具栏与策略控件（宽度不足），保留消息流、操作确认卡片与输入框；窄屏铺满全屏。
  空会话时首屏给出站点操作向的推荐问法（持仓、自选、加自选、资讯），点击即发送；
  输入框在桌面端展开后自动聚焦，发送/停止收为图标按钮，触屏不自动弹软键盘。
- 离开 `/chat` 时仍在流式输出的回复由 store 继续接收，结束后入口按钮显示未读红点，
  打开面板即清除；在整页阅读完回复则不会留下红点。
- 悬浮层不消费、也不清空当前页面的查询串（`?stock=` 等跟进参数只由整页形态处理），
  也不会改写当前页面标题。面板打开期间 Esc 归最上面那层：先关确认卡片 / 策略浮层，再收面板。
- 前置条件与整页相同：需要 `AGENT_ARCH=single` 才会调度站点操作工具。
