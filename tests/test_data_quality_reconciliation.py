# -*- coding: utf-8 -*-
"""Tests for cross-source reconciliation pure detectors + record method."""
from unittest import mock

from data_provider.realtime_types import UnifiedRealtimeQuote, RealtimeSource
from data_provider.base import (
    detect_cross_source_issue,
    detect_daily_cross_source_issue,
    DataFetcherManager,
)


def _quote(price, ts):
    """Build a UnifiedRealtimeQuote with only the fields the detector reads.

    NOTE: the SDD brief used ``provider=``/``pe=``/``pb=``/``market_cap=``,
    which are NOT fields of ``UnifiedRealtimeQuote`` (it uses ``source``,
    ``pe_ratio``, ``pb_ratio``, ``total_mv``/``circ_mv``). This helper builds
    only valid constructor args.
    """
    return UnifiedRealtimeQuote(
        code="600519",
        name="",
        source=RealtimeSource.TENCENT,
        price=price,
        change_pct=None,
        change_amount=None,
        volume=None,
        amount=None,
        open_price=price,
        high=price,
        low=price,
        pre_close=price,
        pe_ratio=None,
        pb_ratio=None,
        total_mv=None,
        circ_mv=None,
        provider_timestamp=ts,
        fetched_at=None,
        currency=None,
        market=None,
        is_stale=None,
        stale_seconds=None,
    )


def _make_manager():
    """Build a minimal DataFetcherManager without initializing network fetchers.

    ``DataProviderManager._create_default()`` (from the brief) does not exist in
    base.py (the class is ``DataFetcherManager``); the established scaffold
    pattern for unit tests is ``__new__`` + ``_ensure_concurrency_guards`` (see
    the comment on ``DataFetcherManager._ensure_concurrency_guards``).
    """
    mgr = DataFetcherManager.__new__(DataFetcherManager)
    mgr._ensure_concurrency_guards()
    return mgr


class _Cfg:
    """Stand-in for the Config dataclass; only exposes the fields we read."""

    def __init__(self, *, enabled=True, threshold_pct=1.0, tolerance_sec=3600):
        self.data_quality_reconciliation_enabled = enabled
        self.data_quality_price_diff_threshold_pct = threshold_pct
        self.data_quality_date_mismatch_tolerance_seconds = tolerance_sec


def test_detector_ok_no_issue():
    q1 = _quote(10.0, "2026-09-05T10:00:00")
    q2 = _quote(10.05, "2026-09-05T10:00:00")
    assert detect_cross_source_issue(q1, q2, price_threshold_pct=1.0, date_tolerance_seconds=3600) is None


def test_detector_price_discrepancy():
    q1 = _quote(10.0, "2026-09-05T10:00:00")
    q2 = _quote(9.0, "2026-09-05T10:00:00")  # 10% 价差 > 1%。
    res = detect_cross_source_issue(q1, q2, price_threshold_pct=1.0, date_tolerance_seconds=3600)
    assert res is not None and res["issue_type"] == "price_discrepancy"


def test_detector_date_mismatch():
    q1 = _quote(10.0, "2026-09-05T10:00:00")
    q2 = _quote(10.0, "2026-09-02T10:00:00")  # 相差数天。
    res = detect_cross_source_issue(q1, q2, price_threshold_pct=1.0, date_tolerance_seconds=3600)
    assert res is not None and res["issue_type"] == "date_mismatch"


def test_detector_missing_field():
    q1 = _quote(None, "2026-09-05T10:00:00")  # price 缺失。
    q2 = _quote(10.0, "2026-09-05T10:00:00")
    res = detect_cross_source_issue(q1, q2, price_threshold_pct=1.0, date_tolerance_seconds=3600)
    assert res is not None and res["issue_type"] == "field_missing"


def test_detector_negative_price_skips_price_compare():
    q1 = _quote(0.0, "2026-09-05T10:00:00")  # 无价。
    q2 = _quote(9.0, "2026-09-05T10:00:00")
    res = detect_cross_source_issue(q1, q2, price_threshold_pct=1.0, date_tolerance_seconds=3600)
    assert res is not None and res["issue_type"] == "field_missing"


