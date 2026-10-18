import json
import logging
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


def test_blank_undo_hint_is_not_emitted():
    events = []
    result = json.dumps({
        "kind": "fx_refresh",
        "summary": "已刷新汇率",
        "result": {"updated": 3},
        "undo_hint": "   ",
    })
    rewritten = _maybe_emit_action_event(
        _ToolCall("demo"), result, events.append, 1, _registry("direct", False)
    )
    assert events == []
    assert rewritten == result


def test_unknown_kind_is_not_emitted():
    events = []
    result = json.dumps({"kind": "not_a_kind", "summary": "x", "task_id": "a"})
    rewritten = _maybe_emit_action_event(
        _ToolCall("demo"), result, events.append, 1, _registry("task", False)
    )
    assert events == []
    assert rewritten == result


def test_direct_execution_is_audited(caplog):
    registry = _registry("direct", False)
    result = json.dumps({
        "kind": "fx_refresh", "summary": "已刷新汇率",
        "result": {"updated": 3}, "undo_hint": "再次刷新",
    })
    with caplog.at_level(logging.INFO, logger="src.agent.runner"):
        _maybe_emit_action_event(_ToolCall("demo"), result, lambda _: None, 1, registry)
    assert any("agent_action=direct_executed" in record.message for record in caplog.records)


def test_proposal_emission_is_audited(caplog):
    registry = _registry("proposal", False)
    result = json.dumps({
        "kind": "alert_create", "summary": "已生成告警规则提案",
        "proposal": {"type": "alert_create"},
    })
    with caplog.at_level(logging.INFO, logger="src.agent.runner"):
        _maybe_emit_action_event(_ToolCall("demo"), result, lambda _: None, 1, registry)
    assert any("agent_action=proposal_emitted" in record.message for record in caplog.records)


def test_task_submission_is_audited(caplog):
    registry = _registry("task", False)
    result = json.dumps({
        "kind": "stock_analysis", "summary": "已提交 600519 分析",
        "task_id": "abc", "status_endpoint": "/api/v1/analysis/status/abc",
    })
    with caplog.at_level(logging.INFO, logger="src.agent.runner"):
        _maybe_emit_action_event(_ToolCall("demo"), result, lambda _: None, 1, registry)
    assert any("agent_action=task_submitted" in record.message for record in caplog.records)


def test_valid_action_is_audited_without_callback(caplog):
    registry = _registry("direct", False)
    result = json.dumps({
        "kind": "fx_refresh", "summary": "已刷新汇率",
        "result": {"updated": 3}, "undo_hint": "再次刷新",
    })
    with caplog.at_level(logging.INFO, logger="src.agent.runner"):
        rewritten = _maybe_emit_action_event(_ToolCall("demo"), result, None, 1, registry)
    # 没有回调时仍然校验并记录审计行；返回给模型的字符串保持不变。
    assert rewritten == json.dumps({"message": "已刷新汇率"}, ensure_ascii=False)
    lines = [r.message for r in caplog.records if "agent_action=" in r.message]
    assert len(lines) == 1
    assert "agent_action=direct_executed" in lines[0]
    assert "tool=demo" in lines[0]
    assert "kind=fx_refresh" in lines[0]
    assert "success=true" in lines[0]


def test_valid_task_submission_keeps_task_id_without_callback(caplog):
    registry = _registry("task", False)
    result = json.dumps({
        "kind": "stock_analysis", "summary": "已提交 600519 分析",
        "task_id": "abc", "status_endpoint": "/api/v1/analysis/status/abc",
    })
    with caplog.at_level(logging.INFO, logger="src.agent.runner"):
        _maybe_emit_action_event(_ToolCall("demo"), result, None, 1, registry)
    line = next(r.message for r in caplog.records if "agent_action=" in r.message)
    assert "agent_action=task_submitted" in line
    assert "tool=demo" in line
    assert "success=true" in line
    assert "task_id=abc" in line


def test_invalid_action_payload_is_audited_as_rejected(caplog):
    registry = _registry("direct", False)
    result = json.dumps({
        "kind": "fx_refresh", "summary": "已刷新汇率",
        "result": {"updated": 3}, "undo_hint": "   ",
    })
    with caplog.at_level(logging.INFO, logger="src.agent.runner"):
        rewritten = _maybe_emit_action_event(_ToolCall("demo"), result, None, 1, registry)
    assert rewritten == result
    lines = [r.message for r in caplog.records if "agent_action=" in r.message]
    assert len(lines) == 1
    assert "agent_action=action_rejected" in lines[0]
    assert "tool=demo" in lines[0]
    assert "kind=fx_refresh" in lines[0]
    assert "success=false" in lines[0]


def test_missing_kind_is_audited_with_dash(caplog):
    registry = _registry("task", False)
    result = json.dumps({"summary": "missing kind"})
    with caplog.at_level(logging.INFO, logger="src.agent.runner"):
        _maybe_emit_action_event(_ToolCall("demo"), result, None, 1, registry)
    line = next(r.message for r in caplog.records if "agent_action=" in r.message)
    assert "agent_action=action_rejected" in line
    assert "kind=-" in line
    assert "success=false" in line


def test_non_action_mode_tool_is_not_audited(caplog):
    registry = _registry("none", True)
    result = json.dumps({"kind": "fx_refresh", "summary": "x"})
    with caplog.at_level(logging.INFO, logger="src.agent.runner"):
        _maybe_emit_action_event(_ToolCall("demo"), result, None, 1, registry)
    assert not any("agent_action=" in r.message for r in caplog.records)
