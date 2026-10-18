# 问股全站操作智能助手 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `/chat` 问股助手在除认证与密钥外的全站范围内查询、直执行低风险操作、发起长任务、确认高风险写操作。

**Architecture:** 扩展现有 `ToolPolicy`，用 `execution_mode`（none/direct/proposal/task）声明每个工具的执行语义；runner 按策略发 `action_proposal` / `action_executed` / `action_task` 三类事件；工具全部调用现有 service 层，不新增 REST 契约、不新增数据库表。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy、pytest；React 18、TypeScript、Zustand、Vitest、Testing Library。

**Spec:** `docs/superpowers/specs/2026-10-17-agent-chat-site-operations-design.md`

## Global Constraints

- 未经用户明确确认，不执行 `git commit`、`git tag`、`git push`；计划中的每个 commit 步骤都必须先询问用户。
- commit message 使用英文，不添加 `Co-Authored-By`。
- 不写死密钥、账号、路径、模型名、端口或环境差异逻辑。
- 所有工具必须调用 `src/services/` 或 `src/repositories/`，不在工具里发 HTTP 请求调用本机 API。
- 认证路由（`api/v1/endpoints/auth.py`）和配置 schema 中 `is_sensitive=True` 的字段永不注册工具。
- 所有有副作用工具必须 `read_only=False`、`cancellation_safe=False`；`direct` 工具必须声明 `undo_hint`。
- 默认拒绝：`policy_status != "declared"` 或 `execution_mode` 非法的工具不得进入注册表。
- `docs/CHANGELOG.md` 的 `[Unreleased]` 使用扁平条目 `- [类型] 描述`，禁止新增 `###` 类目标题。
- 涉及 Web UI 的 PR 必须在 PR 描述附截图；验收截图不得作为仓库文件合入。
- 后端验证优先 `./scripts/ci_gate.sh`；前端验证 `npm run lint`、`npx vitest run`、`npm run build`。

---

## File Structure

### 后端

| 文件 | 责任 |
| --- | --- |
| `src/agent/tools/registry.py`（修改） | `ToolPolicy` 增加执行元数据；`ToolDefinition.validate_action_policy()`；严格校验 |
| `src/agent/action_contract.py`（新建） | 三类事件允许的 `kind` 单一源 |
| `src/agent/runner.py`（修改） | 按策略发三类动作事件 |
| `src/agent/factory.py`（修改） | 注册新工具；总开关；严格校验 |
| `src/agent/tools/site_read_tools.py`（新建） | 11 个只读聚合工具 |
| `src/agent/tools/site_action_tools.py`（新建） | 7 个低风险直执行工具 |
| `src/agent/tools/site_task_tools.py`（新建） | 9 个长任务工具 |
| `src/agent/tools/action_tools.py`（修改） | 提案工具扩展与改名 |
| `src/agent/tools/alert_tools.py`（修改） | 告警提案改名与扩展 |
| `src/services/site_task_submitter.py`（新建） | 把仅存在于端点的后台提交逻辑下沉，供端点与工具共用 |
| `src/services/runtime_scheduler.py`（修改） | 提供进程级调度服务访问器，供只读状态工具使用 |
| `api/v1/endpoints/agent.py`（修改） | `TOOL_DISPLAY_NAMES` 覆盖新工具 |
| `api/v1/endpoints/screening.py`、`analysis.py`（修改） | 改为调用下沉后的提交函数，行为不变 |
| `src/config.py`、`src/core/config_registry.py`（修改） | `AGENT_CHAT_SITE_OPERATIONS_ENABLED` |

### 前端

| 文件 | 责任 |
| --- | --- |
| `apps/dsa-web/src/types/agentAction.ts`（新建） | 已执行/任务事件类型与解析 |
| `apps/dsa-web/src/types/actionProposal.ts`（修改） | 扩展提案 kind |
| `apps/dsa-web/src/utils/actionProposal.ts`（修改） | 新 kind 的 apply 分支 |
| `apps/dsa-web/src/stores/agentChatStore.ts`（修改） | 三类事件 reducer |
| `apps/dsa-web/src/pages/ChatPage.tsx`（修改） | 已执行/任务卡片 |
| `apps/dsa-web/src/api/intelligence.ts`（新建） | 情报源客户端（当前 Web 无对应 UI） |
| `apps/dsa-web/src/api/portfolio.ts`（修改） | 增加 `updateAccount` |
| `apps/dsa-web/src/locales/*`（修改） | 新卡片文案中英齐全 |

### 测试

`tests/test_agent_tool_policy.py`、`tests/test_agent_action_events.py`、`tests/test_agent_site_read_tools.py`、`tests/test_agent_site_action_tools.py`、`tests/test_agent_site_task_tools.py`、`tests/test_agent_write_proposals.py`、`tests/test_agent_config_tool_security.py`、`tests/test_runtime_scheduler_singleton.py`；前端 `src/types/__tests__/agentAction.test.ts`、`src/stores/__tests__/agentChatStore.test.ts`、`src/pages/__tests__/ChatPage.test.tsx`。

---

## Task 1: ToolPolicy 执行元数据与默认拒绝

**Files:**
- Modify: `src/agent/tools/registry.py`
- Modify: `src/agent/tools/alert_tools.py:38-45`
- Modify: `src/agent/tools/action_tools.py:30-36`
- Test: `tests/test_agent_tool_policy.py`

**Interfaces:**
- Produces: `EXECUTION_MODES`、`RISK_LEVELS`、`ToolPolicy.execution_mode`、`ToolPolicy.risk_level`、`ToolPolicy.undo_hint`、`ToolDefinition.validate_action_policy()`
- Consumes: 现有 `ToolRegistry.validate_tool_policies(strict=True)`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_agent_tool_policy.py
import pytest

from src.agent.tools.registry import (
    EXECUTION_MODES,
    RISK_LEVELS,
    ToolDefinition,
    ToolPolicy,
)


def _tool(**policy_kwargs):
    return ToolDefinition(
        name="demo",
        description="demo",
        parameters=[],
        handler=lambda: None,
        policy=ToolPolicy.declared(**policy_kwargs),
    )


def test_execution_enums_are_exact():
    assert EXECUTION_MODES == {"none", "direct", "proposal", "task"}
    assert RISK_LEVELS == {"read", "low", "high"}


def test_direct_requires_undo_hint():
    tool = _tool(read_only=False, execution_mode="direct", risk_level="low")
    with pytest.raises(ValueError, match="undo_hint"):
        tool.validate_action_policy()


def test_direct_with_undo_hint_passes():
    tool = _tool(
        read_only=False,
        execution_mode="direct",
        risk_level="low",
        undo_hint="再次调用并反向操作即可撤销",
    )
    tool.validate_action_policy()


def test_write_mode_cannot_be_read_only_or_cancellation_safe():
    for mode in ("direct", "proposal", "task"):
        tool = _tool(
            read_only=True,
            execution_mode=mode,
            risk_level="high",
            cancellation_safe=True,
            undo_hint="x",
        )
        with pytest.raises(ValueError):
            tool.validate_action_policy()


def test_none_mode_must_be_read_risk():
    tool = _tool(read_only=True, execution_mode="none", risk_level="high")
    with pytest.raises(ValueError):
        tool.validate_action_policy()


def test_unknown_mode_rejected():
    tool = _tool(read_only=True, execution_mode="write", risk_level="read")
    with pytest.raises(ValueError, match="execution_mode"):
        tool.validate_action_policy()


def test_default_registry_passes_strict_validation():
    from src.agent.factory import get_tool_registry

    issues = get_tool_registry().validate_tool_policies(strict=True)
    assert issues == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_agent_tool_policy.py -q`
Expected: FAIL，`ImportError: cannot import name 'EXECUTION_MODES'`

- [ ] **Step 3: 实现**

`src/agent/tools/registry.py` 在 `SUPPORTED_TOOL_SURFACE_SCOPE_DIMENSIONS` 后加入：

```python
EXECUTION_MODES = frozenset({"none", "direct", "proposal", "task"})
RISK_LEVELS = frozenset({"read", "low", "high"})
```

`ToolPolicy` 增加字段：

```python
    execution_mode: str = "none"
    risk_level: str = "read"
    undo_hint: Optional[str] = None
```

`ToolPolicy.declared` 增加参数并透传：

```python
        execution_mode: str = "none",
        risk_level: str = "read",
        undo_hint: Optional[str] = None,
```

```python
        return cls(
            read_only=read_only,
            side_effects=list(side_effects or []),
            permissions=list(permissions or []),
            policy_status="declared",
            scope_dimensions=list(scope_dimensions or []),
            cancellation_safe=bool(cancellation_safe),
            execution_mode=execution_mode,
            risk_level=risk_level,
            undo_hint=undo_hint,
        )
```

`to_public_dict` 增加三个字段：

```python
            "execution_mode": self.execution_mode,
            "risk_level": self.risk_level,
            "undo_hint": self.undo_hint,
```

`ToolDefinition` 增加方法：

```python
    def validate_action_policy(self) -> None:
        """Reject tools whose execution metadata cannot be safely dispatched."""
        policy = self.policy
        if policy.policy_status != "declared":
            raise ValueError(f"{self.name}: tool policy is not declared")
        if policy.execution_mode not in EXECUTION_MODES:
            raise ValueError(
                f"{self.name}: invalid execution_mode {policy.execution_mode!r}"
            )
        if policy.risk_level not in RISK_LEVELS:
            raise ValueError(f"{self.name}: invalid risk_level {policy.risk_level!r}")
        if policy.execution_mode == "none":
            if policy.read_only is not True:
                raise ValueError(f"{self.name}: execution_mode=none requires read_only=True")
            if policy.risk_level != "read":
                raise ValueError(f"{self.name}: execution_mode=none requires risk_level=read")
            return
        if policy.read_only is not False:
            raise ValueError(
                f"{self.name}: execution_mode={policy.execution_mode} requires read_only=False"
            )
        if policy.cancellation_safe:
            raise ValueError(
                f"{self.name}: execution_mode={policy.execution_mode} must not be cancellation_safe"
            )
        if policy.execution_mode == "direct" and not (policy.undo_hint or "").strip():
            raise ValueError(f"{self.name}: execution_mode=direct requires undo_hint")
```

`ToolRegistry.validate_tool_policies` 在 `if not strict: continue` 之后追加：

```python
            try:
                tool_def.validate_action_policy()
            except ValueError as exc:
                issues.append({
                    "tool": tool_def.name,
                    "code": "action_policy_invalid",
                    "message": str(exc),
                })
```

现有提案工具的 policy 改为：

```python
_ALERT_PROPOSAL_POLICY = ToolPolicy.declared(
    read_only=False,
    side_effects=["write"],
    execution_mode="proposal",
    risk_level="high",
    cancellation_safe=False,
)
```

`action_tools.py` 的 `_PROPOSAL_POLICY` 同样增加 `execution_mode="proposal", risk_level="high"`。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_agent_tool_policy.py -q`
Expected: PASS

- [ ] **Step 5: Commit（等待用户明确确认）**

```bash
git add src/agent/tools/registry.py src/agent/tools/alert_tools.py src/agent/tools/action_tools.py tests/test_agent_tool_policy.py
git commit -m "feat(agent): add execution metadata to tool policies"
```

---

## Task 2: 三类动作事件与 runner 泛化

**Files:**
- Create: `src/agent/action_contract.py`
- Modify: `src/agent/runner.py:679-740`
- Test: `tests/test_agent_action_events.py`

**Interfaces:**
- Consumes: `ToolPolicy.execution_mode`
- Produces: `ACTION_PROPOSAL_KINDS`、`ACTION_EXECUTED_KINDS`、`ACTION_TASK_KINDS`；`_maybe_emit_action_event(tc, result_str, progress_callback, step, tool_registry)` 返回改写后的 `result_str`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_agent_action_events.py
import json
from dataclasses import dataclass

from src.agent.runner import _maybe_emit_action_event
from src.agent.tools.registry import ToolDefinition, ToolPolicy, ToolRegistry


@dataclass
class _ToolCall:
    name: str


def _registry(mode: str, read_only: bool) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="demo",
        description="demo",
        parameters=[],
        handler=lambda: None,
        policy=ToolPolicy.declared(
            read_only=read_only,
            execution_mode=mode,
            risk_level="read" if mode == "none" else "low",
            undo_hint="reverse it" if mode == "direct" else None,
        ),
    ))
    return registry


def test_direct_emits_executed_event():
    events = []
    result = json.dumps({
        "kind": "fx_refresh",
        "summary": "已刷新汇率",
        "result": {"updated": 3},
        "undo_hint": "再次刷新即可覆盖",
    })
    rewritten = _maybe_emit_action_event(
        _ToolCall("demo"), result, events.append, 1, _registry("direct", False)
    )
    assert events[0]["type"] == "action_executed"
    assert events[0]["kind"] == "fx_refresh"
    assert json.loads(rewritten) == {"message": "已刷新汇率"}


def test_task_emits_task_event():
    events = []
    result = json.dumps({
        "kind": "stock_analysis",
        "summary": "已提交 600519 分析",
        "task_id": "abc",
        "status_endpoint": "/api/v1/analysis/status/abc",
    })
    rewritten = _maybe_emit_action_event(
        _ToolCall("demo"), result, events.append, 1, _registry("task", False)
    )
    assert events[0]["type"] == "action_task"
    assert events[0]["task_id"] == "abc"
    assert json.loads(rewritten) == {"message": "已提交 600519 分析"}


def test_unknown_kind_is_not_emitted():
    events = []
    result = json.dumps({"kind": "not_a_kind", "summary": "x", "task_id": "a"})
    rewritten = _maybe_emit_action_event(
        _ToolCall("demo"), result, events.append, 1, _registry("task", False)
    )
    assert events == []
    assert rewritten == result
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_agent_action_events.py -q`
Expected: FAIL，`ImportError: cannot import name '_maybe_emit_action_event'`

- [ ] **Step 3: 实现**

新建 `src/agent/action_contract.py`：

```python
# -*- coding: utf-8 -*-
"""Kinds that the agent may dispatch to the chat UI."""

from __future__ import annotations

ACTION_PROPOSAL_KINDS = frozenset({
    "alert_create", "alert_update", "alert_delete",
    "portfolio_trade",
    "portfolio_cash_create", "portfolio_cash_delete",
    "portfolio_corporate_create", "portfolio_corporate_delete",
    "portfolio_account_create", "portfolio_account_update", "portfolio_account_deactivate",
    "signal_create", "signal_status", "signal_feedback", "signal_reassess",
    "paper_reset",
    "history_delete_records", "history_delete_by_code",
    "intelligence_source_create", "intelligence_source_update",
    "intelligence_source_delete", "intelligence_defaults_create",
    "config_update",
    "scheduler_run",
    "notification_test",
})

ACTION_EXECUTED_KINDS = frozenset({
    "watchlist_change",
    "alert_rule_toggle",
    "alert_rule_test",
    "fx_refresh",
    "paper_refresh",
    "intel_source_test",
    "llm_channel_test",
})

ACTION_TASK_KINDS = frozenset({
    "stock_analysis",
    "market_review",
    "backtest_run",
    "screening_run",
    "intelligence_fetch",
    "fund_nav_backfill",
    "fund_nav_run",
    "signal_outcomes_run",
    "paper_backfill",
})
```

`src/agent/runner.py` 删除 `_PROPOSAL_TOOL_NAMES` 与 `_ALLOWED_PROPOSAL_KINDS`，替换为：

```python
from src.agent.action_contract import (
    ACTION_EXECUTED_KINDS,
    ACTION_PROPOSAL_KINDS,
    ACTION_TASK_KINDS,
)

_ACTION_KINDS = {
    "proposal": ACTION_PROPOSAL_KINDS,
    "direct": ACTION_EXECUTED_KINDS,
    "task": ACTION_TASK_KINDS,
}


def _maybe_emit_action_event(
    tc,
    result_str: str,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]],
    step: int,
    tool_registry: ToolRegistry,
) -> str:
    """Emit one action event for direct/proposal/task tools and hide raw JSON from the model."""
    if not progress_callback:
        return result_str
    tool_def = tool_registry.get(tc.name)
    if tool_def is None:
        return result_str
    mode = tool_def.policy.execution_mode
    allowed = _ACTION_KINDS.get(mode)
    if allowed is None:
        return result_str
    try:
        payload = json.loads(result_str)
    except (TypeError, ValueError):
        return result_str
    if not isinstance(payload, dict):
        return result_str
    kind = payload.get("kind")
    summary = payload.get("summary")
    if not isinstance(kind, str) or kind not in allowed:
        return result_str
    if not isinstance(summary, str) or not summary.strip():
        return result_str

    if mode == "proposal":
        proposal = payload.get("proposal")
        if not isinstance(proposal, dict):
            return result_str
        progress_callback(
            stream_event("action_proposal", kind=kind, proposal=proposal, summary=summary)
        )
    elif mode == "direct":
        result = payload.get("result")
        undo_hint = payload.get("undo_hint")
        if not isinstance(result, dict) or not isinstance(undo_hint, str):
            return result_str
        progress_callback(stream_event(
            "action_executed",
            kind=kind,
            summary=summary,
            result=result,
            undo_hint=undo_hint,
        ))
    else:
        task_id = payload.get("task_id")
        status_endpoint = payload.get("status_endpoint")
        if not isinstance(task_id, str) or not isinstance(status_endpoint, str):
            return result_str
        progress_callback(stream_event(
            "action_task",
            kind=kind,
            summary=summary,
            task_id=task_id,
            status_endpoint=status_endpoint,
            poll_interval_ms=2000,
        ))
    return json.dumps({"message": summary}, ensure_ascii=False)
```

两处调用点改为：

```python
        result_str = _maybe_emit_action_event(
            tc, result_str, progress_callback, step, tool_registry
        )
```

```python
            result_str = _maybe_emit_action_event(
                tc_item, result_str, progress_callback, step, tool_registry
            )
```

- [ ] **Step 4: 运行测试**

Run: `uv run python -m pytest tests/test_agent_action_events.py tests/test_agent_write_proposals.py -q`
Expected: PASS（`test_agent_write_proposals.py` 若引用旧函数名，改为 `_maybe_emit_action_event` 并补 `get_tool_registry()`）

- [ ] **Step 5: Commit（等待用户明确确认）**

```bash
git add src/agent/action_contract.py src/agent/runner.py tests/test_agent_action_events.py
git commit -m "feat(agent): generalize action events for direct and task tools"
```

---

## Task 3: 前端事件类型与 store reducer

**Files:**
- Create: `apps/dsa-web/src/types/agentAction.ts`
- Modify: `apps/dsa-web/src/stores/agentChatStore.ts:47-69, 594-600, 640-700`
- Test: `apps/dsa-web/src/types/__tests__/agentAction.test.ts`
- Test: `apps/dsa-web/src/stores/__tests__/agentChatStore.test.ts`

**Interfaces:**
- Produces: `ActionExecuted`、`ActionTask`、`parseActionExecutedEvent()`、`parseActionTaskEvent()`；`Message.actionExecutions?`、`Message.actionTasks?`
- Consumes: SSE 事件 `action_executed`、`action_task`

- [ ] **Step 1: 写失败测试**

```typescript
// apps/dsa-web/src/types/__tests__/agentAction.test.ts
import { describe, expect, it } from 'vitest';
import {
  parseActionExecutedEvent,
  parseActionTaskEvent,
} from '../agentAction';

