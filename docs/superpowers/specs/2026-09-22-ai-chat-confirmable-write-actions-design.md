# 问股「AI 提案 → 用户确认 → 系统执行」统一通道 — 设计文档

> 目标：让问股（Web `/chat`）的 AI 能够发起**需要用户确认的写操作**，首个落地场景是
> 「把一笔买入/卖出记入账户」与「自选股增删」。现有告警提案机制已在生产运行，但它是
> 按工具名硬编码在三层的**约定**而非机制；本次把它收敛为单一的 `action_proposal`
> 通道，新增动作只需加一个工具 + 一个 kind + 一个 apply 分支。

## 1. 背景与现状

### 1.1 用户问题

用户在问股里要求把「场外基金 25900 份、成本净值 1.80」记入账户，AI 回答自己
「工具都是只读的，只能查询持仓快照」，让用户去券商 App 手动录入。

这不是提示词说谎：agent 工具面里确实**没有任何写工具**。`src/agent/factory.py:193-220`
本次改动**前**注册的 19 个工具（实测 `len(get_tool_registry().list_names())`；改动后为 21）
全部声明 `read_only=True`，其中唯一带"动作"语义的是 `propose_alert`。

### 1.2 已存在的告警提案链路（本次要泛化的对象）

| 层 | 位置 | 行为 |
| --- | --- | --- |
| 工具 | `src/agent/tools/alert_tools.py:149` | 只校验不落库，返回 `{proposal, summary}` |
| runner | `src/agent/runner.py:613,616-643` | 按**工具名**拦截，发 `alert_proposal` SSE 事件，把工具结果改写成 `{"message": summary}` 避免原始 JSON 回流给模型 |
| store | `apps/dsa-web/src/stores/agentChatStore.ts:563-573` | 收事件，`toCamelCase` 后挂在 `done` 时提交的助手消息上（`:642`/`:662`） |
| 卡片 | `apps/dsa-web/src/pages/ChatPage.tsx:1055-1105` | 只读摘要 + 「确认创建/取消」两按钮 |
| 落库 | 浏览器调 `POST /api/v1/alerts/rules` | AI 从不持有写权限，写发生在用户会话下 |

关键性质：`normalize_alert_parameters`（`src/services/alert_service.py:143-195`）由 API 与
工具**共享**，因此"提案通过 ⇒ 接口接受"是可保证的（`alert_service.py:146-148` 明确写了
这一点）。新动作必须沿用这条契约。

### 1.3 现状的三个约束

1. **机制层硬编码**：runner 用 `_ALERT_PROPOSAL_TOOL_NAME` 单值判断，前端 store 用
   `event.type === 'alert_proposal'` 单值判断，卡片只有一种渲染。加第二个动作必须三层
   同时改，否则出现平行实现。
2. **提案工具不进只读面**：`propose_alert` 声明 `cancellation_safe=False`
   （`alert_tools.py:39-41`），`src/agent/tool_surface.py:177` 据此把它挡在 Codex 受控面
   之外。新工具必须同样声明。
3. **标签表有测试强制**：`tests/test_agent_tool_label_parity.py` 从真实注册表取全部工具名，
   断言 `runner._THINKING_TOOL_LABELS` 与 `api/v1/endpoints/agent.py` 的
   `TOOL_DISPLAY_NAMES` 都覆盖且含中文。新增工具漏标即红。

### 1.4 目标写接口已存在（这是可行性基础）

| 动作 | 接口 | 请求体 schema（服务端） |
| --- | --- | --- |
| 记一笔买入/卖出 | `POST /api/v1/portfolio/trades`（`api/v1/endpoints/portfolio.py:204`） | `PortfolioTradeCreateRequest`（`api/v1/schemas/portfolio.py:45-57`） |
| 加入自选 | `POST /api/v1/stocks/watchlist/add`（`api/v1/endpoints/stocks.py:421`） | `WatchlistRequest{stock_code, list_name?}` |
| 移出自选 | `POST /api/v1/stocks/watchlist/remove`（`api/v1/endpoints/stocks.py:459`） | 同上 |

前端客户端也已存在：`portfolioApi.createTrade`（`apps/dsa-web/src/api/portfolio.ts:179`）、
`systemConfigApi.addToWatchlist` / `removeFromWatchlist`（`api/systemConfig.ts:358,371`，
注意是**位置参数**）。

