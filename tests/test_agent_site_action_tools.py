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


def test_dry_run_alert_rule_previews_without_claiming_delivery(monkeypatch):
    class FakeAlert:
        def test_rule(self, rule_id):
            return {
                "rule_id": rule_id,
                "status": "triggered",
                "triggered": True,
                "evaluated_count": 3,
                "triggered_count": 2,
                "degraded_count": 0,
                "skipped_count": 0,
                "target_results": [],
            }

    monkeypatch.setattr(
        "src.agent.tools.site_action_tools._alert_service", lambda: FakeAlert()
    )
    result = json.loads(_registry().execute(
        "dry_run_alert_rule", **{"rule_id": 7}
    ))
    assert result["kind"] == "alert_rule_test"
    # summary 必须区分「会触发」与「通知已投递」
    assert "2/3" in result["summary"]
    assert "会触发" in result["summary"]
    assert "未发送任何通知" in result["summary"]
    assert "测试通知" not in result["summary"]
    assert "已发送" not in result["summary"]
    # 聚合计数进入 result，并明确标记未验证投递
    assert result["result"]["evaluated_count"] == 3
    assert result["result"]["triggered_count"] == 2
    assert result["result"]["delivery_verified"] is False
    assert "不发送通知" in result["undo_hint"]


def test_refresh_paper_valuation_uses_default_account_id(monkeypatch):
    calls = []

    class FakePaper:
        def get_or_create_account(self):
            # 真实载荷键是 account_id（PaperService._account_payload），不是 id。
            return {"account_id": 7}

        def run_daily_valuation(self, **kwargs):
            calls.append(kwargs)
            return {"account_id": kwargs["account_id"], "as_of_date": "2026-10-18"}

    monkeypatch.setattr(
        "src.agent.tools.site_action_tools._paper_service", lambda: FakePaper()
    )
    result = json.loads(_registry().execute("refresh_paper_valuation"))
    assert "error" not in result
    assert result["kind"] == "paper_refresh"
    assert calls[0]["account_id"] == 7
    assert calls[0]["as_of_date"] is None
    assert calls[0]["force"] is False


def test_refresh_paper_valuation_service_error_is_not_reported_as_bad_input(monkeypatch):
    class FakePaper:
        def get_or_create_account(self):
            return {"account_id": 7}

        def run_daily_valuation(self, **kwargs):
            raise RuntimeError("database is locked")

    monkeypatch.setattr(
        "src.agent.tools.site_action_tools._paper_service", lambda: FakePaper()
    )
    try:
        _registry().execute("refresh_paper_valuation")
    except RuntimeError as exc:
        assert str(exc) == "database is locked"
    else:
        raise AssertionError("service error must propagate, not become an input error")


def test_dry_run_alert_rule_copies_do_not_claim_delivery():
    tool = next(t for t in ALL_SITE_ACTION_TOOLS if t.name == "dry_run_alert_rule")
    assert "发送测试通知" not in tool.description
    assert "不发送任何通知" in tool.description
    assert "验证" in tool.description
