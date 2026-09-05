# -*- coding: utf-8 -*-
import os
import tempfile
from unittest import mock

from src.services.runtime_scheduler import RuntimeSchedulerService


def _svc(alert_calls, runner):
    def alert(content):
        alert_calls.append(content)
        return True

    svc = RuntimeSchedulerService(
        config_provider=mock.Mock(),
        task_runner=runner,
        alert_sender=alert,
        owns_schedule=False,
    )
    # Isolate the cross-process file lock to a unique, unheld path so that
    # acquire() succeeds under test (the real DB lock file may already be held
    # by a running process). Does not change production lock/retry behavior.
    svc._analysis_lock_path = lambda config: os.path.join(
        tempfile.mkdtemp(), "test.analysis.lock"
    )
    return svc


def test_failure_triggers_alert_once_when_runner_returns_false():
    calls = []
    runner = mock.Mock(return_value=False)  # runner returns False => raise
    svc = _svc(calls, runner)
    svc._run_analysis_locked(None)
    assert len(calls) == 1
    assert "每日股票分析失败" in calls[0]


def test_failure_alert_suppressed_same_day():
    calls = []
    runner = mock.Mock(return_value=False)
    svc = _svc(calls, runner)
    svc._run_analysis_locked(None)
    svc._run_analysis_locked(None)  # 第二次同日 -> 不重复
    assert len(calls) == 1


def test_success_resets_failure_alert_gate():
    calls = []
    runner = mock.Mock(side_effect=[False, True])  # 先失败后成功
    svc = _svc(calls, runner)
    svc._run_analysis_locked(None)       # 失败 -> alert (date=day)
    svc._run_analysis_locked(None)       # 成功 -> reset gate
    svc._run_analysis_locked(None)       # 再失败 -> 允许再 alert
    assert len(calls) == 2