账户解析能力也已具备：`get_portfolio_snapshot`（`src/agent/tools/data_tools.py:603`）的返回里
每个账户都带 `account_id` 与 `account_name`（`data_tools.py:180-181`）。

## 2. 目标与非目标

**目标**

1. 问股 AI 能发起持仓录入与自选增删两类写操作提案，用户在聊天里确认后才落库。
2. 提案通道统一为单一 `action_proposal` 事件，告警一并迁入，不保留第二套通道。
3. 保持「AI 零写权限」：写始终由浏览器在用户会话下调既有 REST 接口。
4. 保持「提案通过 ⇒ 接口接受」的确定性，避免确认后才 400。

**非目标**

- 不做可编辑卡片（卡片上改数量/成本/账户），不做**一个提案承载多个动作**。
  注意这不等于"一轮对话只能有一个提案"——一轮里可以有多个提案，每个提案一个动作，
  前端必须**全部**渲染成卡片（见 §6.2 的累积要求）。
- 不做删除或修改已有 trade / 持仓记录（只做新增类）。
- 不做提案持久化：未确认卡片不落库，刷新页面即丢（与告警现状一致）。不新增 pending 表、不新增接口。
- 不做 bot（飞书/DingTalk）交互式确认：bot 仍只是在最终回复末尾追加一行提示。
- 不新增任何配置开关。
- Codex 只读后端仍不提供任何提案工具。

## 3. 统一提案通道（核心机制）

### 3.1 工具返回信封 = 事件字段

提案工具统一返回（**仅 runner 读取**，不会回流给模型）：

```json
{
  "kind": "portfolio_trade",
  "summary": "在「A股主账户」记一笔买入 005827 25900 @ CNY 1.6706（2026-09-22，约支出 CNY 43268.54）",
  "proposal": { "account_id": 1, "symbol": "005827", "trade_date": "2026-09-22",
                "side": "buy", "quantity": 25900, "price": 1.6706, "fee": 0, "tax": 0 }
}
```

- `kind` 取值集合（runner 校验）：`alert` / `portfolio_trade` / `watchlist_add` / `watchlist_remove`
- `proposal` **恰好是对应写接口的请求体**（snake_case），确认时前端可直接提交，不做字段重排
- `summary` 由**工具处理器**生成，不由模型自由生成——卡片上的文字因此与已校验的 payload 一致

`kind` 由工具自己给出而非 runner 查表，因为 `propose_watchlist_change` 一个工具要能提案
`add` 与 `remove` 两种动作。

### 3.2 runner 泛化

`runner.py:613,616-643` 改为：

```python
# 提案工具名集合；kind 由工具返回的信封给出，runner 不猜
_PROPOSAL_TOOL_NAMES = frozenset({
    "propose_alert", "propose_portfolio_trade", "propose_watchlist_change",
})
_ALLOWED_PROPOSAL_KINDS = frozenset({
    "alert", "portfolio_trade", "watchlist_add", "watchlist_remove",
})
```

`_maybe_emit_alert_proposal`（`runner.py:616-643`）→ `_maybe_emit_action_proposal`，
放行条件逐条对齐现有实现（`:630-641`）并新增一条：

1. `tc.name` 不在 `_PROPOSAL_TOOL_NAMES`，或没有 `progress_callback` → 放行（现有）
2. 结果不是可解析的 JSON dict → 放行（现有）。**工具报错天然走这条**：`{"error": ...}`
   没有 `proposal`/`summary` 键，因此不需要单独的 error 分支
3. `proposal` 不是 dict 或 `summary` 不是 str → 放行（现有，**不收紧**为空串判断，
   与 `agentChatStore.ts:566` 的前端判断保持一致）
4. **`kind` 不在 `_ALLOWED_PROPOSAL_KINDS` 内 → 放行（新增）**，防止工具发出前端无法
   分发的 kind

满足后发射 `stream_event("action_proposal", kind=..., summary=..., proposal=...)`
（`stream_events.py:13`，字段名沿用现有 `proposal`），并把工具结果改写成
`{"message": summary}`。两个调用点（`runner.py:758`、`:804`）同步改名。

`alert_tools.py` 的 `_handle_propose_alert` 返回值从 `{proposal, summary}` 改为
`{kind: "alert", summary, proposal}`；`proposal` 内容不变（仍是
`AlertRuleCreateRequest` 形状）。

### 3.3 事件契约

新增事件类型 `action_proposal`，取代 `alert_proposal`：