def test_daily_detector_mismatch():
    res = detect_daily_cross_source_issue(
        10.0, "2026-09-05", 10.0, "2026-09-02",
        price_threshold_pct=1.0, date_tolerance_seconds=3600,
    )
    assert res is not None and res["issue_type"] == "date_mismatch"


def test_daily_detector_ok():
    res = detect_daily_cross_source_issue(
        10.0, "2026-09-05", 10.0, "2026-09-05",
        price_threshold_pct=1.0, date_tolerance_seconds=3600,
    )
    assert res is None


def _daily_df(*, close, date):
    """Build a daily-close DataFrame shaped like ``_clean_data`` output.

    ``_clean_data`` sorts by ``date`` then ``reset_index(drop=True)``, so the
    frame has a RangeIndex (integer row numbers) with the trade date in the
    ``date`` column. This mirrors the REAL shape the reconciles sees — the bug
    was reading ``df.index[-1]`` (an integer) as the trade date.
    """
    import pandas as pd
    return pd.DataFrame({"date": [date], "close": [close], "volume": [1000000]})


def test_daily_reconcile_reads_date_column_for_price_discrepancy():
    """Real RangeIndex df (date in column) must detect a price discrepancy.

    Regression for MAJOR-1: ``_reconcile_daily_cross_source`` previously read
    ``df.index[-1]`` (an integer RowNumber) as the trade date, which made
    ``detect_daily_cross_source_issue`` fail to parse and ALWAYS return
    ``field_missing``. It must instead read the ``date`` column and detect the
    real issue.
    """
    import pandas as pd
    mgr = _make_manager()
    primary_df = _daily_df(close=10.0, date=pd.Timestamp("2026-09-05"))
    cross_df = _daily_df(close=9.0, date=pd.Timestamp("2026-09-05"))  # 10% 价差。
    with mock.patch("src.config.get_config", return_value=_Cfg(enabled=True)):
        with mock.patch.object(mgr, "_update_data_quality_alert", return_value=None) as ma:
            mgr._reconcile_daily_cross_source(
                primary_df, cross_df, market="cn", stock_code="600519",
                primary_source="tencent", secondary_source="akshare_sina",
            )
    ma.assert_called_once()
    assert ma.call_args.kwargs["issue_type"] == "price_discrepancy"


def test_daily_reconcile_reads_date_column_for_date_mismatch():
    """Real RangeIndex df (date in column) must detect a date mismatch.

    Guards the same MAJOR-1 path: with date in the ``date`` column, different
    trade days must surface ``date_mismatch`` (not ``field_missing``).
    """
    import pandas as pd
    mgr = _make_manager()
    primary_df = _daily_df(close=10.0, date=pd.Timestamp("2026-09-05"))
    cross_df = _daily_df(close=10.0, date=pd.Timestamp("2026-09-02"))
    with mock.patch("src.config.get_config", return_value=_Cfg(enabled=True)):
        with mock.patch.object(mgr, "_update_data_quality_alert", return_value=None) as ma:
            mgr._reconcile_daily_cross_source(
                primary_df, cross_df, market="cn", stock_code="600519",
                primary_source="tencent", secondary_source="akshare_sina",
            )
    ma.assert_called_once()
    assert ma.call_args.kwargs["issue_type"] == "date_mismatch"


def test_record_method_hit_triggers_update():
    """Hit (10% price diff, gate on) should call _update_data_quality_alert."""
    mgr = _make_manager()
    q1 = _quote(10.0, "2026-09-05T10:00:00")
    q2 = _quote(9.0, "2026-09-05T10:00:00")
    with mock.patch("src.config.get_config", return_value=_Cfg(enabled=True)):
        with mock.patch.object(mgr, "_update_data_quality_alert", return_value=None) as ma:
            mgr._reconcile_realtime_cross_source(q1, q2, market="cn", stock_code="600519")
    ma.assert_called_once()
    assert ma.call_args.kwargs["issue_type"] == "price_discrepancy"


