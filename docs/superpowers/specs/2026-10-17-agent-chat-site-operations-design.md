# 问股全站操作智能助手 — 设计文档

> 目标：让 `/chat` 的问股助手从「只读问答 + 三类写提案」扩展为覆盖站点主要操作面的智能助手。
> 除认证与密钥外，用户可以在聊天里查询、直执行低风险操作、发起长任务、确认高风险写操作。
>
> **范围已确认**：除密钥与认证外全覆盖；低风险直执行、其余确认；删除类只需普通确认卡片；
> 基金复盘评估与信号后验评估无限制运行，改为后台任务。

## 1. 背景与现状

### 1.1 已有能力

| 层 | 现状 |
| --- | --- |
| 工具面 | `src/agent/factory.py` 注册 21 个工具：18 个只读 + 3 个提案（`propose_alert` / `propose_portfolio_trade` / `propose_watchlist_change`） |
| 策略元数据 | `src/agent/tools/registry.py` 的 `ToolPolicy` 已有 `read_only` / `side_effects` / `permissions` / `policy_status` / `cancellation_safe` |
| 写通道 | `src/agent/runner.py` 的 `_maybe_emit_action_proposal` 按工具名拦截，发 `action_proposal` SSE 事件 |
| 前端 | `apps/dsa-web/src/stores/agentChatStore.ts` 接收事件；`apps/dsa-web/src/pages/ChatPage.tsx` 渲染确认卡片 |
| 浏览器执行 | 用户确认后由浏览器调用既有 REST 接口，AI 不持有写权限 |
| 任务队列 | `src/services/task_queue.py` 的 `submit_background_task` 支持任意后台 callable，状态可经 `GET /api/v1/analysis/status/{task_id}` 查询 |

### 1.2 差距

- 只读工具只覆盖行情、技术面、资金流、筹码、板块、新闻、组合快照、回测摘要，**无法查询历史报告、告警、任务、筛选、信号、基金复盘、模拟盘、组合流水、情报源、配置状态**。
- 写能力只有 3 类，且全部要求确认；无法直执行低风险操作。
- 无法发起任何长任务：个股/基金分析、大盘复盘、回测、筛选、情报抓取、基金复盘评估、信号后验评估、模拟盘回填。
- 没有统一的风险分级、执行事件和撤销语义。

### 1.3 设计约束

- 复用现有 service 层与 REST 契约，不新增平行实现。
- 不在工具里发 HTTP 请求调用本机 API；工具直接调用 service。
- 认证路由与敏感配置字段永不进入工具面。
- 现有 3 个提案工具的行为对用户保持兼容（名字统一调整除外，见 §6.5）。

## 2. 目标与非目标

### 2.1 目标

1. 问股可以查询全站主要业务数据。
2. 低风险、可逆或只写派生数据的操作直接执行，并在聊天里显示结果与撤销提示。
3. 高风险写操作走统一确认卡片，服务端生成摘要，卡片展示真实请求体。
4. 长任务返回任务句柄，聊天里显示进度，不阻塞对话。
5. 新增操作全部有风险分级、标签、测试和文档，默认拒绝未声明工具。

### 2.2 非目标

- 不做通用 `call_site_api` 工具，不允许模型自由拼 method/path/body。
- 不开放认证、密钥、配置导入导出。
- 不做前端 UI 指令（导航、预填表单、点击按钮）。
- 不做 CSV 文件上传式导入（聊天无可靠文件通道）。
- 不改现有同步 REST 接口的语义；后台化只发生在 agent 工具侧。
- 不改 `decision_signals`、`fund_nav_reviews` 的表结构和 `engine_version`。

## 3. 核心机制

### 3.1 工具策略扩展

在 `ToolPolicy` 增加：

```python
execution_mode: str = "none"   # none | direct | proposal | task
risk_level: str = "read"       # read | low | high
undo_hint: Optional[str] = None
```

- `none`：只读，现有 18 个工具。
- `direct`：工具内部直接执行低风险写操作。
- `proposal`：只校验并返回提案信封，等待用户确认。
- `task`：提交后台任务，返回任务句柄。

约束：

