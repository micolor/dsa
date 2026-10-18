import json

import pytest
from sqlalchemy import func, select

import src.agent.tools.site_read_tools as site_read_tools
from src.agent.tools.registry import ToolRegistry
from src.agent.tools.site_read_tools import ALL_SITE_READ_TOOLS


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in ALL_SITE_READ_TOOLS:
        registry.register(tool)
    return registry


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """Real sqlite DB so read-only handlers can be checked for hidden writes."""
    from src.config import Config
    from src.storage import DatabaseManager

    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    db_path = tmp_path / "site_read_tools.db"
    monkeypatch.setenv("ENV_FILE", str(env_path))
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    # Force the lazy service hooks to build against the isolated database.
    monkeypatch.setattr(site_read_tools, "_paper_service", None)
    monkeypatch.setattr(site_read_tools, "_portfolio_service", None)
    from src.services import portfolio_cache

    portfolio_cache.clear()
    Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()


def _row_counts(db):
    from src.storage import (
        PaperAccountRecord,
        PaperEquitySnapshotRecord,
        PaperPositionRecord,
        PaperSignalRecord,
        PaperTradeRecord,
        PortfolioAccount,
        PortfolioDailySnapshot,
        PortfolioPosition,
        PortfolioPositionLot,
        PortfolioTrade,
    )

    tables = {
        "paper_accounts": PaperAccountRecord,
        "paper_positions": PaperPositionRecord,
        "paper_trades": PaperTradeRecord,
        "paper_signals": PaperSignalRecord,
        "paper_snapshots": PaperEquitySnapshotRecord,
        "portfolio_accounts": PortfolioAccount,
        "portfolio_trades": PortfolioTrade,
        "portfolio_positions": PortfolioPosition,
        "portfolio_lots": PortfolioPositionLot,
        "portfolio_snapshots": PortfolioDailySnapshot,
    }
    with db.get_session() as session:
        return {
            name: session.execute(select(func.count()).select_from(model)).scalar() or 0
            for name, model in tables.items()
        }


def _paper_account_updated_at(db, account_id):
    from src.storage import PaperAccountRecord

    with db.get_session() as session:
        return session.execute(
            select(PaperAccountRecord.updated_at).where(PaperAccountRecord.id == account_id)
        ).scalar_one()


def test_read_tools_are_read_only_and_strictly_valid():
    registry = _registry()
    for name in (
        "get_history_view",
        "get_alert_view",
        "get_analysis_task_view",
        "get_backtest_view",
        "get_screening_view",
        "get_signal_view",
        "get_fund_review_view",
        "get_paper_view",
        "get_portfolio_ledger_view",
        "get_intelligence_view",
        "get_config_view",
        "get_scheduler_view",
    ):
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


def _backtest_double(calls):
    class FakeBacktest:
        def get_global_summary(self, **kwargs):
            calls.append(("global", kwargs))
            return {"scope": "overall"}

        def get_stock_summary(self, code, **kwargs):
            calls.append(("stock", code, kwargs))
            return {"scope": "stock", "code": code}

        def get_skill_summary(self, skill_id, **kwargs):
            calls.append(("skill", skill_id, kwargs))
            return {"scope": "skill", "skill_id": skill_id}

        def get_strategy_summary(self, strategy_id, **kwargs):
            calls.append(("strategy", strategy_id, kwargs))
            return {"scope": "strategy", "strategy_id": strategy_id}

        def get_recent_evaluations(self, **kwargs):
            calls.append(("recent", kwargs))
            return {"items": [], "total": 0}

    return FakeBacktest()


def test_get_backtest_view_dispatches_by_scope(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._backtest_service",
        lambda: _backtest_double(calls),
    )
    registry = _registry()
    registry.execute("get_backtest_view", **{"view": "summary", "scope": "stock", "stock_code": "600519"})
    assert calls[0][0] == "stock"
    assert calls[0][1] == "600519"

    registry.execute("get_backtest_view", **{"view": "summary", "scope": "skill", "skill_id": "trend"})
    registry.execute("get_backtest_view", **{"view": "summary", "scope": "strategy", "strategy_id": "core"})
    registry.execute("get_backtest_view", **{"view": "summary", "scope": "overall", "eval_window_days": 30})
    assert calls[1] == ("skill", "trend", {"eval_window_days": None})
    assert calls[2] == ("strategy", "core", {"eval_window_days": None})
    assert calls[3] == ("global", {"eval_window_days": 30})