describe('agentAction', () => {
  it('parses an executed event', () => {
    expect(parseActionExecutedEvent({
      type: 'action_executed',
      kind: 'fx_refresh',
      summary: '已刷新汇率',
      result: { updated: 3 },
      undo_hint: '再次刷新即可覆盖',
    })).toEqual({
      kind: 'fx_refresh',
      summary: '已刷新汇率',
      result: { updated: 3 },
      undoHint: '再次刷新即可覆盖',
    });
  });

  it('parses a task event', () => {
    expect(parseActionTaskEvent({
      type: 'action_task',
      kind: 'stock_analysis',
      summary: '已提交分析',
      task_id: 'abc',
      status_endpoint: '/api/v1/analysis/status/abc',
      poll_interval_ms: 2000,
    })).toEqual({
      kind: 'stock_analysis',
      summary: '已提交分析',
      taskId: 'abc',
      statusEndpoint: '/api/v1/analysis/status/abc',
      pollIntervalMs: 2000,
    });
  });

  it('rejects unknown kinds and blank summaries', () => {
    expect(parseActionExecutedEvent({ kind: 'nope', summary: 'x', result: {}, undo_hint: 'x' }))
      .toBeUndefined();
    expect(parseActionTaskEvent({
      kind: 'stock_analysis', summary: '  ', task_id: 'a',
      status_endpoint: '/x', poll_interval_ms: 2000,
    })).toBeUndefined();
  });
});
```

store 测试追加一个用例，复用现有 `createStreamResponse` 与 `startStream` 写法：

```typescript
it('attaches executed and task actions to the assistant message', async () => {
  vi.mocked(agentApi.chatStream).mockResolvedValue(createStreamResponse([
    accepted('req-1'),
    `data: ${JSON.stringify({
      type: 'action_executed', kind: 'fx_refresh', summary: '已刷新汇率',
      result: { updated: 3 }, undo_hint: '再次刷新即可覆盖',
    })}`,
    `data: ${JSON.stringify({
      type: 'action_task', kind: 'stock_analysis', summary: '已提交分析',
      task_id: 'abc', status_endpoint: '/api/v1/analysis/status/abc',
      poll_interval_ms: 2000,
    })}`,
    `data: ${JSON.stringify({ type: 'done', success: true, content: '完成' })}`,
  ]));
  await useAgentChatStore.getState().startStream({ message: '刷新汇率并分析 600519' });
  const message = useAgentChatStore.getState().messages.at(-1);
  expect(message?.actionExecutions?.[0].kind).toBe('fx_refresh');
  expect(message?.actionTasks?.[0].taskId).toBe('abc');
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd apps/dsa-web && npx vitest run src/types/__tests__/agentAction.test.ts src/stores/__tests__/agentChatStore.test.ts`
Expected: FAIL，模块不存在

- [ ] **Step 3: 实现**

`apps/dsa-web/src/types/agentAction.ts`：

```typescript
export const ACTION_EXECUTED_KINDS = [
  'watchlist_change',
  'alert_rule_toggle',
  'alert_rule_test',
  'fx_refresh',
  'paper_refresh',
  'intel_source_test',
  'llm_channel_test',
] as const;

export type ActionExecutedKind = (typeof ACTION_EXECUTED_KINDS)[number];

export interface ActionExecuted {
  kind: ActionExecutedKind;
  summary: string;
  result: Record<string, unknown>;
  undoHint: string;
}

export const ACTION_TASK_KINDS = [
  'stock_analysis',
  'market_review',
  'backtest_run',
  'screening_run',
  'intelligence_fetch',
  'fund_nav_backfill',
  'fund_nav_run',
  'signal_outcomes_run',
  'paper_backfill',
] as const;

export type ActionTaskKind = (typeof ACTION_TASK_KINDS)[number];

export interface ActionTask {
  kind: ActionTaskKind;
  summary: string;
  taskId: string;
  statusEndpoint: string;
  pollIntervalMs: number;
}

function isKind<T extends readonly string[]>(values: T, value: unknown): value is T[number] {
  return typeof value === 'string' && (values as readonly string[]).includes(value);
}

export function parseActionExecutedEvent(event: unknown): ActionExecuted | undefined {
  const payload = (event ?? {}) as Record<string, unknown>;
  if (!isKind(ACTION_EXECUTED_KINDS, payload.kind)) return undefined;
  if (typeof payload.summary !== 'string' || !payload.summary.trim()) return undefined;
  if (!payload.result || typeof payload.result !== 'object' || Array.isArray(payload.result)) return undefined;
  if (typeof payload.undo_hint !== 'string' || !payload.undo_hint.trim()) return undefined;
  return {
    kind: payload.kind,
    summary: payload.summary,
    result: payload.result as Record<string, unknown>,
    undoHint: payload.undo_hint,
  };
}

export function parseActionTaskEvent(event: unknown): ActionTask | undefined {
  const payload = (event ?? {}) as Record<string, unknown>;
  if (!isKind(ACTION_TASK_KINDS, payload.kind)) return undefined;
  if (typeof payload.summary !== 'string' || !payload.summary.trim()) return undefined;
  if (typeof payload.task_id !== 'string' || !payload.task_id) return undefined;
  if (typeof payload.status_endpoint !== 'string' || !payload.status_endpoint.startsWith('/')) return undefined;
  const pollIntervalMs = typeof payload.poll_interval_ms === 'number'
    ? Math.max(1000, Math.min(10_000, payload.poll_interval_ms))
    : 2000;
  return {
    kind: payload.kind,
    summary: payload.summary,
    taskId: payload.task_id,
    statusEndpoint: payload.status_endpoint,
    pollIntervalMs,
  };
}
```

`agentChatStore.ts`：

- import 类型与解析函数。
- `Message` 增加：

```typescript
  actionExecutions?: ActionExecuted[];
  actionTasks?: ActionTask[];
```

- 流处理局部变量：

```typescript
      const pendingExecutions: ActionExecuted[] = [];
      const pendingTasks: ActionTask[] = [];
```

- 在 `action_proposal` 分支后加入：

```typescript
        if (event.type === 'action_executed') {
          const executed = parseActionExecutedEvent(event);
          if (executed) pendingExecutions.push(executed);
          return;
        }

        if (event.type === 'action_task') {
          const task = parseActionTaskEvent(event);
          if (task) pendingTasks.push(task);
          return;
        }
```

- 两处助手消息对象增加：

```typescript
                actionExecutions: pendingExecutions.length > 0 ? pendingExecutions : undefined,
                actionTasks: pendingTasks.length > 0 ? pendingTasks : undefined,
```

- [ ] **Step 4: 运行测试**

Run: `cd apps/dsa-web && npx vitest run src/types/__tests__/agentAction.test.ts src/stores/__tests__/agentChatStore.test.ts`
Expected: PASS

- [ ] **Step 5: Commit（等待用户明确确认）**

```bash
git add apps/dsa-web/src/types/agentAction.ts apps/dsa-web/src/types/__tests__/agentAction.test.ts apps/dsa-web/src/stores/agentChatStore.ts apps/dsa-web/src/stores/__tests__/agentChatStore.test.ts
git commit -m "feat(web): ingest executed and task action events"
```

---

## Task 4: 总开关与注册表严格校验

**Files:**
- Modify: `src/config.py:1232-1237, 2254-2262`
- Modify: `.env.example`
- Modify: `src/agent/factory.py:193-224`
- Test: `tests/test_agent_tool_policy.py`

**Interfaces:**
- Produces: `Config.agent_chat_site_operations_enabled: bool`；注册表在开关关闭时不注册新工具
- Consumes: Task 1 的 `validate_action_policy()`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_agent_tool_policy.py 追加
def test_site_tools_absent_when_kill_switch_off(monkeypatch):
    import src.agent.factory as factory

    monkeypatch.setattr(
        factory, "_site_operations_enabled", lambda: False, raising=False
    )
    factory._TOOL_REGISTRY = None
    try:
        registry = factory.get_tool_registry()
        assert "get_history_view" not in registry
        assert "propose_portfolio_trade" in registry
    finally:
        factory._TOOL_REGISTRY = None


def test_site_tools_present_by_default():
    from src.agent.factory import get_tool_registry

    registry = get_tool_registry()
    assert "get_history_view" in registry
    assert "get_config_view" in registry
    assert registry.get("get_history_view").policy.execution_mode == "none"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_agent_tool_policy.py -q -k kill_switch`
Expected: FAIL，`get_history_view` 不存在

- [ ] **Step 3: 实现**

`src/config.py` 在 `fund_nav_review_auto_restore_enabled` 后加入：

```python
    # 问股全站操作：关闭后只保留原有问答/分析工具，不注册站点读写工具
    agent_chat_site_operations_enabled: bool = True
```

在 `from_env` 对应位置加入：

```python
            agent_chat_site_operations_enabled=(
                os.getenv('AGENT_CHAT_SITE_OPERATIONS_ENABLED', 'true').lower() != 'false'
            ),
```

`.env.example` 在 `FUND_NAV_REVIEW_AUTO_RESTORE_ENABLED` 之后加入：

```bash
# 问股全站操作：低风险操作直执行、高风险操作生成确认卡片；关闭后仅保留原有只读问答与分析工具
#AGENT_CHAT_SITE_OPERATIONS_ENABLED=true
```

`src/agent/factory.py`：

```python
def _site_operations_enabled() -> bool:
    from src.config import get_config

    return bool(getattr(get_config(), "agent_chat_site_operations_enabled", True))


def get_tool_registry():
    """Return a cached ToolRegistry (built once, shared across requests)."""
    global _TOOL_REGISTRY
    if _TOOL_REGISTRY is not None:
        return _TOOL_REGISTRY

    from src.agent.tools.registry import ToolRegistry
    from src.agent.tools.data_tools import ALL_DATA_TOOLS
    from src.agent.tools.analysis_tools import ALL_ANALYSIS_TOOLS
    from src.agent.tools.search_tools import ALL_SEARCH_TOOLS
    from src.agent.tools.market_tools import ALL_MARKET_TOOLS
    from src.agent.tools.backtest_tools import ALL_BACKTEST_TOOLS
    from src.agent.tools.alert_tools import ALL_ALERT_TOOLS
    from src.agent.tools.action_tools import ALL_ACTION_TOOLS

    tool_modules = [
        ALL_DATA_TOOLS,
        ALL_ANALYSIS_TOOLS,
        ALL_SEARCH_TOOLS,
        ALL_MARKET_TOOLS,
        ALL_BACKTEST_TOOLS,
        ALL_ALERT_TOOLS,
        ALL_ACTION_TOOLS,
    ]
    if _site_operations_enabled():
        from src.agent.tools.site_read_tools import ALL_SITE_READ_TOOLS
        from src.agent.tools.site_action_tools import ALL_SITE_ACTION_TOOLS
        from src.agent.tools.site_task_tools import ALL_SITE_TASK_TOOLS

        tool_modules.extend([
            ALL_SITE_READ_TOOLS,
            ALL_SITE_ACTION_TOOLS,
            ALL_SITE_TASK_TOOLS,
        ])

    registry = ToolRegistry()
    for tool_fn in [tool for group in tool_modules for tool in group]:
        registry.register(tool_fn)
    issues = registry.validate_tool_policies(strict=True)
    if issues:
        raise ValueError(f"invalid agent tool policies: {issues}")

    _TOOL_REGISTRY = registry
    logger.info("[AgentFactory] ToolRegistry cached (%d tools)", len(registry))
    return _TOOL_REGISTRY
```

注意：Step 1 的 `test_site_tools_absent_when_kill_switch_off` 依赖 `_site_operations_enabled` monkeypatch；注册表缓存必须在测试前重置。此任务尚未创建三个 site 工具模块，因此先只落地配置、开关函数与 factory 的空分组兼容：

```python
    if _site_operations_enabled():
        try:
            from src.agent.tools.site_read_tools import ALL_SITE_READ_TOOLS
            from src.agent.tools.site_action_tools import ALL_SITE_ACTION_TOOLS
            from src.agent.tools.site_task_tools import ALL_SITE_TASK_TOOLS
        except ImportError:
            tool_modules.extend([])
        else:
            tool_modules.extend([
                ALL_SITE_READ_TOOLS,
                ALL_SITE_ACTION_TOOLS,
                ALL_SITE_TASK_TOOLS,
            ])
```

Task 6 创建 `site_read_tools.py` 后，把 `try/except ImportError` 分支收回为直接 import（删除 `except ImportError`）。这是唯一允许的临时状态，必须在本计划结束时消除。

- [ ] **Step 4: 运行测试**

Run: `uv run python -m pytest tests/test_agent_tool_policy.py -q -k "kill_switch or site_tools"`
Expected: `kill_switch` 用例通过；`site_tools_present_by_default` 在 Task 6 之前仍失败（工具尚未创建），这是预期的。Task 6 完成后再跑一次。

- [ ] **Step 5: Commit（等待用户明确确认）**

```bash
git add src/config.py .env.example src/agent/factory.py tests/test_agent_tool_policy.py
git commit -m "feat(agent): add site operations kill switch"
```

---

## Task 5: 已执行卡片与任务进度卡片

**Files:**
- Modify: `apps/dsa-web/src/pages/ChatPage.tsx:1296-1355, 1390-1480`
- Modify: `apps/dsa-web/src/locales/zh-CN.ts`、`apps/dsa-web/src/locales/en-US.ts`
- Test: `apps/dsa-web/src/pages/__tests__/ChatPage.test.tsx`

**Interfaces:**
- Consumes: `Message.actionExecutions`、`Message.actionTasks`（Task 3）
- Produces: 两类卡片组件；任务卡片轮询 `GET <statusEndpoint>`，终态后停止

- [ ] **Step 1: 写失败测试**

先确认现有测试的 mock 方式：

Run: `grep -n "actionProposal\|action_proposal" apps/dsa-web/src/pages/__tests__/ChatPage.test.tsx | head`

在 `ChatPage.test.tsx` 追加：

```typescript
it('renders executed and task cards from the assistant message', () => {
  renderChatPage({
    messages: [{
      id: 'm1',
      role: 'assistant',
      content: '完成',
      timestamp: new Date().toISOString(),
      actionExecutions: [{
        kind: 'fx_refresh',
        summary: '已刷新汇率',
        result: { updated: 3 },
        undoHint: '再次刷新即可覆盖',
      }],
      actionTasks: [{
        kind: 'stock_analysis',
        summary: '已提交分析',
        taskId: 'task-1',
        statusEndpoint: '/api/v1/analysis/status/task-1',
        pollIntervalMs: 60000,
      }],
    }],
  });

  expect(screen.getByText('已刷新汇率')).toBeInTheDocument();
  expect(screen.getByText('再次刷新即可覆盖')).toBeInTheDocument();
  expect(screen.getByText('已提交分析')).toBeInTheDocument();
});
```

如果 `renderChatPage` 不存在，沿用该文件现有的 store 初始化写法：`useAgentChatStore.setState({ messages: [...] })` 后 `render(<ChatPage />)`，并 mock `agentApi` 与 `useNavigate`。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd apps/dsa-web && npx vitest run src/pages/__tests__/ChatPage.test.tsx -t "renders executed and task cards"`
Expected: FAIL，文案不渲染

- [ ] **Step 3: 实现**

在 `ChatPage.tsx` 的 `renderActionProposalCard` 之后加入：

```tsx
  const renderActionExecutionCard = (msg: Message, execution: ActionExecuted, index: number) => (
    <div key={`${msg.id}#exec-${index}`} className="mb-3 mt-2">
      <InlineAlert
        variant="success"
        title={t('chat.actionExecutedTitle')}
        message={(
          <span className="flex flex-col gap-1">
            <span className="font-medium">{execution.summary}</span>
            <span className="text-xs">{t('chat.actionUndoHint', { hint: execution.undoHint })}</span>
          </span>
        )}
      />
    </div>
  );

  const renderActionTaskCard = (msg: Message, task: ActionTask, index: number) => (
    <div key={`${msg.id}#task-${index}`} className="mb-3 mt-2">
      <InlineAlert
        variant="info"
        title={t('chat.actionTaskTitle')}
        message={(
          <span className="flex flex-col gap-1">
            <span className="font-medium">{task.summary}</span>
            <span className="text-xs">
              {t('chat.actionTaskStatus', { status: actionTaskStatus[`${msg.id}#${index}`] ?? t('chat.actionTaskPending') })}
            </span>
          </span>
        )}
      />
    </div>
  );
```

组件顶部状态：

```tsx
  const [actionTaskStatus, setActionTaskStatus] = useState<Record<string, string>>({});
```

轮询 effect（放在组件内、依赖 `messages`）：

```tsx
  useEffect(() => {
    const pending = messages.flatMap((msg) =>
      (msg.actionTasks ?? []).map((task, index) => ({ key: `${msg.id}#${index}`, task }))
    ).filter(({ key }) => !actionTaskStatus[key] || actionTaskStatus[key] === t('chat.actionTaskPending'));

    if (pending.length === 0) return undefined;

    const timers = pending.map(({ key, task }) => window.setTimeout(async () => {
      try {
        const response = await fetch(task.statusEndpoint, { credentials: 'include' });
        if (!response.ok) return;
        const payload = await response.json() as { status?: string; message?: string; progress?: number };
        const label = payload.message
          ? `${payload.status ?? ''} · ${payload.message}`
          : (payload.status ?? '');
        setActionTaskStatus((current) => ({ ...current, [key]: label }));
        if (payload.status === 'completed' || payload.status === 'failed'
          || payload.status === 'cancelled' || payload.status === 'cancel_requested') {
          return;
        }
      } catch {
        return;
      }
      setActionTaskStatus((current) => ({ ...current }));
    }, task.pollIntervalMs));
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, [messages, actionTaskStatus, t]);
```

注意：上述 effect 依赖 `actionTaskStatus` 会导致每轮重建 timer，但仅在非终态时继续；为避免无限重排，使用单独的 `taskPollTick` 状态：

```tsx
  const [taskPollTick, setTaskPollTick] = useState(0);
```

effect 依赖 `[messages, taskPollTick]`，请求结束后 `setTaskPollTick((tick) => tick + 1)`，并把终态 key 记入 `useRef<Set<string>>` 跳过后续轮询。

渲染处（助手消息内容之后）：

```tsx
                {(msg.actionExecutions ?? []).map((execution, index) => renderActionExecutionCard(msg, execution, index))}
                {(msg.actionTasks ?? []).map((task, index) => renderActionTaskCard(msg, task, index))}
```

locales 增加键（两份都要）：

```typescript
    actionExecutedTitle: '已执行',
    actionUndoHint: '撤销方式：{{hint}}',
    actionTaskTitle: '后台任务',
    actionTaskPending: '等待中',
    actionTaskStatus: '状态：{{status}}',
```

英文：

```typescript
    actionExecutedTitle: 'Executed',
    actionUndoHint: 'To undo: {{hint}}',
    actionTaskTitle: 'Background task',
    actionTaskPending: 'Pending',
    actionTaskStatus: 'Status: {{status}}',
```

- [ ] **Step 4: 运行测试**

Run: `cd apps/dsa-web && npx vitest run src/pages/__tests__/ChatPage.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit（等待用户明确确认）**

```bash
git add apps/dsa-web/src/pages/ChatPage.tsx apps/dsa-web/src/pages/__tests__/ChatPage.test.tsx apps/dsa-web/src/locales/zh-CN.ts apps/dsa-web/src/locales/en-US.ts
git commit -m "feat(web): render executed and background task cards"
```

---

## Task 6: 只读工具一（历史 / 告警 / 任务）

**Files:**
- Create: `src/agent/tools/site_read_tools.py`
- Modify: `src/agent/factory.py`（去掉临时 `try/except ImportError`）
- Test: `tests/test_agent_site_read_tools.py`

**Interfaces:**
- Produces: `ALL_SITE_READ_TOOLS`、`get_history_view`、`get_alert_view`、`get_analysis_task_view`
- Consumes: `HistoryService`、`AlertService`、`AnalysisTaskQueue`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_agent_site_read_tools.py
import json

from src.agent.tools.registry import ToolRegistry
from src.agent.tools.site_read_tools import ALL_SITE_READ_TOOLS


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in ALL_SITE_READ_TOOLS:
        registry.register(tool)
    return registry


def test_read_tools_are_read_only_and_strictly_valid():
    registry = _registry()
    for name in ("get_history_view", "get_alert_view", "get_analysis_task_view"):
        policy = registry.get(name).policy
        assert policy.read_only is True
        assert policy.execution_mode == "none"
        assert policy.risk_level == "read"
    assert registry.validate_tool_policies(strict=True) == []


def test_get_history_view_returns_service_payload(monkeypatch):
    calls = {}

    class FakeHistoryService:
        def get_history_list(self, **kwargs):
            calls.update(kwargs)
            return {"items": [{"id": 1}], "total": 1, "page": 1, "limit": 20}

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._history_service",
        lambda: FakeHistoryService(),
    )
    result = _registry().execute("get_history_view", **{"stock_code": "600519", "limit": 5})
    assert json.loads(result)["total"] == 1
    assert calls["stock_code"] == "600519"
    assert calls["limit"] == 5


def test_get_analysis_task_view_requires_known_view(monkeypatch):
    class FakeQueue:
        def get_task(self, task_id):
            return None

        def list_all_tasks(self, limit=50, status=None):
            return []

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._task_queue", lambda: FakeQueue()
    )
    result = _registry().execute("get_analysis_task_view", **{"view": "bogus"})
    payload = json.loads(result)
    assert payload["error"]


def test_get_alert_view_passes_filters(monkeypatch):
    calls = {}

    class FakeAlertService:
        def list_rules(self, **kwargs):
            calls.update(kwargs)
            return {"items": [], "total": 0, "page": 1, "page_size": 20}

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._alert_service",
        lambda: FakeAlertService(),
    )
    _registry().execute("get_alert_view", **{"view": "rules", "enabled": True, "page_size": 10})
    assert calls["enabled"] is True
    assert calls["page_size"] == 10
```

`ToolRegistry.execute` 返回字符串的现有行为若需确认，先看 `tests/test_agent_*tools*.py` 中已有调用方式，保持一致；若 `execute` 返回 `(result, success)` 之类结构，按现有签名调整断言。

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_agent_site_read_tools.py -q`
Expected: FAIL，模块不存在

- [ ] **Step 3: 实现**

`src/agent/tools/site_read_tools.py`：

