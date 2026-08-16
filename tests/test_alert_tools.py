"""Tests for the ``propose_alert`` agent tool and its SSE emission hook."""

import json
from types import SimpleNamespace

from src.agent.runner import _maybe_emit_alert_proposal
from src.agent.tools.alert_tools import _handle_propose_alert, propose_alert_tool
from src.agent.factory import get_tool_registry


def test_tool_registered_and_policy_read_only():
    registry = get_tool_registry()
    tool = registry.resolve("propose_alert")
    assert tool is not None
    assert tool.category == "action"
    assert tool.policy.read_only is True


def test_propose_price_cross_valid():
    result = _handle_propose_alert(target="600519", alert_type="price_cross", parameters={"price": 1800})
    assert "error" not in result
    proposal = result["proposal"]
    assert proposal["target_scope"] == "single_symbol"
    assert proposal["target"] == "600519"
    assert proposal["alert_type"] == "price_cross"
    assert proposal["parameters"] == {"direction": "above", "price": 1800.0}
    assert proposal["severity"] == "info"
    assert result["summary"] == "「600519」价格上穿 ¥1800"


def test_propose_technical_type_normalizes_params():
    result = _handle_propose_alert(
        target="600519",
        alert_type="rsi_threshold",
        parameters={"direction": "above", "period": 12, "threshold": 70},
    )
    assert "error" not in result
    assert result["proposal"]["parameters"] == {"direction": "above", "period": 12, "threshold": 70.0}
    assert result["summary"] == "「600519」RSI(12) 高于 70"


def test_propose_default_name_and_reason():
    result = _handle_propose_alert(
        target="AAPL",
        alert_type="volume_spike",
        parameters={"multiplier": 2},
        reason="放量突破",
    )
    assert "error" not in result
    assert result["proposal"]["name"] == "AAPL volume spike 2.0x"
    assert result["summary"].endswith("（放量突破）")


def test_propose_invalid_type_returns_error():
    result = _handle_propose_alert(target="600519", alert_type="bogus", parameters={})
    assert result["error"]


def test_propose_invalid_params_returns_error():
    result = _handle_propose_alert(target="600519", alert_type="price_cross", parameters={"price": -5})
    assert "price must be > 0" in result["error"]


def test_propose_empty_target_returns_error():
    result = _handle_propose_alert(target="   ", alert_type="price_cross", parameters={"price": 1})
    assert result["error"]


def test_maybe_emit_alert_proposal_emits_event_and_rewrites_result():
    events = []
    result = _handle_propose_alert(target="600519", alert_type="price_cross", parameters={"price": 1800})
    tc = SimpleNamespace(name="propose_alert")
    out = _maybe_emit_alert_proposal(tc, json.dumps(result, ensure_ascii=False), events.append, step=3)

    assert len(events) == 1
    event = events[0]
    assert event["type"] == "alert_proposal"
    assert event["summary"] == result["summary"]
    assert event["proposal"] == result["proposal"]
    # LLM-facing result is rewritten to a short note, not the raw proposal JSON.
    assert json.loads(out) == {"message": result["summary"]}


def test_maybe_emit_alert_proposal_ignores_non_propose_tool():
    events = []
    tc = SimpleNamespace(name="get_realtime_quote")
    out = _maybe_emit_alert_proposal(tc, '{"quote": 1}', events.append, step=3)
    assert out == '{"quote": 1}'
    assert events == []


def test_maybe_emit_alert_proposal_ignores_error_result():
    events = []
    tc = SimpleNamespace(name="propose_alert")
    out = _maybe_emit_alert_proposal(tc, json.dumps({"error": "x"}), events.append, step=3)
    assert json.loads(out) == {"error": "x"}
    assert events == []