def test_get_backtest_view_recent_forwards_filters_and_clamps(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._backtest_service",
        lambda: _backtest_double(calls),
    )
    payload = json.loads(_registry().execute("get_backtest_view", **{
        "view": "recent",
        "stock_code": "600519",
        "eval_window_days": 5,
        "limit": 500,
        "analysis_phase": "intraday",
    }))
    assert payload["items"] == []
    assert calls == [("recent", {
        "code": "600519",
        "eval_window_days": 5,
        "limit": 100,
        "analysis_phase": "intraday",
    })]


def test_get_backtest_view_rejects_invalid_arguments(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._backtest_service",
        lambda: _backtest_double(calls),
    )
    registry = _registry()
    payload = json.loads(registry.execute("get_backtest_view", **{"view": "summary", "scope": "stock"}))
    assert payload["error"] == "stock_code is required for scope=stock"
    payload = json.loads(registry.execute("get_backtest_view", **{"view": "bogus"}))
    assert "unknown view" in payload["error"]
    payload = json.loads(registry.execute("get_backtest_view", **{"view": "summary", "scope": "bogus"}))
    assert "unknown scope" in payload["error"]
    assert calls == []


def test_get_backtest_view_tool_exposes_stock_code_parameter():
    tool = next(item for item in ALL_SITE_READ_TOOLS if item.name == "get_backtest_view")
    names = [param.name for param in tool.parameters]
    assert "stock_code" in names
    assert "code" not in names
    assert "stock" in tool.policy.scope_dimensions


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


def test_get_screening_service_instance_wires_database_manager(monkeypatch):
    import src.agent.tools.site_read_tools as site_read_tools

    marker = object()
    monkeypatch.setattr(site_read_tools, "_screening_service", None)
    monkeypatch.setattr(site_read_tools, "_get_db", lambda: marker)
    monkeypatch.setattr("src.config.get_config", lambda: object())

    service = site_read_tools._screening_service_instance()

    assert service.db_manager is marker


def test_get_screening_history_forwards_filters_and_clamps(monkeypatch):
    calls = {}

    class FakeScreening:
        db_manager = object()

        def history(self, **kwargs):
            calls.update(kwargs)
            return {"enabled": True, "runs": [], "run_count": 0}

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._screening_service",
        lambda: FakeScreening(),
    )
    payload = json.loads(_registry().execute("get_screening_view", **{
        "view": "history",
        "strategy": "dual_low",
        "market": "cn",
        "limit": 500,
    }))
    assert payload["run_count"] == 0
    assert "screening_history_unavailable" not in json.dumps(payload)
    assert calls == {"limit": 100, "strategy": "dual_low", "market": "cn"}


def test_get_screening_history_detail_and_not_found(monkeypatch):
    from fastapi import HTTPException

    calls = []

    class FakeScreening:
        db_manager = object()

        def history_detail(self, run_id):
            calls.append(run_id)
            if run_id == "missing":
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": "screening_run_not_found",
                        "message": f"选股运行 {run_id} 不存在。",
                    },
                )
            return {"enabled": True, "run_id": run_id, "results": []}

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._screening_service",
        lambda: FakeScreening(),
    )
    registry = _registry()
    payload = json.loads(registry.execute("get_screening_view", **{"view": "history", "run_id": "run-1"}))
    assert payload["run_id"] == "run-1"
    payload = json.loads(registry.execute("get_screening_view", **{"view": "history", "run_id": "missing"}))
    assert payload["error"] == "screening_run_not_found"
    assert payload["status_code"] == 404
    assert calls == ["run-1", "missing"]


def test_get_screening_hotspots_clamps_top_and_rejects_unknown_view(monkeypatch):
    calls = {}

    class FakeScreening:
        def hotspots(self, **kwargs):
            calls.update(kwargs)
            return {"items": []}

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._screening_service",
        lambda: FakeScreening(),
    )
    registry = _registry()
    payload = json.loads(registry.execute("get_screening_view", **{"view": "hotspots", "top": 500}))
    assert payload["items"] == []
    assert calls == {"top": 50}
    payload = json.loads(registry.execute("get_screening_view", **{"view": "bogus"}))
    assert "unknown view" in payload["error"]