```python
# -*- coding: utf-8 -*-
"""Read-only site views for the chat assistant."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from src.agent.tools.registry import ToolDefinition, ToolParameter, ToolPolicy

_READ_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=["db_read"],
    execution_mode="none",
    risk_level="read",
    cancellation_safe=True,
)

_history_service = None
_alert_service = None
_task_queue = None


def _history_service_instance():
    global _history_service
    if _history_service is None:
        from src.services.history_service import HistoryService
        _history_service = HistoryService()
    return _history_service


def _alert_service_instance():
    global _alert_service
    if _alert_service is None:
        from src.services.alert_service import AlertService
        _alert_service = AlertService()
    return _alert_service


def _task_queue_instance():
    global _task_queue
    if _task_queue is None:
        from src.services.task_queue import get_task_queue
        _task_queue = get_task_queue()
    return _task_queue


def _dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _error(message: str) -> str:
    return _dump({"error": message})


def _handle_get_history_view(
    stock_code: Optional[str] = None,
    report_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = 1,
    limit: int = 20,
    record_id: Optional[int] = None,
) -> str:
    from src.services.history_service import HistoryService

    service = _history_service_instance()
    if record_id is not None:
        detail = HistoryService().get_history_detail_by_id(int(record_id))
        return _dump(detail or {"error": f"history record {record_id} not found"})
    return _dump(service.get_history_list(
        stock_code=stock_code,
        report_type=report_type,
        start_date=start_date,
        end_date=end_date,
        page=max(1, int(page)),
        limit=max(1, min(int(limit), 100)),
    ))


def _handle_get_alert_view(
    view: str = "rules",
    rule_id: Optional[int] = None,
    enabled: Optional[bool] = None,
    alert_type: Optional[str] = None,
    target: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> str:
    service = _alert_service_instance()
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 100))
    if view == "rules":
        if rule_id is not None:
            return _dump(service.get_rule(int(rule_id)))
        return _dump(service.list_rules(
            enabled=enabled,
            alert_type=alert_type,
            target=target,
            page=page,
            page_size=page_size,
        ))
    if view == "triggers":
        return _dump(service.list_triggers(
            rule_id=rule_id,
            target=target,
            status=status,
            page=page,
            page_size=page_size,
        ))
    if view == "notifications":
        return _dump(service.list_notifications(
            trigger_id=rule_id,
            page=page,
            page_size=page_size,
        ))
    return _error(f"unknown view: {view}; expected rules|triggers|notifications")


def _handle_get_analysis_task_view(
    view: str = "list",
    task_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 20,
) -> str:
    queue = _task_queue_instance()
    if view == "detail":
        if not task_id:
            return _error("task_id is required for view=detail")
        task = queue.get_task(task_id)
        if task is None:
            return _error(f"task {task_id} not found")
        return _dump({
            "task": task.to_dict() if hasattr(task, "to_dict") else vars(task),
            "flow_events": queue.get_task_flow_events(task_id),
        })
    if view == "list":
        statuses = [item.strip() for item in status.split(",")] if status else None
        tasks = queue.list_all_tasks(limit=max(1, min(int(limit), 100)), status=statuses)
        return _dump({
            "tasks": [task.to_dict() if hasattr(task, "to_dict") else vars(task) for task in tasks],
            "stats": queue.get_task_stats(),
        })
    return _error(f"unknown view: {view}; expected list|detail")


get_history_view = ToolDefinition(
    name="get_history_view",
    description=(
        "读取分析历史：列表（可按股票/报告类型/日期筛选）或单条详情（record_id）。"
        "用于回答「上次分析结论是什么」「最近分析过哪些股票」。"
    ),
    parameters=[
        ToolParameter(name="stock_code", type="string", description="股票代码，可省略", required=False),
        ToolParameter(name="report_type", type="string", description="报告类型，例如 detailed", required=False),
        ToolParameter(name="start_date", type="string", description="起始日期 YYYY-MM-DD", required=False),
        ToolParameter(name="end_date", type="string", description="结束日期 YYYY-MM-DD", required=False),
        ToolParameter(name="page", type="integer", description="页码，默认 1", required=False),
        ToolParameter(name="limit", type="integer", description="每页条数，默认 20，最大 100", required=False),
        ToolParameter(name="record_id", type="integer", description="提供时返回该历史记录详情", required=False),
    ],
    handler=_handle_get_history_view,
    category="site_read",
    policy=_READ_POLICY,
)

get_alert_view = ToolDefinition(
    name="get_alert_view",
    description="读取告警规则、触发记录或通知记录，用于解释「为什么没收到告警」。",
    parameters=[
        ToolParameter(name="view", type="string", description="rules|triggers|notifications", required=False),
        ToolParameter(name="rule_id", type="integer", description="规则 ID；rules 视图返回该规则详情", required=False),
        ToolParameter(name="enabled", type="boolean", description="按启用状态筛选规则", required=False),
        ToolParameter(name="alert_type", type="string", description="告警类型", required=False),
        ToolParameter(name="target", type="string", description="标的代码或关键词", required=False),
        ToolParameter(name="status", type="string", description="触发记录状态", required=False),
        ToolParameter(name="page", type="integer", description="页码，默认 1", required=False),
        ToolParameter(name="page_size", type="integer", description="每页条数，默认 20，最大 100", required=False),
    ],
    handler=_handle_get_alert_view,
    category="site_read",
    policy=_READ_POLICY,
)

get_analysis_task_view = ToolDefinition(
    name="get_analysis_task_view",
    description="读取分析任务队列：任务列表与统计，或单任务详情与运行流程。",
    parameters=[
        ToolParameter(name="view", type="string", description="list|detail", required=False),
        ToolParameter(name="task_id", type="string", description="view=detail 时必填", required=False),
        ToolParameter(name="status", type="string", description="逗号分隔的状态筛选", required=False),
        ToolParameter(name="limit", type="integer", description="最多返回任务数，默认 20", required=False),
    ],
    handler=_handle_get_analysis_task_view,
    category="site_read",
    policy=_READ_POLICY,
)

ALL_SITE_READ_TOOLS = [get_history_view, get_alert_view, get_analysis_task_view]
```

若 `TaskInfo` 没有 `to_dict()`，改用 `dataclasses.asdict(task)`；实现前先 `grep -n "def to_dict\|@dataclass" src/services/task_queue.py` 确认。

- [ ] **Step 4: 运行测试**

Run: `uv run python -m pytest tests/test_agent_site_read_tools.py tests/test_agent_tool_policy.py -q`
Expected: PASS（`site_tools_present_by_default` 现在也应通过）

- [ ] **Step 5: Commit（等待用户明确确认）**

```bash
git add src/agent/tools/site_read_tools.py src/agent/factory.py tests/test_agent_site_read_tools.py
git commit -m "feat(agent): add history, alert and task read tools"
```

---

## Task 7: 只读工具二（回测 / 选股 / 决策信号）

**Files:**
- Modify: `src/agent/tools/site_read_tools.py`
- Test: `tests/test_agent_site_read_tools.py`

**Interfaces:**
- Produces: `get_backtest_view`、`get_screening_view`、`get_signal_view`
- Consumes: `BacktestService`、`ScreeningService(config)`、`DecisionSignalService`、`DecisionSignalOutcomeService`

- [ ] **Step 1: 写失败测试**

```python
def test_get_backtest_view_dispatches_by_scope(monkeypatch):
    calls = []

    class FakeBacktest:
        def get_global_summary(self, **kwargs):
            calls.append(("global", kwargs))
            return {"scope": "overall"}

        def get_stock_summary(self, code, **kwargs):
            calls.append(("stock", code, kwargs))
            return {"scope": "stock", "code": code}

        def get_recent_evaluations(self, **kwargs):
            calls.append(("recent", kwargs))
            return {"items": [], "total": 0}

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._backtest_service",
        lambda: FakeBacktest(),
    )
    registry = _registry()
    registry.execute("get_backtest_view", **{"view": "summary", "scope": "stock", "code": "600519"})
    assert calls[0][0] == "stock"
    assert calls[0][1] == "600519"


def test_get_screening_view_validates_strategy(monkeypatch):
    class FakeScreening:
        def strategies(self):
            return {"strategies": [{"id": "momentum"}]}

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._screening_service",
        lambda: FakeScreening(),
    )
    payload = json.loads(_registry().execute("get_screening_view", **{"view": "strategies"}))
    assert payload["strategies"][0]["id"] == "momentum"


def test_get_signal_view_reads_outcomes_and_stats(monkeypatch):
    class FakeSignals:
        def list_signals(self, **kwargs):
            return {"items": [{"id": 1}], "total": 1}

    class FakeOutcomes:
        def get_stats(self, **kwargs):
            return {"buckets": []}

        def list_outcomes(self, **kwargs):
            return {"items": [], "total": 0}

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._signal_service", lambda: FakeSignals()
    )
    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._outcome_service", lambda: FakeOutcomes()
    )
    registry = _registry()
    payload = json.loads(registry.execute("get_signal_view", **{"view": "signals", "stock_code": "600519"}))
    assert payload["total"] == 1
    payload = json.loads(registry.execute("get_signal_view", **{"view": "stats"}))
    assert payload["buckets"] == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_agent_site_read_tools.py -q -k "backtest or screening or signal_view"`
Expected: FAIL，`KeyError` / 未知工具

- [ ] **Step 3: 实现**

在 `site_read_tools.py` 追加：

```python
_backtest_service = None
_screening_service = None
_signal_service = None
_outcome_service = None

_BACKTEST_EVAL_WINDOW = None


def _backtest_service_instance():
    global _backtest_service
    if _backtest_service is None:
        from src.services.backtest_service import BacktestService
        _backtest_service = BacktestService()
    return _backtest_service


def _screening_service_instance():
    global _screening_service
    if _screening_service is None:
        from src.config import get_config
        from src.services.screening_service import ScreeningService
        _screening_service = ScreeningService(get_config())
    return _screening_service


def _signal_service_instance():
    global _signal_service
    if _signal_service is None:
        from src.services.decision_signal_service import DecisionSignalService
        _signal_service = DecisionSignalService()
    return _signal_service


def _outcome_service_instance():
    global _outcome_service
    if _outcome_service is None:
        from src.services.decision_signal_outcome_service import (
            DecisionSignalOutcomeService,
        )
        _outcome_service = DecisionSignalOutcomeService()
    return _outcome_service


def _handle_get_backtest_view(
    view: str = "summary",
    scope: str = "overall",
    code: Optional[str] = None,
    skill_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
    eval_window_days: Optional[int] = None,
    limit: int = 20,
    analysis_phase: Optional[str] = None,
) -> str:
    service = _backtest_service_instance()
    if view == "recent":
        return _dump(service.get_recent_evaluations(
            code=code,
            eval_window_days=eval_window_days,
            limit=max(1, min(int(limit), 100)),
            analysis_phase=analysis_phase,
        ))
    if view != "summary":
        return _error(f"unknown view: {view}; expected summary|recent")
    if scope == "stock":
        if not code:
            return _error("code is required for scope=stock")
        return _dump(service.get_stock_summary(code, eval_window_days=eval_window_days))
    if scope == "skill":
        if not skill_id:
            return _error("skill_id is required for scope=skill")
        return _dump(service.get_skill_summary(skill_id, eval_window_days=eval_window_days))
    if scope == "strategy":
        if not strategy_id:
            return _error("strategy_id is required for scope=strategy")
        return _dump(service.get_strategy_summary(strategy_id, eval_window_days=eval_window_days))
    if scope == "overall":
        return _dump(service.get_global_summary(eval_window_days=eval_window_days))
    return _error(f"unknown scope: {scope}")


def _handle_get_screening_view(
    view: str = "strategies",
    run_id: Optional[str] = None,
    strategy: str = "",
    market: str = "",
    limit: int = 20,
    topic: Optional[str] = None,
    top: int = 12,
) -> str:
    service = _screening_service_instance()
    if view == "strategies":
        return _dump(service.strategies())
    if view == "status":
        return _dump(service.status())
    if view == "history":
        if run_id:
            return _dump(service.history_detail(run_id))
        return _dump(service.history(limit=max(1, min(int(limit), 100)), strategy=strategy, market=market))
    if view == "hotspots":
        if topic:
            return _dump(service.hotspot_detail(topic=topic))
        return _dump(service.hotspots(top=max(1, min(int(top), 50))))
    return _error(f"unknown view: {view}; expected strategies|status|history|hotspots")


def _handle_get_signal_view(
    view: str = "signals",
    signal_id: Optional[int] = None,
    stock_code: Optional[str] = None,
    status: Optional[str] = None,
    action: Optional[str] = None,
    horizon: Optional[str] = None,
    outcome: Optional[str] = None,
    eval_status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> str:
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 100))
    if view == "signals":
        if signal_id is not None:
            return _dump(_signal_service_instance().get_signal(int(signal_id)))
        return _dump(_signal_service_instance().list_signals(
            stock_code=stock_code,
            status=status,
            action=action,
            page=page,
            page_size=page_size,
        ))
    if view == "latest_active":
        if not stock_code:
            return _error("stock_code is required for view=latest_active")
        return _dump(_signal_service_instance().get_latest_active(stock_code=stock_code))
    if view == "outcomes":
        return _dump(_outcome_service_instance().list_outcomes(
            signal_id=signal_id,
            horizon=horizon,
            eval_status=eval_status,
            outcome=outcome,
            page=page,
            page_size=page_size,
        ))
    if view == "stats":
        return _dump(_outcome_service_instance().get_stats(
            horizons=[horizon] if horizon else None,
        ))
    return _error(f"unknown view: {view}; expected signals|latest_active|outcomes|stats")
```

追加 `ToolDefinition` 与导出：

```python
get_backtest_view = ToolDefinition(
    name="get_backtest_view",
    description=(
        "读取回测/后验统计：整体、单股、skill 或策略摘要，或最近评估明细。"
        "用于回答「最近判断准不准」「某只股票历史回测如何」。"
    ),
    parameters=[
        ToolParameter(name="view", type="string", description="summary|recent", required=False),
        ToolParameter(name="scope", type="string", description="overall|stock|skill|strategy", required=False),
        ToolParameter(name="code", type="string", description="scope=stock 时必填", required=False),
        ToolParameter(name="skill_id", type="string", description="scope=skill 时必填", required=False),
        ToolParameter(name="strategy_id", type="string", description="scope=strategy 时必填", required=False),
        ToolParameter(name="eval_window_days", type="integer", description="评估窗口天数", required=False),
        ToolParameter(name="analysis_phase", type="string", description="recent 视图的阶段筛选", required=False),
        ToolParameter(name="limit", type="integer", description="recent 返回条数，最大 100", required=False),
    ],
    handler=_handle_get_backtest_view,
    category="site_read",
    policy=_READ_POLICY,
)

get_screening_view = ToolDefinition(
    name="get_screening_view",
    description="读取选股策略列表、运行状态、历史结果或热点快照。",
    parameters=[
        ToolParameter(name="view", type="string", description="strategies|status|history|hotspots", required=False),
        ToolParameter(name="run_id", type="string", description="history 视图返回单次详情", required=False),
        ToolParameter(name="strategy", type="string", description="history 筛选策略", required=False),
        ToolParameter(name="market", type="string", description="history 筛选市场", required=False),
        ToolParameter(name="limit", type="integer", description="history 返回条数，最大 100", required=False),
        ToolParameter(name="topic", type="string", description="提供时返回该热点详情", required=False),
        ToolParameter(name="top", type="integer", description="热点条数，最大 50", required=False),
    ],
    handler=_handle_get_screening_view,
    category="site_read",
    policy=_READ_POLICY,
)

get_signal_view = ToolDefinition(
    name="get_signal_view",
    description="读取决策信号、某标的当前有效信号、信号后验结果或统计。",
    parameters=[
        ToolParameter(name="view", type="string", description="signals|latest_active|outcomes|stats", required=False),
        ToolParameter(name="signal_id", type="integer", description="信号 ID", required=False),
        ToolParameter(name="stock_code", type="string", description="股票代码", required=False),
        ToolParameter(name="status", type="string", description="信号状态筛选", required=False),
        ToolParameter(name="action", type="string", description="信号动作筛选", required=False),
        ToolParameter(name="horizon", type="string", description="后验周期 1d/3d/5d/10d 等", required=False),
        ToolParameter(name="outcome", type="string", description="后验结果筛选", required=False),
        ToolParameter(name="eval_status", type="string", description="后验评估状态筛选", required=False),
        ToolParameter(name="page", type="integer", description="页码，默认 1", required=False),
        ToolParameter(name="page_size", type="integer", description="每页条数，最大 100", required=False),
    ],
    handler=_handle_get_signal_view,
    category="site_read",
    policy=_READ_POLICY,
)

ALL_SITE_READ_TOOLS = [
    get_history_view,
    get_alert_view,
    get_analysis_task_view,
    get_backtest_view,
    get_screening_view,
    get_signal_view,
]
```

- [ ] **Step 4: 运行测试**

Run: `uv run python -m pytest tests/test_agent_site_read_tools.py -q`
Expected: PASS

- [ ] **Step 5: Commit（等待用户明确确认）**

```bash
git add src/agent/tools/site_read_tools.py tests/test_agent_site_read_tools.py
git commit -m "feat(agent): add backtest, screening and signal read tools"
```

---

## Task 8: 只读工具三（基金复盘 / 模拟盘 / 组合 / 情报 / 配置）

**Files:**
- Modify: `src/agent/tools/site_read_tools.py`
- Modify: `src/services/runtime_scheduler.py`
- Modify: `api/app.py:277-289`
- Test: `tests/test_agent_site_read_tools.py`
- Test: `tests/test_runtime_scheduler_singleton.py`

**Interfaces:**
- Produces: `get_fund_review_view`、`get_paper_view`、`get_portfolio_ledger_view`、`get_intelligence_view`、`get_config_view`；`get_runtime_scheduler_service()` / `set_runtime_scheduler_service()`
- Consumes: 各 domain service、`SystemConfigService`

- [ ] **Step 1: 写失败测试**

```python
def test_get_config_view_never_returns_sensitive_fields(monkeypatch):
    class FakeConfigService:
        def get_schema(self):
            return {"categories": [{
                "category": "base",
                "title": "Base",
                "fields": [
                    {"key": "STOCK_LIST", "title": "Stocks", "is_sensitive": False},
                    {"key": "DEEPSEEK_API_KEY", "title": "Key", "is_sensitive": True},
                ],
            }]}

        def get_config(self, include_schema=True, mask_token="******"):
            return {"items": [
                {"key": "STOCK_LIST", "value": "600519"},
                {"key": "DEEPSEEK_API_KEY", "value": "******"},
            ]}

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._config_service",
        lambda: FakeConfigService(),
    )
    payload = json.loads(_registry().execute("get_config_view", **{}))
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "600519" in serialized
    assert "DEEPSEEK_API_KEY" not in serialized
    assert "******" not in serialized


def test_get_portfolio_ledger_view_uses_account_filter(monkeypatch):
    calls = {}

    class FakePortfolio:
        def list_accounts(self, include_inactive=False):
            return [{"id": 7, "name": "Main"}]

        def list_trade_events(self, **kwargs):
            calls.update(kwargs)
            return {"items": [], "total": 0}

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._portfolio_service",
        lambda: FakePortfolio(),
    )
    _registry().execute("get_portfolio_ledger_view", **{"view": "trades", "account_id": 7})
    assert calls["account_id"] == 7
```

```python
# tests/test_runtime_scheduler_singleton.py
from src.services import runtime_scheduler as module


def test_scheduler_accessor_round_trip():
    previous = module.get_runtime_scheduler_service()
    sentinel = object()
    try:
        module.set_runtime_scheduler_service(sentinel)
        assert module.get_runtime_scheduler_service() is sentinel
    finally:
        module.set_runtime_scheduler_service(previous)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_agent_site_read_tools.py tests/test_runtime_scheduler_singleton.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`src/services/runtime_scheduler.py` 在模块级常量后加入：

```python
_runtime_scheduler_service: Optional["RuntimeSchedulerService"] = None


def set_runtime_scheduler_service(service: Optional["RuntimeSchedulerService"]) -> None:
    """Publish the process-local scheduler instance for read-only consumers."""
    global _runtime_scheduler_service
    _runtime_scheduler_service = service


def get_runtime_scheduler_service() -> Optional["RuntimeSchedulerService"]:
    """Return the running scheduler instance, or None outside the API process."""
    return _runtime_scheduler_service
```

`api/app.py`：

```python
    from src.services.runtime_scheduler import set_runtime_scheduler_service

    app.state.runtime_scheduler_service = runtime_scheduler_service
    set_runtime_scheduler_service(runtime_scheduler_service)
```

shutdown 处：

```python
    if runtime_scheduler is not None:
        runtime_scheduler.stop()
        delattr(app.state, "runtime_scheduler_service")
        set_runtime_scheduler_service(None)
```

`site_read_tools.py` 追加：

```python
_fund_review_service = None
_paper_service = None
_portfolio_service = None
_intelligence_service = None
_config_service = None


def _fund_review_service_instance():
    global _fund_review_service
    if _fund_review_service is None:
        from src.services.fund_nav_review_service import FundNavReviewService
        _fund_review_service = FundNavReviewService()
    return _fund_review_service


def _paper_service_instance():
    global _paper_service
    if _paper_service is None:
        from src.services.paper_service import PaperService
        _paper_service = PaperService()
    return _paper_service


def _portfolio_service_instance():
    global _portfolio_service
    if _portfolio_service is None:
        from src.services.portfolio_service import PortfolioService
        _portfolio_service = PortfolioService()
    return _portfolio_service


def _intelligence_service_instance():
    global _intelligence_service
    if _intelligence_service is None:
        from src.services.intelligence_service import IntelligenceService
        _intelligence_service = IntelligenceService()
    return _intelligence_service


def _config_service_instance():
    global _config_service
    if _config_service is None:
        from src.services.system_config_service import SystemConfigService
        _config_service = SystemConfigService()
    return _config_service


def _paper_account_id() -> int:
    account = _paper_service_instance().get_or_create_account()
    return int(account["id"])


