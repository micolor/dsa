from types import SimpleNamespace
from datetime import date
from unittest import mock
from src.services.runtime_scheduler import RuntimeSchedulerService


def _svc(runner):
    svc = RuntimeSchedulerService(
        config_provider=mock.Mock(), task_runner=runner, owns_schedule=False)
    svc._analysis_lock_path = lambda config: "/tmp/test-backfill.lock"
    return svc


config_with_backfill_disabled = SimpleNamespace(
    runtime_backfill_enabled=False, runtime_backfill_max_days=1)


def test_backfill_triggered_when_gap_beyond_max_days():
    runs = []
    def _runner(config, args, stocks):
        runs.append(1)
        return True
    svc = _svc(_runner)
    svc._last_success_at = "2026-09-01T18:00:00"  # 距有效交易日（09-05）差 4 天 > max_days=1
    with mock.patch("src.services.runtime_scheduler.get_effective_trading_date", return_value=date(2026, 9, 5)):
        svc._maybe_trigger_backfill(None)
    assert len(runs) == 1


def test_backfill_skipped_within_max_days():
    runs = []
    def _runner(config, args, stocks):
        runs.append(1)
        return True
    svc = _svc(_runner)
    svc._last_success_at = "2026-09-04T18:00:00"  # 距 09-05 差 1 天，未超 max_days=1
    with mock.patch("src.services.runtime_scheduler.get_effective_trading_date", return_value=date(2026, 9, 5)):
        svc._maybe_trigger_backfill(None)
    assert len(runs) == 0


def test_backfill_disabled_or_no_success_noop():
    runs = []
    def _runner(config, args, stocks):
        runs.append(1)
        return True
    svc = _svc(_runner)
    # 无 _last_success_at -> noop
    with mock.patch("src.services.runtime_scheduler.get_effective_trading_date", return_value=date(2026, 9, 5)):
        svc._maybe_trigger_backfill(None)
    assert len(runs) == 0
    # disabled -> noop
    svc._last_success_at = "2026-09-01T18:00:00"
    with mock.patch.object(svc, "_reload_config",
                           return_value=config_with_backfill_disabled):
        svc._maybe_trigger_backfill(None)
    assert len(runs) == 0