def _signal_double(calls):
    class FakeSignals:
        def get_signal(self, signal_id):
            calls.append(("detail", signal_id))
            return {"id": signal_id}

        def list_signals(self, **kwargs):
            calls.append(("list", kwargs))
            return {"items": [{"id": 1}], "total": 1}

        def get_latest_active(self, **kwargs):
            calls.append(("latest", kwargs))
            return {"items": [], "total": 0}

    return FakeSignals()


def test_get_signal_view_forwards_filters_and_clamps(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._signal_service",
        lambda: _signal_double(calls),
    )
    payload = json.loads(_registry().execute("get_signal_view", **{
        "view": "signals",
        "stock_code": "600519",
        "status": "active",
        "action": "buy",
        "page": 0,
        "page_size": 500,
    }))
    assert payload["total"] == 1
    assert calls == [("list", {
        "stock_code": "600519",
        "status": "active",
        "action": "buy",
        "page": 1,
        "page_size": 100,
    })]


def test_get_signal_view_signal_detail_and_latest_active(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._signal_service",
        lambda: _signal_double(calls),
    )
    registry = _registry()
    payload = json.loads(registry.execute("get_signal_view", **{"view": "signals", "signal_id": 7}))
    assert payload["id"] == 7
    assert calls[0] == ("detail", 7)

    payload = json.loads(registry.execute("get_signal_view", **{"view": "latest_active"}))
    assert payload["error"] == "stock_code is required for view=latest_active"
    assert len(calls) == 1

    payload = json.loads(registry.execute("get_signal_view", **{"view": "latest_active", "stock_code": "600519"}))
    assert payload["total"] == 0
    assert calls[1] == ("latest", {"stock_code": "600519"})


def test_get_signal_view_outcomes_forwards_filters_and_clamps(monkeypatch):
    calls = {}

    class FakeOutcomes:
        def list_outcomes(self, **kwargs):
            calls.update(kwargs)
            return {"items": [], "total": 0}

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._outcome_service",
        lambda: FakeOutcomes(),
    )
    payload = json.loads(_registry().execute("get_signal_view", **{
        "view": "outcomes",
        "signal_id": 3,
        "horizon": "5d",
        "eval_status": "done",
        "outcome": "win",
        "page": 2,
        "page_size": 500,
    }))
    assert payload["total"] == 0
    assert calls == {
        "signal_id": 3,
        "horizon": "5d",
        "eval_status": "done",
        "outcome": "win",
        "page": 2,
        "page_size": 100,
    }


def test_get_signal_view_reads_stats_and_rejects_unsupported_filters(monkeypatch):
    calls = []

    class FakeOutcomes:
        def get_stats(self, **kwargs):
            calls.append(kwargs)
            return {"buckets": []}

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._outcome_service",
        lambda: FakeOutcomes(),
    )
    registry = _registry()
    payload = json.loads(registry.execute("get_signal_view", **{
        "view": "stats",
        "horizon": "5d",
        "status": "active",
    }))
    assert payload["buckets"] == []
    assert calls == [{"horizons": ["5d"], "statuses": ["active"]}]

    payload = json.loads(registry.execute("get_signal_view", **{
        "view": "stats",
        "stock_code": "600519",
    }))
    assert payload["error"] == "unsupported_filters_for_stats"
    assert payload["filters"] == ["stock_code"]
    assert len(calls) == 1

    payload = json.loads(registry.execute("get_signal_view", **{
        "view": "stats",
        "signal_id": 9,
        "action": "buy",
        "outcome": "win",
        "eval_status": "done",
    }))
    assert payload["error"] == "unsupported_filters_for_stats"
    assert payload["filters"] == ["signal_id", "action", "outcome", "eval_status"]

    # skill_id 只服务于 skill_stats；stats 分支必须拒绝它，而不是返回全量
    # 统计让模型当成单 skill 的结论。
    payload = json.loads(registry.execute("get_signal_view", **{
        "view": "stats",
        "skill_id": "trend",
    }))
    assert payload["error"] == "unsupported_filters_for_stats"
    assert payload["filters"] == ["skill_id"]
    assert len(calls) == 1

    payload = json.loads(registry.execute("get_signal_view", **{"view": "bogus"}))
    assert "unknown view" in payload["error"]