| 字段 | 说明 |
| --- | --- |
| `type` | `"action_proposal"` |
| `kind` | 四个取值之一 |
| `summary` | 卡片展示的中文摘要 |
| `proposal` | 对应写接口的请求体（snake_case） |

`alert_proposal` 事件名退役。消费方只有同仓的前端 store（`agentChatStore.ts:563`）与
bot（`bot/commands/ask.py:268`），本次一并改；该事件名未写入 `docs/agent-stream-events.md`
（该文档的事件表本身就没收告警事件，属既有缺口，本次补文档）。

## 4. 两个新工具与校验同源

新增 `src/agent/tools/action_tools.py`，导出 `ALL_ACTION_TOOLS`，在 `factory.py` 与
`ALL_ALERT_TOOLS` 并列注册。`propose_alert` 留在 `alert_tools.py` 不动（避免无谓 import 抖动）。

两工具策略与 `propose_alert` 逐字对齐：

```python
ToolPolicy.declared(read_only=True, side_effects=[], permissions=[],
                    scope_dimensions=[], cancellation_safe=False)
```

`cancellation_safe=False` 使它们自动被 Codex 受控面拒绝（`tool_surface.py:177`）。两工具
`category="action"`。

### 4.1 `propose_portfolio_trade`

参数：`account_id`(int, 必填)、`symbol`(str, 必填)、`side`(enum `buy`/`sell`, 必填)、
`quantity`(number>0, 必填)、`price`(number>0, 必填)、`trade_date`(str `YYYY-MM-DD`, 可选)、
`fee`/`tax`(number, 可选, 默认 0)、`market`(enum, 可选)、`note`(可选)、`reason`(可选)。

校验（逐条对齐 `PortfolioTradeCreateRequest`，`api/v1/schemas/portfolio.py:45-57`）：

- `PortfolioService().list_accounts()` 解析 `account_id`；不存在或未激活 → 返回 `{"error": ...}`
  并**列出可用账户**，让模型自我纠正或反问用户
- `side ∈ {buy, sell}`；`quantity > 0`；`price > 0`；`fee >= 0`；`tax >= 0`
- `symbol` 去空白后非空且 ≤16 字符
- `trade_date` 缺省由**工具**填 `date.today()`（服务器本地日期）。用户要记历史交易必须
  显式给出；日期会显示在卡片上，确认前可见

summary 形如：`在「A股主账户」记一笔买入 005827 25900 @ CNY 1.6706（2026-09-22，约支出 CNY 43268.54）`。

- 金额：买入为 `quantity × price + fee + tax`，卖出为 `quantity × price - fee - tax`（费用在卖出时是
  **扣掉**而非加上），并用 `约支出` / `约收入` 标出方向，避免卖出卡片高估净收入。
- 币种：优先级 `proposal.currency`（模型显式给的标的计价币种）→ `account.base_currency` → 仅数字。
  一律渲染**币种代码**而非符号：成交价是标的的计价币种，未必等于账户本位币（美元账户买 A 股，价是
  CNY），而 `¥` 本身在 CNY/JPY 之间存在歧义（`market` 枚举里确有 `jp`）。
- **刻意不写「股」或「份」**：账户层把 quantity 当纯数字，而按裸代码可靠区分股票与场外基金没有现成
  能力，硬猜单位会在另一类标的上写错。
- 所有数值字段必须拒绝非有限浮点（`math.isfinite`）。`json.loads('{"price": 1e999}')` 会得到 `inf`，
  而 `inf <= 0` / `nan <= 0` 均为 `False`，仅靠范围判断拦不住：`inf` 会被工具与 Pydantic 双双接受，
  最终 `json.dumps` 输出非标准 JSON `Infinity` 使前端 `JSON.parse` 失败；`nan` 则被工具放行却在确认时
  被 Pydantic 拒，正好破坏本设计的核心契约。

### 4.2 `propose_watchlist_change`

参数：`action`(enum `add`/`remove`, 必填)、`stock_code`(str, 必填)、`list_name`(str, 可选)、
`reason`(可选)。

- `stock_code` 用**共享的** `validate_and_normalize_stock_code`（见 §5）校验；非法 → `{"error": ...}`
- `list_name` 给出时，必须命中已存在的 `WATCHLIST_<NAME>`（用共享的 `list_named_watchlists`
  枚举），否则 `{"error": ...}` 并列出合法列表名。**这条校验是必需的**：不校验的话，模型
  编一个列表名就会静默新建一个命名自选列表（真实的配置写入副作用）
