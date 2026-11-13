# -*- coding: utf-8 -*-
"""Runtime scheduler wiring for the proactive daily brief."""

from __future__ import annotations

from types import SimpleNamespace

from src.services.runtime_scheduler import RuntimeSchedulerService


def _service():
    service = object.__new__(RuntimeSchedulerService)
    service._background_task_cache = {}
    service._background_task_registered_names = set()
    service._reload_config = lambda: SimpleNamespace()  # only called when the task runs
    return service


def test_registers_brief_task_when_enabled():
    service = _service()
    config = SimpleNamespace(agent_proactive_brief_enabled=True)

    tasks = service._current_proactive_brief_background_tasks(config)

    assert len(tasks) == 1
    assert tasks[0]["name"] == "proactive_daily_brief"
    assert tasks[0]["interval_seconds"] == 300
    assert tasks[0]["run_immediately"] is True


def test_skips_brief_task_when_disabled():
    service = _service()
    config = SimpleNamespace(agent_proactive_brief_enabled=False)

    assert service._current_proactive_brief_background_tasks(config) == []


def test_registers_event_analysis_task_when_enabled():
    service = _service()
    config = SimpleNamespace(
        agent_event_analysis_enabled=True,
        agent_event_analysis_interval_minutes=10,
    )

    tasks = service._current_event_analysis_background_tasks(config)

    assert len(tasks) == 1
    assert tasks[0]["name"] == "event_driven_agent"
    assert tasks[0]["interval_seconds"] == 600
    assert tasks[0]["run_immediately"] is False


def test_skips_event_analysis_task_when_disabled():
    service = _service()
    config = SimpleNamespace(agent_event_analysis_enabled=False)

    assert service._current_event_analysis_background_tasks(config) == []


def test_registers_weekly_review_task_when_enabled():
    service = _service()
    config = SimpleNamespace(agent_weekly_review_enabled=True)

    tasks = service._current_weekly_review_background_tasks(config)

    assert len(tasks) == 1
    assert tasks[0]["name"] == "weekly_review"
    assert tasks[0]["interval_seconds"] == 300
    assert tasks[0]["run_immediately"] is False


def test_skips_weekly_review_task_when_disabled():
    service = _service()
    config = SimpleNamespace(agent_weekly_review_enabled=False)

    assert service._current_weekly_review_background_tasks(config) == []


def test_registers_opportunity_scan_task_when_enabled():
    service = _service()
    config = SimpleNamespace(agent_opportunity_scan_enabled=True)

    tasks = service._current_opportunity_scan_background_tasks(config)

    assert len(tasks) == 1
    assert tasks[0]["name"] == "opportunity_scan"
    assert tasks[0]["interval_seconds"] == 300
    assert tasks[0]["run_immediately"] is False


def test_skips_opportunity_scan_task_when_disabled():
    service = _service()
    config = SimpleNamespace(agent_opportunity_scan_enabled=False)

    assert service._current_opportunity_scan_background_tasks(config) == []