def test_get_signal_view_tool_exposes_stock_code_parameter():
    tool = next(item for item in ALL_SITE_READ_TOOLS if item.name == "get_signal_view")
    names = [param.name for param in tool.parameters]
    assert "stock_code" in names
    assert "stock" in tool.policy.scope_dimensions


def test_get_fund_review_view_forwards_filters_and_clamps(monkeypatch):
    calls = []

    class FakeFundReview:
        def list_reviews(self, **kwargs):
            calls.append(("reviews", kwargs))
            return {"items": [], "total": 0, "page": 1, "page_size": 100}

        def get_stats(self, **kwargs):
            calls.append(("stats", kwargs))
            return {"horizons": [], "breakdowns": {}}

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._fund_review_service",
        lambda: FakeFundReview(),
    )
    registry = _registry()
    payload = json.loads(registry.execute("get_fund_review_view", **{
        "view": "reviews",
        "fund_code": "fund:161725",
        "horizon": "3m",
        "eval_status": "evaluated",
        "page": 0,
        "page_size": 500,
    }))
    assert payload["total"] == 0
    assert calls == [("reviews", {
        "fund_code": "fund:161725",
        "horizon": "3m",
        "eval_status": "evaluated",
        "page": 1,
        "page_size": 100,
    })]

    payload = json.loads(registry.execute("get_fund_review_view", **{
        "view": "stats",
        "horizon": "6m",
    }))
    assert payload["breakdowns"] == {}
    assert calls[1] == ("stats", {"horizons": ["6m"]})

    payload = json.loads(registry.execute("get_fund_review_view", **{"view": "bogus"}))
    assert "unknown view" in payload["error"]

    for view in ("reviews", "stats"):
        payload = json.loads(registry.execute("get_fund_review_view", **{
            "view": view,
            "horizon": "9x",
        }))
        assert payload["error"] == "invalid_horizon"
        assert payload["allowed"] == ["1m", "3m", "6m", "1y"]
    assert len(calls) == 2


def test_get_paper_view_uses_default_account_and_clamps(monkeypatch):
    calls = []

    class FakePaper:
        def get_existing_account_id(self, account_id=None):
            calls.append(("account", account_id))
            return 42 if account_id is None else int(account_id)

        def get_trades(self, account_id, page=1, limit=50):
            calls.append(("trades", account_id, page, limit))
            return {"items": [], "total": 0}

        def get_snapshot(self, account_id):
            calls.append(("snapshot", account_id))
            return {"account_id": account_id}

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._paper_service",
        lambda: FakePaper(),
    )
    registry = _registry()
    payload = json.loads(registry.execute("get_paper_view", **{
        "view": "trades",
        "limit": 500,
    }))
    assert payload["total"] == 0
    assert calls == [("account", None), ("trades", 42, 1, 200)]

    payload = json.loads(registry.execute("get_paper_view", **{
        "view": "snapshot",
        "account_id": 7,
    }))
    assert payload["account_id"] == 7
    assert calls[2:] == [("account", 7), ("snapshot", 7)]

    payload = json.loads(registry.execute("get_paper_view", **{"view": "bogus"}))
    assert "unknown view" in payload["error"]
    assert len(calls) == 4


def test_get_paper_view_returns_notice_when_no_account_exists(monkeypatch):
    class FakePaper:
        def get_existing_account_id(self, account_id=None):
            return None

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._paper_service",
        lambda: FakePaper(),
    )
    payload = json.loads(_registry().execute("get_paper_view", **{"view": "snapshot"}))
    assert payload == {"account": None, "message": "no paper trading account"}


def test_get_paper_view_clamps_equity_curve(monkeypatch):
    class FakePaper:
        def get_existing_account_id(self, account_id=None):
            return 1

        def get_equity_curve(self, account_id):
            return [
                {"trade_date": f"2026-01-{index:02d}", "net_value": float(index)}
                for index in range(1, 6)
            ]

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._paper_service",
        lambda: FakePaper(),
    )
    registry = _registry()
    payload = json.loads(registry.execute("get_paper_view", **{
        "view": "equity",
        "equity_limit": 2,
    }))
    assert payload["total"] == 5
    assert payload["limit"] == 2
    assert payload["limit_cap"] == 365
    assert payload["truncated"] is True
    assert [item["net_value"] for item in payload["items"]] == [4.0, 5.0]

    payload = json.loads(registry.execute("get_paper_view", **{
        "view": "equity",
        "equity_limit": 9999,
    }))
    assert payload["limit"] == 365
    assert payload["truncated"] is False
    assert len(payload["items"]) == 5


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


