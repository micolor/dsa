"""Tests for the ``propose_alert_rule_change`` agent tool and its SSE emission hook."""

import json
from types import SimpleNamespace

from src.agent.runner import _maybe_emit_action_event
from src.agent.tools.alert_tools import (
    _handle_propose_alert_rule_change,
    propose_alert_rule_change_tool,
)
from src.agent.factory import get_tool_registry


def _invoke(name, args):
    # Handlers return the envelope dict; the runner serializes it for the LLM.
    tool = get_tool_registry().resolve(name)
    assert tool is not None, f"{name} 未注册"
    return tool.handler(**args)


def test_tool_registered_with_proposal_policy():
    registry = get_tool_registry()
    tool = registry.resolve("propose_alert_rule_change")
    assert tool is not None
    assert tool.category == "action"
    assert tool.policy.read_only is False
    assert tool.policy.execution_mode == "proposal"
    assert tool.policy.cancellation_safe is False


def test_alert_rule_change_create_envelope():
    payload = _invoke(
        "propose_alert_rule_change",
        {
            "action": "create",
            "target": "600519",
            "alert_type": "price_cross",
            "parameters": {"price": 1800},
        },
    )
    assert payload["kind"] == "alert_create"
    assert payload["proposal"]["target"] == "600519"
    assert payload["proposal"]["alert_type"] == "price_cross"
    assert payload["proposal"]["parameters"] == {"direction": "above", "price": 1800.0}


def test_alert_rule_change_delete_requires_rule_id():
    payload = _invoke("propose_alert_rule_change", {"action": "delete"})
    assert payload["error"]


def test_alert_rule_change_delete_envelope():
    payload = _invoke("propose_alert_rule_change", {"action": "delete", "rule_id": 7})
    assert payload["kind"] == "alert_delete"
    assert payload["proposal"] == {"rule_id": 7}


def test_alert_rule_change_update_envelope():
    payload = _invoke(
        "propose_alert_rule_change",
        {
            "action": "update",
            "rule_id": 5,
            "target": "AAPL",
            "alert_type": "volume_spike",
            "parameters": {"multiplier": 2},
            "severity": "warning",
        },
    )
    assert payload["kind"] == "alert_update"
    assert payload["proposal"]["rule_id"] == 5
    assert payload["proposal"]["severity"] == "warning"
    assert payload["summary"].startswith("更新告警规则")


def test_alert_rule_change_update_requires_rule_id():
    payload = _invoke(
        "propose_alert_rule_change",
        {"action": "update", "target": "AAPL", "alert_type": "volume_spike", "parameters": {"multiplier": 2}},
    )
    assert payload["error"]


def test_alert_rule_change_rejects_unknown_action():
    payload = _invoke("propose_alert_rule_change", {"action": "toggle"})
    assert "action" in payload["error"]


def test_propose_price_cross_valid():
    result = _handle_propose_alert_rule_change(
        action="create", target="600519", alert_type="price_cross", parameters={"price": 1800}
    )
    assert "error" not in result
    proposal = result["proposal"]
    assert proposal["target_scope"] == "single_symbol"
    assert proposal["target"] == "600519"
    assert proposal["alert_type"] == "price_cross"
    assert proposal["parameters"] == {"direction": "above", "price": 1800.0}
    assert proposal["severity"] == "info"
    assert result["kind"] == "alert_create"
    assert result["summary"] == "「600519」价格上穿 ¥1800"


def test_propose_technical_type_normalizes_params():
    result = _handle_propose_alert_rule_change(
        action="create",
        target="600519",
        alert_type="rsi_threshold",
        parameters={"direction": "above", "period": 12, "threshold": 70},
    )
    assert "error" not in result
    assert result["proposal"]["parameters"] == {"direction": "above", "period": 12, "threshold": 70.0}
    assert result["summary"] == "「600519」RSI(12) 高于 70"


def test_propose_default_name_and_reason():
    result = _handle_propose_alert_rule_change(
        action="create",
        target="AAPL",
        alert_type="volume_spike",
        parameters={"multiplier": 2},
        reason="放量突破",
    )
    assert "error" not in result
    assert result["proposal"]["name"] == "AAPL volume spike 2.0x"
    assert result["summary"].endswith("（放量突破）")


def test_reason_is_length_bounded():
    """reason 会流进 summary → SSE 事件 → 确认卡片，模型不能往里塞几 KB 文本。

    上限与 `action_tools` 的提案工具**共用同一份**帮助函数，这里断言实际截断结果，
    而不是断言它们「应该一样」。
    """
    from src.agent.tools.action_tools import _REASON_LIMIT

    result = _handle_propose_alert_rule_change(
        action="create",
        target="AAPL",
        alert_type="volume_spike",
        parameters={"multiplier": 2},
        reason="很" * 5000,
    )
    assert "error" not in result
    assert result["summary"].endswith("（" + "很" * _REASON_LIMIT + "）")
    assert len(result["summary"]) < 400


def test_propose_invalid_type_returns_error():
    result = _handle_propose_alert_rule_change(
        action="create", target="600519", alert_type="bogus", parameters={}
    )
    assert result["error"]


def test_propose_invalid_params_returns_error():
    result = _handle_propose_alert_rule_change(
        action="create", target="600519", alert_type="price_cross", parameters={"price": -5}
    )
    assert "price must be > 0" in result["error"]


def test_propose_empty_target_returns_error():
    result = _handle_propose_alert_rule_change(
        action="create", target="   ", alert_type="price_cross", parameters={"price": 1}
    )
    assert result["error"]


def test_maybe_emit_action_event_emits_event_and_rewrites_result():
    events = []
    result = _handle_propose_alert_rule_change(
        action="create", target="600519", alert_type="price_cross", parameters={"price": 1800}
    )
    tc = SimpleNamespace(name="propose_alert_rule_change")
    out = _maybe_emit_action_event(
        tc, json.dumps(result, ensure_ascii=False), events.append, step=3,
        tool_registry=get_tool_registry(),
    )

    assert len(events) == 1
    event = events[0]
    assert event["type"] == "action_proposal"
    assert event["kind"] == "alert_create"
    assert event["summary"] == result["summary"]
    assert event["proposal"] == result["proposal"]
    # LLM-facing result is rewritten to a short note, not the raw proposal JSON.
    assert json.loads(out) == {"message": result["summary"]}


def test_maybe_emit_action_event_ignores_non_propose_tool():
    events = []
    tc = SimpleNamespace(name="get_realtime_quote")
    out = _maybe_emit_action_event(
        tc, '{"quote": 1}', events.append, step=3, tool_registry=get_tool_registry()
    )
    assert out == '{"quote": 1}'
    assert events == []


def test_maybe_emit_action_event_ignores_error_result():
    events = []
    tc = SimpleNamespace(name="propose_alert_rule_change")
    out = _maybe_emit_action_event(
        tc, json.dumps({"error": "x"}), events.append, step=3,
        tool_registry=get_tool_registry(),
    )
    assert json.loads(out) == {"error": "x"}
    assert events == []


def test_tool_definition_name_and_policy():
    assert propose_alert_rule_change_tool.name == "propose_alert_rule_change"
    assert propose_alert_rule_change_tool.policy.execution_mode == "proposal"