- `action` 决定 `kind`：`add` → `watchlist_add`，`remove` → `watchlist_remove`
- `proposal` 形如 `{"stock_code": "600519", "list_name": null}`，即 `WatchlistRequest` 形状

summary 形如：`把「600519」加入自选` / `把「600519」移出自选`；带命名列表时
`把「600519」加入自选列表「短线池」`。

### 4.3 工具参数命名：`symbol`，不是 `stock_code`

`src/agent/tools/execution.py:189-193` 的 `_is_stock_scoped_tool` 只按**参数名**判断
（`param.name == "stock_code"`）。若本工具声明名为 `stock_code` 的参数，`_guard_tool_stock_scope`
（`:210-240`）会在单股会话里对不在该会话范围内的代码**硬阻断**，返回
`{"error": "stock_scope_violation", ..., "retriable": False}`——模型连重试或换个说法都不行。
后果是用户在 600519 的会话里说「把宁德时代加入自选」被静默拒绝，而那是用户明确点名的标的。

因此 handler 参数名与 `ToolParameter(name=...)` 都用 `symbol`（与 `propose_portfolio_trade` 一致，
也和 `propose_alert` 用 `target` 的做法同源），但 `proposal` 里仍写 `stock_code` 以匹配
`WatchlistRequest`。参数名与请求体字段名不同是刻意的，并有测试钉住该决定。

### 4.4 已知不覆盖的失败路径

- 卖出提案无法预先排除 `PortfolioOversellError`（409，可卖数量不足）——预判需要模拟持仓，
  超出本次范围。该失败走卡片的「提交失败，请重试」分支，用户可重试或放弃。
- **提案不幂等**：工具不暴露 `trade_uid`，而 `PortfolioTradeCreateRequest` 也没有 `dedup_hash`
  字段，所以同一笔交易被确认两次会插入两条记录。这是**刻意**的：卡片在 `applying` 期间禁用按钮
  已覆盖误触双击，而给一个确定性 `trade_uid` 会拦住用户合法地记录两笔同日同价交易。若将来要
  幂等，需先给请求体加 `dedup_hash` 字段。
- **未来日期被接受**：与端点一致（端点的 `date` 无上下界）。日期会显示在确认卡片上，确认前可见。

## 5. `watchlist_service` 提取（本次唯一动的存量后端代码）

自选列表的读写逻辑目前是 `api/v1/endpoints/stocks.py` 的**私有 API 层函数**：
`_watchlist_env_key`(`:59`)、`_read_watchlist_codes`(`:77`)、`_write_watchlist_codes`(`:93`)、
`_list_named_watchlists`(`:106`)、`_validate_and_normalize_stock_code`(`:135`)、
`_STOCK_CODE_RE`(`:118`)。从 `src/agent` 反向 import API 层是分层倒置，还会把 FastAPI 拖进
工具模块（这些函数抛 `HTTPException`，也不符合工具需要返回 `{"error": ...}` 的语义）。

**做法**：逻辑搬进 `src/services/watchlist_service.py`，导出
`resolve_watchlist_key` / `read_watchlist_codes` / `write_watchlist_codes` /
`list_named_watchlists` / `validate_and_normalize_stock_code`（后者抛 `ValueError`）。

`api/v1/endpoints/stocks.py` 中的同名私有函数**保留函数名与 400 语义**，函数体改为调用
service 并在 `ValueError` 处转成 `HTTPException(400, ...)`——5 个调用点（`:436`、`:473`、
`:524`、`:597` 等）一行都不用改，API 契约不变。

## 6. 前端

### 6.1 新类型 `apps/dsa-web/src/types/actionProposal.ts`

```ts
/**
 * 提案 kind 的**单一源**：运行时数组在前，类型从中派生。
 *
 * 刻意不写成「联合类型 + 另在 store 里放一份运行时数组」——那样两份可以漂移，而真正决定
 * 事件是否被接受的是运行时数组（union 只在编译期存在）。漏一个 kind 意味着该类提案的卡片
 * 永远不渲染，且 `tsc` 不会报错：`readonly ActionProposalKind[]` 只约束成员合法，不要求
 * 覆盖全部成员。派生写法让二者不可能不一致。
 */
export const ACTION_PROPOSAL_KINDS = [
  'alert',
  'portfolio_trade',
  'watchlist_add',
  'watchlist_remove',
] as const;

export type ActionProposalKind = (typeof ACTION_PROPOSAL_KINDS)[number];

export interface ActionProposal {
  kind: ActionProposalKind;
  /** 卡片展示的中文摘要，由后端工具处理器生成 */
  summary: string;
  /** 对应写接口的请求体，保持后端原样的 snake_case，类型未知 */
  proposal: unknown;
}
```

