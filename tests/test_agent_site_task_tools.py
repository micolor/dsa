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


def test_start_market_review_never_forwards_send_notification(monkeypatch):
    calls = []

    def fake_submit(*, send_notification=False, region=None):
        calls.append({"send_notification": send_notification, "region": region})
        return FakeTask()

    monkeypatch.setattr(
        "src.agent.tools.site_task_tools.submit_market_review", fake_submit
    )
    # 模型仍按旧 schema 传入 send_notification=True：必须被吞掉，不能透传给 submitter。
    payload = json.loads(_registry().execute(
        "start_market_review",
        **{"region": "cn", "send_notification": True},
    ))
    assert payload["kind"] == "market_review"
    assert calls == [{"send_notification": False, "region": "cn"}]


def test_start_market_review_schema_does_not_declare_send_notification():
    tool = next(t for t in ALL_SITE_TASK_TOOLS if t.name == "start_market_review")
    names = {param.name for param in tool.parameters}
    assert "send_notification" not in names
    assert names == {"region"}


def test_run_fund_nav_reviews_has_no_limit_parameter():
    tool = next(t for t in ALL_SITE_TASK_TOOLS if t.name == "run_fund_nav_reviews")
    assert "limit" not in {param.name for param in tool.parameters}


def test_task_tools_declare_write_side_effect():
    for tool in ALL_SITE_TASK_TOOLS:
        assert tool.policy.side_effects == ["write"], tool.name


def test_backfill_paper_signals_resolves_default_account_id(monkeypatch):
    calls = []

    class FakePaperService:
        def get_or_create_account(self):
            # Mirrors PaperService._account_payload: the id key is account_id.
            return {
                "account_id": 7,
                "name": "default",
                "initial_capital": 100000.0,
                "cash": 100000.0,
                "status": "active",
                "snapshot": {},
            }

    def fake_submit(account, from_date, to_date=None):
        calls.append((account, from_date, to_date))
        return FakeTask()

    monkeypatch.setattr("src.services.paper_service.PaperService", FakePaperService)
    monkeypatch.setattr(
        "src.agent.tools.site_task_tools.submit_paper_backfill", fake_submit
    )
    payload = json.loads(_registry().execute(
        "backfill_paper_signals", **{"from_date": "2026-10-01"}
    ))
    assert payload["kind"] == "paper_backfill"
    assert calls[0][0] == 7
    assert calls[0][1].isoformat() == "2026-10-01"
