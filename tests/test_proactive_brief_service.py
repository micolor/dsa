# -*- coding: utf-8 -*-
"""Tests for the proactive daily brief (deterministic, no network)."""

from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pandas as pd

from src.services.proactive_brief_service import ProactiveBriefService


TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 5, 15, 30, tzinfo=TZ)


class _FakeFetcher:
    def __init__(self):
        self.quotes = {
            "600519": SimpleNamespace(
                code="600519", name="贵州茅台", price=1452.0,
                change_pct=0.38, volume_ratio=0.72,
            ),
            "000001": SimpleNamespace(
                code="000001", name="平安银行", price=11.2,
                change_pct=-6.1, volume_ratio=2.3,
            ),
        }
        self.indices = [
            {"code": "sh000001", "name": "上证指数", "current": 3689.91, "change_pct": 0.1},
            {"code": "sz399001", "name": "深证成指", "current": 11471.09, "change_pct": 1.19},
        ]

    def get_realtime_quote(self, symbol):
        return self.quotes.get(symbol)

    def get_stock_name(self, symbol):
        return None

    def get_main_indices(self, region="cn"):
        return self.indices


class _FakePortfolio:
    def __init__(self, positions=None):
        self._positions = positions or []

    def get_portfolio_snapshot(self, *, include_realtime=True):
        return {
            "accounts": [{
                "account_name": "主账户",
                "positions": self._positions,
            }],
        }


class _FakeAlerts:
    def __init__(self, items=None):
        self._items = items or []

    def list_triggers(self, *, page=1, page_size=20):
        return {"items": self._items, "total": len(self._items), "page": page, "page_size": page_size}


class _FakeDb:
    def __init__(self, messages=None):
        self.messages = list(messages or [])
        self.runs = set()
        self.saved = []

    def save_proactive_message(self, message_type, title, content):
        self.saved.append({"type": message_type, "title": title, "content": content})
        return len(self.saved)

    def get_conversation_messages(self, session_id, limit=100):
        return list(self.messages[:limit])

    def has_proactive_brief_run(self, brief_date, slot):
        return (brief_date, slot) in self.runs

    def save_proactive_brief_run(self, brief_date, slot, session_id, message_id=None):
        self.runs.add((brief_date, slot))


def _history_loader(symbol, days):
    ma20 = 1400.0 if symbol == "600519" else 12.0
    df = pd.DataFrame([{
        "close": 1452.0 if symbol == "600519" else 11.2,
        "pct_chg": 0.38 if symbol == "600519" else -6.1,
        "ma20": ma20,
        "volume_ratio": 0.72 if symbol == "600519" else 2.3,
    }])
    return df, "fake"


class _FakeNotifier:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def send(self, content, **kwargs):
        self.calls.append({"content": content, **kwargs})
        return self.result


def _service(*, positions=None, alerts=None, config=None, db=None, notifier=None, notify=False):
    return ProactiveBriefService(
        config or SimpleNamespace(
            stock_list=["600519", "000001"],
            agent_proactive_brief_enabled=True,
            agent_proactive_brief_times="08:30,15:30",
            agent_proactive_brief_watchlist_limit=20,
            agent_proactive_brief_notify=notify,
        ),
        fetcher=_FakeFetcher(),
        history_loader=_history_loader,
        portfolio_service=_FakePortfolio(positions),
        alert_service=_FakeAlerts(alerts),
        db=db or _FakeDb(),
        notifier=notifier,
        clock=lambda: NOW,
    )


class TestProactiveBriefGeneration(unittest.TestCase):
    def test_brief_contains_market_positions_watchlist_and_alerts(self):
        positions = [{
            "symbol": "600519",
            "quantity": 100,
            "market_value_base": 145200.0,
            "unrealized_pnl_pct": -12.5,
            "last_price": 1452.0,
        }]
        alerts = [{
            "triggered_at": NOW.isoformat(),
            "target": "600519",
            "reason": "跌破 MA20",
            "status": "triggered",
        }]
        brief = _service(positions=positions, alerts=alerts).generate(slot="15:30", now=NOW)

        self.assertIn("助手日报 · 2026-11-05 15:30", brief.markdown)
        self.assertIn("上证指数", brief.markdown)
        self.assertIn("贵州茅台", brief.markdown)
        self.assertIn("平安银行", brief.markdown)
        self.assertIn("跌破 MA20", brief.markdown)
        self.assertIn("MA20 下方", brief.markdown)
        self.assertIn("浮亏 -12.50%", brief.markdown)
        self.assertIn("今日下跌 -6.10%", brief.markdown)
        self.assertNotIn("<!--", brief.markdown)
        self.assertTrue(brief.highlights)

    def test_brief_degrades_when_no_positions_or_watchlist(self):
        config = SimpleNamespace(
            stock_list=[],
            agent_proactive_brief_enabled=True,
            agent_proactive_brief_times="08:30,15:30",
            agent_proactive_brief_watchlist_limit=20,
        )
        brief = _service(config=config).generate(slot="manual", now=NOW)

        self.assertIn("当前没有持仓", brief.markdown)
        self.assertIn("未配置自选股", brief.markdown)

    def test_run_due_is_idempotent_for_the_same_slot(self):
        service = _service()
        first = service.run_due(now=NOW)
        self.assertIsNotNone(first)
        self.assertFalse(first.skipped)

        # The date + slot run record makes the next tick skip before any network work.
        with patch.object(service, "generate", side_effect=AssertionError("must not regenerate")):
            second = service.run_due(now=NOW)
        self.assertIsNone(second)

    def test_manual_run_always_appends(self):
        db = _FakeDb()
        result = _service(db=db).run(slot="manual", now=NOW)
        self.assertFalse(result.skipped)
        self.assertEqual(result.message_id, 1)
        self.assertEqual(len(db.saved), 1)

    def test_sends_notification_when_enabled(self):
        notifier = _FakeNotifier()
        service = _service(notifier=notifier, notify=True)
        service.run(slot="manual", now=NOW)

        self.assertEqual(len(notifier.calls), 1)
        call = notifier.calls[0]
        self.assertIn("助手日报", call["content"])
        self.assertEqual(call["dedup_key"], "proactive-brief:2026-11-05:manual")

    def test_does_not_notify_when_disabled(self):
        notifier = _FakeNotifier()
        service = _service(notifier=notifier, notify=False)
        service.run(slot="manual", now=NOW)

        self.assertEqual(notifier.calls, [])


if __name__ == "__main__":
    unittest.main()