def test_get_portfolio_ledger_view_dispatches_views_and_clamps(monkeypatch):
    calls = []

    class FakePortfolio:
        def list_accounts(self, include_inactive=False):
            calls.append(("accounts", include_inactive))
            return [{"id": 7}]

        def get_portfolio_snapshot_readonly(self, **kwargs):
            calls.append(("snapshot", kwargs))
            return {"total_value": 0}

        def list_cash_ledger_events(self, **kwargs):
            calls.append(("cash", kwargs))
            return {"items": [], "total": 0}

        def list_corporate_action_events(self, **kwargs):
            calls.append(("corporate", kwargs))
            return {"items": [], "total": 0}

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._portfolio_service",
        lambda: FakePortfolio(),
    )
    registry = _registry()
    payload = json.loads(registry.execute("get_portfolio_ledger_view", **{
        "view": "accounts",
        "include_inactive": True,
    }))
    assert payload["accounts"] == [{"id": 7}]

    registry.execute("get_portfolio_ledger_view", **{"view": "snapshot", "account_id": 7})
    registry.execute("get_portfolio_ledger_view", **{
        "view": "cash",
        "account_id": 7,
        "direction": "in",
        "page": 0,
        "page_size": 500,
    })
    registry.execute("get_portfolio_ledger_view", **{
        "view": "corporate",
        "symbol": "600519",
        "action_type": "cash_dividend",
        "page_size": 500,
    })
    assert calls == [
        ("accounts", True),
        ("snapshot", {"account_id": 7}),
        ("cash", {
            "account_id": 7,
            "direction": "in",
            "page": 1,
            "page_size": 100,
        }),
        ("corporate", {
            "account_id": None,
            "symbol": "600519",
            "action_type": "cash_dividend",
            "page": 1,
            "page_size": 100,
        }),
    ]

    payload = json.loads(registry.execute("get_portfolio_ledger_view", **{"view": "bogus"}))
    assert "unknown view" in payload["error"]


def test_get_paper_view_does_not_write_on_empty_db(isolated_db):
    before = _row_counts(isolated_db)
    assert before["paper_accounts"] == 0

    payload = json.loads(_registry().execute("get_paper_view", **{"view": "snapshot"}))

    assert payload == {"account": None, "message": "no paper trading account"}
    assert _row_counts(isolated_db) == before


def test_get_paper_view_does_not_write_for_existing_account(isolated_db):
    from src.repositories.paper_repo import PaperRepository

    account = PaperRepository(isolated_db).ensure_account()
    before = _row_counts(isolated_db)
    updated_before = _paper_account_updated_at(isolated_db, account.id)

    payload = json.loads(_registry().execute("get_paper_view", **{"view": "snapshot"}))

    assert payload["account_id"] == account.id
    assert _row_counts(isolated_db) == before
    assert _paper_account_updated_at(isolated_db, account.id) == updated_before


def test_get_paper_view_unknown_account_id_does_not_create(isolated_db):
    before = _row_counts(isolated_db)

    payload = json.loads(_registry().execute("get_paper_view", **{
        "view": "snapshot",
        "account_id": 999,
    }))

    assert payload == {"account": None, "message": "no paper trading account"}
    assert _row_counts(isolated_db) == before


