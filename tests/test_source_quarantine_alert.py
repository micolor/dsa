# -*- coding: utf-8 -*-
"""数据源熔断（可用→隔离）跃迁时的单次告警通知测试。"""
from unittest import mock

from data_provider.base import DataFetcherManager
from data_provider.realtime_types import CircuitBreaker


def _mk_fetcher(name="akshare"):
    f = mock.Mock(name=name)
    f.name = name
    f.priority = 1
    return f


def test_quarantine_open_fires_alert_once(monkeypatch):
    calls = []
    monkeypatch.setattr(DataFetcherManager, "_get_daily_source_health",
                        classmethod(lambda cls: CircuitBreaker(failure_threshold=1, cooldown_seconds=300.0)))
    monkeypatch.setattr(
        "src.services.system_alert.send_system_alert",  # base.py 内部以 `from ... import` 形式导入，需 monkeypatch 源头
        lambda content, dedup_key: calls.append((content, dedup_key)) or True,
    )
    DataFetcherManager.reset_daily_source_health()
    f = _mk_fetcher()
    DataFetcherManager._record_daily_source_failure(f, "cn", "timeout")
    assert len(calls) == 1
    assert calls[0][1].startswith("source-quarantine:cn:akshare")
    # 再次失败（已熔断）不再重复
    DataFetcherManager._record_daily_source_failure(f, "cn", "timeout")
    assert len(calls) == 1