def _handle_get_fund_review_view(
    view: str = "reviews",
    fund_code: Optional[str] = None,
    horizon: Optional[str] = None,
    eval_status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> str:
    service = _fund_review_service_instance()
    if view == "reviews":
        return _dump(service.list_reviews(
            fund_code=fund_code,
            horizon=horizon,
            eval_status=eval_status,
            page=max(1, int(page)),
            page_size=max(1, min(int(page_size), 100)),
        ))
    if view == "stats":
        return _dump(service.get_stats(horizons=[horizon] if horizon else None))
    return _error(f"unknown view: {view}; expected reviews|stats")


def _handle_get_paper_view(
    view: str = "snapshot",
    account_id: Optional[int] = None,
    limit: int = 50,
) -> str:
    service = _paper_service_instance()
    account = int(account_id) if account_id is not None else _paper_account_id()
    limit = max(1, min(int(limit), 200))
    if view == "snapshot":
        return _dump(service.get_snapshot(account))
    if view == "positions":
        return _dump(service.get_positions(account, limit=limit))
    if view == "trades":
        return _dump(service.get_trades(account, limit=limit))
    if view == "signals":
        return _dump(service.get_signals(account, limit=limit))
    if view == "equity":
        return _dump(service.get_equity_curve(account))
    return _error(f"unknown view: {view}; expected snapshot|positions|trades|signals|equity")


def _handle_get_portfolio_ledger_view(
    view: str = "accounts",
    account_id: Optional[int] = None,
    include_inactive: bool = False,
    symbol: Optional[str] = None,
    side: Optional[str] = None,
    direction: Optional[str] = None,
    action_type: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> str:
    service = _portfolio_service_instance()
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 100))
    if view == "accounts":
        return _dump({"accounts": service.list_accounts(include_inactive=include_inactive)})
    if view == "snapshot":
        return _dump(service.get_portfolio_snapshot(account_id=account_id))
    if view == "trades":
        return _dump(service.list_trade_events(
            account_id=account_id, symbol=symbol, side=side, page=page, page_size=page_size,
        ))
    if view == "cash":
        return _dump(service.list_cash_ledger_events(
            account_id=account_id, direction=direction, page=page, page_size=page_size,
        ))
    if view == "corporate":
        return _dump(service.list_corporate_action_events(
            account_id=account_id, symbol=symbol, action_type=action_type,
            page=page, page_size=page_size,
        ))
    return _error(f"unknown view: {view}; expected accounts|snapshot|trades|cash|corporate")


def _handle_get_intelligence_view(
    view: str = "sources",
    source_id: Optional[int] = None,
    page: int = 1,
    page_size: int = 20,
) -> str:
    service = _intelligence_service_instance()
    if view == "sources":
        return _dump(service.list_sources())
    if view == "templates":
        return _dump(service.list_source_templates())
    if view == "items":
        return _dump(service.list_items(
            source_id=source_id,
            page=max(1, int(page)),
            page_size=max(1, min(int(page_size), 100)),
        ))
    return _error(f"unknown view: {view}; expected sources|templates|items")


def _handle_get_config_view(key: Optional[str] = None) -> str:
    service = _config_service_instance()
    schema = service.get_schema()
    config = service.get_config(include_schema=False)
    values = {item["key"]: item["value"] for item in config.get("items", [])}
    fields = []
    for category in schema.get("categories", []):
        for field in category.get("fields", []):
            if field.get("is_sensitive"):
                continue
            field_key = field.get("key")
            if key and field_key != key:
                continue
            fields.append({
                "key": field_key,
                "title": field.get("title"),
                "category": category.get("category"),
                "data_type": field.get("data_type"),
                "is_editable": field.get("is_editable"),
                "value": values.get(field_key),
            })
    if key and not fields:
        return _error(f"unknown or sensitive config key: {key}")
    return _dump({"config_version": config.get("config_version"), "fields": fields})


def _handle_get_scheduler_view() -> str:
    from src.services.runtime_scheduler import get_runtime_scheduler_service

    scheduler = get_runtime_scheduler_service()
    if scheduler is None:
        return _error("scheduler is not running in this process")
    return _dump(scheduler.status())
```

`ToolDefinition` 与导出（追加到列表）：

```python
get_fund_review_view = ToolDefinition(
    name="get_fund_review_view",
    description="读取场外基金净值复盘台账或统计（区间收益、最大回撤）。",
    parameters=[
        ToolParameter(name="view", type="string", description="reviews|stats", required=False),
        ToolParameter(name="fund_code", type="string", description="基金代码，可带 fund: 前缀", required=False),
        ToolParameter(name="horizon", type="string", description="1m/3m/6m/1y", required=False),
        ToolParameter(name="eval_status", type="string", description="pending/evaluated/unable", required=False),
        ToolParameter(name="page", type="integer", description="页码", required=False),
        ToolParameter(name="page_size", type="integer", description="每页条数，最大 100", required=False),
    ],
    handler=_handle_get_fund_review_view,
    category="site_read",
    policy=_READ_POLICY,
)

get_paper_view = ToolDefinition(
    name="get_paper_view",
    description="读取模拟盘账户快照、持仓、成交、信号或净值曲线。",
    parameters=[
        ToolParameter(name="view", type="string", description="snapshot|positions|trades|signals|equity", required=False),
        ToolParameter(name="account_id", type="integer", description="默认模拟盘账户", required=False),
        ToolParameter(name="limit", type="integer", description="明细条数，最大 200", required=False),
    ],
    handler=_handle_get_paper_view,
    category="site_read",
    policy=_READ_POLICY,
)

get_portfolio_ledger_view = ToolDefinition(
    name="get_portfolio_ledger_view",
    description="读取真实持仓账户、组合快照、交易流水、资金流水或公司行动流水。",
    parameters=[
        ToolParameter(name="view", type="string", description="accounts|snapshot|trades|cash|corporate", required=False),
        ToolParameter(name="account_id", type="integer", description="账户 ID", required=False),
        ToolParameter(name="include_inactive", type="boolean", description="accounts 视图含停用账户", required=False),
        ToolParameter(name="symbol", type="string", description="标的筛选", required=False),
        ToolParameter(name="side", type="string", description="买卖方向", required=False),
        ToolParameter(name="direction", type="string", description="资金方向", required=False),
        ToolParameter(name="action_type", type="string", description="公司行动类型", required=False),
        ToolParameter(name="page", type="integer", description="页码", required=False),
        ToolParameter(name="page_size", type="integer", description="每页条数，最大 100", required=False),
    ],
    handler=_handle_get_portfolio_ledger_view,
    category="site_read",
    policy=_READ_POLICY,
)

get_intelligence_view = ToolDefinition(
    name="get_intelligence_view",
    description="读取情报源配置、模板或已抓取情报条目。",
    parameters=[
        ToolParameter(name="view", type="string", description="sources|templates|items", required=False),
        ToolParameter(name="source_id", type="integer", description="items 视图按来源筛选", required=False),
        ToolParameter(name="page", type="integer", description="页码", required=False),
        ToolParameter(name="page_size", type="integer", description="每页条数，最大 100", required=False),
    ],
    handler=_handle_get_intelligence_view,
    category="site_read",
    policy=_READ_POLICY,
)

get_config_view = ToolDefinition(
    name="get_config_view",
    description=(
        "读取非敏感系统配置项（密钥、Token、密码类字段永远不会返回）。"
        "用于回答「当前自选股是什么」「调度是否开启」。"
    ),
    parameters=[
        ToolParameter(name="key", type="string", description="只返回指定配置键", required=False),
    ],
    handler=_handle_get_config_view,
    category="site_read",
    policy=_READ_POLICY,
)

get_scheduler_view = ToolDefinition(
    name="get_scheduler_view",
    description="读取当前进程内调度器状态与下次运行时间。",
    parameters=[],
    handler=_handle_get_scheduler_view,
    category="site_read",
    policy=_READ_POLICY,
)
```

导出列表：

```python
ALL_SITE_READ_TOOLS = [
    get_history_view,
    get_alert_view,
    get_analysis_task_view,
    get_backtest_view,
    get_screening_view,
    get_signal_view,
    get_fund_review_view,
    get_paper_view,
    get_portfolio_ledger_view,
    get_intelligence_view,
    get_config_view,
    get_scheduler_view,
]
```

- [ ] **Step 4: 运行测试**

Run: `uv run python -m pytest tests/test_agent_site_read_tools.py tests/test_runtime_scheduler_singleton.py -q`
Expected: PASS

- [ ] **Step 5: Commit（等待用户明确确认）**

```bash
git add src/agent/tools/site_read_tools.py src/services/runtime_scheduler.py api/app.py tests/test_agent_site_read_tools.py tests/test_runtime_scheduler_singleton.py
git commit -m "feat(agent): add fund, paper, portfolio, intel and config read tools"
```

---

## Task 9: 低风险直执行工具（7 个）

**Files:**
- Create: `src/agent/tools/site_action_tools.py`
- Test: `tests/test_agent_site_action_tools.py`

**Interfaces:**
- Produces: `ALL_SITE_ACTION_TOOLS`；每个 handler 返回 `{kind, summary, result, undo_hint}`
- Consumes: `watchlist_service`、`AlertService`、`PortfolioService`、`PaperService`、`IntelligenceService`、`SystemConfigService`、`get_config().llm_channels`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_agent_site_action_tools.py
import json

from src.agent.tools.registry import ToolRegistry
from src.agent.tools.site_action_tools import ALL_SITE_ACTION_TOOLS


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in ALL_SITE_ACTION_TOOLS:
        registry.register(tool)
    return registry


def test_direct_tools_declare_direct_mode_with_undo_hint():
    registry = _registry()
    assert len(ALL_SITE_ACTION_TOOLS) == 7
    for tool in ALL_SITE_ACTION_TOOLS:
        policy = tool.policy
        assert policy.execution_mode == "direct"
        assert policy.read_only is False
        assert policy.cancellation_safe is False
        assert policy.undo_hint
        tool.validate_action_policy()
    assert registry.validate_tool_policies(strict=True) == []


def test_manage_watchlist_add_executes_against_service(monkeypatch):
    written = {}

    class FakeConfig:
        def get_config(self, include_schema=False):
            return {"items": [{"key": "STOCK_LIST", "value": "600519"}]}

        def update(self, **kwargs):
            written.update(kwargs)

    monkeypatch.setattr(
        "src.agent.tools.site_action_tools._system_config_service",
        lambda: FakeConfig(),
    )
    result = json.loads(_registry().execute(
        "manage_watchlist", **{"action": "add", "symbol": "000001"}
    ))
    assert result["kind"] == "watchlist_change"
    assert written["items"][0]["value"] == "600519,000001"
    assert result["result"]["stock_codes"] == ["600519", "000001"]


def test_manage_watchlist_remove_is_idempotent(monkeypatch):
    written = {}

    class FakeConfig:
        def get_config(self, include_schema=False):
            return {"items": [{"key": "STOCK_LIST", "value": "600519"}]}

        def update(self, **kwargs):
            written.update(kwargs)

    monkeypatch.setattr(
        "src.agent.tools.site_action_tools._system_config_service",
        lambda: FakeConfig(),
    )
    result = json.loads(_registry().execute(
        "manage_watchlist", **{"action": "remove", "symbol": "000001"}
    ))
    assert result["result"]["unchanged"] is True
    assert written == {}


def test_set_alert_rule_enabled_calls_service(monkeypatch):
    calls = []

    class FakeAlert:
        def enable_rule(self, rule_id, enabled):
            calls.append((rule_id, enabled))
            return {"id": rule_id, "enabled": enabled}

    monkeypatch.setattr(
        "src.agent.tools.site_action_tools._alert_service", lambda: FakeAlert()
    )
    result = json.loads(_registry().execute(
        "set_alert_rule_enabled", **{"rule_id": 3, "enabled": False}
    ))
    assert result["kind"] == "alert_rule_toggle"
    assert calls == [(3, False)]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_agent_site_action_tools.py -q`
Expected: FAIL，模块不存在

- [ ] **Step 3: 实现**

`src/agent/tools/site_action_tools.py`：

```python
# -*- coding: utf-8 -*-
"""Low-risk, reversible site operations that execute immediately.

Every handler returns the ``action_executed`` envelope; the runner emits it to the
chat UI. These tools must be idempotent or cheaply reversible, and must declare an
``undo_hint`` so the user can always see how to reverse the effect.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from src.agent.tools.registry import ToolDefinition, ToolParameter, ToolPolicy
from src.services.watchlist_service import (
    list_named_watchlists,
    read_watchlist_codes,
    resolve_watchlist_key,
    validate_and_normalize_stock_code,
    write_watchlist_codes,
)

_DIRECT_POLICY = ToolPolicy.declared(
    read_only=False,
    side_effects=["write"],
    execution_mode="direct",
    risk_level="low",
    undo_hint="再次调用本工具并把参数改为相反值即可恢复",
    cancellation_safe=False,
)

_alert_service = None
_paper_service = None
_portfolio_service = None
_intelligence_service = None
_system_config_service = None


def _alert_service_instance():
    global _alert_service
    if _alert_service is None:
        from src.services.alert_service import AlertService
        _alert_service = AlertService()
    return _alert_service


def _paper_service_instance():
    global _paper_service
    if _paper_service is None:
        from src.services.paper_service import PaperService
        _paper_service = PaperService()
    return _paper_service


def _portfolio_service_instance():
    global _portfolio_service
    if _portfolio_service is None:
        from src.services.portfolio_service import PortfolioService
        _portfolio_service = PortfolioService()
    return _portfolio_service


def _intelligence_service_instance():
    global _intelligence_service
    if _intelligence_service is None:
        from src.services.intelligence_service import IntelligenceService
        _intelligence_service = IntelligenceService()
    return _intelligence_service


def _system_config_service_instance():
    global _system_config_service
    if _system_config_service is None:
        from src.services.system_config_service import SystemConfigService
        _system_config_service = SystemConfigService()
    return _system_config_service


def _watchlist_match_key(code: str) -> str:
    from data_provider.base import normalize_stock_code

    normalized = normalize_stock_code(code.strip())
    if re.fullmatch(r"\d{5}", normalized):
        return f"HK{normalized}"
    return normalized.upper()


def _resolve_named_list(list_name: str) -> Optional[str]:
    raw = str(list_name or "").strip()
    if not raw:
        return None
    service = _system_config_service_instance()
    named = list_named_watchlists(service)
    wanted = resolve_watchlist_key(raw)
    if any(item["key"] == wanted for item in named):
        return raw
    available = [
        item["name"] for item in named
        if resolve_watchlist_key(item["name"]) == item["key"]
    ]
    raise ValueError(
        f"自选列表「{raw}」不存在；可用列表：{'、'.join(available) or '无'}"
    )


def _handle_manage_watchlist(
    action: str,
    symbol: str,
    list_name: str = "",
) -> Dict[str, Any]:
    norm_action = str(action or "").strip().lower()
    if norm_action not in ("add", "remove"):
        return {"error": f"action 必须是 add/remove，收到 {action!r}"}
    try:
        norm_code = validate_and_normalize_stock_code(str(symbol or ""))
        resolved_list = _resolve_named_list(list_name)
    except ValueError as exc:
        return {"error": str(exc)}

    service = _system_config_service_instance()
    codes: List[str] = read_watchlist_codes(service, resolved_list)
    keys = [_watchlist_match_key(code) for code in codes]
    target_key = _watchlist_match_key(norm_code)
    changed = False

    if norm_action == "add":
        if target_key not in keys:
            codes.append(str(symbol).strip())
            changed = True
    else:
        if target_key in keys:
            codes.pop(keys.index(target_key))
            changed = True

    if changed:
        write_watchlist_codes(service, codes, resolved_list)

    target = f"自选列表「{resolved_list}」" if resolved_list else "自选"
    verb = "加入" if norm_action == "add" else "移出"
    state = "" if changed else "（原本已是该状态，未做改动）"
    return {
        "kind": "watchlist_change",
        "summary": f"已把「{norm_code}」{verb}{target}{state}",
        "result": {
            "action": norm_action,
            "stock_code": norm_code,
            "list_name": resolved_list,
            "stock_codes": codes,
            "unchanged": not changed,
        },
        "undo_hint": (
            "再次调用 manage_watchlist，传入相反的 action 即可恢复"
            if changed else "本次未改动任何数据，无需撤销"
        ),
    }


def _handle_set_alert_rule_enabled(rule_id: int, enabled: bool) -> Dict[str, Any]:
    rule = _alert_service_instance().enable_rule(int(rule_id), bool(enabled))
    state = "启用" if enabled else "停用"
    return {
        "kind": "alert_rule_toggle",
        "summary": f"已{state}告警规则 #{int(rule_id)}",
        "result": {"rule": rule},
        "undo_hint": f"再次调用并传 enabled={str(not enabled).lower()} 即可恢复",
    }


def _handle_dry_run_alert_rule(rule_id: int) -> Dict[str, Any]:
    result = _alert_service_instance().test_rule(int(rule_id))
    return {
        "kind": "alert_rule_test",
        "summary": f"已对告警规则 #{int(rule_id)} 执行测试通知",
        "result": result,
        "undo_hint": "测试只发送一条通知，不修改规则；如需撤回通知请在外部渠道删除",
    }


def _handle_refresh_fx_rates(
    account_id: Optional[int] = None,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    from datetime import date

    parsed_as_of = date.fromisoformat(as_of) if as_of else None
    result = _portfolio_service_instance().refresh_fx_rates(
        account_id=int(account_id) if account_id is not None else None,
        as_of=parsed_as_of,
    )
    return {
        "kind": "fx_refresh",
        "summary": "已刷新组合汇率",
        "result": result,
        "undo_hint": "汇率缓存会被下次刷新覆盖，无需手动撤销",
    }


def _handle_refresh_paper_valuation(
    account_id: Optional[int] = None,
    as_of: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    from datetime import date

    service = _paper_service_instance()
    account = int(account_id) if account_id is not None else int(
        service.get_or_create_account()["id"]
    )
    parsed_as_of = date.fromisoformat(as_of) if as_of else None
    result = service.run_daily_valuation(
        account_id=account, as_of_date=parsed_as_of, force=bool(force)
    )
    return {
        "kind": "paper_refresh",
        "summary": "已刷新模拟盘估值",
        "result": result,
        "undo_hint": "同一日的估值快照会被下次刷新覆盖，无需手动撤销",
    }


def _handle_test_intelligence_source(source_id: int) -> Dict[str, Any]:
    result = _intelligence_service_instance().fetch_source(int(source_id), dry_run=True)
    return {
        "kind": "intel_source_test",
        "summary": f"已测试情报源 #{int(source_id)}",
        "result": result,
        "undo_hint": "试抓取不写入情报库，无需撤销",
    }


def _handle_test_llm_channel(name: str) -> Dict[str, Any]:
    import os

    from src.config import get_config

    channel_name = str(name or "").strip()
    channels = getattr(get_config(), "llm_channels", []) or []
    channel = next(
        (item for item in channels if str(item.get("name", "")).strip() == channel_name),
        None,
    )
    if channel is None:
        available = "、".join(str(item.get("name")) for item in channels) or "无"
        return {"error": f"LLM 渠道「{channel_name}」不存在；可用渠道：{available}"}

    keys = channel.get("api_keys") or []
    if not keys:
        return {"error": f"LLM 渠道「{channel_name}」没有可用的 API Key"}
    if os.getenv("DSA_AGENT_ALLOW_SECRET_TEST", "true").lower() == "false":
        return {"error": "当前环境禁止由助手直接测试 LLM 渠道"}

    result = _system_config_service_instance().test_llm_channel(
        name=channel_name,
        protocol=str(channel.get("protocol") or ""),
        api_surface=str(channel.get("api_surface") or "chat_completions"),
        base_url=str(channel.get("base_url") or ""),
        api_key=str(keys[0]),
        models=list(channel.get("models") or []),
        enabled=bool(channel.get("enabled", True)),
    )
    return {
        "kind": "llm_channel_test",
        "summary": f"已测试 LLM 渠道「{channel_name}」",
        "result": {key: value for key, value in result.items() if "key" not in key.lower()},
        "undo_hint": "测试不修改配置，无需撤销",
    }
```

工具定义：

