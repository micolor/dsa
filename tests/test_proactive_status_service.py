# -*- coding: utf-8 -*-
"""Tests for proactive task status observability."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from src.services.proactive_status_service import ProactiveStatusService

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 7, 9, 0, tzinfo=TZ)


class _FakeDb:
    def get_proactive_type_last_activity(self):
        return {
            "daily_brief": "2026-11-06T15:30:00+08:00",
            "event": "2026-11-07T08:55:00+08:00",
        }


def _config():
    return SimpleNamespace(
        agent_proactive_brief_enabled=True,
        agent_proactive_brief_times="08:30,15:30",
        agent_event_analysis_enabled=True,
        agent_event_analysis_interval_minutes=10,
        agent_weekly_review_enabled=True,
        agent_weekly_review_day=4,
        agent_weekly_review_time="15:30",
        agent_opportunity_scan_enabled=True,
        agent_opportunity_scan_times="08:45,15:45",
    )


def test_status_reports_last_and_next_runs():
    status = ProactiveStatusService(config=_config(), db=_FakeDb()).get_status(NOW)
    tasks = {task["key"]: task for task in status["tasks"]}

    assert tasks["daily_brief"]["last_run"] == "2026-11-06T15:30:00+08:00"
    assert tasks["daily_brief"]["next_run"].startswith("2026-11-07T15:30:00")
    assert tasks["event"]["last_run"] == "2026-11-07T08:55:00+08:00"
    assert tasks["event"]["next_run"].startswith("2026-11-07T09:10:00")
    assert tasks["weekly"]["enabled"] is True
    assert tasks["opportunity"]["next_run"].startswith("2026-11-07T15:45:00")


def test_disabled_task_has_no_next_run():
    config = _config()
    config.agent_opportunity_scan_enabled = False
    status = ProactiveStatusService(config=config, db=_FakeDb()).get_status(NOW)
    tasks = {task["key"]: task for task in status["tasks"]}

    assert tasks["opportunity"]["enabled"] is False
    assert tasks["opportunity"]["next_run"] is None
