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


config_with_backfill_enabled = SimpleNamespace(
    runtime_backfill_enabled=True, runtime_backfill_max_days=1)


def _svc_for_real_run(runner):
    import os
    import tempfile

    svc = RuntimeSchedulerService(
        config_provider=mock.Mock(), task_runner=runner, owns_schedule=False)
    svc._analysis_lock_path = lambda config: os.path.join(
        tempfile.mkdtemp(), "test.analysis.lock")
    return svc


def test_real_run_triggers_backfill_from_the_previous_success_time():
    """走真实入口：缺口基准必须是「上一次成功」，而不是刚被覆盖的本次成功。

    成功分支先把 ``_last_success_at`` 写成 now，再调 ``_maybe_trigger_backfill``；
    若后者读实例字段，gap 恒为 0 或 ±1，判据 ``gap > max_days`` 永不成立，
    跨交易日缺口只能靠手工补跑。
    """
    runs = []

    def _runner(config, args, stocks):
        runs.append(1)
        return True

    svc = _svc_for_real_run(_runner)
    svc._last_success_at = "2026-09-01T18:00:00"  # 距有效交易日（09-05）差 4 天
    with mock.patch.object(svc, "_reload_config",
                           return_value=config_with_backfill_enabled), \
            mock.patch("src.services.runtime_scheduler.get_effective_trading_date",
                       return_value=date(2026, 9, 5)):
        svc._run_analysis_locked(None)
    # 正常一次 + 补跑一次
    assert len(runs) == 2


def test_real_run_does_not_backfill_on_the_first_ever_success():
    runs = []

    def _runner(config, args, stocks):
        runs.append(1)
        return True

    svc = _svc_for_real_run(_runner)
    with mock.patch.object(svc, "_reload_config",
                           return_value=config_with_backfill_enabled), \
            mock.patch("src.services.runtime_scheduler.get_effective_trading_date",
                       return_value=date(2026, 9, 5)):
        svc._run_analysis_locked(None)
    assert len(runs) == 1


def test_backfill_never_recurses_even_when_gap_still_open():
    """补跑本身再判一次缺口也必须停在第一层，否则会无限递归。

    这里把缺口函数钉成恒为开放（每次成功都看到同样的旧基准），
    没有 ``_backfill_in_progress`` 闸门时这一调用会一路递归到 RecursionError。
    """
    runs = []

    def _runner(config, args, stocks):
        runs.append(1)
        return True

    svc = _svc_for_real_run(_runner)
    svc._last_success_at = "2026-09-01T18:00:00"
    with mock.patch.object(svc, "_reload_config",
                           return_value=config_with_backfill_enabled), \
            mock.patch.object(svc, "_trading_day_gap_since", return_value=9):
        svc._run_analysis_locked(None)
    assert len(runs) == 2


def test_negative_max_days_is_clamped_and_does_not_recurse():
    runs = []

    def _runner(config, args, stocks):
        runs.append(1)
        return True

    svc = _svc_for_real_run(_runner)
    svc._last_success_at = "2026-09-05T18:00:00"  # gap = 0
    negative = SimpleNamespace(
        runtime_backfill_enabled=True, runtime_backfill_max_days=-5)
    with mock.patch.object(svc, "_reload_config", return_value=negative), \
            mock.patch("src.services.runtime_scheduler.get_effective_trading_date",
                       return_value=date(2026, 9, 5)):
        svc._run_analysis_locked(None)
    assert len(runs) == 1