- `execution_mode in {"direct","proposal","task"}` 必须 `read_only=False`、`cancellation_safe=False`。
- `execution_mode == "direct"` 必须提供 `undo_hint`。
- `policy_status != "declared"` 或 `execution_mode` 非法的工具不得注册。
- Codex 只读后端继续按 `cancellation_safe=False` 过滤，因此所有有副作用的工具不会出现在 Codex 工具面。

### 3.2 默认拒绝

`src/agent/factory.py` 构建注册表后调用 `validate_operation_tools()`：

- 遍历注册表，缺 `policy_status="declared"` 或 `execution_mode` 的工具直接抛错。
- 新增测试 `tests/test_agent_tool_policy.py` 断言：
  - 每个工具的 `execution_mode` 属于枚举；
  - `direct` 工具有 `undo_hint`；
  - 工具名不在认证/敏感配置排除清单；
  - 高风险工具全部是 `proposal`，不允许 `direct`。

### 3.3 事件泛化

`src/agent/runner.py`：

- 将 `_PROPOSAL_TOOL_NAMES` 单值判断改为读取工具策略的 `execution_mode`。
- `_maybe_emit_action_proposal` 泛化为 `_maybe_emit_action_event`，按模式发事件：

| 事件 | 触发 | 字段 |
| --- | --- | --- |
| `action_proposal` | `execution_mode=proposal` | `kind`, `summary`, `proposal` |
| `action_executed` | `execution_mode=direct` | `kind`, `summary`, `result`, `undo_hint` |
| `action_task` | `execution_mode=task` | `kind`, `summary`, `task_id`, `status_endpoint`, `poll_interval_ms` |

- 事件字段沿用现有 `stream_event()` 机制。
- 工具返回的原始 JSON 不回流给模型：runner 把结果改写为简短 `message`，与现有提案行为一致。

### 3.4 前端

- `apps/dsa-web/src/stores/agentChatStore.ts` 增加 `action_executed` 与 `action_task` 的 reducer，挂到当前助手消息上。
- `apps/dsa-web/src/pages/ChatPage.tsx` 增加两种卡片：
  - 已执行卡片：摘要、结果、撤销提示；不提供自动撤销按钮（撤销由对应工具反向执行）。
  - 任务卡片：摘要、状态、进度、错误；按 `status_endpoint` 轮询，完成后刷新相关页面数据。
- 现有确认卡片复用，删除类只在文案上标注「不可撤销」，不要求输入确认文本。
- 竞态沿用 `requestIdRef` 写法，切换会话时停止轮询。

### 3.5 审计

- `direct` 执行写结构化日志：`event=agent_direct_action`、`kind`、`tool`、`summary`、`success`。
- 执行摘要保留在助手消息中，作为用户可见审计。
- v1 不新增审计表。

### 3.6 总开关

新增 `AGENT_CHAT_SITE_OPERATIONS_ENABLED`，默认 `true`：

- `false` 时注册表只保留现有 21 个工具。
- 同步更新 `.env.example`、配置文档与 CHANGELOG。

## 4. 操作面清单

### 4.1 只读工具（新增 11 个，`execution_mode=none`）

| 工具 | 参数 | 覆盖的现有接口 |
| --- | --- | --- |
| `get_history_view` | `part`, `record_id?`, `stock_code?`, `page?`, `page_size?` | `GET /history`、`/history/stocks`、`/history/{id}`、`/markdown`、`/news`、`/diagnostics`、`/flow` |
| `get_alert_view` | `part`, `rule_id?`, `page?` | `GET /alerts/rules`、`/rules/{id}`、`/triggers`、`/notifications` |
| `get_analysis_task_view` | `part`, `task_id?` | `GET /analysis/tasks`、`/analysis/status/{id}`、`/analysis/tasks/{id}/flow` |
| `get_backtest_view` | `part`, `code?` | `GET /backtest/results`、`/performance`、`/performance/{code}` |
| `get_screening_view` | `part`, `task_id?`, `run_id?`, `topic?` | `GET /screening/status`、`/strategies`、`/hotspots`、`/hotspots/{topic}`、`/history`、`/history/{run_id}`、`/source-history`、`/screen/tasks/{id}` |
| `get_signal_view` | `part`, `stock_code?`, `signal_id?`, `page?` | `GET /decision-signals`、`/latest/{code}`、`/{id}`、`/outcomes`、`/outcomes/stats`、`/skill-outcomes/stats`、`/{id}/feedback` |
| `get_fund_review_view` | `part`, `fund_code?`, `horizon?`, `start_date?`, `end_date?` | `GET /fund-nav-reviews`、`/stats`、`/nav-range` |
| `get_paper_view` | `part` | `GET /paper/account`、`/snapshot`、`/equity-curve`、`/positions`、`/trades`、`/signals` |
| `get_portfolio_ledger_view` | `part`, `account_id?`, `symbol?` | `GET /portfolio/accounts`、`/trades`、`/cash-ledger`、`/corporate-actions`、`/risk` |
| `get_intelligence_view` | `part`, `source_id?`, `page?` | `GET /intelligence/sources`、`/sources/templates`、`/items` |
| `get_config_view` | `part` | `GET /system/config`（脱敏）、`/scheduler/status`、`/config/schema`、`/usage/summary`、`/usage/dashboard`、`/data-quality/discrepancies`、`/notification-deliveries/deliveries` |

