# -*- coding: utf-8 -*-
"""Tests for the weekly review service (no network)."""

from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pandas as pd

from src.services.weekly_review_service import WeeklyReviewService

TZ = ZoneInfo("Asia/Shanghai")
FRIDAY = datetime(2026, 11, 6, 15, 30, tzinfo=TZ)
THURSDAY = datetime(2026, 11, 5, 15, 30, tzinfo=TZ)


class _FakeFetcher:
    def get_stock_name(self, symbol):
        return {"600519": "贵州茅台", "000001": "平安银行"}.get(symbol)


class _FakePortfolio:
    def get_portfolio_snapshot(self, *, include_realtime=True):
        return {
            "total_equity": 100000.0,
            "unrealized_pnl": 1200.0,
            "realized_pnl": 300.0,
            "accounts": [{
                "account_id": 2,
                "account_name": "主账户",
                "total_equity": 100000.0,
                "positions": [{
                    "symbol": "600519",
                    "market_value_base": 50000.0,
                    "unrealized_pnl_pct": -12.5,
                }],
            }],
        }


class _FakeRisk:
    def get_risk_report(self):
        return {"drawdown": {"current_drawdown_pct": -20.0}}


class _FakeOutcomes:
    def list_outcomes(self, **kwargs):
        return {
            "items": [
                {"eval_status": "completed", "outcome": "hit", "stock_return_pct": 4.0, "excess_return_pct": 1.5},
                {"eval_status": "completed", "outcome": "miss", "stock_return_pct": -2.0, "excess_return_pct": -0.5},
                {"eval_status": "completed", "outcome": "neutral", "stock_return_pct": 0.2, "excess_return_pct": 0.1},
            ],
        }


class _FakeAlerts:
    def list_triggers(self, **kwargs):
        return {"items": [{
            "target": "600519",
            "reason": "跌破 MA20",
            "triggered_at": FRIDAY.isoformat(),
        }]}


class _FakeDb:
    def __init__(self):
        self.runs = set()
        self.session_messages = []
        self.saved = []

    def save_proactive_message(self, message_type, title, content):
        self.saved.append({"type": message_type, "title": title, "content": content})
        return len(self.saved)

    def has_proactive_brief_run(self, brief_date, slot):
        return (brief_date, slot) in self.runs

    def save_proactive_brief_run(self, brief_date, slot, session_id, message_id=None):
        self.runs.add((brief_date, slot))

    def get_conversation_messages(self, session_id, limit=200):
        return list(self.session_messages)


class _FakeNotifier:
    def __init__(self):
        self.calls = []

    def send(self, content, **kwargs):
        self.calls.append({"content": content, **kwargs})
        return True


def _history_loader(symbol, days):
    close = 110.0 if symbol == "600519" else 90.0
    prior = 100.0
    rows = [{"close": prior} for _ in range(5)] + [{"close": close}]
    return pd.DataFrame(rows), "fake"


def _service(*, enabled=True, notify=True, db=None, stock_list=None):
    config = SimpleNamespace(
        stock_list=stock_list or ["600519", "000001"],
        agent_risk_profile_horizon="medium",
        agent_risk_profile_max_drawdown_pct=15.0,
        agent_risk_profile_max_position_pct=35.0,
        agent_risk_profile_notes="",
        agent_weekly_review_enabled=enabled,
        agent_weekly_review_day=4,
        agent_weekly_review_time="15:30",
        agent_weekly_review_notify=notify,
    )
    return WeeklyReviewService(
        config,
        fetcher=_FakeFetcher(),
        history_loader=_history_loader,
        portfolio_service=_FakePortfolio(),
        risk_service=_FakeRisk(),
        outcome_service=_FakeOutcomes(),
        alert_service=_FakeAlerts(),
        db=db or _FakeDb(),
        notifier=_FakeNotifier(),
        clock=lambda: FRIDAY,
    )


class TestWeeklyReviewService(unittest.TestCase):
    def test_generate_contains_account_decision_risk_and_movers(self):
        review = _service().generate(FRIDAY)

        self.assertIn("每周复盘", review.markdown)
        self.assertIn("总权益", review.markdown)
        self.assertIn("命中率", review.markdown)
        self.assertIn("单票集中度", review.markdown)
        self.assertIn("组合回撤", review.markdown)
        self.assertIn("贵州茅台", review.markdown)
        self.assertIn("平安银行", review.markdown)
        self.assertIn("跌破 MA20", review.markdown)
        self.assertTrue(review.summary)

    def test_single_positive_mover_is_not_repeated_as_a_laggard(self):
        review = _service(stock_list=["600519"]).generate(FRIDAY)

        self.assertIn("领涨 贵州茅台", review.markdown)
        self.assertNotIn("领跌 贵州茅台", review.markdown)

    def test_run_persists_and_deduplicates_per_week(self):
        db = _FakeDb()
        service = _service(db=db)
        review = service.run(FRIDAY, force=True)
        self.assertIsNotNone(review)
        self.assertEqual(review.message_id, 1)
        self.assertTrue(review.notified)
        self.assertEqual(len(db.runs), 1)
        self.assertEqual(len(db.saved), 1)

        again = service.run(FRIDAY, force=False)
        self.assertIsNone(again)

    def test_run_due_only_fires_on_configured_day_and_time(self):
        service = _service()
        self.assertIsNone(service.run_due(THURSDAY))
        review = service.run_due(FRIDAY)
        self.assertIsNotNone(review)
        self.assertEqual(review.message_id, 1)

    def test_disabled_service_does_not_run_due(self):
        self.assertIsNone(_service(enabled=False).run_due(FRIDAY))


if __name__ == "__main__":
    unittest.main()
