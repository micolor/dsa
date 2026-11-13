# -*- coding: utf-8 -*-
"""Tests for event-driven Agent analysis (no network, no real LLM)."""

from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pandas as pd

from src.services.event_analysis_service import EventAnalysisService

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 5, 14, 30, tzinfo=TZ)


class _FakeFetcher:
    def __init__(self):
        self.quotes = {
            "600519": SimpleNamespace(name="贵州茅台", price=1452.0, change_pct=0.38, pre_close=1446.0, volume_ratio=0.72),
            "000001": SimpleNamespace(name="平安银行", price=11.2, change_pct=-6.1, pre_close=11.9, volume_ratio=2.3),
        }

    def get_realtime_quote(self, symbol):
        return self.quotes.get(symbol)

    def get_stock_name(self, symbol):
        return None


class _FakePortfolio:
    def get_portfolio_snapshot(self, *, include_realtime=False):
        return {
            "accounts": [{
                "positions": [{
                    "symbol": "000001",
                    "quantity": 1000,
                    "market_value_base": 11200.0,
                    "unrealized_pnl_pct": -12.5,
                }],
            }],
        }


class _FakeAlerts:
    def list_triggers(self, *, page=1, page_size=20):
        return {
            "items": [{
                "id": 7,
                "target": "600519",
                "reason": "跌破 MA20",
                "observed_value": 1452.0,
                "threshold": 1500.0,
                "triggered_at": NOW.isoformat(),
                "status": "triggered",
            }],
        }


class _FakeDb:
    def __init__(self):
        self.runs = set()
        self.messages = []
        self.session_messages = []
        self.saved = []
        self.runs_today = 0

    def save_proactive_message(self, message_type, title, content):
        self.saved.append({"type": message_type, "title": title, "content": content})
        return len(self.saved)

    def has_event_agent_run(self, event_key):
        return event_key in self.runs

    def count_event_agent_runs_for_date(self, brief_date):
        return self.runs_today

    def save_event_agent_run(self, event_key, stock_code, event_type, message_id=None):
        self.runs.add(event_key)
        self.messages.append({"event_key": event_key, "message_id": message_id})

    def get_conversation_messages(self, session_id, limit=100):
        return list(self.session_messages)


class _FakeLlm:
    def __init__(self, content="事件值得关注：价格已跌破 MA20，量能放大。先观察是否放量收回，暂不追涨。"):
        self.content = content
        self.calls = []

    def call_completion(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return SimpleNamespace(content=self.content, provider="litellm" if self.content else "error")


class _FakeNotifier:
    def __init__(self):
        self.calls = []

    def send(self, content, **kwargs):
        self.calls.append({"content": content, **kwargs})
        return True


def _history_loader(symbol, days):
    ma20 = 1500.0 if symbol == "600519" else 12.0
    df = pd.DataFrame([{
        "close": 1452.0 if symbol == "600519" else 11.2,
        "pct_chg": 0.38 if symbol == "600519" else -6.1,
        "ma5": ma20,
        "ma10": ma20,
        "ma20": ma20,
        "volume_ratio": 0.72 if symbol == "600519" else 2.3,
    }])
    return df, "fake"


def _service(*, enabled=True, notify=True, llm=None, db=None, notifier=None):
    config = SimpleNamespace(
        stock_list=["600519", "000001"],
        agent_proactive_brief_watchlist_limit=20,
        agent_event_analysis_enabled=enabled,
        agent_event_analysis_interval_minutes=10,
        agent_event_analysis_max_per_run=3,
        agent_event_analysis_notify=notify,
        agent_event_analysis_timeout_s=30,
        agent_proactive_market_guard=True,
        agent_event_analysis_max_per_day=20,
    )
    return EventAnalysisService(
        config,
        fetcher=_FakeFetcher(),
        history_loader=_history_loader,
        portfolio_service=_FakePortfolio(),
        alert_service=_FakeAlerts(),
        db=db or _FakeDb(),
        llm=llm or _FakeLlm(),
        notifier=notifier or _FakeNotifier(),
        news_loader=lambda _symbol, _name: [],
        clock=lambda: NOW,
    )


class TestEventAnalysisService(unittest.TestCase):
    def test_collects_alert_and_default_watchlist_events(self):
        events = _service().collect_events(now=NOW, lookback_minutes=60)

        types = {event.event_type for event in events}
        self.assertIn("alert", types)
        self.assertIn("below_ma20", types)
        self.assertIn("big_move", types)
        self.assertIn("volume_spike", types)
        self.assertIn("position_loss", types)
        self.assertTrue(all(event.snapshot.get("symbol") for event in events))

    def test_run_once_analyses_persists_and_notifies(self):
        db = _FakeDb()
        llm = _FakeLlm()
        notifier = _FakeNotifier()
        service = _service(llm=llm, db=db, notifier=notifier)
        results = service.run_once(now=NOW, force=True, max_events=10)

        self.assertEqual(len(results), 6)
        self.assertEqual(len(llm.calls), 6)
        self.assertEqual(len(db.runs), 6)
        self.assertEqual(len(db.saved), 6)
        self.assertEqual(len(notifier.calls), 6)

        # Already processed events do not trigger another LLM call.
        second = service.run_once(now=NOW, force=True, max_events=10)
        self.assertEqual(second, [])

    def test_disabled_service_skips_unless_forced(self):
        service = _service(enabled=False)
        self.assertEqual(service.run_once(now=NOW), [])

    def test_market_guard_skips_outside_trading_window(self):
        llm = _FakeLlm()
        service = _service(llm=llm)
        with patch("src.services.proactive_market_guard.is_trading_day", return_value=False), \
             patch("src.services.proactive_market_guard.is_event_window", return_value=False):
            results = service.run_once(now=NOW, force=False)
        self.assertEqual(results, [])
        self.assertEqual(llm.calls, [])

    def test_daily_budget_stops_scheduled_analysis(self):
        llm = _FakeLlm()
        db = _FakeDb()
        db.runs_today = 20
        service = _service(llm=llm, db=db)
        with patch("src.services.proactive_market_guard.is_trading_day", return_value=True), \
             patch("src.services.proactive_market_guard.is_event_window", return_value=True):
            results = service.run_once(now=NOW, force=False)
        self.assertEqual(results, [])
        self.assertEqual(llm.calls, [])

    def test_empty_llm_response_is_not_persisted(self):
        db = _FakeDb()
        service = _service(llm=_FakeLlm(content=""), db=db)
        results = service.run_once(now=NOW, force=True)
        self.assertEqual(results, [])
        self.assertEqual(db.runs, set())
        self.assertEqual(db.saved, [])


if __name__ == "__main__":
    unittest.main()