跨三个域，不塞进 `types/alerts.ts`。`proposal` 保持 `unknown`：各 kind 形状不同，硬塞泛型
只会堆断言；`switch (p.kind)` 收窄的是 `kind` 本身，每个分支内再把 `proposal` 断成该接口
的请求体类型（`AlertRuleCreateRequest` / `PortfolioTradeCreateRequest` / watchlist 请求体）。
四个 kind 的成员形状完全相同，因此不做判别联合（那只会在四个 `proposal` 都是 `unknown`
时产出四个同形成员）。

现有 `AlertProposal`（`types/alerts.ts:94`）随之删除——它只被 store 与 ChatPage 使用，
属本次改动产生的孤儿。

### 6.2 store（`apps/dsa-web/src/stores/agentChatStore.ts`）

- `Message.alertProposal?: AlertProposal`（`:56`）→ **`actionProposals?: ActionProposal[]`**；
  两处 attach（`:642`、`:662`）跟改。**数组而不是单值**：一轮对话可以有多次提案工具调用
  （「把这三只加入自选」会让模型对每只各调一次），单值会后写覆盖先写、静默丢掉先到的提案，
  而并行工具路径下发射顺序是完成顺序、丢哪个还不确定。这与 §7 给 bot 定的"累积而不是只留最后一条"
  是同一条理由。
- 事件分支（`:563-573`）改为 `event.type === 'action_proposal'`，用抽出的
  `parseActionProposalEvent(event)` 解析后 **push 进数组**，**不再在 store 里 `toCamelCase`**
- 卡片状态表的键从 `msg.id` 改为 `` `${msg.id}#${index}` ``——一条消息可能有多张卡片，
  按 `msg.id` 记会让它们的状态互相覆盖
- kind 守卫**从 `types/actionProposal.ts` import `ACTION_PROPOSAL_KINDS`**，不在 store 里另声明
  一份数组（见 §6.1：那份数组是唯一权威，store 只消费它）
- `done` 时才 attach 的既有防竞态注释与结构（`:424-425`、`:630-670`）保持不变

把 camelCase 转换从 store 挪到 apply 边界：现在有三种 payload 类型、各有各的客户端签名，
转换与调用应待在同一处。

**跨语言一致性由测试钉住。** `_ALLOWED_PROPOSAL_KINDS`（Python，runner 白名单）与
`ACTION_PROPOSAL_KINDS`（TS，唯一权威数组）是一份跨语言契约，没有任何结构性联系。若 Python
白名单长出 TS 数组之外，runner 会发射一个前端不认识的 kind，前端按"未知 kind 一律丢弃"处理
（`actionProposal.ts` 的 default 分支抛错、store 的守卫直接不挂卡片），于是 bot 会把用户送去
一个永远不渲染的卡片。按仓库既有做法（`tests/test_paper_disposition_labels.py` 解析 TS 源文件
而不是另抄清单），在 Python 侧加一条测试读 `apps/dsa-web/src/types/actionProposal.ts` 里的
`ACTION_PROPOSAL_KINDS`，断言其与 `_ALLOWED_PROPOSAL_KINDS` 集合相等。

### 6.3 apply 分发：`apps/dsa-web/src/utils/actionProposal.ts`（新）

```ts
export async function applyActionProposal(p: ActionProposal): Promise<void>
```

`switch (p.kind)`：

| kind | 调用 |
| --- | --- |
| `alert` | `alertsApi.createRule(toCamelCase<AlertRuleCreateRequest>(p.proposal))` |
| `portfolio_trade` | `portfolioApi.createTrade(toCamelCase<PortfolioTradeCreateRequest>(p.proposal))` |
| `watchlist_add` | `systemConfigApi.addToWatchlist(payload.stockCode, payload.listName)` |
| `watchlist_remove` | `systemConfigApi.removeFromWatchlist(payload.stockCode, payload.listName)` |

