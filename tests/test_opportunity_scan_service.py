# -*- coding: utf-8 -*-
"""Tests for the opportunity scan service (no network)."""

from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from src.services.opportunity_scan_service import OpportunityScanService

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 5, 8, 45, tzinfo=TZ)


class _FakeScreening:
    def __init__(self, *, created_at="2026-11-04T15:30:00+08:00", candidates=None):
        self.created_at = created_at
        self.screen_calls = []
        self.candidates = candidates if candidates is not None else [{
            "code": "600967",
            "name": "内蒙一机",
            "score": 72.2,
            "llm_score": 78.0,
            "llm_theme": "国防军工 / 地面兵装",
            "llm_thesis": "资金承接扎实，量价配合良好。",
            "llm_risks": ["换手偏高", "PE 109 估值偏高"],
            "llm_watch_items": ["次日量比能否维持 1.5 以上"],
        }]

    def history(self, limit=1):
        return {"runs": [{"run_id": "run-1", "created_at": self.created_at}]}

    def history_detail(self, run_id):
        return {
            "run_id": run_id,
            "created_at": self.created_at,
            "result": {"candidates": self.candidates},
        }

    def screen(self, **kwargs):
        self.screen_calls.append(kwargs)
        self.created_at = "2026-11-05T08:44:00+08:00"
        return {"candidates": self.candidates}

    def hotspots(self, top=5):
        return {
            "stale": False,
            "stale_age_hours": 0,
            "cached_at": "2026-11-05T08:00:00Z",
            "hotspots": [{
                "name": "光伏辅材",
                "change_pct": 9.94,
                "heat_score": 98.6,
                "state": "加速发酵",
            }],
        }


class _FakePortfolio:
    def get_portfolio_snapshot(self, *, include_realtime=False):
        return {"accounts": [{"positions": [{"symbol": "600519"}]}]}


class _FakeDb:
    def __init__(self):
        self.runs = set()
        self.messages = []
        self.saved = []

    def save_proactive_message(self, message_type, title, content):
        self.saved.append({"type": message_type, "title": title, "content": content})
        return len(self.saved)

    def has_proactive_brief_run(self, brief_date, slot):
        return (brief_date, slot) in self.runs

    def save_proactive_brief_run(self, brief_date, slot, session_id, message_id=None):
        self.runs.add((brief_date, slot))

    def get_conversation_messages(self, session_id, limit=200):
        return list(self.messages)


class _FakeNotifier:
    def __init__(self):
        self.calls = []

    def send(self, content, **kwargs):
        self.calls.append({"content": content, **kwargs})
        return True


def _service(*, screening=None, db=None, enabled=True, notify=True, refresh=False):
    config = SimpleNamespace(
        stock_list=["600967"],
        agent_opportunity_scan_enabled=enabled,
        agent_opportunity_scan_times="08:45,15:45",
        agent_opportunity_scan_max_candidates=5,
        agent_opportunity_scan_max_age_hours=72,
        agent_opportunity_scan_notify=notify,
        agent_opportunity_scan_refresh=refresh,
        agent_opportunity_scan_refresh_strategy="capital_heat",
        agent_opportunity_scan_refresh_market="cn",
        agent_opportunity_scan_refresh_max_results=20,
        agent_proactive_quiet_hours="",
    )
    return OpportunityScanService(
        config,
        screening_service=screening or _FakeScreening(),
        portfolio_service=_FakePortfolio(),
        db=db or _FakeDb(),
        notifier=_FakeNotifier(),
        clock=lambda: NOW,
    )


class TestOpportunityScanService(unittest.TestCase):
    def test_generate_contains_candidates_hotspots_and_ownership(self):
        scan = _service().generate(slot="08:45", now=NOW)

        self.assertIn("内蒙一机", scan.markdown)
        self.assertIn("国防军工", scan.markdown)
        self.assertIn("换手偏高", scan.markdown)
        self.assertIn("次日量比", scan.markdown)
        self.assertIn("已在自选", scan.markdown)
        self.assertIn("光伏辅材", scan.markdown)
        self.assertNotIn("已过期", scan.markdown)

    def test_generate_warns_when_screening_snapshot_is_stale(self):
        screening = _FakeScreening(created_at="2026-10-18T07:05:00+08:00")
        scan = _service(screening=screening).generate(slot="08:45", now=NOW)

        self.assertIn("选股快照已过期", scan.markdown)
        self.assertIn("建议先刷新数据", scan.markdown)

    def test_auto_refresh_runs_screening_when_snapshot_is_stale(self):
        screening = _FakeScreening(created_at="2026-10-18T07:05:00+08:00")
        scan = _service(screening=screening, refresh=True).generate(slot="08:45", now=NOW)

        self.assertEqual(len(screening.screen_calls), 1)
        self.assertEqual(screening.screen_calls[0]["strategy"], "capital_heat")
        self.assertEqual(screening.screen_calls[0]["market"], "cn")
        self.assertNotIn("选股快照已过期", scan.markdown)

    def test_stale_snapshot_is_not_refreshed_when_auto_refresh_is_off(self):
        screening = _FakeScreening(created_at="2026-10-18T07:05:00+08:00")
        scan = _service(screening=screening, refresh=False).generate(slot="08:45", now=NOW)

        self.assertEqual(screening.screen_calls, [])
        self.assertIn("选股快照已过期", scan.markdown)

    def test_generate_handles_missing_screening_run(self):
        service = _service()
        service._screening_service = MagicMock()
        service._screening_service.history.return_value = {"runs": []}

        scan = service.generate(slot="manual", now=NOW)

        self.assertIn("暂无选股快照", scan.markdown)

    def test_run_persists_and_deduplicates_same_slot(self):
        db = _FakeDb()
        service = _service(db=db)

        first = service.run_due(NOW)
        self.assertIsNotNone(first)
        self.assertEqual(first.message_id, 1)
        self.assertTrue(first.notified)
        self.assertEqual(len(db.runs), 1)

        second = service.run_due(NOW)
        self.assertIsNone(second)

    def test_disabled_service_does_not_run_due(self):
        self.assertIsNone(_service(enabled=False).run_due(NOW))


if __name__ == "__main__":
    unittest.main()
