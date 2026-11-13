# -*- coding: utf-8 -*-
"""Tests for the independent proactive data refresh."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.services.proactive_data_refresh_service import ProactiveDataRefreshService

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 9, 8, 40, tzinfo=TZ)


class _FakeScreening:
    def __init__(self):
        self.screen_calls = []
        self.hotspot_calls = []

    def screen(self, **kwargs):
        self.screen_calls.append(kwargs)
        return {"candidates": [{"code": "600519"}, {"code": "000001"}]}

    def hotspots(self, **kwargs):
        self.hotspot_calls.append(kwargs)
        return {"hotspots": [{"name": "测试热点"}]}


class _FakeDb:
    def __init__(self):
        self.runs = set()

    def has_proactive_brief_run(self, brief_date, slot):
        return (brief_date, slot) in self.runs

    def save_proactive_brief_run(self, brief_date, slot, session_id, message_id=None):
        self.runs.add((brief_date, slot))


def _service(screening=None, db=None):
    config = SimpleNamespace(
        agent_data_refresh_enabled=True,
        agent_data_refresh_times="08:40,15:40",
        agent_data_refresh_strategy="capital_heat",
        agent_data_refresh_market="cn",
        agent_data_refresh_max_results=20,
        agent_proactive_market_guard=True,
    )
    return ProactiveDataRefreshService(
        config,
        screening_service=screening or _FakeScreening(),
        db=db or _FakeDb(),
        clock=lambda: NOW,
    )


def test_run_refreshes_screening_and_hotspots():
    screening = _FakeScreening()
    db = _FakeDb()
    result = _service(screening=screening, db=db).run(force=True, now=NOW)

    assert result["status"] == "ok"
    assert result["candidates"] == 2
    assert result["hotspots"] == 1
    assert screening.screen_calls[0]["strategy"] == "capital_heat"
    assert screening.hotspot_calls[0]["refresh"] is True
    assert db.runs


def test_run_due_is_idempotent_for_same_slot():
    screening = _FakeScreening()
    db = _FakeDb()
    service = _service(screening=screening, db=db)
    with patch("src.services.proactive_market_guard.is_trading_day", return_value=True):
        first = service.run_due(NOW)
        second = service.run_due(NOW)

    assert first is not None
    assert second is None
    assert len(screening.screen_calls) == 1


def test_market_guard_skips_non_trading_day():
    screening = _FakeScreening()
    service = _service(screening=screening)
    with patch("src.services.proactive_market_guard.is_trading_day", return_value=False):
        result = service.run_due(NOW)

    assert result is None
    assert screening.screen_calls == []