`default` 分支抛错（kind 已在服务端校验，此处是纵深防御）。放 `utils/` 而非 ChatPage 内联，
是为了能像 `src/utils/__tests__/format.test.ts` 那样单独测：喂一个 `portfolio_trade` 提案，
断言调到 `createTrade` 且参数正确。

### 6.4 卡片（`apps/dsa-web/src/pages/ChatPage.tsx`）

- `AlertProposalStatus` → `ActionProposalStatus`（五态不变）；`alertProposalStatus` map →
  `actionProposalStatus`，**键为 `` `${msg.id}#${index}` ``**（见 §6.2：一条消息可能有多张卡片）
- `handleCreateAlertProposal`（`:370-385`）→ `handleApplyActionProposal(cardKey, proposal)`，
  内部只调 `applyActionProposal`；成功/失败态语义不变
- `renderAlertProposalCard`（`:1055-1105`）→ `renderActionProposalCard(msg, proposal, index)`；
  挂载点（`:1500`）改为对 `msg.actionProposals` **逐个渲染**（`?.map(...)`），一条消息 N 张卡片
- 「取消」仍是纯前端状态（卡片消失），不落任何东西

### 6.5 i18n（`apps/dsa-web/src/i18n/uiText.ts`，zh 与 en 两段同步）

删除 6 个 `chat.alertProposal*`（`:1056-1061`、`:2203-2208`），新增 6 个 `chat.actionProposal*`：
标题「待确认操作」、确认「确认」、取消「取消」、提交中「正在提交…」、成功「已提交」、
失败「提交失败，请重试」（en 段对应 Action to confirm / Confirm / Cancel / Submitting… /
Submitted / Failed to submit, please retry）。

**刻意不做 per-kind 文案**：卡片的信息量全在 `summary` 上（由工具处理器生成，不可被模型
编造），按钮再按 kind 分「确认创建/确认录入/确认加入」只是装饰，却要 4 倍 i18n 键并让
zh/en 两段更容易漂移。

### 6.6 桌面端

`apps/dsa-desktop` 复用 web 构建产物，零改动。

## 7. bot（`bot/commands/ask.py`）

- 事件判断 `event.get("type") != "alert_proposal"` → `!= "action_proposal"`
- `alert_hint: Dict[str, str]` → `action_hints: List[str]`（累积，不覆盖）
- 末尾提示行**对所有 kind 统一**为「（请在 Web 端「问股」中确认）」；`kind` 因此完全不需要读取

**为什么不按 kind 分「可用 /alert」这一支（初版设计写错了，此处更正）：** 本仓库**没有 `/alert`
这个 bot 命令**——`bot/commands/` 下不存在 `alert.py`，`git log --all` 显示它历史上从未出现过，
`ALL_COMMANDS`（`bot/commands/__init__.py`）也没有它，因此 `/alert` 会落到 `bot/dispatcher.py` 的
`未知命令: alert` 分支。原先那句「可用 /alert 或 Web 端落库」是**让用户去执行一个必然报错的命令**，
属于对用户说了错话，比"提示不出现"更糟。唯一能真正创建告警规则的路径是
`POST /api/v1/alerts/rules`（Web 端确认卡片）。

- 另外：一次会话可能产出**多个**提案（并行工具路径下完成顺序还不确定），`action_hint` 用单个
  `(summary, kind)` 会静默丢掉除最后一个以外的全部，对从不打开 Web 端的 Feishu 用户等于第一次提案
  完全不可见。改为累积成列表、每个提案渲染一行提示（`BotResponse` 是一次性文本，多一行没有成本）。

## 8. 系统提示词（`src/agent/executor.py`）

两套会话提示词都要加：`LEGACY_DEFAULT_CHAT_SYSTEM_PROMPT` 与 `CHAT_SYSTEM_PROMPT`
（各自规则 6 是告警规则，新区块追加其后，两处逐字相同），共**三条**：

- **规则 7 · 持仓录入提案** — 触发条件是**请求**或**正在为该笔录入补齐信息**，不是"对话里出现过
  买入金额"（后者会让模型在假设句、复盘闲聊时也开卡片）。除账户外，**份额/成本/方向缺失时同样
  先追问，不得用行情价或估算值顶替**——否则在"必须"压力下模型会拿现价当成本，落出一条错的记录。
