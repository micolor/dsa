# -*- coding: utf-8 -*-
"""Scheduled task CRUD, calendar occurrences and slot dedup."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from src.services.scheduled_task_service import (
    ScheduledTaskService,
    ScheduledTaskValidationError,
)
from src.storage import DatabaseManager


def _service(tmp_path):
    # DatabaseManager is a process singleton; reset it so each test gets its own file.
    DatabaseManager._instance = None  # type: ignore[attr-defined]
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'scheduled.db'}")
    return ScheduledTaskService(config=SimpleNamespace(stock_list=[]), db=db), db


def test_create_update_list_delete(tmp_path):
    service, _ = _service(tmp_path)

    task = service.create_task(
        {
            "name": "每日复盘",
            "task_type": "market_review",
            "time_of_day": "15:30",
            "weekdays": [0, 1, 2, 3, 4],
            "payload": {"region": "cn", "notify": False},
            "trading_day_only": True,
        }
    )
    assert task["task_type"] == "market_review"
    assert task["weekdays"] == [0, 1, 2, 3, 4]
    assert service.list_tasks()[0]["name"] == "每日复盘"

    updated = service.update_task(task["id"], {"enabled": False, "time_of_day": "16:00"})
    assert updated["enabled"] is False
    assert updated["time_of_day"] == "16:00"

    assert service.delete_task(task["id"]) is True
    assert service.list_tasks() == []


def test_validation_rejects_bad_payload(tmp_path):
    service, _ = _service(tmp_path)

    with pytest.raises(ScheduledTaskValidationError):
        service.create_task(
            {
                "name": "bad",
                "task_type": "screening",
                "time_of_day": "25:00",
                "weekdays": [0],
                "payload": {"strategy": "capital_heat"},
            }
        )
    with pytest.raises(ScheduledTaskValidationError):
        service.create_task(
            {
                "name": "bad",
                "task_type": "agent_prompt",
                "time_of_day": "09:00",
                "weekdays": [0],
                "payload": {"prompt": "hi"},
            }
        )


def test_quick_mode_is_normalized(tmp_path):
    service, _ = _service(tmp_path)

    task = service.create_task(
        {
            "name": "快速行情",
            "task_type": "stock_analysis",
            "time_of_day": "09:00",
            "weekdays": [0],
            "payload": {"stocks": ["600519"], "mode": "quick", "notify": True},
        }
    )

    assert task["payload"]["mode"] == "quick"
    assert task["payload"]["notify"] is True


def test_quick_mode_dispatch_skips_analysis_pipeline(tmp_path, monkeypatch):
    service, _ = _service(tmp_path)
    task = service.create_task(
        {
            "name": "快速行情",
            "task_type": "stock_analysis",
            "time_of_day": "09:00",
            "weekdays": [0],
            "payload": {"stocks": ["600519"], "mode": "quick"},
        }
    )
    calls = []

    class FakeQuick:
        def __init__(self, config):
            self.config = config

        def analyze(self, codes, notify=False):
            calls.append((list(codes), notify))
            return "快速摘要"

    monkeypatch.setattr(
        "src.services.quick_analysis_service.QuickAnalysisService",
        FakeQuick,
    )

    status, message = service._dispatch(task)

    assert status == "success"
    assert message == "快速摘要"
    assert calls == [(["600519"], False)]


def test_data_refresh_and_paper_auto_trade_payloads_are_normalized(tmp_path):
    service, _ = _service(tmp_path)

    refresh = service.create_task(
        {
            "name": "数据刷新",
            "task_type": "data_refresh",
            "time_of_day": "08:40",
            "weekdays": [0, 1, 2, 3, 4],
            "payload": {"strategy": "capital_heat", "market": "cn", "max_results": 20},
        }
    )
    trade = service.create_task(
        {
            "name": "模拟盘执行",
            "task_type": "paper_auto_trade",
            "time_of_day": "16:30",
            "weekdays": [0, 1, 2, 3, 4],
            "payload": {"lookback_days": 7},
        }
    )

    assert refresh["payload"] == {"strategy": "capital_heat", "market": "cn", "max_results": 20}
    assert refresh["task_type"] == "data_refresh"
    assert trade["payload"] == {"lookback_days": 7}

    with pytest.raises(ScheduledTaskValidationError):
        service.create_task(
            {
                "name": "bad",
                "task_type": "paper_auto_trade",
                "time_of_day": "16:30",
                "weekdays": [0],
                "payload": {"lookback_days": 0},
            }
        )


def test_data_refresh_dispatch_runs_screening_and_hotspots(tmp_path, monkeypatch):
    service, _ = _service(tmp_path)
    task = service.create_task(
        {
            "name": "数据刷新",
            "task_type": "data_refresh",
            "time_of_day": "08:40",
            "weekdays": [0, 1, 2, 3, 4],
            "payload": {"strategy": "capital_heat", "market": "cn", "max_results": 20},
            "trading_day_only": False,
        }
    )
    calls = []

    class FakeRefresh:
        def __init__(self, config, *, db=None):
            self.config = config
            self.db = db

        def run(self, *, slot, force, strategy, market, max_results):
            calls.append((slot, force, strategy, market, max_results))
            return {"status": "ok", "candidates": 5, "hotspots": 4}

    class FakeQueue:
        def submit_background_task(self, run_task, **_kwargs):
            run_task()
            return SimpleNamespace(task_id="refresh-task-1")

    monkeypatch.setattr(
        "src.services.proactive_data_refresh_service.ProactiveDataRefreshService",
        FakeRefresh,
    )
    monkeypatch.setattr(
        "src.services.task_queue.get_task_queue",
        lambda: FakeQueue(),
    )

    status, message = service._dispatch(task)

    assert status == "submitted"
    assert "refresh-task-1" in message
    assert calls == [(f"manual:{task['id']}", True, "capital_heat", "cn", 20)]


def test_paper_auto_trade_dispatch_reports_retryable_failure(tmp_path, monkeypatch):
    service, _ = _service(tmp_path)
    task = service.create_task(
        {
            "name": "模拟盘执行",
            "task_type": "paper_auto_trade",
            "time_of_day": "16:30",
            "weekdays": [0, 1, 2, 3, 4],
            "payload": {"lookback_days": 7},
            "trading_day_only": False,
        }
    )
    calls = []

    class FakePaper:
        def __init__(self, db):
            self.db = db

        def auto_trade_once(self, *, lookback_days):
            calls.append(lookback_days)
            return {
                "status": "ok",
                "processed": 1,
                "skipped": 2,
                "unavailable": 1,
                "errors": 0,
            }

    monkeypatch.setattr("src.services.paper_service.PaperService", FakePaper)

    status, message = service._dispatch(task)

    assert status == "failed"
    assert "缺行情 1 条" in message
    assert calls == [7]


def test_calendar_returns_weekday_occurrences(tmp_path):
    service, _ = _service(tmp_path)
    service.create_task(
        {
            "name": "周一选股",
            "task_type": "screening",
            "time_of_day": "09:10",
            "weekdays": [0],
            "payload": {"strategy": "capital_heat", "market": "cn"},
            "trading_day_only": False,
        }
    )

    calendar = service.calendar(date(2026, 11, 9), date(2026, 11, 15))

    slots = [item["scheduled_for"] for item in calendar["occurrences"]]
    assert slots == ["2026-11-09T09:10"]
    assert calendar["occurrences"][0]["status"] in {"missed", "scheduled"}


def test_calendar_maps_run_status_to_occurrence(tmp_path):
    service, db = _service(tmp_path)
    task = service.create_task(
        {
            "name": "周一复盘",
            "task_type": "market_review",
            "time_of_day": "15:30",
            "weekdays": [0],
            "payload": {"region": "cn"},
            "trading_day_only": False,
        }
    )
    scheduled_for = datetime(2026, 11, 9, 15, 30)
    db.save_scheduled_task_run(
        task_id=task["id"],
        trigger_source="schedule",
        status="submitted",
        scheduled_for=scheduled_for,
        slot_key=f"{task['id']}:{scheduled_for.isoformat(timespec='minutes')}",
    )

    calendar = service.calendar(date(2026, 11, 9), date(2026, 11, 9))
    occurrence = next(
        item for item in calendar["occurrences"] if item["task"]["id"] == task["id"]
    )

    assert occurrence["status"] == "submitted"


def test_agent_prompt_dispatch_uses_general_single_agent(tmp_path, monkeypatch):
    service, _ = _service(tmp_path)
    task = service.create_task(
        {
            "name": "持仓周报",
            "task_type": "agent_prompt",
            "time_of_day": "18:00",
            "weekdays": [0],
            "payload": {"prompt": "分析我的持仓风险并列出三个观察点"},
            "trading_day_only": False,
        }
    )
    calls = []

    class FakeExecutor:
        def chat(self, *, message, session_id):
            calls.append((message, session_id))
            return SimpleNamespace(content="结论")

    monkeypatch.setattr(
        "src.agent.factory.build_general_chat_executor",
        lambda config: FakeExecutor(),
    )

    status, message = service._dispatch(task)

    assert status == "success"
    assert message == "结论"
    assert calls == [("分析我的持仓风险并列出三个观察点", f"scheduled:{task['id']}")]


def test_same_schedule_slot_executes_once(tmp_path):
    service, _ = _service(tmp_path)
    task = service.create_task(
        {
            "name": "回测",
            "task_type": "backtest",
            "time_of_day": "09:00",
            "weekdays": [0],
            "payload": {"limit": 10},
            "trading_day_only": False,
        }
    )
    calls = []

    def fake_dispatch(target):
        calls.append(target["id"])
        return "submitted", "ok"

    service._dispatch = fake_dispatch  # type: ignore[assignment]
    slot = datetime(2026, 11, 9, 9, 0)

    first = service.execute(task, trigger_source="schedule", scheduled_for=slot)
    second = service.execute(task, trigger_source="schedule", scheduled_for=slot)

    assert first["status"] == "submitted"
    assert second["status"] == "skipped"
    assert calls == [task["id"]]


def test_run_due_accepts_timezone_aware_now(tmp_path):
    from zoneinfo import ZoneInfo

    service, _ = _service(tmp_path)
    service.create_task(
        {
            "name": "aware",
            "task_type": "market_review",
            "time_of_day": "15:30",
            "weekdays": [0, 1, 2, 3, 4, 5, 6],
            "payload": {"region": "cn"},
            "trading_day_only": False,
        }
    )
    service._dispatch = lambda task: ("submitted", "ok")  # type: ignore[assignment]

    results = service.run_due(datetime(2026, 11, 9, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai")))

    assert results and results[0]["status"] == "submitted"


def test_run_due_only_fires_in_current_minute(tmp_path):
    service, _ = _service(tmp_path)
    service.create_task(
        {
            "name": "盘后复盘",
            "task_type": "market_review",
            "time_of_day": "15:30",
            "weekdays": [0, 1, 2, 3, 4, 5, 6],
            "payload": {"region": "cn"},
            "trading_day_only": False,
        }
    )
    calls = []
    service._dispatch = lambda task: (calls.append(task["id"]), ("submitted", "ok"))[1]  # type: ignore[assignment]

    service.run_due(datetime(2026, 11, 9, 15, 31, 30))
    service.run_due(datetime(2026, 11, 9, 15, 45, 0))

    assert len(calls) == 1
