# -*- coding: utf-8 -*-
"""RuntimeSchedulerService signal-outcome background task unit tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from src.services.runtime_scheduler import RuntimeSchedulerService


def _svc():
    svc = RuntimeSchedulerService(config_provider=mock.Mock(), owns_schedule=False)
    svc._analysis_lock_path = lambda config: "/tmp/test-signal-outcome.lock"
    return svc


enabled_config = SimpleNamespace(signal_outcome_auto_eval_enabled=True)
disabled_config = SimpleNamespace(signal_outcome_auto_eval_enabled=False)


def test_task_registered_when_enabled():
    svc = _svc()
    tasks = svc._current_signal_outcome_background_tasks(enabled_config)
    assert len(tasks) == 1
    assert tasks[0]["name"] == "signal_outcome_evaluation"
    assert tasks[0]["run_immediately"] is True
    assert tasks[0]["interval_seconds"] == 1800
    assert callable(tasks[0]["task"])


def test_task_removed_when_disabled():
    svc = _svc()
    svc._background_task_cache["signal_outcome_evaluation"] = {
        "task": lambda: None,
        "interval_seconds": 1800,
    }
    svc._background_task_registered_names.add("signal_outcome_evaluation")
    assert svc._current_signal_outcome_background_tasks(disabled_config) == []
    assert "signal_outcome_evaluation" not in svc._background_task_cache
    assert "signal_outcome_evaluation" not in svc._background_task_registered_names


def test_task_invokes_both_services():
    svc = _svc()
    task = svc._current_signal_outcome_background_tasks(enabled_config)[0]["task"]
    skill = mock.Mock()
    decision = mock.Mock()
    with mock.patch(
        "src.services.skill_opinion_outcome_service.SkillOpinionOutcomeService",
        return_value=skill,
    ), mock.patch(
        "src.services.decision_signal_outcome_service.DecisionSignalOutcomeService",
        return_value=decision,
    ):
        task()
    skill.run_outcomes.assert_called_once()
    decision.run_outcomes.assert_called_once()


def test_task_survives_service_failure():
    svc = _svc()
    task = svc._current_signal_outcome_background_tasks(enabled_config)[0]["task"]
    skill = mock.Mock()
    skill.run_outcomes.side_effect = RuntimeError("boom")
    decision = mock.Mock()
    with mock.patch(
        "src.services.skill_opinion_outcome_service.SkillOpinionOutcomeService",
        return_value=skill,
    ), mock.patch(
        "src.services.decision_signal_outcome_service.DecisionSignalOutcomeService",
        return_value=decision,
    ):
        task()  # must not raise
    decision.run_outcomes.assert_called_once()