- **规则 8 · 自选增删提案** — 用 `propose_watchlist_change`；命名列表只在用户点名时传 `list_name`。
- **规则 9 · 写操作与配置改动一律走提案** — 含一条**诚实的出路**：修改/删除已有记录这类**没有对应
  提案工具**的操作，要如实说明需在对应页面处理，**不要用其他提案工具顶替**。缺了这半句会造出死局——
  规则 9 禁止别的路径、规则 7 禁止说"我无法写入"、而没有可用工具，模型只能产出一张摘要与用户
  要求相矛盾的卡片（"改掉刚记错的那笔"正是上线后最可能的请求之一）。末尾还有一句把禁止面扩到全部
  提案场景：**对能通过提案工具完成的写操作，不得声称自己只能查询、无法写入**——规则 7 的那句带
  "在该笔录入有对应提案工具时"的限定语，其禁令只覆盖持仓录入；而用户最初拒答的根因是模型的一个
  **全局信念**（"我的工具都是只读的"），所以自选（规则 8）也需要同一句禁止，放在规则 9 比再抄一份
  到位。标题与正文已对齐（正文枚举的是账户/持仓/自选，告警仍由规则 6 管）。

**账户消歧**写在规则 7 内（先 `get_portfolio_snapshot` 读 `account_id`/`account_name`，仍不唯一就先
追问、不许猜）：卡片无法防止把记录写进一个"貌似合理但错误"的账户，所以这条是**唯一**保护，必须由
测试钉住而不是靠人工维护。

`CODEX_CHAT_SYSTEM_PROMPT` 是只读 Codex 面，**不加**这三条。理由比"那边不暴露提案工具"更强一层：
Codex 面在**列举工具时**就按 `cancellation_safe_only=True` 过滤掉了提案工具（`codex_agent_backend.py`），
而 `get_portfolio_snapshot` 也因未声明 `cancellation_safe` 同样不在该面上——规则 7 同时引用这两者，
所以加进去是**双重**损坏。`AGENT_SYSTEM_PROMPT` / `LEGACY_DEFAULT_AGENT_SYSTEM_PROMPT` 属分析报告面
（现有告警规则也只存在于两套 chat 提示词中），同样不动。

一致性由 `tests/test_chat_prompt_proposal_rules.py` 钉住：两个 chat 提示词含两个工具名与
"先追问用户"/"在用户确认前"等关键子句（**用不含 markdown 强调标记的语义核心**，避免纯排版清理
把测试弄红）；Codex 提示词不含提案工具名、`propose_alert` 与 `get_portfolio_snapshot`；两个分析
提示词不含任何一个；并有一条从 `get_tool_registry()` 反查的断言，确认提示词点名的四个工具都真实
存在（同时抓两个方向的漂移）。

## 9. 工具标签表（两处，测试强制）

| 表 | 位置 | 新增 |
| --- | --- | --- |
| `_THINKING_TOOL_LABELS` | `src/agent/runner.py:70-91` | `propose_portfolio_trade`: 持仓录入提案生成；`propose_watchlist_change`: 自选变更提案生成 |
| `TOOL_DISPLAY_NAMES` | `api/v1/endpoints/agent.py:25-44` | `propose_portfolio_trade`: 生成持仓录入提案；`propose_watchlist_change`: 生成自选变更提案（与既有 `propose_alert: 生成告警提案` 的动词风格一致） |

`tests/test_agent_tool_label_parity.py` 会自动覆盖；漏标即测试失败。

## 10. 验证矩阵

### 10.1 后端

- 新增 `tests/test_action_tools.py`：
  - 两工具注册进 registry、`category="action"`、`read_only=True`、`cancellation_safe=False`
  - 账户不存在 / 已停用 → 返回 `error` 且列出可用账户
  - `side` 非法、`quantity<=0`、`price<=0` 被拒
  - `trade_date` 缺省补今天；显式给出时原样透传
  - `list_name` 不存在 → 报错并列出合法列表名；缺省 → `list_name` 为 null
  - `action` 决定 kind：`watchlist_add` / `watchlist_remove`
  - summary 文案（含账户名、份额、成本、日期）
  - `proposal` 字段与 `PortfolioTradeCreateRequest` / `WatchlistRequest` 逐字段同名
- runner 发射器（镜像 `tests/test_alert_tools.py:69-95` 三条 + 新增一条）：
  正常信封发射事件且结果被改写；非提案工具放行；`{"error": ...}` 结果不发射；
  **`kind` 越界 / `summary` 非 str / `proposal` 非 dict → 不发射且按原始结果放行**