def test_get_portfolio_ledger_snapshot_never_replays_or_writes(isolated_db):
    from datetime import date

    from src.repositories.portfolio_repo import PortfolioRepository
    from src.services.portfolio_service import PortfolioService
    from src.storage import PortfolioDailySnapshot

    repo = PortfolioRepository(isolated_db)
    account = repo.create_account(
        name="Main",
        broker=None,
        market="cn",
        base_currency="CNY",
    )
    repo.add_trade(
        account_id=account.id,
        trade_uid="trade-1",
        symbol="600519",
        market="cn",
        currency="CNY",
        trade_date=date(2026, 1, 5),
        side="buy",
        quantity=100.0,
        price=10.0,
        fee=0.0,
        tax=0.0,
    )

    # No persisted snapshot yet: the handler must report that instead of
    # replaying (which would insert positions/lots/snapshot rows).
    empty_counts = _row_counts(isolated_db)
    payload = json.loads(_registry().execute("get_portfolio_ledger_view", **{"view": "snapshot"}))
    assert payload["read_only"] is True
    assert payload["accounts"][0]["message"] == "no cached portfolio snapshot"
    assert _row_counts(isolated_db) == empty_counts

    # Seed one persisted snapshot through the normal write path, then prove the
    # read-only handler leaves the cached rows untouched.
    PortfolioService().get_portfolio_snapshot(account_id=account.id, include_realtime=False)
    before = _row_counts(isolated_db)
    with isolated_db.get_session() as session:
        snapshot_before = session.execute(select(PortfolioDailySnapshot)).scalars().one()
        snapshot_state = (
            snapshot_before.id,
            snapshot_before.updated_at,
            snapshot_before.total_equity,
        )

    payload = json.loads(_registry().execute("get_portfolio_ledger_view", **{"view": "snapshot"}))

    assert payload["accounts"][0]["account_id"] == account.id
    assert payload["total_equity"] == pytest.approx(snapshot_state[2])
    assert _row_counts(isolated_db) == before
    with isolated_db.get_session() as session:
        snapshot_after = session.execute(select(PortfolioDailySnapshot)).scalars().one()
    assert (
        snapshot_after.id,
        snapshot_after.updated_at,
        snapshot_after.total_equity,
    ) == snapshot_state


def test_get_intelligence_view_forwards_filters_and_clamps(monkeypatch):
    calls = []

    class FakeIntelligence:
        def list_sources(self, **kwargs):
            calls.append(("sources", kwargs))
            return {"items": [], "total": 0}

        def list_source_templates(self, **kwargs):
            calls.append(("templates", kwargs))
            return {"items": [], "total": 0}

        def list_items(self, **kwargs):
            calls.append(("items", kwargs))
            return {"items": [], "total": 0}

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._intelligence_service",
        lambda: FakeIntelligence(),
    )
    registry = _registry()
    payload = json.loads(registry.execute("get_intelligence_view", **{"view": "sources"}))
    assert payload["total"] == 0
    registry.execute("get_intelligence_view", **{"view": "templates"})
    registry.execute("get_intelligence_view", **{
        "view": "items",
        "source_id": 3,
        "page": 0,
        "page_size": 500,
    })
    assert calls == [
        ("sources", {}),
        ("templates", {}),
        ("items", {"source_id": 3, "page": 1, "page_size": 100}),
    ]

    payload = json.loads(registry.execute("get_intelligence_view", **{"view": "bogus"}))
    assert "unknown view" in payload["error"]


def test_get_scheduler_view_reports_status(monkeypatch):
    class FakeScheduler:
        def status(self):
            return {"enabled": True, "next_run_at": "2026-10-18T09:00:00"}

    monkeypatch.setattr(
        "src.services.runtime_scheduler.get_runtime_scheduler_service",
        lambda: FakeScheduler(),
    )
    payload = json.loads(_registry().execute("get_scheduler_view", **{}))
    assert payload["enabled"] is True
    assert payload["next_run_at"] == "2026-10-18T09:00:00"

    monkeypatch.setattr(
        "src.services.runtime_scheduler.get_runtime_scheduler_service",
        lambda: None,
    )
    payload = json.loads(_registry().execute("get_scheduler_view", **{}))
    assert payload["error"] == "scheduler is not running in this process"


def test_new_read_tools_declare_no_stock_scope_unless_they_take_stock_code():
    for name in (
        "get_fund_review_view",
        "get_paper_view",
        "get_portfolio_ledger_view",
        "get_intelligence_view",
        "get_config_view",
        "get_scheduler_view",
    ):
        tool = next(item for item in ALL_SITE_READ_TOOLS if item.name == name)
        assert "stock_code" not in [param.name for param in tool.parameters]
        assert tool.policy.scope_dimensions == []
        assert tool.policy.cancellation_safe is True
    assert _registry().validate_tool_policies(strict=True) == []