def test_hit_persists_and_alerts():
    """Hit should persist a record and emit a system alert (both mocked)."""
    mgr = _make_manager()
    q1 = _quote(10.0, "2026-09-05T10:00:00")
    q2 = _quote(9.0, "2026-09-05T10:00:00")
    with mock.patch("src.config.get_config", return_value=_Cfg(enabled=True)):
        with mock.patch("src.repositories.data_quality_discrepancy_repo.DataQualityDiscrepancyRepository") as m_repo:
            with mock.patch("src.services.system_alert.send_system_alert", return_value=True) as m_alert:
                mgr._reconcile_realtime_cross_source(q1, q2, market="cn", stock_code="600519")
    m_repo.return_value.record_discrepancy.assert_called_once()
    m_alert.assert_called_once()


def test_gate_off_does_nothing():
    """Gate (data_quality_reconciliation_enabled=False) -> no reconcile, no alert."""
    mgr = _make_manager()
    q1 = _quote(10.0, "2026-09-05T10:00:00")
    q2 = _quote(9.0, "2026-09-05T10:00:00")
    with mock.patch("src.config.get_config", return_value=_Cfg(enabled=False)):
        with mock.patch.object(mgr, "_update_data_quality_alert") as mock_alert:
            mgr._reconcile_realtime_cross_source(q1, q2, market="cn", stock_code="600519")
    mock_alert.assert_not_called()


def test_reconcile_never_raises_on_record_failure():
    """A failing _update_data_quality_alert must not break the fetch path."""
    mgr = _make_manager()
    q1 = _quote(10.0, "2026-09-05T10:00:00")
    q2 = _quote(9.0, "2026-09-05T10:00:00")
    with mock.patch("src.config.get_config", return_value=_Cfg(enabled=True)):
        with mock.patch.object(
            mgr, "_update_data_quality_alert", side_effect=RuntimeError("boom")
        ):
            # Must not raise.
            mgr._reconcile_realtime_cross_source(q1, q2, market="cn", stock_code="600519")


def test_reconcile_with_none_cross_is_noop():
    """A secondary source that is None must be silently skipped (no alert)."""
    mgr = _make_manager()
    q1 = _quote(10.0, "2026-09-05T10:00:00")
    with mock.patch("src.config.get_config", return_value=_Cfg(enabled=True)):
        with mock.patch.object(mgr, "_update_data_quality_alert") as mock_alert:
            mgr._reconcile_realtime_cross_source(q1, None, market="cn", stock_code="600519")
    mock_alert.assert_not_called()


def test_us_realtime_reconcile_fetches_secondary_once():
    """Reconciliation must not double-fetch the secondary source.

    When the primary quote needs supplement, ``secondary_src`` is fetched once
    to fill fields AND reused by the reconciliation — a second fetch would be a
    duplicate network call. We count ``_try_fetcher_quote`` per source and
    assert the secondary (YfinanceFetcher) is fetched EXACTLY once.
    """
    mgr = _make_manager()
    longbridge = mock.MagicMock()
    longbridge.name = "LongbridgeFetcher"
    longbridge.priority = 5
    longbridge.is_available_for_request.return_value = True
    longbridge.get_realtime_quote.return_value = _quote(10.0, "2026-09-05T10:00:00")
    yfinance = mock.MagicMock()
    yfinance.name = "YfinanceFetcher"
    yfinance.priority = 4
    yfinance.get_realtime_quote.return_value = _quote(9.0, "2026-09-05T10:00:00")
    mgr._fetchers = [longbridge, yfinance]
    mgr._refresh_fetcher_indexes_locked()

    cfg = mock.Mock(
        enable_realtime_quote=True,
        realtime_source_priority="efinance,akshare_em,tushare",
        realtime_cache_ttl=600,
        data_quality_reconciliation_enabled=True,
        data_quality_price_diff_threshold_pct=1.0,
        data_quality_date_mismatch_tolerance_seconds=3600,
    )
    with mock.patch("src.config.get_config", return_value=cfg):
        with mock.patch.object(mgr, "_update_data_quality_alert", return_value=None):
            with mock.patch.object(mgr, "_try_fetcher_quote", wraps=mgr._try_fetcher_quote) as m_try:
                quote = mgr.get_realtime_quote("AAPL")

    assert quote is not None
    yfinance_calls = [c for c in m_try.call_args_list if c.args[1] == "YfinanceFetcher"]
    assert len(yfinance_calls) == 1, (
        f"secondary source YfinanceFetcher fetched {len(yfinance_calls)} times; "
        "expected exactly once (supplement + reconcile must share one fetch)"
    )