- `python -m pytest -m "not network"`、`./scripts/ci_gate.sh`

### 10.2 前端

- 新增 `src/utils/__tests__/actionProposal.test.ts`：4 个 kind 各一条，断言打到正确的客户端
  与参数（含 watchlist 的位置参数形态）
- 改 `src/stores/__tests__/agentChatStore.test.ts:348`（事件名与字段名）
- 改 `src/pages/__tests__/ChatPage.test.tsx`（卡片断言）
- `npm run lint && npm run build && npm run test`

### 10.3 端到端手工验证

起服务后：在问股中说「把 25900 份 005827、成本 1.6706 记到 A股主账户」→ 出现确认卡片 →
点确认 → 持仓页出现该笔；再验证「把 600519 加入自选」与「移出自选」。截图作为交付证据，
**不入库**（AGENTS.md 第 21 条：审查/验收截图不得作为仓库文件合入）。

## 11. 文档同步

- `docs/agent-stream-events.md`：事件表补一行 `action_proposal`（含 kind 取值、`proposal`
  为写接口请求体）。该文档今天连 `alert_proposal` 都没收录，属既有缺口，一并对齐；
  该文档无英文版，无需同步。
- `docs/CHANGELOG.md` `[Unreleased]`：扁平格式 `- [新功能] ...` / `- [改进] ...`，
  不加类目标题。
- **不需要改**：`docs/alerts.md`、`docs/full-guide.md`、`docs/bot-command.md`、
  `docs/bot-command_EN.md` —— 已逐条确认均未描述 AI 提案链路或 bot 的提议提示行，
  因此不存在中英双语同步问题。

## 12. 风险与回滚

**风险**

1. **动了已上线的告警链路**（事件名 `alert_proposal` → `action_proposal`）。缓解：
   消费方只有同仓的前端 store 与 bot，一并改；后端有 runner 测试、前端有 store/ChatPage
   测试兜底。灰度窗口内（后端已更新、前端未更新）老前端会把未知类型当进度事件收进
   `progressSteps`（`agentChatStore.ts:578`），表现为**卡片不出现但不报错**；而 Docker
   镜像前后端同源构建，该窗口实际不存在。
2. **校验不同源导致"提案通过但确认时 400"**。已用共享校验消除；唯一无法预先排除的是
   卖出 409（§4.3）。
3. **模型编造列表名造成配置写入副作用**。已用 `list_name` 存在性校验消除（§4.2）。
4. **模型仍然口头拒绝**。新增规则 7/8 明确赋予提案能力并要求不得自称只读；端到端手工
   验证（§10.3）就是验证这一点。
5. **`trade_date` 默认取服务器本地日期**，跨时区用户记当日交易可能差一天。日期显示在
   确认卡片上，确认前可见可取消。

**回滚**：整体 revert 单个 commit。无数据库迁移、无新表、无新配置项。回滚后已确认产生的
持仓/自选记录照常存在（那是正常业务数据，与本次代码无关）。

**已核实但**刻意不在本次范围内**的潜在脆弱性**：`apps/dsa-web/src/api/alerts.ts` 的
`toSnakeRulePayload` 用一份**固定字段映射表**转换 `parameters`，因此后端将来新增某个告警参数而
前端映射表漏补时，该参数会在确认落库时被**静默丢弃**（卡片上写了、规则里没有）。我逐项核对了
`propose_alert` 能产出的全部符号告警参数键（`direction` / `price` / `change_pct` / `multiplier` /
`window` / `period` / `threshold` / `fast_period` / `slow_period` / `signal_period` / `k_period` /
`d_period`），**全部已在映射表内**，所以今天没有缺口，这是既有的潜在脆弱性而非新引入的问题。
若要去掉它，可加一条与本设计 §6.2 同类的一致性测试（解析 TS 映射表键集合，断言
`normalize_alert_parameters` 的产出键 ⊆ 该集合），但那属于告警链路的独立改动，不在本次范围。

## 13. 交付结构

按 AGENTS.md §9：改了什么 / 为什么这么改 / 验证情况 / 未验证项 / 风险点 / 回滚方式。
未验证项需明确写出：`apps/dsa-desktop` 未单独验证（复用 web 产物）；Codex 只读面未做
端到端验证（仅由 `cancellation_safe=False` 与既有 `tool_surface.py:177` 保证）。