现有 18 个只读工具保持不变。

### 4.2 低风险直执行（7 个，`execution_mode=direct`）

| 工具 | 动作 | 撤销提示 |
| --- | --- | --- |
| `manage_watchlist` | `POST /stocks/watchlist/add`、`/remove` | 反向调用 add/remove |
| `set_alert_rule_enabled` | `POST /alerts/rules/{id}/enable`、`/disable` | 反向启停 |
| `dry_run_alert_rule` | `POST /alerts/rules/{id}/test` | 无副作用，无需撤销 |
| `refresh_fx_rates` | `POST /portfolio/fx/refresh` | 只刷新缓存 |
| `refresh_paper_valuation` | `POST /paper/refresh` | 重新执行即可覆盖 |
| `test_intelligence_source` | `POST /intelligence/sources/test`、`POST /sources/{id}/fetch?dry_run=true` | 只读演练 |
| `test_llm_channel` | `POST /system/llm/test-channel` | 不写配置 |

准入条件（测试强制）：可逆或只写派生数据、不涉及资金账本、不向外发送消息。

### 4.3 长任务（9 个，`execution_mode=task`）

| 工具 | 后台逻辑 | 状态来源 |
| --- | --- | --- |
| `start_stock_analysis` | `POST /analysis/analyze` 等价的 service 调用 | `/analysis/status/{id}` |
| `start_market_review` | 大盘复盘运行时；`send_notification` 固定 `false` | `/analysis/status/{id}` |
| `run_backtest` | `BacktestService.run_backtest` | `/analysis/status/{id}` |
| `start_screening` | 复用 `POST /screening/screen/tasks` 的下沉 service | `/screening/screen/tasks/{id}` |
| `fetch_intelligence` | 单个/全部启用情报源抓取 | `/analysis/status/{id}` |
| `backfill_fund_nav_reviews` | `backfill_from_history` | `/analysis/status/{id}` |
| `run_fund_nav_reviews` | `run_reviews`，不设 `limit` | `/analysis/status/{id}` |
| `run_signal_outcomes` | 信号或 skill 后验评估，不设 `limit` | `/analysis/status/{id}` |
| `backfill_paper_signals` | `POST /paper/backfill` 等价 service | `/analysis/status/{id}` |

实现要点：

- 统一通过 `get_task_queue().submit_background_task(...)` 提交。
- 任务返回结果必须有非空 dict，否则队列会判失败。
- 任务卡片轮询间隔 2s，完成或失败后停止。
- 共享 `max_workers=3`：高峰期排队，卡片显示 `pending` 状态并给出说明。

### 4.4 高风险确认（11 个，`execution_mode=proposal`）

| 工具 | 动作 |
| --- | --- |
| `propose_portfolio_trade` | 交易录入（已有，保留） |
| `propose_portfolio_ledger_entry` | 现金/公司行为流水新增与删除 |
| `propose_portfolio_account_change` | 账户创建、修改、停用 |
| `propose_alert_rule_change` | 告警规则创建、修改、删除（替代 `propose_alert`） |
| `propose_signal_change` | 信号创建、状态修改、用户反馈、重评估 |
| `propose_paper_reset` | 模拟盘重置 |
| `propose_history_deletion` | 按记录或按代码删除历史分析 |
| `propose_intelligence_source_change` | 情报源创建、修改、删除、创建默认源 |
| `propose_config_update` | 非敏感配置修改，逐字段白名单 |
| `propose_scheduler_run` | 立即执行定时分析（会产生分析与通知） |
| `propose_notification_test` | 通知渠道测试（会真实外发） |