```python
manage_watchlist = ToolDefinition(
    name="manage_watchlist",
    description=(
        "把股票加入或移出自选（默认列表或已存在的命名列表）。这是低风险直执行操作，"
        "会立即写入配置并返回执行结果。列表名不存在时必须报错，不得臆造新列表。"
    ),
    parameters=[
        ToolParameter(name="action", type="string", description="add|remove", required=True),
        ToolParameter(name="symbol", type="string", description="股票代码", required=True),
        ToolParameter(name="list_name", type="string", description="命名自选列表，省略为默认自选", required=False),
    ],
    handler=_handle_manage_watchlist,
    category="site_action",
    policy=_DIRECT_POLICY,
)

set_alert_rule_enabled = ToolDefinition(
    name="set_alert_rule_enabled",
    description="启用或停用一条已存在的告警规则。",
    parameters=[
        ToolParameter(name="rule_id", type="integer", description="告警规则 ID", required=True),
        ToolParameter(name="enabled", type="boolean", description="true 启用 / false 停用", required=True),
    ],
    handler=_handle_set_alert_rule_enabled,
    category="site_action",
    policy=_DIRECT_POLICY,
)

dry_run_alert_rule = ToolDefinition(
    name="dry_run_alert_rule",
    description="对一条告警规则发送测试通知，验证通知链路。",
    parameters=[
        ToolParameter(name="rule_id", type="integer", description="告警规则 ID", required=True),
    ],
    handler=_handle_dry_run_alert_rule,
    category="site_action",
    policy=_DIRECT_POLICY,
)

refresh_fx_rates = ToolDefinition(
    name="refresh_fx_rates",
    description="刷新组合汇率缓存。",
    parameters=[
        ToolParameter(name="account_id", type="integer", description="账户 ID，省略为全部账户", required=False),
        ToolParameter(name="as_of", type="string", description="汇率日期 YYYY-MM-DD", required=False),
    ],
    handler=_handle_refresh_fx_rates,
    category="site_action",
    policy=_DIRECT_POLICY,
)

refresh_paper_valuation = ToolDefinition(
    name="refresh_paper_valuation",
    description="重新计算模拟盘当日估值。",
    parameters=[
        ToolParameter(name="account_id", type="integer", description="模拟盘账户 ID", required=False),
        ToolParameter(name="as_of", type="string", description="估值日期 YYYY-MM-DD", required=False),
        ToolParameter(name="force", type="boolean", description="覆盖已有同日快照", required=False),
    ],
    handler=_handle_refresh_paper_valuation,
    category="site_action",
    policy=_DIRECT_POLICY,
)

test_intelligence_source = ToolDefinition(
    name="test_intelligence_source",
    description="对已保存的情报源做一次试抓取，不写入情报库。",
    parameters=[
        ToolParameter(name="source_id", type="integer", description="情报源 ID", required=True),
    ],
    handler=_handle_test_intelligence_source,
    category="site_action",
    policy=_DIRECT_POLICY,
)

test_llm_channel = ToolDefinition(
    name="test_llm_channel",
    description="测试一个已保存的 LLM 渠道是否可用；密钥由服务端读取，绝不作为参数传递。",
    parameters=[
        ToolParameter(name="name", type="string", description="已保存渠道名", required=True),
    ],
    handler=_handle_test_llm_channel,
    category="site_action",
    policy=_DIRECT_POLICY,
)

ALL_SITE_ACTION_TOOLS = [
    manage_watchlist,
    set_alert_rule_enabled,
    dry_run_alert_rule,
    refresh_fx_rates,
    refresh_paper_valuation,
    test_intelligence_source,
    test_llm_channel,
]
```

注意：`_DIRECT_POLICY` 的 `undo_hint` 是通用兜底，每个 handler 的返回里再给具体 hint；验证失败提示属于错误，不进入事件。

- [ ] **Step 4: 运行测试**

Run: `uv run python -m pytest tests/test_agent_site_action_tools.py -q`
Expected: PASS

- [ ] **Step 5: 移除旧的 `propose_watchlist_change`**

`src/agent/tools/action_tools.py` 删除 `propose_watchlist_change`、`_ACTION_KINDS`、`_KIND_VERBS`、`_resolve_named_list`、`_handle_propose_watchlist_change`，`ALL_ACTION_TOOLS` 只保留 `propose_portfolio_trade`。同步删除 `tests/` 中针对该工具的用例，或改为 `manage_watchlist` 语义；`api/v1/endpoints/agent.py::TOOL_DISPLAY_NAMES` 与 runner 标签同步删除 `propose_watchlist_change`（标签任务在 Task 15 统一收口，此处先保证测试通过）。

Run: `uv run python -m pytest tests/test_agent_action_tools.py tests/test_agent_tool_label_parity.py -q`
Expected: PASS（若标签测试失败，按报错补齐/删除名称）

- [ ] **Step 6: Commit（等待用户明确确认）**

```bash
git add src/agent/tools/site_action_tools.py src/agent/tools/action_tools.py tests/test_agent_site_action_tools.py tests/test_agent_action_tools.py
git commit -m "feat(agent): add low-risk direct site actions"
```

---

## Task 10: 长任务提交服务（下沉端点逻辑）

**Files:**
- Create: `src/services/site_task_submitter.py`
- Modify: `api/v1/endpoints/analysis.py:134-176, 540-581`
- Modify: `api/v1/endpoints/screening.py:133-195`
- Test: `tests/test_site_task_submitter.py`

**Interfaces:**
- Produces:
  - `submit_stock_analysis(stock_code, *, report_type="detailed", force_refresh=False) -> TaskInfo`
  - `submit_market_review(*, send_notification=False, region=None) -> TaskInfo`
  - `run_market_review_background(...) -> dict`
  - `submit_screening(strategy, market, max_results, variant_seed="") -> TaskInfo`
  - `submit_backtest(code=None, *, force=False, limit=200) -> TaskInfo`
  - `submit_intelligence_fetch() -> TaskInfo`
  - `submit_fund_nav_backfill(...) -> TaskInfo`
  - `submit_fund_nav_reviews(...) -> TaskInfo`
  - `submit_signal_outcomes(...) -> TaskInfo`
  - `submit_paper_backfill(account_id, from_date, to_date=None) -> TaskInfo`
- Consumes: `get_task_queue()`、各 domain service

- [ ] **Step 1: 写失败测试**

```python
# tests/test_site_task_submitter.py
from unittest.mock import MagicMock

from src.services import site_task_submitter as submitter


class FakeTask:
    def __init__(self, task_id="t1"):
        self.task_id = task_id


def test_submit_stock_analysis_uses_queue(monkeypatch):
    queue = MagicMock()
    queue.submit_task.return_value = FakeTask()
    monkeypatch.setattr(submitter, "_queue", lambda: queue)
    task = submitter.submit_stock_analysis("600519", force_refresh=True)
    assert task.task_id == "t1"
    assert queue.submit_task.call_args.args[0] == "600519"
    assert queue.submit_task.call_args.kwargs["force_refresh"] is True


def test_submit_signal_outcomes_loops_until_exhausted(monkeypatch):
    queue = MagicMock()
    queue.submit_background_task.return_value = FakeTask()

    class FakeOutcome:
        def __init__(self):
            self.calls = 0

        def run_outcomes(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {"evaluated": 500, "created": 0, "updated": 0}
            if self.calls == 2:
                return {"evaluated": 120, "created": 0, "updated": 0}
            return {"evaluated": 0, "created": 0, "updated": 0}

    monkeypatch.setattr(submitter, "_queue", lambda: queue)
    monkeypatch.setattr(submitter, "_outcome_service", lambda: FakeOutcome())

    task = submitter.submit_signal_outcomes()
    assert task.task_id == "t1"

    run_task = queue.submit_background_task.call_args.args[0]
    result = run_task()
    assert result["evaluated"] == 620
    assert result["rounds"] == 3


def test_market_review_releases_lock_on_submit_failure(monkeypatch):
    queue = MagicMock()
    queue.submit_background_task.side_effect = RuntimeError("boom")
    monkeypatch.setattr(submitter, "_queue", lambda: queue)
    monkeypatch.setattr(submitter, "try_acquire_market_review_lock", lambda config: "token")
    released = []
    monkeypatch.setattr(
        submitter, "release_market_review_lock", lambda token: released.append(token)
    )
    monkeypatch.setattr(submitter, "get_config", lambda: MagicMock(market_review_region="cn"))
    try:
        submitter.submit_market_review()
    except RuntimeError:
        pass
    assert released == ["token"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_site_task_submitter.py -q`
Expected: FAIL，模块不存在

- [ ] **Step 3: 实现**

`src/services/site_task_submitter.py`：

```python
# -*- coding: utf-8 -*-
"""Background task submissions shared by REST endpoints and agent task tools.

The endpoint modules used to own these lambdas. Moving them here keeps the agent
tools from importing the API layer and guarantees both entry points submit the
same job with the same queue metadata.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from src.config import Config, get_config
from src.core.market_review_lock import (
    MarketReviewExecutionLock,
    release_market_review_lock,
    try_acquire_market_review_lock,
)
from src.services.task_queue import get_task_queue

logger = logging.getLogger(__name__)


def _queue():
    return get_task_queue()


def _outcome_service():
    from src.services.decision_signal_outcome_service import DecisionSignalOutcomeService
    return DecisionSignalOutcomeService()


def _fund_review_service():
    from src.services.fund_nav_review_service import FundNavReviewService
    return FundNavReviewService()


def _paper_service():
    from src.services.paper_service import PaperService
    return PaperService()


def submit_stock_analysis(
    stock_code: str,
    *,
    report_type: str = "detailed",
    force_refresh: bool = False,
):
    return _queue().submit_task(
        stock_code,
        report_type=report_type,
        force_refresh=bool(force_refresh),
        query_source="agent",
    )


def run_market_review_background(
    send_notification: bool,
    effective_region: str,
    lock_token: Optional[MarketReviewExecutionLock] = None,
    config: Optional[Config] = None,
    query_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run market review after the API response has been accepted."""
    from src.core.market_review import run_market_review
    from src.core.market_review_runtime import build_market_review_runtime

    runtime_config = config or get_config()
    try:
        notifier, analyzer, search_service = build_market_review_runtime(runtime_config)
        review_kwargs = {
            "notifier": notifier,
            "analyzer": analyzer,
            "search_service": search_service,
            "config": runtime_config,
            "send_notification": send_notification,
            "override_region": effective_region,
            "return_structured": True,
            "trigger_source": "api",
        }
        if query_id:
            review_kwargs["query_id"] = query_id
        report = run_market_review(**review_kwargs)
        if not report:
            raise RuntimeError("大盘复盘未返回可持久化报告")
        if hasattr(report, "report"):
            return {
                "result": report.report,
                "market_review_payload": getattr(report, "market_review_payload", None),
                "region": effective_region,
            }
        return {"result": report, "region": effective_region}
    finally:
        release_market_review_lock(lock_token)


def submit_market_review(*, send_notification: bool = False, region: Optional[str] = None):
    from src.utils.market_review_region import normalize_market_review_region_lenient

    config = get_config()
    effective_region = normalize_market_review_region_lenient(
        region or config.market_review_region
    ) or "cn"
    lock_token = try_acquire_market_review_lock(config)
    if lock_token is None:
        raise RuntimeError("大盘复盘正在执行中，请稍后再试")
    task_id = uuid.uuid4().hex
    try:
        return _queue().submit_background_task(
            lambda: run_market_review_background(
                send_notification,
                effective_region=effective_region,
                lock_token=lock_token,
                config=config,
                query_id=task_id,
            ),
            stock_code="market_review",
            stock_name="大盘复盘",
            message="大盘复盘任务已提交",
            task_id=task_id,
            region=effective_region,
        )
    except Exception:
        release_market_review_lock(lock_token)
        raise


def submit_screening(
    strategy: str,
    market: str,
    max_results: int,
    variant_seed: str = "",
):
    from src.services.screening_service import ScreeningService

    queue = _queue()
    task_id = uuid.uuid4().hex

    def run_screen() -> Dict[str, Any]:
        queue.update_task_progress(task_id, 20, "正在执行选股，外部数据源较慢时会持续后台运行")

        def report_progress(progress: int, message: str) -> None:
            queue.update_task_progress(task_id, progress, message)

        result = ScreeningService(config=get_config()).screen(
            strategy=strategy,
            market=market,
            max_results=max_results,
            selection_seed=variant_seed,
            progress_callback=report_progress,
        )
        queue.update_task_progress(task_id, 98, "选股已完成，正在整理候选")
        return result

    return queue.submit_background_task(
        run_screen,
        stock_code="screening_screen",
        stock_name=f"{strategy} / {market}",
        report_type="screening_screen",
        message="选股任务已提交",
        task_id=task_id,
        trace_id=task_id,
    )


def submit_backtest(
    code: Optional[str] = None,
    *,
    force: bool = False,
    limit: int = 200,
):
    from src.services.backtest_service import BacktestService

    return _queue().submit_background_task(
        lambda: BacktestService().run_backtest(
            code=code, force=bool(force), limit=int(limit)
        ),
        stock_code=code or "backtest",
        stock_name="回测" if not code else f"{code} 回测",
        report_type="backtest_run",
        message="回测任务已提交",
    )


def submit_intelligence_fetch():
    from src.services.intelligence_service import IntelligenceService

    return _queue().submit_background_task(
        lambda: IntelligenceService().fetch_enabled_sources(),
        stock_code="intelligence_fetch",
        stock_name="情报抓取",
        report_type="intelligence_fetch",
        message="情报抓取任务已提交",
    )


def submit_fund_nav_backfill(
    *,
    fund_code: Optional[str] = None,
    since: Optional[date] = None,
    until: Optional[date] = None,
    dry_run: bool = True,
    resolve_missing: bool = False,
):
    service = _fund_review_service()
    return _queue().submit_background_task(
        lambda: service.backfill_from_history(
            fund_code=fund_code,
            since=since,
            until=until,
            dry_run=bool(dry_run),
            resolve_missing=bool(resolve_missing),
            limit=10_000,
        ),
        stock_code=fund_code or "fund_nav_backfill",
        stock_name="基金净值复盘补录",
        report_type="fund_nav_backfill",
        message="基金净值复盘补录任务已提交",
    )


def submit_fund_nav_reviews(
    *,
    fund_code: Optional[str] = None,
    horizons: Optional[List[str]] = None,
    force: bool = False,
):
    service = _fund_review_service()

    def run_until_exhausted() -> Dict[str, Any]:
        total = 0
        rounds = 0
        while True:
            result = service.run_reviews(
                fund_code=fund_code,
                horizons=horizons,
                force=bool(force),
                limit=400,
            )
            rounds += 1
            evaluated = int(result.get("evaluated", 0) or 0)
            total += evaluated
            if evaluated == 0:
                result["evaluated"] = total
                result["rounds"] = rounds
                return result

    return _queue().submit_background_task(
        run_until_exhausted,
        stock_code=fund_code or "fund_nav_review",
        stock_name="基金净值复盘评估",
        report_type="fund_nav_review",
        message="基金净值复盘评估任务已提交",
    )


def submit_signal_outcomes(
    *,
    stock_code: Optional[str] = None,
    horizons: Optional[List[str]] = None,
    force: bool = False,
):
    service = _outcome_service()

    def run_until_exhausted() -> Dict[str, Any]:
        total = 0
        rounds = 0
        while True:
            result = service.run_outcomes(
                stock_code=stock_code,
                horizons=horizons,
                force=bool(force),
                limit=500,
            )
            rounds += 1
            progressed = sum(
                int(result.get(key, 0) or 0)
                for key in ("evaluated", "created", "updated")
            )
            total += progressed
            if progressed == 0:
                result["evaluated"] = total
                result["rounds"] = rounds
                return result

    return _queue().submit_background_task(
        run_until_exhausted,
        stock_code=stock_code or "signal_outcomes",
        stock_name="信号后验评估",
        report_type="signal_outcomes",
        message="信号后验评估任务已提交",
    )


def submit_paper_backfill(
    account_id: int,
    from_date: date,
    to_date: Optional[date] = None,
):
    service = _paper_service()
    return _queue().submit_background_task(
        lambda: service.backfill_history(
            account_id=int(account_id),
            from_date=from_date,
            to_date=to_date,
        ),
        stock_code=f"paper_account_{int(account_id)}",
        stock_name="模拟盘回填",
        report_type="paper_backfill",
        message="模拟盘回填任务已提交",
    )
```

**端点改为复用**：

`api/v1/endpoints/analysis.py`：

```python
from src.services.site_task_submitter import (
    run_market_review_background as _run_market_review_background,
    submit_market_review as _submit_market_review,
)
```

删除本地 `_run_market_review_background` 与 540-581 的提交块，替换为：

```python
    try:
        task = _submit_market_review(
            send_notification=request.send_notification,
            region=effective_region,
        )
    except RuntimeError as exc:
        raise api_error(409, "duplicate_market_review", str(exc))
```

保留原 route 的 `lock_token` 获取逻辑？`_submit_market_review` 已内部获取锁，因此删除端点里的 `_try_acquire_market_review_lock` 与 `except: release`。`effective_region` 仍用于响应。

`screening.py` 的提交块改为：

```python
    task = submit_screening(
        strategy=request.strategy,
        market=request.market,
        max_results=request.max_results,
        variant_seed=request.variant_seed,
    )
```

并删除本地 `run_screen`/`submit_background_task` 代码。

- [ ] **Step 4: 运行测试**

Run: `uv run python -m pytest tests/test_site_task_submitter.py tests/test_api_market_review.py tests/test_screening_api.py -q`
Expected: PASS（测试文件名以 `ls tests | grep -i "screening\|market_review"` 实际结果为准；端点行为不变）

- [ ] **Step 5: Commit（等待用户明确确认）**

```bash
git add src/services/site_task_submitter.py api/v1/endpoints/analysis.py api/v1/endpoints/screening.py tests/test_site_task_submitter.py
git commit -m "refactor(tasks): extract background task submissions into a service"
```

---

## Task 11: 长任务工具（9 个）

**Files:**
- Create: `src/agent/tools/site_task_tools.py`
- Test: `tests/test_agent_site_task_tools.py`

**Interfaces:**
- Produces: `ALL_SITE_TASK_TOOLS`；handler 返回 `{kind, summary, task_id, status_endpoint}`
- Consumes: Task 10 的 `submit_*` 函数

- [ ] **Step 1: 写失败测试**