def test_get_config_view_hides_sensitive_fields(monkeypatch):
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

    payload = json.loads(_registry().execute("get_config_view", **{"key": "DEEPSEEK_API_KEY"}))
    assert payload["error"] == "unknown or sensitive config key: DEEPSEEK_API_KEY"


def test_get_history_view_record_parts(monkeypatch):
    calls = []

    class FakeHistory:
        def get_history_detail_by_id(self, record_id):
            calls.append(("detail", record_id))
            return {"id": record_id}

        def get_news_intel_by_record_id(self, record_id):
            calls.append(("news", record_id))
            return {"record_id": record_id, "news": []}

        def get_markdown_report(self, record_id):
            calls.append(("markdown", record_id))
            return "# 报告" if record_id == "9" else ""

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._history_service",
        lambda: FakeHistory(),
    )
    registry = _registry()
    payload = json.loads(registry.execute("get_history_view", **{"record_id": 9}))
    assert payload == {"id": 9}
    payload = json.loads(registry.execute("get_history_view", **{"record_id": 9, "part": "news"}))
    assert payload["news"] == []
    payload = json.loads(registry.execute("get_history_view", **{"record_id": 9, "part": "markdown"}))
    assert payload == {"record_id": 9, "markdown": "# 报告"}
    payload = json.loads(registry.execute("get_history_view", **{"record_id": 10, "part": "markdown"}))
    assert "not found" in payload["error"]
    payload = json.loads(registry.execute("get_history_view", **{"record_id": 9, "part": "bogus"}))
    assert "unknown part" in payload["error"]
    assert calls == [
        ("detail", 9),
        ("news", 9),
        ("markdown", "9"),
        ("markdown", "10"),
    ]


def test_get_alert_view_notifications_use_trigger_id(monkeypatch):
    calls = []

    class FakeAlert:
        def list_notifications(self, **kwargs):
            calls.append(kwargs)
            return {"items": [], "total": 0}

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._alert_service",
        lambda: FakeAlert(),
    )
    _registry().execute("get_alert_view", **{
        "view": "notifications",
        "rule_id": 3,
        "trigger_id": 11,
        "page_size": 500,
    })
    # notifications 必须按 trigger_id 过滤，rule_id 不能冒充触发记录 ID。
    assert calls == [{"trigger_id": 11, "page": 1, "page_size": 100}]


def test_get_config_view_usage_view(monkeypatch):
    calls = []

    class FakeDb:
        def get_llm_usage_summary(self, from_dt, to_dt):
            calls.append(("summary", from_dt, to_dt))
            return {"total_tokens": 42}

        def get_llm_usage_records(self, from_dt, to_dt, limit=50):
            calls.append(("records", limit))
            return [{"id": 1, "total_tokens": 42}]

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._get_db",
        lambda: FakeDb(),
    )
    registry = _registry()
    payload = json.loads(registry.execute("get_config_view", **{
        "view": "usage",
        "period": "month",
        "page_size": 500,
    }))
    assert payload["view"] == "usage"
    assert payload["summary"] == {"total_tokens": 42}
    assert payload["records"] == [{"id": 1, "total_tokens": 42}]
    assert calls[0][0] == "summary"
    assert calls[1] == ("records", 100)

    payload = json.loads(registry.execute("get_config_view", **{"view": "usage", "period": "year"}))
    assert "period must be" in payload["error"]


def test_get_config_view_data_quality_and_notifications(monkeypatch):
    from types import SimpleNamespace

    calls = []

    class FakeRepo:
        def __init__(self, kind):
            self.kind = kind

        def list_discrepancies(self, **kwargs):
            calls.append((self.kind, kwargs))
            return ([SimpleNamespace(
                id=1, market="cn", stock_code="600519", issue_type="price",
                primary_source="a", secondary_source="b", primary_price=1.0,
                secondary_price=2.0, price_diff_pct=0.5, primary_ts=None,
                secondary_ts=None, detail="{}", created_at=None,
            )], 1)

        def list_deliveries(self, **kwargs):
            calls.append((self.kind, kwargs))
            return ([SimpleNamespace(
                id=2, route_type="analysis", channel="feishu", attempt=1,
                success=True, error_code=None, retryable=False, latency_ms=12,
                diagnostics="{}", created_at=None,
            )], 1)

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._data_quality_repository",
        lambda: FakeRepo("dq"),
    )
    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._notification_delivery_repository",
        lambda: FakeRepo("notification"),
    )
    registry = _registry()
    payload = json.loads(registry.execute("get_config_view", **{
        "view": "data_quality",
        "page": 0,
        "page_size": 500,
    }))
    assert payload["total"] == 1
    assert payload["items"][0]["stock_code"] == "600519"
    payload = json.loads(registry.execute("get_config_view", **{"view": "notifications"}))
    assert payload["items"][0]["channel"] == "feishu"
    assert calls == [
        ("dq", {"page": 1, "page_size": 100}),
        ("notification", {"page": 1, "page_size": 20}),
    ]


