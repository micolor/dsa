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