提案信封保持现有契约：

```json
{
  "kind": "portfolio_trade",
  "summary": "服务端生成的摘要",
  "proposal": { "...": "对应写接口的请求体" }
}
```

- `kind` 由工具给出，runner 校验白名单。
- `summary` 由工具处理器生成，不由模型自由生成。
- 删除类只在卡片上标注不可撤销，不做二次输入确认。
- 配置修改卡片展示字段级 old → new 差异，敏感字段在工具层直接拒绝。

### 4.5 明确排除

- `POST /auth/*` 全部路由。
- `PUT /system/config` 中 `is_sensitive=True` 的字段；`POST /config/import`、`GET /config/export`。
- 券商 CSV 文件上传导入（`/portfolio/imports/csv/*`）。
- 纯 UI 操作：分享图下载、Markdown 复制、主题切换、页面导航。

## 5. 执行语义

### 5.1 direct

1. 工具校验参数，调用 service。
2. 返回 `{kind, summary, result, undo_hint}`。
3. runner 发 `action_executed`，并把工具结果改写为简短 `message`。
4. 失败时返回结构化错误，不改写为成功事件；模型向用户解释失败原因。

### 5.2 proposal

保持现状：工具只校验不落库；用户确认后浏览器调用既有 REST 接口。新增动作必须保证「提案通过 ⇒ 接口接受」，复用 API 与工具共享的规范化函数。

### 5.3 task

1. 工具提交后台任务，返回 `{kind, summary, task_id, status_endpoint}`。
2. runner 发 `action_task`。
3. 卡片轮询状态接口，完成后展示摘要与结果入口。
4. 提交失败直接返回错误，不伪造任务 ID。

## 6. 实施分期

| 阶段 | 内容 | 独立可回滚 |
| --- | --- | --- |
| 0 | `ToolPolicy` 扩展、默认拒绝校验、runner 事件泛化、前端 reducer 与卡片骨架、现有提案回归 | 是 |
| 1 | 11 个只读聚合工具 | 是 |
| 2 | 7 个直执行工具与撤销提示 | 是 |
| 3 | 9 个长任务工具与任务卡片 | 是 |
| 4 | 11 个高风险提案工具 | 是 |
| 5 | 文档、CHANGELOG、`.env.example`、验收记录 | 是 |

阶段 0 完成前不注册任何新工具；阶段 1-4 每个阶段单独提交。

### 6.1 阶段 0

- 扩展 `ToolPolicy` 与 `ToolDefinition` 校验。
- runner 泛化，现有 3 个提案事件字段不变。
- 前端新增两个 reducer 与卡片骨架，先用测试桩事件验证。
- 新增 `tests/test_agent_tool_policy.py`。

### 6.2 阶段 1

- 新增 `src/agent/tools/site_read_tools.py`（或按域拆分为 history/alert/signal 等文件，单文件不超过约 500 行）。
- 每个工具只做参数归一化 + service 调用 + 结果裁剪；不返回原始大 JSON。
- 查询结果统一限制条数，避免把整张表塞进上下文。

### 6.3 阶段 2

- 新增 `src/agent/tools/site_action_tools.py`。
- `manage_watchlist` 替代 `propose_watchlist_change`；同步更新标签表、测试、文档。
- 每个工具声明 `undo_hint`。

### 6.4 阶段 3

- 新增 `src/agent/tools/site_task_tools.py`。
- 端点里仅存在于 API 层的提交逻辑下沉到 service，端点改为调用 service；不改变 REST 行为。
- `start_market_review` 固定 `send_notification=false`；需要推送时引导用户走「立即调度」确认卡片或页面。
- `run_fund_nav_reviews` 与 `run_signal_outcomes` 不设 `limit`。

### 6.5 阶段 4

