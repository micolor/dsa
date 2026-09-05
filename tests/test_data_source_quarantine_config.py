from types import SimpleNamespace
from unittest.mock import patch

from data_provider.base import DataFetcherManager


def test_daily_source_health_respects_config_threshold_cooldown():
    # Patch get_config() (not os.environ): Config is a process-wide singleton, so
    # env vars alone would be ineffective once config was built by an earlier test.
    with patch(
        "src.config.get_config",
        return_value=SimpleNamespace(
            data_source_quarantine_threshold=5,
            data_source_quarantine_recovery_seconds=42,
            runtime_analysis_failure_alert_enabled=True,
            runtime_backfill_enabled=True,
            runtime_backfill_max_days=1,
        ),
    ):
        DataFetcherManager.reset_daily_source_health()
        breaker = DataFetcherManager._get_daily_source_health()
        assert breaker.failure_threshold == 5
        assert breaker.cooldown_seconds == 42
        # 取消 reset 后的缓存，恢复默认
        DataFetcherManager.reset_daily_source_health()
