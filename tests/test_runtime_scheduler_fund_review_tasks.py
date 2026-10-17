# -*- coding: utf-8 -*-
"""RuntimeSchedulerService fund NAV review background task tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from src.services.runtime_scheduler import RuntimeSchedulerService


def _svc():
    svc = RuntimeSchedulerService(config_provider=mock.Mock(), owns_schedule=False)
    svc._analysis_lock_path = lambda config: "/tmp/test-fund-nav-review.lock"
    return svc


enabled_config = SimpleNamespace(fund_nav_review_auto_eval_enabled=True)
disabled_config = SimpleNamespace(fund_nav_review_auto_eval_enabled=False)


def test_task_registered_when_enabled():
    svc = _svc()
    tasks = svc._current_fund_nav_review_background_tasks(enabled_config)
    assert len(tasks) == 1
    assert tasks[0]["name"] == "fund_nav_review_evaluation"
    assert tasks[0]["run_immediately"] is True
    assert tasks[0]["interval_seconds"] == 21600
    assert callable(tasks[0]["task"])


def test_task_removed_when_disabled():
    svc = _svc()
    svc._background_task_cache["fund_nav_review_evaluation"] = {
        "task": lambda: None,
        "interval_seconds": 21600,
    }
    svc._background_task_registered_names.add("fund_nav_review_evaluation")
    assert svc._current_fund_nav_review_background_tasks(disabled_config) == []
    assert "fund_nav_review_evaluation" not in svc._background_task_cache
    assert "fund_nav_review_evaluation" not in svc._background_task_registered_names


def test_task_invokes_the_review_service():
    svc = _svc()
    task = svc._current_fund_nav_review_background_tasks(enabled_config)[0]["task"]
    service = mock.Mock()
    with mock.patch(
        "src.services.fund_nav_review_service.FundNavReviewService",
        return_value=service,
    ):
        task()
    service.run_reviews.assert_called_once_with(limit=100)


def test_task_survives_service_failure():
    svc = _svc()
    task = svc._current_fund_nav_review_background_tasks(enabled_config)[0]["task"]
    service = mock.Mock()
    service.run_reviews.side_effect = RuntimeError("boom")
    with mock.patch(
        "src.services.fund_nav_review_service.FundNavReviewService",
        return_value=service,
    ):
        task()  # must not raise
    service.run_reviews.assert_called_once()


def test_background_task_list_includes_fund_review():
    svc = _svc()
    config = SimpleNamespace(
        agent_event_monitor_enabled=False,
        paper_trading_enabled=False,
        signal_outcome_auto_eval_enabled=False,
        fund_nav_review_auto_eval_enabled=True,
    )
    names = [task["name"] for task in svc._current_background_tasks(config)]
    assert names == ["fund_nav_review_evaluation"]