def test_get_fund_review_view_nav_range(monkeypatch):
    calls = []

    class FakeFundReview:
        def get_nav_range(self, **kwargs):
            calls.append(kwargs)
            return {"fund_code": "161725", "points": []}

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._fund_review_service",
        lambda: FakeFundReview(),
    )
    registry = _registry()
    payload = json.loads(registry.execute("get_fund_review_view", **{
        "view": "nav_range",
        "fund_code": "161725",
        "start_date": "2026-01-01",
        "end_date": "2026-03-31",
    }))
    assert payload["points"] == []
    assert calls[0]["fund_code"] == "161725"
    assert calls[0]["start_date"].isoformat() == "2026-01-01"
    assert calls[0]["end_date"].isoformat() == "2026-03-31"

    payload = json.loads(registry.execute("get_fund_review_view", **{"view": "nav_range"}))
    assert "fund_code is required" in payload["error"]
    payload = json.loads(registry.execute("get_fund_review_view", **{
        "view": "nav_range",
        "fund_code": "161725",
        "start_date": "2026-01-01",
    }))
    assert "start_date and end_date are required" in payload["error"]
    payload = json.loads(registry.execute("get_fund_review_view", **{
        "view": "nav_range",
        "fund_code": "161725",
        "start_date": "bad",
        "end_date": "2026-03-31",
    }))
    assert "YYYY-MM-DD" in payload["error"]


def test_get_fund_review_view_nav_range_surfaces_service_value_error(monkeypatch):
    class FakeFundReview:
        def get_nav_range(self, **kwargs):
            raise ValueError("date range must not exceed 366 days")

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._fund_review_service",
        lambda: FakeFundReview(),
    )
    payload = json.loads(_registry().execute("get_fund_review_view", **{
        "view": "nav_range",
        "fund_code": "161725",
        "start_date": "2020-01-01",
        "end_date": "2026-03-31",
    }))
    assert payload["error"] == "date range must not exceed 366 days"


def test_get_screening_view_source_history(monkeypatch):
    calls = []

    class FakeScreening:
        def source_history(self, **kwargs):
            calls.append(kwargs)
            return {"sources": [], "total": 0}

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._screening_service",
        lambda: FakeScreening(),
    )
    payload = json.loads(_registry().execute("get_screening_view", **{
        "view": "source_history",
        "limit": 500,
    }))
    assert payload["sources"] == []
    assert calls == [{"limit": 100}]


def test_get_signal_view_skill_stats(monkeypatch):
    calls = []

    class FakePerformance:
        def get_stats(self, **kwargs):
            calls.append(kwargs)
            return {"buckets": []}

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._skill_performance_service",
        lambda: FakePerformance(),
    )
    registry = _registry()
    payload = json.loads(registry.execute("get_signal_view", **{
        "view": "skill_stats",
        "skill_id": "trend-follow",
        "horizon": "5d",
    }))
    assert payload["buckets"] == []
    assert calls == [{"skill_id": "trend-follow", "horizons": ["5d"]}]

    payload = json.loads(registry.execute("get_signal_view", **{"view": "skill_stats"}))
    assert calls[1] == {"skill_id": None, "horizons": None}

    class RejectingPerformance:
        def get_stats(self, **kwargs):
            raise ValueError("unknown horizon: 9x")

    monkeypatch.setattr(
        "src.agent.tools.site_read_tools._skill_performance_service",
        lambda: RejectingPerformance(),
    )
    payload = json.loads(registry.execute("get_signal_view", **{
        "view": "skill_stats",
        "horizon": "9x",
    }))
    assert payload["error"] == "unknown horizon: 9x"