```python
# tests/test_agent_site_task_tools.py
import json

from src.agent.tools.registry import ToolRegistry
from src.agent.tools.site_task_tools import ALL_SITE_TASK_TOOLS


class FakeTask:
    task_id = "task-123"


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in ALL_SITE_TASK_TOOLS:
        registry.register(tool)
    return registry


def test_task_tools_are_task_mode():
    registry = _registry()
    assert len(ALL_SITE_TASK_TOOLS) == 9
    for tool in ALL_SITE_TASK_TOOLS:
        assert tool.policy.execution_mode == "task"
        assert tool.policy.read_only is False
        assert tool.policy.cancellation_safe is False
    assert registry.validate_tool_policies(strict=True) == []


def test_start_stock_analysis_emits_task_envelope(monkeypatch):
    calls = []

    def fake_submit(code, **kwargs):
        calls.append((code, kwargs))
        return FakeTask()

    monkeypatch.setattr(
        "src.agent.tools.site_task_tools.submit_stock_analysis", fake_submit
    )
    payload = json.loads(_registry().execute(
        "start_stock_analysis", **{"stock_code": "600519", "force_refresh": True}
    ))
    assert payload["kind"] == "stock_analysis"
    assert payload["task_id"] == "task-123"
    assert payload["status_endpoint"] == "/api/v1/analysis/status/task-123"
    assert calls[0][0] == "600519"
    assert calls[0][1]["force_refresh"] is True


def test_run_fund_nav_reviews_has_no_limit_parameter():
    tool = next(t for t in ALL_SITE_TASK_TOOLS if t.name == "run_fund_nav_reviews")
    assert "limit" not in {param.name for param in tool.parameters}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_agent_site_task_tools.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`src/agent/tools/site_task_tools.py`：

```python
# -*- coding: utf-8 -*-
"""Long-running site operations exposed as background tasks."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from src.agent.tools.registry import ToolDefinition, ToolParameter, ToolPolicy
from src.services.site_task_submitter import (
    submit_backtest,
    submit_fund_nav_backfill,
    submit_fund_nav_reviews,
    submit_intelligence_fetch,
    submit_market_review,
    submit_paper_backfill,
    submit_screening,
    submit_signal_outcomes,
    submit_stock_analysis,
)

_TASK_POLICY = ToolPolicy.declared(
    read_only=False,
    side_effects=["task_submit"],
    execution_mode="task",
    risk_level="low",
    cancellation_safe=False,
)


def _envelope(kind: str, summary: str, task) -> Dict[str, Any]:
    return {
        "kind": kind,
        "summary": summary,
        "task_id": str(task.task_id),
        "status_endpoint": f"/api/v1/analysis/status/{task.task_id}",
    }


def _handle_start_stock_analysis(
    stock_code: str,
    report_type: str = "detailed",
    force_refresh: bool = False,
) -> Dict[str, Any]:
    task = submit_stock_analysis(
        stock_code, report_type=report_type, force_refresh=bool(force_refresh)
    )
    return _envelope("stock_analysis", f"已提交「{stock_code}」分析任务", task)


def _handle_start_market_review(
    send_notification: bool = False,
    region: Optional[str] = None,
) -> Dict[str, Any]:
    task = submit_market_review(
        send_notification=bool(send_notification), region=region
    )
    return _envelope("market_review", "已提交大盘复盘任务", task)


def _handle_run_backtest(
    code: Optional[str] = None,
    force: bool = False,
    limit: int = 200,
) -> Dict[str, Any]:
    task = submit_backtest(code, force=bool(force), limit=int(limit))
    target = f"「{code}」" if code else "全量"
    return _envelope("backtest_run", f"已提交{target}回测任务", task)


def _handle_start_screening(
    strategy: str,
    market: str = "cn",
    max_results: int = 20,
    variant_seed: str = "",
) -> Dict[str, Any]:
    task = submit_screening(
        strategy=strategy,
        market=market,
        max_results=int(max_results),
        variant_seed=variant_seed,
    )
    return _envelope("screening_run", f"已提交「{strategy}」选股任务", task)


def _handle_fetch_intelligence() -> Dict[str, Any]:
    task = submit_intelligence_fetch()
    return _envelope("intelligence_fetch", "已提交情报抓取任务", task)


def _handle_backfill_fund_nav_reviews(
    fund_code: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    dry_run: bool = True,
    resolve_missing: bool = False,
) -> Dict[str, Any]:
    task = submit_fund_nav_backfill(
        fund_code=fund_code,
        since=date.fromisoformat(since) if since else None,
        until=date.fromisoformat(until) if until else None,
        dry_run=bool(dry_run),
        resolve_missing=bool(resolve_missing),
    )
    return _envelope("fund_nav_backfill", "已提交基金净值复盘补录任务", task)


def _handle_run_fund_nav_reviews(
    fund_code: Optional[str] = None,
    horizons: Optional[List[str]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    task = submit_fund_nav_reviews(
        fund_code=fund_code, horizons=horizons, force=bool(force)
    )
    return _envelope("fund_nav_run", "已提交基金净值复盘评估任务（不设条数上限）", task)


def _handle_run_signal_outcomes(
    stock_code: Optional[str] = None,
    horizons: Optional[List[str]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    task = submit_signal_outcomes(
        stock_code=stock_code, horizons=horizons, force=bool(force)
    )
    return _envelope("signal_outcomes_run", "已提交信号后验评估任务（不设条数上限）", task)


def _handle_backfill_paper_signals(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    account_id: Optional[int] = None,
) -> Dict[str, Any]:
    from src.services.paper_service import PaperService

    service = PaperService()
    account = int(account_id) if account_id is not None else int(
        service.get_or_create_account()["id"]
    )
    start = date.fromisoformat(from_date) if from_date else date.today().replace(day=1)
    task = submit_paper_backfill(
        account,
        start,
        date.fromisoformat(to_date) if to_date else None,
    )
    return _envelope("paper_backfill", "已提交模拟盘信号回填任务", task)
```

工具定义（略去重复的 `ToolParameter` 说明，按上方 handler 参数一一对应）：

```python
start_stock_analysis = ToolDefinition(
    name="start_stock_analysis",
    description="提交一只股票的分析任务，后台执行；返回任务 ID 供查询进度。",
    parameters=[
        ToolParameter(name="stock_code", type="string", description="股票代码", required=True),
        ToolParameter(name="report_type", type="string", description="报告类型，默认 detailed", required=False),
        ToolParameter(name="force_refresh", type="boolean", description="忽略缓存强制重跑", required=False),
    ],
    handler=_handle_start_stock_analysis,
    category="site_task",
    policy=_TASK_POLICY,
)

start_market_review = ToolDefinition(
    name="start_market_review",
    description="提交大盘复盘任务，后台执行。",
    parameters=[
        ToolParameter(name="send_notification", type="boolean", description="完成后是否推送通知", required=False),
        ToolParameter(name="region", type="string", description="市场区域 cn/hk/us", required=False),
    ],
    handler=_handle_start_market_review,
    category="site_task",
    policy=_TASK_POLICY,
)

run_backtest = ToolDefinition(
    name="run_backtest",
    description="提交后验/回测任务，可指定股票或全量。",
    parameters=[
        ToolParameter(name="code", type="string", description="股票代码，省略为全量", required=False),
        ToolParameter(name="force", type="boolean", description="强制重算", required=False),
        ToolParameter(name="limit", type="integer", description="最多评估条数，默认 200", required=False),
    ],
    handler=_handle_run_backtest,
    category="site_task",
    policy=_TASK_POLICY,
)

start_screening = ToolDefinition(
    name="start_screening",
    description="提交选股任务，后台执行。",
    parameters=[
        ToolParameter(name="strategy", type="string", description="策略 ID", required=True),
        ToolParameter(name="market", type="string", description="市场，默认 cn", required=False),
        ToolParameter(name="max_results", type="integer", description="候选数量上限，默认 20", required=False),
        ToolParameter(name="variant_seed", type="string", description="结果变体种子", required=False),
    ],
    handler=_handle_start_screening,
    category="site_task",
    policy=_TASK_POLICY,
)

fetch_intelligence = ToolDefinition(
    name="fetch_intelligence",
    description="抓取所有已启用情报源，后台执行。",
    parameters=[],
    handler=_handle_fetch_intelligence,
    category="site_task",
    policy=_TASK_POLICY,
)

backfill_fund_nav_reviews = ToolDefinition(
    name="backfill_fund_nav_reviews",
    description="从历史基金报告补录净值复盘锚点，后台执行；默认 dry-run 不写入。",
    parameters=[
        ToolParameter(name="fund_code", type="string", description="基金代码", required=False),
        ToolParameter(name="since", type="string", description="起始日期 YYYY-MM-DD", required=False),
        ToolParameter(name="until", type="string", description="结束日期 YYYY-MM-DD", required=False),
        ToolParameter(name="dry_run", type="boolean", description="默认 true", required=False),
        ToolParameter(name="resolve_missing", type="boolean", description="旧报告联网匹配锚点", required=False),
    ],
    handler=_handle_backfill_fund_nav_reviews,
    category="site_task",
    policy=_TASK_POLICY,
)

run_fund_nav_reviews = ToolDefinition(
    name="run_fund_nav_reviews",
    description="评估所有到期基金复盘锚点，后台持续执行直到没有可评估行；不设条数上限。",
    parameters=[
        ToolParameter(name="fund_code", type="string", description="只评估该基金", required=False),
        ToolParameter(name="horizons", type="array", description="1m/3m/6m/1y 子集", required=False),
        ToolParameter(name="force", type="boolean", description="忽略冷却强制重算", required=False),
    ],
    handler=_handle_run_fund_nav_reviews,
    category="site_task",
    policy=_TASK_POLICY,
)

run_signal_outcomes = ToolDefinition(
    name="run_signal_outcomes",
    description="评估决策信号后验结果，后台持续执行直到没有可评估行；不设条数上限。",
    parameters=[
        ToolParameter(name="stock_code", type="string", description="只评估该股票", required=False),
        ToolParameter(name="horizons", type="array", description="周期子集", required=False),
        ToolParameter(name="force", type="boolean", description="强制重算", required=False),
    ],
    handler=_handle_run_signal_outcomes,
    category="site_task",
    policy=_TASK_POLICY,
)

backfill_paper_signals = ToolDefinition(
    name="backfill_paper_signals",
    description="回填模拟盘历史信号成交，后台执行。",
    parameters=[
        ToolParameter(name="from_date", type="string", description="起始日期 YYYY-MM-DD，默认本月 1 日", required=False),
        ToolParameter(name="to_date", type="string", description="结束日期 YYYY-MM-DD", required=False),
        ToolParameter(name="account_id", type="integer", description="模拟盘账户 ID", required=False),
    ],
    handler=_handle_backfill_paper_signals,
    category="site_task",
    policy=_TASK_POLICY,
)

ALL_SITE_TASK_TOOLS = [
    start_stock_analysis,
    start_market_review,
    run_backtest,
    start_screening,
    fetch_intelligence,
    backfill_fund_nav_reviews,
    run_fund_nav_reviews,
    run_signal_outcomes,
    backfill_paper_signals,
]
```

- [ ] **Step 4: 运行测试**

Run: `uv run python -m pytest tests/test_agent_site_task_tools.py tests/test_agent_site_action_tools.py tests/test_agent_site_read_tools.py -q`
Expected: PASS

- [ ] **Step 5: Commit（等待用户明确确认）**

```bash
git add src/agent/tools/site_task_tools.py tests/test_agent_site_task_tools.py
git commit -m "feat(agent): add background task tools"
```

---

## Task 12: 提案工具一（告警 / 持仓 / 信号 / 模拟盘）

**Files:**
- Modify: `src/agent/tools/alert_tools.py:105-207`
- Modify: `src/agent/tools/action_tools.py`
- Modify: `src/agent/action_contract.py`
- Test: `tests/test_agent_write_proposals.py`

**Interfaces:**
- Produces proposal envelopes：`alert_create|alert_update|alert_delete`、`portfolio_cash_create|portfolio_cash_delete`、`portfolio_corporate_create|portfolio_corporate_delete`、`portfolio_account_create|portfolio_account_update|portfolio_account_deactivate`、`signal_create|signal_status|signal_feedback|signal_reassess`、`paper_reset`
- Consumes: 现有 proposal 事件通道（Task 2）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_agent_write_proposals.py 追加（保留现有 propose_portfolio_trade 用例）
import json

from src.agent.factory import get_tool_registry


def _invoke(name, args):
    return json.loads(get_tool_registry().execute(name, **args))


def test_alert_rule_change_create_envelope():
    payload = _invoke("propose_alert_rule_change", {
        "action": "create",
        "target": "600519",
        "alert_type": "price_above",
        "parameters": {"price": 1800},
    })
    assert payload["kind"] == "alert_create"
    assert payload["proposal"]["target"] == "600519"


def test_alert_rule_change_delete_requires_rule_id():
    payload = _invoke("propose_alert_rule_change", {"action": "delete"})
    assert payload["error"]


def test_portfolio_cash_delete_envelope():
    payload = _invoke("propose_portfolio_ledger_entry", {
        "action": "delete_cash", "entry_id": 9,
    })
    assert payload["kind"] == "portfolio_cash_delete"
    assert payload["proposal"] == {"entry_id": 9}


def test_portfolio_account_deactivate_envelope():
    payload = _invoke("propose_portfolio_account_change", {
        "action": "deactivate", "account_id": 3,
    })
    assert payload["kind"] == "portfolio_account_deactivate"
    assert payload["proposal"] == {"account_id": 3}


def test_signal_status_envelope():
    payload = _invoke("propose_signal_change", {
        "action": "status", "signal_id": 5, "status": "active",
    })
    assert payload["kind"] == "signal_status"
    assert payload["proposal"]["signal_id"] == 5


def test_paper_reset_envelope():
    payload = _invoke("propose_paper_reset", {"initial_capital": 100000})
    assert payload["kind"] == "paper_reset"
    assert payload["proposal"]["initial_capital"] == 100000
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_agent_write_proposals.py -q`
Expected: FAIL，工具不存在

- [ ] **Step 3: 实现告警提案**

`src/agent/tools/alert_tools.py` 用以下实现替换 `_handle_propose_alert` 与 `propose_alert_tool`：

```python
def _handle_propose_alert_rule_change(
    action: str,
    target: str = "",
    alert_type: str = "",
    parameters: Optional[Dict[str, Any]] = None,
    name: str = "",
    severity: str = "info",
    rule_id: Optional[int] = None,
    reason: str = "",
) -> Dict[str, Any]:
    """Validate an alert rule create/update/delete proposal; persists nothing."""
    norm_action = str(action or "").strip().lower()
    if norm_action not in ("create", "update", "delete"):
        return {"error": f"action must be create/update/delete, got {action!r}"}
    if norm_action == "delete":
        if rule_id is None:
            return {"error": "rule_id is required for action=delete"}
        return {
            "kind": "alert_delete",
            "summary": f"删除告警规则 #{int(rule_id)}",
            "proposal": {"rule_id": int(rule_id)},
        }
    if norm_action == "update" and rule_id is None:
        return {"error": "rule_id is required for action=update"}

    norm_type = str(alert_type or "").strip().lower()
    if norm_type not in SYMBOL_ALERT_TYPES:
        return {"error": f"unsupported alert_type: {norm_type}; supported: {sorted(SYMBOL_ALERT_TYPES)}"}
    norm_severity = str(severity or "info").strip().lower()
    if norm_severity not in SUPPORTED_SEVERITIES:
        return {"error": f"unsupported severity: {norm_severity}"}
    try:
        norm_params = normalize_alert_parameters(norm_type, parameters or {})
    except AlertServiceError as exc:
        return {"error": str(exc)}
    norm_target = str(target or "").strip()
    if not norm_target:
        return {"error": "target must not be empty"}

    proposal = {
        "name": (str(name or "").strip() or AlertService._default_rule_name(
            target=norm_target,
            alert_type=norm_type,
            parameters=norm_params,
        ))[:64],
        "target_scope": "single_symbol",
        "target": norm_target,
        "alert_type": norm_type,
        "parameters": norm_params,
        "severity": norm_severity,
    }
    kind = "alert_update" if norm_action == "update" else "alert_create"
    if norm_action == "update":
        proposal["rule_id"] = int(rule_id)
    summary = _build_summary(norm_target, norm_type, norm_params)
    if norm_action == "update":
        summary = f"更新告警规则：{summary}"
    norm_reason = _normalize_reason(reason)
    if norm_reason:
        summary = f"{summary}（{norm_reason}）"
    return {"kind": kind, "summary": summary, "proposal": proposal}
```

```python
propose_alert_rule_change_tool = ToolDefinition(
    name="propose_alert_rule_change",
    description=(
        "生成告警规则的创建/更新/删除提案，交给用户确认；本工具不写入任何规则。"
        "创建和更新需要 target/alert_type/parameters；更新和删除需要 rule_id。\n"
        f"{_PARAMETER_DOC}"
    ),
    parameters=[
        ToolParameter(name="action", type="string", description="create|update|delete", required=True,
                      enum=["create", "update", "delete"]),
        ToolParameter(name="target", type="string", description="股票代码", required=False),
        ToolParameter(name="alert_type", type="string", description="告警类型", required=False,
                      enum=sorted(SYMBOL_ALERT_TYPES)),
        ToolParameter(name="parameters", type="object", description=_PARAMETER_DOC, required=False),
        ToolParameter(name="name", type="string", description="规则名称", required=False),
        ToolParameter(name="severity", type="string", description="严重级别", required=False,
                      enum=sorted(SUPPORTED_SEVERITIES), default="info"),
        ToolParameter(name="rule_id", type="integer", description="更新/删除时的规则 ID", required=False),
        ToolParameter(name="reason", type="string", description="提案理由", required=False),
    ],
    handler=_handle_propose_alert_rule_change,
    category="action",
    policy=_ALERT_PROPOSAL_POLICY,
)

ALL_ALERT_TOOLS = [propose_alert_rule_change_tool]
```

- [ ] **Step 4: 实现持仓/信号/模拟盘提案**

在 `action_tools.py` 追加统一辅助与 handler：

```python
def _proposal(kind: str, summary: str, proposal: Dict[str, Any]) -> Dict[str, Any]:
    return {"kind": kind, "summary": summary, "proposal": proposal}


def _require_positive_int(value: Any, field: str) -> Tuple[Optional[int], Optional[str]]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None, f"{field} 必须是整数，收到 {value!r}"
    if parsed <= 0:
        return None, f"{field} 必须 > 0，收到 {parsed}"
    return parsed, None


def _handle_propose_portfolio_ledger_entry(
    action: str,
    account_id: Any = None,
    event_date: str = "",
    direction: str = "",
    amount: Any = None,
    currency: str = "",
    note: str = "",
    entry_id: Any = None,
    symbol: str = "",
    effective_date: str = "",
    action_type: str = "",
    market: str = "",
    cash_dividend_per_share: Any = None,
    split_ratio: Any = None,
    action_id: Any = None,
) -> Dict[str, Any]:
    norm_action = str(action or "").strip().lower()
    if norm_action in ("delete_cash", "delete_corporate"):
        id_value = entry_id if norm_action == "delete_cash" else action_id
        parsed, err = _require_positive_int(id_value, "id")
        if err:
            return {"error": err}
        if norm_action == "delete_cash":
            return _proposal(
                "portfolio_cash_delete", f"删除资金流水 #{parsed}", {"entry_id": parsed}
            )
        return _proposal(
            "portfolio_corporate_delete", f"删除公司行动 #{parsed}", {"action_id": parsed}
        )

    account, err = _resolve_account(account_id)
    if err:
        return {"error": err}
    if norm_action == "cash":
        if not event_date:
            return {"error": "event_date 不能为空"}
        direction_norm = str(direction or "").strip().lower()
        if direction_norm not in ("in", "out", "deposit", "withdraw"):
            return {"error": f"direction 非法: {direction!r}"}
        amount_value, err = _check_number(amount, "amount", allow_zero=False)
        if err:
            return {"error": err}
        return _proposal(
            "portfolio_cash_create",
            f"录入资金流水 {direction_norm} {_fmt_amount(amount_value)}",
            {
                "account_id": int(account_id),
                "event_date": event_date,
                "direction": direction_norm,
                "amount": amount_value,
                "currency": currency or None,
                "note": note or None,
            },
        )
    if norm_action == "corporate":
        if not symbol or not effective_date or not action_type:
            return {"error": "symbol/effective_date/action_type 不能为空"}
        cash, err = _check_number(
            cash_dividend_per_share, "cash_dividend_per_share", allow_zero=True
        ) if cash_dividend_per_share not in (None, "") else (None, None)
        if err:
            return {"error": err}
        split, err = _check_number(
            split_ratio, "split_ratio", allow_zero=False
        ) if split_ratio not in (None, "") else (None, None)
        if err:
            return {"error": err}
        if cash is None and split is None:
            return {"error": "cash_dividend_per_share 与 split_ratio 至少提供一个"}
        return _proposal(
            "portfolio_corporate_create",
            f"录入公司行动 {action_type} {symbol}",
            {
                "account_id": int(account_id),
                "symbol": symbol,
                "effective_date": effective_date,
                "action_type": action_type,
                "market": market or None,
                "currency": currency or None,
                "cash_dividend_per_share": cash,
                "split_ratio": split,
                "note": note or None,
            },
        )
    return {"error": f"action 必须是 cash/corporate/delete_cash/delete_corporate，收到 {action!r}"}
```

账户提案：

```python
def _handle_propose_portfolio_account_change(
    action: str,
    account_id: Any = None,
    name: str = "",
    broker: str = "",
    market: str = "",
    base_currency: str = "",
    owner_id: str = "",
    is_active: Optional[bool] = None,
) -> Dict[str, Any]:
    norm_action = str(action or "").strip().lower()
    if norm_action == "create":
        if not name.strip():
            return {"error": "name 不能为空"}
        if market not in SUPPORTED_MARKETS:
            return {"error": f"market 必须是 {'/'.join(SUPPORTED_MARKETS)}"}
        proposal = {
            "name": name.strip(),
            "broker": broker or None,
            "market": market,
            "base_currency": (base_currency or "CNY").upper(),
            "owner_id": owner_id or None,
        }
        return _proposal("portfolio_account_create", f"创建持仓账户「{name.strip()}」", proposal)

    parsed, err = _require_positive_int(account_id, "account_id")
    if err:
        return {"error": err}
    if norm_action == "deactivate":
        return _proposal(
            "portfolio_account_deactivate", f"停用持仓账户 #{parsed}", {"account_id": parsed}
        )
    if norm_action == "update":
        payload: Dict[str, Any] = {"account_id": parsed}
        for key, value in (
            ("name", name), ("broker", broker), ("market", market),
            ("base_currency", base_currency), ("owner_id", owner_id),
        ):
            if value:
                payload[key] = value
        if is_active is not None:
            payload["is_active"] = bool(is_active)
        if len(payload) == 1:
            return {"error": "update 至少需要一个可修改字段"}
        return _proposal("portfolio_account_update", f"更新持仓账户 #{parsed}", payload)
    return {"error": f"action 必须是 create/update/deactivate，收到 {action!r}"}
```

信号提案：

```python
_SIGNAL_STATUSES = ("active", "expired", "invalidated", "closed", "draft")
_SIGNAL_FEEDBACK_VALUES = ("useful", "not_useful", "wrong", "partial")


def _handle_propose_signal_change(
    action: str,
    signal_id: Any = None,
    status: str = "",
    feedback_value: str = "",
    reason_code: str = "",
    note: str = "",
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    norm_action = str(action or "").strip().lower()
    if norm_action == "create":
        if not isinstance(payload, dict) or not payload:
            return {"error": "create 需要 payload（DecisionSignalCreateRequest 字段）"}
        required = ("stock_code", "market", "source_type", "trigger_source", "action")
        missing = [key for key in required if not payload.get(key)]
        if missing:
            return {"error": f"payload 缺少字段: {', '.join(missing)}"}
        return _proposal(
            "signal_create",
            f"创建决策信号 {payload['stock_code']} {payload['action']}",
            dict(payload),
        )
    if norm_action == "reassess":
        if not isinstance(payload, dict) or not payload:
            return {"error": "reassess 需要 payload（source_report_id/decision_profile/persist）"}
        return _proposal("signal_reassess", "重算信号建议", dict(payload))

    parsed, err = _require_positive_int(signal_id, "signal_id")
    if err:
        return {"error": err}
    if norm_action == "status":
        norm_status = str(status or "").strip().lower()
        if norm_status not in _SIGNAL_STATUSES:
            return {"error": f"status 必须是 {'/'.join(_SIGNAL_STATUSES)}"}
        return _proposal(
            "signal_status",
            f"把信号 #{parsed} 状态改为 {norm_status}",
            {"signal_id": parsed, "status": norm_status},
        )
    if norm_action == "feedback":
        norm_value = str(feedback_value or "").strip().lower()
        if norm_value not in _SIGNAL_FEEDBACK_VALUES:
            return {"error": f"feedback_value 必须是 {'/'.join(_SIGNAL_FEEDBACK_VALUES)}"}
        proposal = {"signal_id": parsed, "feedback_value": norm_value}
        if reason_code:
            proposal["reason_code"] = reason_code[:64]
        if note:
            proposal["note"] = note[:1000]
        return _proposal("signal_feedback", f"提交信号 #{parsed} 反馈：{norm_value}", proposal)
    return {"error": f"action 必须是 create/status/feedback/reassess，收到 {action!r}"}


def _handle_propose_paper_reset(initial_capital: Any = None) -> Dict[str, Any]:
    proposal: Dict[str, Any] = {}
    if initial_capital not in (None, ""):
        value, err = _check_number(initial_capital, "initial_capital", allow_zero=False)
        if err:
            return {"error": err}
        proposal["initial_capital"] = value
    return _proposal(
        "paper_reset",
        "重置模拟盘账户（清空持仓、成交与信号记录）",
        proposal,
    )
```

工具定义：

```python
propose_portfolio_ledger_entry = ToolDefinition(
    name="propose_portfolio_ledger_entry",
    description=(
        "生成资金流水或公司行动的录入/删除提案，交给用户确认。"
        "action=cash/corporate/delete_cash/delete_corporate。"
    ),
    parameters=[
        ToolParameter(name="action", type="string", description="cash|corporate|delete_cash|delete_corporate", required=True),
        ToolParameter(name="account_id", type="integer", description="账户 ID", required=False),
        ToolParameter(name="event_date", type="string", description="资金流水日期 YYYY-MM-DD", required=False),
        ToolParameter(name="direction", type="string", description="in/out/deposit/withdraw", required=False),
        ToolParameter(name="amount", type="number", description="金额", required=False),
        ToolParameter(name="currency", type="string", description="币种", required=False),
        ToolParameter(name="note", type="string", description="备注", required=False),
        ToolParameter(name="entry_id", type="integer", description="delete_cash 的资金流水 ID", required=False),
        ToolParameter(name="symbol", type="string", description="公司行动标的", required=False),
        ToolParameter(name="effective_date", type="string", description="公司行动生效日", required=False),
        ToolParameter(name="action_type", type="string", description="公司行动类型", required=False),
        ToolParameter(name="market", type="string", description="市场", required=False),
        ToolParameter(name="cash_dividend_per_share", type="number", description="每股现金分红", required=False),
        ToolParameter(name="split_ratio", type="number", description="拆合股比例", required=False),
        ToolParameter(name="action_id", type="integer", description="delete_corporate 的公司行动 ID", required=False),
    ],
    handler=_handle_propose_portfolio_ledger_entry,
    category="action",
    policy=_PROPOSAL_POLICY,
)

propose_portfolio_account_change = ToolDefinition(
    name="propose_portfolio_account_change",
    description="生成持仓账户创建/更新/停用提案，交给用户确认。",
    parameters=[
        ToolParameter(name="action", type="string", description="create|update|deactivate", required=True),
        ToolParameter(name="account_id", type="integer", description="账户 ID", required=False),
        ToolParameter(name="name", type="string", description="账户名称", required=False),
        ToolParameter(name="broker", type="string", description="券商", required=False),
        ToolParameter(name="market", type="string", description="市场", required=False),
        ToolParameter(name="base_currency", type="string", description="本位币", required=False),
        ToolParameter(name="owner_id", type="string", description="所有人标识", required=False),
        ToolParameter(name="is_active", type="boolean", description="更新时是否启用", required=False),
    ],
    handler=_handle_propose_portfolio_account_change,
    category="action",
    policy=_PROPOSAL_POLICY,
)

propose_signal_change = ToolDefinition(
    name="propose_signal_change",
    description=(
        "生成决策信号创建/状态更新/反馈/重算提案，交给用户确认。"
        "create 与 reassess 使用 payload 传完整请求体。"
    ),
    parameters=[
        ToolParameter(name="action", type="string", description="create|status|feedback|reassess", required=True),
        ToolParameter(name="signal_id", type="integer", description="信号 ID", required=False),
        ToolParameter(name="status", type="string", description="新状态", required=False),
        ToolParameter(name="feedback_value", type="string", description="反馈值", required=False),
        ToolParameter(name="reason_code", type="string", description="反馈原因码", required=False),
        ToolParameter(name="note", type="string", description="反馈备注", required=False),
        ToolParameter(name="payload", type="object", description="create/reassess 的完整请求体", required=False),
    ],
    handler=_handle_propose_signal_change,
    category="action",
    policy=_PROPOSAL_POLICY,
)

propose_paper_reset = ToolDefinition(
    name="propose_paper_reset",
    description="生成模拟盘重置提案（破坏性操作，必须用户确认）。",
    parameters=[
        ToolParameter(name="initial_capital", type="number", description="重置后的初始资金", required=False),
    ],
    handler=_handle_propose_paper_reset,
    category="action",
    policy=_PROPOSAL_POLICY,
)
```

更新 `ALL_ACTION_TOOLS` 为四者相加（含既有 `propose_portfolio_trade`）。

`src/agent/action_contract.py` 同步修正：删除 `intelligence_source_update`、`intelligence_source_delete`（当前 API 不存在这两个端点，工具不得声明无法由浏览器执行的 kind）。

- [ ] **Step 5: 运行测试**

Run: `uv run python -m pytest tests/test_agent_write_proposals.py tests/test_agent_tool_label_parity.py -q`
Expected: 新用例通过；标签测试可能因改名失败，Task 15 统一修复，但本任务必须先把 `api/v1/endpoints/agent.py` 与 runner 中的 `propose_alert` / `propose_watchlist_change` 名称替换为 `propose_alert_rule_change`（watchlist 已在 Task 9 删除），否则视为失败。

- [ ] **Step 6: Commit（等待用户明确确认）**

```bash
git add src/agent/tools/alert_tools.py src/agent/tools/action_tools.py src/agent/action_contract.py tests/test_agent_write_proposals.py api/v1/endpoints/agent.py src/agent/runner.py
git commit -m "feat(agent): add portfolio, signal, alert and paper proposal tools"
```

---

## Task 13: 提案工具二（历史删除 / 情报 / 配置 / 调度 / 通知）

**Files:**
- Modify: `src/agent/tools/action_tools.py`
- Test: `tests/test_agent_write_proposals.py`
- Test: `tests/test_agent_config_tool_security.py`

**Interfaces:**
- Produces proposal envelopes：`history_delete_records|history_delete_by_code`、`intelligence_source_create|intelligence_defaults_create`、`config_update`、`scheduler_run`、`notification_test`
- Consumes: `SystemConfigService().get_schema()` 的 `is_sensitive`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_agent_config_tool_security.py
import json

from src.agent.factory import get_tool_registry


def test_config_update_rejects_sensitive_keys(monkeypatch):
    class FakeConfigService:
        def get_schema(self):
            return {"categories": [{"fields": [
                {"key": "ENABLE_REALTIME_QUOTE", "is_sensitive": False},
                {"key": "DEEPSEEK_API_KEY", "is_sensitive": True},
            ]}]}

    monkeypatch.setattr(
        "src.agent.tools.action_tools._system_config_service",
        lambda: FakeConfigService(),
    )
    payload = json.loads(get_tool_registry().execute("propose_config_update", **{
        "config_version": "v1",
        "items": [{"key": "DEEPSEEK_API_KEY", "value": "sk-x"}],
    }))
    assert payload["error"]
    assert "sk-x" not in json.dumps(payload)


def test_config_update_accepts_plain_key(monkeypatch):
    class FakeConfigService:
        def get_schema(self):
            return {"categories": [{"fields": [
                {"key": "ENABLE_REALTIME_QUOTE", "is_sensitive": False},
            ]}]}

    monkeypatch.setattr(
        "src.agent.tools.action_tools._system_config_service",
        lambda: FakeConfigService(),
    )
    payload = json.loads(get_tool_registry().execute("propose_config_update", **{
        "config_version": "v1",
        "items": [{"key": "ENABLE_REALTIME_QUOTE", "value": "false"}],
    }))
    assert payload["kind"] == "config_update"
```

```python
# tests/test_agent_write_proposals.py 追加
def test_history_delete_records_envelope():
    payload = _invoke("propose_history_deletion", {
        "action": "records", "record_ids": [1, 2],
    })
    assert payload["kind"] == "history_delete_records"
    assert payload["proposal"]["record_ids"] == [1, 2]


def test_history_delete_by_code_requires_code():
    payload = _invoke("propose_history_deletion", {"action": "by_code"})
    assert payload["error"]


def test_notification_test_envelope():
    payload = _invoke("propose_notification_test", {"channel": "feishu"})
    assert payload["kind"] == "notification_test"
    assert payload["proposal"]["channel"] == "feishu"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_agent_write_proposals.py tests/test_agent_config_tool_security.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

在 `action_tools.py` 追加：

```python
_system_config_service = None


def _system_config_service_instance():
    global _system_config_service
    if _system_config_service is None:
        from src.services.system_config_service import SystemConfigService
        _system_config_service = SystemConfigService()
    return _system_config_service


def _handle_propose_history_deletion(
    action: str,
    record_ids: Optional[List[int]] = None,
    stock_code: str = "",
) -> Dict[str, Any]:
    norm_action = str(action or "").strip().lower()
    if norm_action == "records":
        ids = [int(value) for value in (record_ids or [])]
        if not ids:
            return {"error": "record_ids 不能为空"}
        return _proposal(
            "history_delete_records",
            f"删除 {len(ids)} 条分析历史记录",
            {"record_ids": ids},
        )
    if norm_action == "by_code":
        norm_code = str(stock_code or "").strip()
        if not norm_code:
            return {"error": "stock_code 不能为空"}
        return _proposal(
            "history_delete_by_code",
            f"删除「{norm_code}」的全部分析历史",
            {"stock_code": norm_code},
        )
    return {"error": f"action 必须是 records/by_code，收到 {action!r}"}


def _sensitive_config_keys() -> set:
    schema = _system_config_service_instance().get_schema()
    return {
        str(field.get("key"))
        for category in schema.get("categories", [])
        for field in category.get("fields", [])
        if field.get("is_sensitive")
    }


def _handle_propose_intelligence_source_change(
    action: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    norm_action = str(action or "").strip().lower()
    if norm_action == "create":
        if not isinstance(payload, dict) or not payload:
            return {"error": "create 需要 payload"}
        return _proposal(
            "intelligence_source_create",
            f"创建情报源「{payload.get('name') or '未命名'}」",
            dict(payload),
        )
    if norm_action == "defaults":
        return _proposal(
            "intelligence_defaults_create",
            "创建内置默认情报源",
            {},
        )
    return {"error": f"action 必须是 create/defaults，收到 {action!r}"}


def _handle_propose_config_update(
    config_version: str,
    items: List[Dict[str, str]],
) -> Dict[str, Any]:
    if not config_version:
        return {"error": "config_version 不能为空"}
    if not items:
        return {"error": "items 不能为空"}
    sensitive = _sensitive_config_keys()
    blocked = [item.get("key") for item in items if item.get("key") in sensitive]
    if blocked:
        return {
            "error": (
                "以下配置属于密钥/敏感字段，问股助手不能修改："
                + "、".join(str(key) for key in blocked)
                + "。请到设置页手动修改。"
            )
        }
    keys = [str(item.get("key") or "").strip() for item in items]
    if any(not key for key in keys):
        return {"error": "items 中存在空 key"}
    return _proposal(
        "config_update",
        f"修改 {len(items)} 项系统配置：{'、'.join(keys)}",
        {
            "config_version": config_version,
            "items": [{"key": str(item["key"]), "value": str(item["value"])} for item in items],
        },
    )


def _handle_propose_scheduler_run(send_notification: bool = True) -> Dict[str, Any]:
    return _proposal(
        "scheduler_run",
        "立即执行一次调度分析",
        {"send_notification": bool(send_notification)},
    )


def _handle_propose_notification_test(
    channel: str,
    title: str = "",
    content: str = "",
) -> Dict[str, Any]:
    norm_channel = str(channel or "").strip().lower()
    if not norm_channel:
        return {"error": "channel 不能为空"}
    proposal: Dict[str, Any] = {"channel": norm_channel}
    if title:
        proposal["title"] = title[:80]
    if content:
        proposal["content"] = content[:1000]
    return _proposal(
        "notification_test",
        f"向通知渠道「{norm_channel}」发送测试消息",
        proposal,
    )
```

工具定义：

```python
propose_history_deletion = ToolDefinition(
    name="propose_history_deletion",
    description="生成删除分析历史的提案：按记录 ID 或按股票代码。删除属破坏性操作，必须用户确认。",
    parameters=[
        ToolParameter(name="action", type="string", description="records|by_code", required=True),
        ToolParameter(name="record_ids", type="array", description="要删除的记录 ID 列表", required=False),
        ToolParameter(name="stock_code", type="string", description="按股票代码删除全部历史", required=False),
    ],
    handler=_handle_propose_history_deletion,
    category="action",
    policy=_PROPOSAL_POLICY,
)

propose_intelligence_source_change = ToolDefinition(
    name="propose_intelligence_source_change",
    description="生成创建情报源或创建默认情报源的提案。",
    parameters=[
        ToolParameter(name="action", type="string", description="create|defaults", required=True),
        ToolParameter(name="payload", type="object", description="create 的完整请求体", required=False),
    ],
    handler=_handle_propose_intelligence_source_change,
    category="action",
    policy=_PROPOSAL_POLICY,
)

propose_config_update = ToolDefinition(
    name="propose_config_update",
    description=(
        "生成系统配置修改提案。密钥、Token、密码等敏感字段一律拒绝；"
        "config_version 必须来自 get_config_view 的返回。"
    ),
    parameters=[
        ToolParameter(name="config_version", type="string", description="当前配置版本号", required=True),
        ToolParameter(name="items", type="array", description="[{key, value}] 列表", required=True),
    ],
    handler=_handle_propose_config_update,
    category="action",
    policy=_PROPOSAL_POLICY,
)

propose_scheduler_run = ToolDefinition(
    name="propose_scheduler_run",
    description="生成立即执行一次调度分析的提案。",
    parameters=[
        ToolParameter(name="send_notification", type="boolean", description="是否推送通知，默认 true", required=False),
    ],
    handler=_handle_propose_scheduler_run,
    category="action",
    policy=_PROPOSAL_POLICY,
)

propose_notification_test = ToolDefinition(
    name="propose_notification_test",
    description="生成向某个通知渠道发送测试消息的提案。",
    parameters=[
        ToolParameter(name="channel", type="string", description="通知渠道名", required=True),
        ToolParameter(name="title", type="string", description="测试标题", required=False),
        ToolParameter(name="content", type="string", description="测试内容", required=False),
    ],
    handler=_handle_propose_notification_test,
    category="action",
    policy=_PROPOSAL_POLICY,
)
```

把五个工具加入 `ALL_ACTION_TOOLS`。

- [ ] **Step 4: 运行测试**

Run: `uv run python -m pytest tests/test_agent_write_proposals.py tests/test_agent_config_tool_security.py -q`
Expected: PASS

- [ ] **Step 5: Commit（等待用户明确确认）**

```bash
git add src/agent/tools/action_tools.py tests/test_agent_write_proposals.py tests/test_agent_config_tool_security.py
git commit -m "feat(agent): add history, intel, config, scheduler and notification proposals"
```

---

## Task 14: 前端提案分发与缺失客户端

**Files:**
- Modify: `apps/dsa-web/src/types/actionProposal.ts`
- Modify: `apps/dsa-web/src/utils/actionProposal.ts`
- Create: `apps/dsa-web/src/api/intelligence.ts`
- Modify: `apps/dsa-web/src/api/portfolio.ts`
- Modify: `apps/dsa-web/src/api/utils.ts`
- Test: `apps/dsa-web/src/utils/__tests__/actionProposal.test.ts`
- Test: `apps/dsa-web/src/api/__tests__/intelligence.test.ts`
- Test: `apps/dsa-web/src/api/__tests__/utils.test.ts`

**Interfaces:**
- Consumes: Task 12/13 的 kind 集合
- Produces: `applyActionProposal` 对全部 kind 的分发；`portfolioApi.updateAccount`；`intelligenceApi.createSource/createFromTemplate/createDefaults/listSources/listItems`

- [ ] **Step 1: 写失败测试**

```typescript
// apps/dsa-web/src/utils/__tests__/actionProposal.test.ts 追加
import { vi } from 'vitest';
import { applyActionProposal } from '../actionProposal';
import { alertsApi } from '../../api/alerts';
import { portfolioApi } from '../../api/portfolio';
import { decisionSignalsApi } from '../../api/decisionSignals';
import { paperApi } from '../../api/paper';
import { historyApi } from '../../api/history';

vi.mock('../../api/alerts', () => ({ alertsApi: { createRule: vi.fn(), updateRule: vi.fn(), deleteRule: vi.fn() } }));
vi.mock('../../api/portfolio', () => ({
  portfolioApi: {
    createTrade: vi.fn(), createCashLedger: vi.fn(), deleteCashLedger: vi.fn(),
    createCorporateAction: vi.fn(), deleteCorporateAction: vi.fn(),
    createAccount: vi.fn(), updateAccount: vi.fn(), deleteAccount: vi.fn(),
  },
}));
vi.mock('../../api/decisionSignals', () => ({
  decisionSignalsApi: { create: vi.fn(), updateStatus: vi.fn(), putFeedback: vi.fn(), reassess: vi.fn() },
}));
vi.mock('../../api/paper', () => ({ paperApi: { reset: vi.fn() } }));
vi.mock('../../api/history', () => ({ historyApi: { deleteRecords: vi.fn(), deleteByCode: vi.fn() } }));

it('dispatches alert_update to updateRule', async () => {
  vi.mocked(alertsApi.updateRule).mockResolvedValue({} as never);
  await applyActionProposal({
    kind: 'alert_update',
    summary: 'x',
    proposal: { rule_id: 7, name: 'n', target_scope: 'single_symbol', target: '600519', alert_type: 'price_above', parameters: { price: 1 }, severity: 'info' },
  });
  expect(portfolioApi.createTrade).not.toHaveBeenCalled();
  expect(alertsApi.updateRule).toHaveBeenCalledWith(7, expect.objectContaining({ name: 'n' }));
});

it('dispatches paper_reset to paperApi.reset', async () => {
  vi.mocked(paperApi.reset).mockResolvedValue({} as never);
  await applyActionProposal({ kind: 'paper_reset', summary: 'x', proposal: { initial_capital: 100000 } });
  expect(paperApi.reset).toHaveBeenCalledWith(100000);
});

it('dispatches history_delete_records', async () => {
  vi.mocked(historyApi.deleteRecords).mockResolvedValue({ deleted: 2 });
  await applyActionProposal({ kind: 'history_delete_records', summary: 'x', proposal: { record_ids: [1, 2] } });
  expect(historyApi.deleteRecords).toHaveBeenCalledWith([1, 2]);
});
```

`apps/dsa-web/src/api/__tests__/intelligence.test.ts`：

```typescript
import { vi } from 'vitest';
import { intelligenceApi } from '../intelligence';
import { apiClient } from '../client';

vi.mock('../client', () => ({ apiClient: { get: vi.fn(), post: vi.fn() } }));

it('creates a source with a snake_case payload', async () => {
  vi.mocked(apiClient.post).mockResolvedValue({ data: { id: 1 } });
  await intelligenceApi.createSource({ name: 'n', sourceType: 'rss', url: 'https://x' } as never);
  expect(apiClient.post).toHaveBeenCalledWith('/api/v1/intelligence/sources', expect.objectContaining({ source_type: 'rss' }));
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd apps/dsa-web && npx vitest run src/utils/__tests__/actionProposal.test.ts src/api/__tests__/intelligence.test.ts`
Expected: FAIL，`updateAccount` / intelligence 模块不存在

- [ ] **Step 3: 实现**

`types/actionProposal.ts` 的 `ACTION_PROPOSAL_KINDS` 替换为：

```typescript
export const ACTION_PROPOSAL_KINDS = [
  'alert_create',
  'alert_update',
  'alert_delete',
  'portfolio_trade',
  'portfolio_cash_create',
  'portfolio_cash_delete',
  'portfolio_corporate_create',
  'portfolio_corporate_delete',
  'portfolio_account_create',
  'portfolio_account_update',
  'portfolio_account_deactivate',
  'signal_create',
  'signal_status',
  'signal_feedback',
  'signal_reassess',
  'paper_reset',
  'history_delete_records',
  'history_delete_by_code',
  'intelligence_source_create',
  'intelligence_defaults_create',
  'config_update',
  'scheduler_run',
  'notification_test',
] as const;
```

`utils/actionProposal.ts` 的 switch 补齐（保留原有 `alert`/`watchlist_*` 分支会触发穷尽性错误，直接删除它们）：

```typescript
    case 'alert_create':
      await alertsApi.createRule(toCamelCase<AlertRuleCreateRequest>(proposal.proposal));
      return;
    case 'alert_update': {
      const { ruleId, ...rest } = toCamelCase<AlertRuleCreateRequest & { ruleId: number }>(
        proposal.proposal,
      );
      await alertsApi.updateRule(ruleId, rest);
      return;
    }
    case 'alert_delete':
      await alertsApi.deleteRule(Number((proposal.proposal as { rule_id: number }).rule_id));
      return;
    case 'portfolio_cash_create':
      await portfolioApi.createCashLedger(toCamelCase(proposal.proposal));
      return;
    case 'portfolio_cash_delete':
      await portfolioApi.deleteCashLedger(Number((proposal.proposal as { entry_id: number }).entry_id));
      return;
    case 'portfolio_corporate_create':
      await portfolioApi.createCorporateAction(toCamelCase(proposal.proposal));
      return;
    case 'portfolio_corporate_delete':
      await portfolioApi.deleteCorporateAction(Number((proposal.proposal as { action_id: number }).action_id));
      return;
    case 'portfolio_account_create':
      await portfolioApi.createAccount(toCamelCase(proposal.proposal));
      return;
    case 'portfolio_account_update': {
      const { accountId, ...rest } = toCamelCase<{ accountId: number } & Record<string, unknown>>(
        proposal.proposal,
      );
      await portfolioApi.updateAccount(accountId, rest);
      return;
    }
    case 'portfolio_account_deactivate':
      await portfolioApi.deleteAccount(Number((proposal.proposal as { account_id: number }).account_id));
      return;
    case 'signal_create':
      await decisionSignalsApi.create(toCamelCase(proposal.proposal));
      return;
    case 'signal_status': {
      const payload = toCamelCase<{ signalId: number; status: string; metadata?: unknown }>(
        proposal.proposal,
      );
      await decisionSignalsApi.updateStatus(payload.signalId, {
        status: payload.status as never,
        metadata: payload.metadata,
      });
      return;
    }
    case 'signal_feedback': {
      const payload = toCamelCase<{
        signalId: number; feedbackValue: string; reasonCode?: string; note?: string;
      }>(proposal.proposal);
      await decisionSignalsApi.putFeedback(payload.signalId, {
        feedbackValue: payload.feedbackValue as never,
        reasonCode: payload.reasonCode,
        note: payload.note,
      });
      return;
    }
    case 'signal_reassess':
      await decisionSignalsApi.reassess(toCamelCase(proposal.proposal));
      return;
    case 'paper_reset': {
      const payload = toCamelCase<{ initialCapital?: number }>(proposal.proposal);
      await paperApi.reset(payload.initialCapital);
      return;
    }
    case 'history_delete_records':
      await historyApi.deleteRecords(
        (proposal.proposal as { record_ids: number[] }).record_ids,
      );
      return;
    case 'history_delete_by_code':
      await historyApi.deleteByCode((proposal.proposal as { stock_code: string }).stock_code);
      return;
    case 'intelligence_source_create':
      await intelligenceApi.createSource(toCamelCase(proposal.proposal));
      return;
    case 'intelligence_defaults_create':
      await intelligenceApi.createDefaults();
      return;
    case 'config_update':
      await systemConfigApi.update(toCamelCase(proposal.proposal));
      return;
    case 'scheduler_run':
      await systemConfigApi.runSchedulerNow();
      return;
    case 'notification_test':
      await systemConfigApi.testNotificationChannel(
        toCamelCase<TestNotificationChannelRequest>(proposal.proposal),
      );
      return;
```

`api/utils.ts` 当前只有 `toCamelCase`，没有反向转换。追加一个递归 `toSnakeCase`（浏览器发请求前的统一边界，避免每个分支手写 snake_case）：

```typescript
/** 将 camelCase 对象键递归转换为 snake_case，用于向 FastAPI 发送请求体。 */
export function toSnakeCase(data: unknown): unknown {
  if (data === null || data === undefined || typeof data !== 'object') {
    return data;
  }
  if (Array.isArray(data)) {
    return data.map(toSnakeCase);
  }
  return Object.fromEntries(
    Object.entries(data as Record<string, unknown>).map(([key, value]) => [
      key.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`),
      toSnakeCase(value),
    ]),
  );
}
```

配套测试追加到 `apps/dsa-web/src/api/__tests__/utils.test.ts`（若无则新建）：

```typescript
import { describe, expect, it } from 'vitest';
import { toSnakeCase } from '../utils';

describe('toSnakeCase', () => {
  it('converts nested keys recursively', () => {
    expect(toSnakeCase({ ruleId: 1, nested: { baseCurrency: 'CNY' } })).toEqual({
      rule_id: 1,
      nested: { base_currency: 'CNY' },
    });
  });

  it('leaves arrays and primitives intact', () => {
    expect(toSnakeCase({ items: [{ listName: 'x' }] })).toEqual({
      items: [{ list_name: 'x' }],
    });
  });
});
```

`api/portfolio.ts` 增加：

```typescript
  async updateAccount(
    accountId: number,
    payload: Partial<PortfolioAccountCreateRequest> & { isActive?: boolean },
  ): Promise<PortfolioAccountItem> {
    const response = await apiClient.patch<Record<string, unknown>>(
      `/api/v1/portfolio/accounts/${accountId}`,
      toSnakeCase(payload),
    );
    return toPortfolioAccountItem(response.data);
  },
```

核对现有文件中的 `toSnakeCase` / `toPortfolioAccountItem` 名称后再使用；若名称不同，沿用该文件已有转换函数。

`api/intelligence.ts` 按 `alerts.ts` 的写法：

```typescript
import { apiClient } from './client';
import { toCamelCase, toSnakeCase } from './utils';

export interface IntelligenceSource {
  id: number;
  name: string;
  sourceType: string;
  enabled: boolean;
}

export const intelligenceApi = {
  async listSources(): Promise<IntelligenceSource[]> {
    const response = await apiClient.get<{ items: Record<string, unknown>[] }>(
      '/api/v1/intelligence/sources',
    );
    return response.data.items.map((item) => toCamelCase<IntelligenceSource>(item));
  },
  async createSource(payload: Record<string, unknown>): Promise<IntelligenceSource> {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/intelligence/sources',
      toSnakeCase(payload),
    );
    return toCamelCase<IntelligenceSource>(response.data);
  },
  async createFromTemplate(templateId: string, overrides: Record<string, unknown> = {}) {
    const response = await apiClient.post<Record<string, unknown>>(
      `/api/v1/intelligence/sources/templates/${templateId}`,
      toSnakeCase(overrides),
    );
    return toCamelCase<IntelligenceSource>(response.data);
  },
  async createDefaults(overrides: Record<string, unknown> = {}) {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/intelligence/sources/defaults',
      toSnakeCase(overrides),
    );
    return response.data;
  },
  async listItems(params: { sourceId?: number; page?: number; pageSize?: number } = {}) {
    const response = await apiClient.get<Record<string, unknown>>(
      '/api/v1/intelligence/items',
      { params: toSnakeCase(params) },
    );
    return response.data;
  },
};
```

- [ ] **Step 4: 运行测试**

Run: `cd apps/dsa-web && npx vitest run src/utils/__tests__/actionProposal.test.ts src/api/__tests__/intelligence.test.ts src/api/__tests__/utils.test.ts && npx tsc -b`
Expected: PASS

- [ ] **Step 5: Commit（等待用户明确确认）**

```bash
git add apps/dsa-web/src/types/actionProposal.ts apps/dsa-web/src/utils/actionProposal.ts apps/dsa-web/src/api/intelligence.ts apps/dsa-web/src/api/portfolio.ts apps/dsa-web/src/api/utils.ts apps/dsa-web/src/utils/__tests__/actionProposal.test.ts apps/dsa-web/src/api/__tests__/intelligence.test.ts apps/dsa-web/src/api/__tests__/utils.test.ts
git commit -m "feat(web): dispatch all site action proposals"
```

---

## Task 15: 系统提示、工具标签、审计日志与文档

**Files:**
- Modify: `src/agent/executor.py:414-462`
- Modify: `src/agent/runner.py:70-130, 700-734`
- Modify: `api/v1/endpoints/agent.py:33-120`
- Modify: `docs/CHANGELOG.md`
- Create: `docs/agent-chat-site-operations.md`
- Modify: `docs/INDEX.md`、`docs/INDEX_EN.md`、`docs/full-guide.md`、`docs/full-guide_EN.md`
- Test: `tests/test_agent_tool_label_parity.py`
- Test: `tests/test_agent_action_events.py`

**Interfaces:**
- Consumes: 全部工具名（Task 6-13）
- Produces: 中英提示词、显示名、审计日志、用户文档

- [ ] **Step 1: 补审计日志测试**

```python
# tests/test_agent_action_events.py 追加
import logging


def test_direct_execution_is_audited(caplog):
    registry = _registry("direct", False)
    result = json.dumps({
        "kind": "fx_refresh", "summary": "已刷新汇率",
        "result": {"updated": 3}, "undo_hint": "再次刷新",
    })
    with caplog.at_level(logging.INFO, logger="src.agent.runner"):
        _maybe_emit_action_event(_ToolCall("demo"), result, lambda _: None, 1, registry)
    assert any("agent_action=direct_executed" in record.message for record in caplog.records)
```

Run: `uv run python -m pytest tests/test_agent_action_events.py -q -k audited`
Expected: FAIL

实现：在 `runner.py` 的 `direct` 分支发事件前加：

```python
        logger.info(
            "[AgentAction] agent_action=direct_executed kind=%s summary=%s",
            kind,
            summary,
        )
```

在 `proposal` 与 `task` 分支分别加：

```python
        logger.info("[AgentAction] agent_action=proposal_emitted kind=%s", kind)
        logger.info("[AgentAction] agent_action=task_submitted kind=%s task_id=%s", kind, task_id)
```

Run: `uv run python -m pytest tests/test_agent_action_events.py -q`
Expected: PASS

- [ ] **Step 2: 更新系统提示（两处相同规则块）**

`src/agent/executor.py` 中 `CHAT_SYSTEM_PROMPT` 与相邻的 `SYSTEM_PROMPT` 都要把规则 6-10 替换为：

```text
6. **低风险操作直接执行** — 以下操作可以**直接调用**工具并立即生效，不需要用户确认：`manage_watchlist`（加/移出自选）、`set_alert_rule_enabled`（启停告警规则）、`dry_run_alert_rule`（测试告警）、`refresh_fx_rates`、`refresh_paper_valuation`、`test_intelligence_source`、`test_llm_channel`。执行后如实说明做了什么以及如何撤销。
7. **长任务直接提交** — 分析、大盘复盘、回测、选股、情报抓取、基金复盘评估、信号后验、模拟盘回填一律通过 `start_*` / `run_*` / `backfill_*` 任务工具提交到后台，然后告诉用户任务已提交并可用任务卡片查看进度；**不要**在主对话里轮询等待任务完成。
8. **高风险写操作必须走提案** — 以下操作**必须**调用对应 `propose_*` 工具生成卡片，由用户确认后由 Web 端执行：告警规则增删改（`propose_alert_rule_change`）、持仓交易/资金流水/公司行动/账户变更、决策信号创建/状态/反馈/重算、模拟盘重置、分析历史删除、情报源创建、系统配置修改、立即调度、通知渠道测试。**绝不在用户确认前调用任何写接口**，也不得声称只能查询而拒绝发起提案。
9. **敏感字段永不触碰** — 任何 API Key、Token、密码、Cookie 既不能作为工具参数，也不能出现在回答里。`propose_config_update` 拒绝敏感字段时，应引导用户到设置页手动修改。认证、登录、会话相关操作一律不代做。
10. **先读后写** — 修改或删除前先用 `get_*_view` 读取真实数据确认 ID 与当前状态；找不到目标时如实说明，不得猜测 ID。
11. **自选列表名** — 用户点名命名自选列表时才传 `list_name`；列表不存在时如实报错并列出可用列表，不得创建新列表。
12. **图中股票** — 用户提供图片且消息里已有代码时，只有明确表达「加入自选/关注」意图才调用 `manage_watchlist`；仅出现代码不得主动操作。
```

保留原规则 1-5 与 `{skills_section}{language_section}` 占位符。

- [ ] **Step 3: 更新工具显示名**

`src/agent/runner.py::_THINKING_TOOL_LABELS` 与 `api/v1/endpoints/agent.py::TOOL_DISPLAY_NAMES` 同步增加（两份内容必须一致）：

```python
    "get_history_view": "读取分析历史",
    "get_alert_view": "读取告警视图",
    "get_analysis_task_view": "读取分析任务",
    "get_backtest_view": "读取回测统计",
    "get_screening_view": "读取选股视图",
    "get_signal_view": "读取决策信号",
    "get_fund_review_view": "读取基金复盘",
    "get_paper_view": "读取模拟盘",
    "get_portfolio_ledger_view": "读取持仓账本",
    "get_intelligence_view": "读取情报视图",
    "get_config_view": "读取系统配置",
    "get_scheduler_view": "读取调度状态",
    "manage_watchlist": "管理自选",
    "set_alert_rule_enabled": "启停告警规则",
    "dry_run_alert_rule": "测试告警规则",
    "refresh_fx_rates": "刷新汇率",
    "refresh_paper_valuation": "刷新模拟盘估值",
    "test_intelligence_source": "测试情报源",
    "test_llm_channel": "测试 LLM 渠道",
    "start_stock_analysis": "提交个股分析",
    "start_market_review": "提交大盘复盘",
    "run_backtest": "提交回测",
    "start_screening": "提交选股",
    "fetch_intelligence": "抓取情报",
    "backfill_fund_nav_reviews": "补录基金复盘",
    "run_fund_nav_reviews": "评估基金复盘",
    "run_signal_outcomes": "评估信号后验",
    "backfill_paper_signals": "回填模拟盘信号",
    "propose_portfolio_trade": "持仓录入提案",
    "propose_portfolio_ledger_entry": "资金/公司行动提案",
    "propose_portfolio_account_change": "持仓账户变更提案",
    "propose_alert_rule_change": "告警规则提案",
    "propose_signal_change": "信号变更提案",
    "propose_paper_reset": "模拟盘重置提案",
    "propose_history_deletion": "历史删除提案",
    "propose_intelligence_source_change": "情报源变更提案",
    "propose_config_update": "配置修改提案",
    "propose_scheduler_run": "立即调度提案",
    "propose_notification_test": "通知测试提案",
```

删除 `"propose_alert"` 与 `"propose_watchlist_change"` 两条。Run: `uv run python -m pytest tests/test_agent_tool_label_parity.py -q`，Expected: PASS。若测试还要求注册表内每个工具都有标签，以报错列表为准补齐。

- [ ] **Step 4: 写用户文档**

`docs/agent-chat-site-operations.md`（中文）：

```markdown
# 问股全站操作助手

问股页面（`/chat`）在原有问答与分析能力之上，可以读取全站数据、直接执行低风险可逆操作、
提交长任务，并对高风险写操作生成确认卡片。开关：`AGENT_CHAT_SITE_OPERATIONS_ENABLED`
（默认 `true`）。关闭后只保留原有只读分析工具。

## 执行语义

| 模式 | 含义 | 用户看到 |
| --- | --- | --- |
| `none` | 只读 | 无卡片 |
| `direct` | 立即执行 | 「已执行」卡片 + 撤销方式 |
| `task` | 提交后台任务 | 任务卡片 + 状态轮询 |
| `proposal` | 生成确认卡片 | 确认/取消，确认后由浏览器调用既有 REST 写接口 |

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

- 每次直接执行、提案、任务提交都会写入 `[AgentAction]` 结构化日志。
- 直接执行在卡片上展示撤销方式；提案在用户确认前不产生任何副作用。
- 关闭 `AGENT_CHAT_SITE_OPERATIONS_ENABLED` 可立即收回全部站点读写工具，原有问答不受影响。
```

`docs/INDEX.md` 与 `docs/INDEX_EN.md` 在对应分类下加一行链接；`docs/full-guide.md` 与
`docs/full-guide_EN.md` 在问股章节后加一段摘要并链接到该文档。

- [ ] **Step 5: CHANGELOG**

`docs/CHANGELOG.md` 的 `[Unreleased]` 末尾追加扁平条目（不加标题）：

```markdown
- [新功能] 问股助手升级为全站操作助手：新增 11 个只读视图工具、7 个低风险直执行工具、9 个后台任务工具和 10 个写操作提案工具；新增「已执行」「后台任务」卡片，基金净值复盘与信号后验可按无条数上限后台评估；新增 `AGENT_CHAT_SITE_OPERATIONS_ENABLED` 总开关。详见 `docs/agent-chat-site-operations.md`
- [改进] 告警提案工具更名为 `propose_alert_rule_change` 并支持增删改；自选增删改为低风险直执行 `manage_watchlist`
```

- [ ] **Step 6: 运行测试**

Run: `uv run python -m pytest tests/test_agent_tool_label_parity.py tests/test_agent_action_events.py tests/test_agent_factory.py -q`
Expected: PASS

- [ ] **Step 7: Commit（等待用户明确确认）**

```bash
git add src/agent/executor.py src/agent/runner.py api/v1/endpoints/agent.py docs/CHANGELOG.md docs/agent-chat-site-operations.md docs/INDEX.md docs/INDEX_EN.md docs/full-guide.md docs/full-guide_EN.md tests/test_agent_action_events.py
git commit -m "docs(agent): document site operations and update prompts"
```

---

## Task 16: 全量验证与交付说明

**Files:**
- 不新增文件；修复前 15 个任务暴露的失败

- [ ] **Step 1: 后端编译与定向测试**

```bash
uv run python -m py_compile \
  src/agent/tools/registry.py src/agent/action_contract.py src/agent/runner.py \
  src/agent/factory.py src/agent/tools/site_read_tools.py \
  src/agent/tools/site_action_tools.py src/agent/tools/site_task_tools.py \
  src/agent/tools/action_tools.py src/agent/tools/alert_tools.py \
  src/services/site_task_submitter.py src/services/runtime_scheduler.py
uv run python -m pytest \
  tests/test_agent_tool_policy.py tests/test_agent_action_events.py \
  tests/test_agent_site_read_tools.py tests/test_agent_site_action_tools.py \
  tests/test_agent_site_task_tools.py tests/test_agent_write_proposals.py \
  tests/test_agent_config_tool_security.py tests/test_site_task_submitter.py \
  tests/test_runtime_scheduler_singleton.py tests/test_agent_tool_label_parity.py -q
```

Expected: 全部 PASS。

- [ ] **Step 2: CI gate**

```bash
./scripts/ci_gate.sh
```

Expected: PASS。若受本地 `.env` 影响出现配置/调度类失败，先按
`env-ci-gate-config-failures` 记录的方式复跑 `offline-tests`，确认是否为既有环境问题。

- [ ] **Step 3: 前端门禁**

```bash
cd apps/dsa-web
npx tsc -b
npm run lint
npx vitest run src/types/__tests__/agentAction.test.ts \
  src/utils/__tests__/actionProposal.test.ts \
  src/api/__tests__/intelligence.test.ts \
  src/stores/__tests__/agentChatStore.test.ts \
  src/pages/__tests__/ChatPage.test.tsx tests/ui_governance.test.ts
npm run build
```

Expected: PASS。

- [ ] **Step 4: 手工冒烟（本地，`--serve-only`）**

> 使用 `uv run python main.py --serve-only`，避免 `SCHEDULE_RUN_IMMEDIATELY` 触发全量分析。

1. 问「现在自选里有哪些股票」→ 触发 `get_config_view` / `get_history_view`，无卡片。
2. 问「把 600519 加入自选」→ 出现「已执行」卡片；刷新设置页确认已写入。
3. 问「帮我分析 600519」→ 出现后台任务卡片，状态从 pending 变 completed。
4. 问「删除 600519 的某条历史」→ 出现确认卡片；取消后无副作用，确认后历史消失。
5. 问「把 API Key 改成 xxx」→ 助手拒绝并提示设置页。
6. 设置 `AGENT_CHAT_SITE_OPERATIONS_ENABLED=false` 重启 → 「把 600519 加入自选」不再有直执行能力。

- [ ] **Step 5: 交付说明**

按 AGENTS.md §9 输出：

- 改了什么
- 为什么这么改
- 验证情况（含 CI / 手工）
- 未验证项（例如未覆盖真实通知渠道、未验证 Codex 只读后端）
- 风险点（直执行扩大 AI 权限面、提案 kind 与 Web 客户端必须同步）
- 回滚方式（关闭开关；或整体 revert 本系列提交；无数据库迁移）

- [ ] **Step 6: Commit（仅在用户确认后）**

如果前 15 个任务已分步提交，本任务通常只需提交 Step 1-3 产生的修复；否则请用户确认是否提交。

---

## Self-Review 记录

**Spec 覆盖检查：**

| Spec 章节 | 覆盖任务 |
| --- | --- |
| §3 ToolPolicy 扩展、默认拒绝、事件泛化、审计、开关 | Task 1、2、4、15 |
| §4.1 11 个只读工具 | Task 6-8（实际 12 个，含 `get_scheduler_view`；spec 的 `get_config_view` 已覆盖调度状态需求） |
| §4.2 7 个直执行工具 | Task 9 |
| §4.3 9 个任务工具 | Task 10-11 |
| §4.4 提案工具 | Task 12-13、14 |
| §4.5 排除项 | Task 13（敏感配置）、Task 15 文档 |
| §5 执行语义 | Task 2、3、5 |
| §6 分阶段 | Task 1-4 基础设施 → 6-8 只读 → 9 直执行 → 10-11 任务 → 12-14 提案与前端 → 15-16 文档验证 |
| §7 测试 | 每个任务的失败测试 + Task 16 全量 |
| §8 风险/回滚 | Task 15 文档、Task 16 交付说明 |
| §9 兼容性 | 提案 kind 扩展、旧工具删除在 Task 9/14/15 显式处理 |

**与 spec 的主动偏差（需在 PR 描述中说明）：**

1. 情报源**更新/删除**未实现：`api/v1/endpoints/intelligence.py` 当前只有创建、模板、默认源、试抓取、抓取、列表，没有 update/delete 端点；按「不新增 REST 契约」约束排除。
2. 只读工具从 11 个变为 12 个：新增 `get_scheduler_view`，因为调度状态原先只存在于 `app.state`，不提供访问器则 `get_config_view` 无法回答「下次什么时候跑」。
3. `propose_alert` 改名为 `propose_alert_rule_change`，`propose_watchlist_change` 删除并由 `manage_watchlist` 直执行替代，属于已确认的「低风险直执行」口径。

**占位符扫描：** 计划中不含 TBD/TODO；所有 handler、类型、事件字段和验证命令均已给出。需要实现者先核对的少数现有符号（`TaskInfo.to_dict`、`toSnakeCase`、`ScreeningService` 构造参数）已在对应步骤内写明核对命令。

**类型一致性：** `execution_mode` 取值、三类事件字段（`kind/summary/result/undo_hint`、`task_id/status_endpoint`）、前端 `ActionExecuted/ActionTask` 字段、提案 kind 列表在 Task 1/2/3/12/13/14 之间一致。