- 扩展 `src/agent/tools/action_tools.py` 与 `alert_tools.py`。
- `propose_alert` 更名 `propose_alert_rule_change`，`propose_watchlist_change` 更名 `manage_watchlist`；同步更新：
  - `runner._THINKING_TOOL_LABELS`
  - `api/v1/endpoints/agent.py` 的 `TOOL_DISPLAY_NAMES`
  - `tests/test_agent_tool_label_parity.py`
  - 前端 `ChatPage` 提案 kind 映射
  - 文档
- 配置更新工具按 `src/core/config_registry.py` 的 `is_sensitive` 过滤字段。

### 6.6 阶段 5

- 新增 `docs/agent-chat-operations.md`。
- 更新 `docs/agent-stream-events.md`、`docs/INDEX.md`、`docs/INDEX_EN.md`、`.env.example`、`docs/CHANGELOG.md`。
- CHANGELOG 使用扁平条目，不新增 `###` 标题。

## 7. 测试

### 7.1 后端

- `tests/test_agent_tool_policy.py`：策略完整性、默认拒绝、直执行必须有 `undo_hint`、高风险不得直执行、排除项不在注册表。
- `tests/test_agent_site_read_tools.py`：每个工具的参数归一化与 service 调用、结果裁剪、分页透传。
- `tests/test_agent_site_action_tools.py`：直执行成功/失败、撤销提示、日志字段。
- `tests/test_agent_site_task_tools.py`：任务提交、task_id 返回、提交失败不伪造 ID、`send_notification=false`。
- `tests/test_agent_action_events.py`：三种事件的字段与 runner 改写行为。
- `tests/test_agent_write_proposals.py`：每个提案的 payload 能被对应 REST schema 接受；删除类 kind 正确。
- `tests/test_agent_config_tool_security.py`：敏感字段拒绝；认证路由不在白名单。
- 现有 `tests/test_agent_tool_label_parity.py` 扩展覆盖全部新工具。

### 7.2 前端

- `agentChatStore` 新事件 reducer 测试。
- `ChatPage` 三类卡片渲染与轮询测试（成功、失败、超时、切换会话停止轮询）。
- `tests/ui_governance.test.ts` 保持通过。
- `npm run lint`、`npx vitest run`、`npm run build`。

### 7.3 手工验收

- `--serve-only` 启动，避免触发全量分析。
- 逐阶段验证：
  - 查询类工具能回答页面上的数据。
  - 直执行后刷新页面确认状态变化，并按撤销提示恢复。
  - 高风险操作先出现卡片，取消后无变化，确认后落库。
  - 长任务返回任务卡片，进度推进，完成后结果可查。
  - 关闭总开关后新工具全部消失，现有 21 个工具行为不变。

## 8. 风险与回滚

| 风险 | 缓解 |
| --- | --- |
| 工具面从 21 增至约 56，模型选择退化、提示词成本上升 | 统一工具描述模板；阶段化上线；补工具选择回归；必要时按域裁剪工具面 |
| 直执行越过确认 | 只开放 7 个白名单；测试强制 `undo_hint`；结构化日志 |
| 长任务占满共享队列 | 卡片显示排队状态；本阶段接受排队，后续评估独立 executor |
| 非敏感配置仍可能破坏运行 | 字段级 old → new 展示；危险项（调度、数据源优先级）另行标注 |
| 模型选错对象 | 服务端校验 + 服务端摘要；卡片展示真实 payload |
| 删除类误操作 | 普通确认 + 不可撤销标注；提案 payload 由服务端生成，不采用模型自由文本 |

回滚：

- 关闭 `AGENT_CHAT_SITE_OPERATIONS_ENABLED` → 回退到现有 21 个工具。
- 每个阶段独立 revert，无数据库迁移，不改现有 REST 契约。
- 若需更细粒度回滚，按阶段移除对应工具注册即可。

## 9. 兼容性

- REST API：不变；长任务后台化只发生在 agent 工具侧。
- Codex 只读后端：所有有副作用工具声明 `cancellation_safe=False`，不会进入 Codex 工具面；行为与现状一致。
- 现有提案用户体验：确认卡片不变；仅工具名和 `kind` 命名调整。
- 数据库：无新增表、无迁移。
- 配置：新增一个总开关，默认开启。
