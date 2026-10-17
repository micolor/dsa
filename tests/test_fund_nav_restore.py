# tests/test_fund_nav_restore.py
from data_provider.fund_fetcher import (
    FundDividendEvent,
    FundDistributionEvents,
    FundSplitEvent,
    NavRecord,
)
from src.services.fund_nav_restore import (
    compute_adjusted_series,
    compute_restored_metrics,
)


def _nav(day, unit, acc):
    return NavRecord(date=day, unit_nav=unit, acc_nav=acc, change_pct=0.0)


def test_dividend_reinvested_return_beats_unit_return():
    navs = [
        _nav("2025-01-02", 1.00, 1.10),
        _nav("2025-01-03", 0.90, 1.10),
        _nav("2025-01-06", 0.99, 1.10),
    ]
    events = FundDistributionEvents(
        dividends=[FundDividendEvent("2025-01-03", 0.10)]
    )
    result = compute_restored_metrics(navs, 0, 2, events)
    assert result["restored_basis"] == "dividend_reinvested"
    assert result["restored_return_pct"] == 10.0
    assert result["acc_nav_return_pct"] == 0.0


def test_split_multiplies_shares():
    navs = [
        _nav("2025-01-02", 2.00, 2.00),
        _nav("2025-01-03", 1.00, 2.00),
        _nav("2025-01-06", 1.10, 2.20),
    ]
    events = FundDistributionEvents(splits=[FundSplitEvent("2025-01-03", 2.0)])
    result = compute_restored_metrics(navs, 0, 2, events)
    assert result["restored_basis"] == "dividend_reinvested"
    assert result["restored_return_pct"] == 10.0


def test_no_events_still_uses_reinvested_basis():
    navs = [_nav("2025-01-02", 1.00, 1.10), _nav("2025-01-06", 1.10, 1.21)]
    result = compute_restored_metrics(navs, 0, 1, FundDistributionEvents())
    assert result["restored_basis"] == "dividend_reinvested"
    assert result["restored_events"] == 0
    assert result["restored_return_pct"] == 10.0


def test_invalid_distribution_event_falls_back_to_acc_nav():
    navs = [_nav("2025-01-02", 1.00, 1.10), _nav("2025-01-06", 1.10, 1.21)]
    events = FundDistributionEvents(
        dividends=[FundDividendEvent("2025-01-03", 0.0)]
    )
    result = compute_restored_metrics(navs, 0, 1, events)
    assert result["restored_basis"] == "acc_nav"
    assert result["restored_reason"] == "invalid_distribution_event"
    assert result["restored_return_pct"] == 10.0


def test_distribution_fetch_error_falls_back_to_acc_then_unit():
    navs = [_nav("2025-01-02", 1.00, 1.10), _nav("2025-01-06", 1.10, 1.21)]
    result = compute_restored_metrics(
        navs, 0, 1, None, events_error="distribution_fetch_error"
    )
    assert result["restored_basis"] == "acc_nav"

    navs_no_acc = [
        _nav("2025-01-02", 1.00, 0.0),
        _nav("2025-01-06", 1.10, 0.0),
    ]
    result = compute_restored_metrics(
        navs_no_acc, 0, 1, None, events_error="distribution_fetch_error"
    )
    assert result["restored_basis"] == "unit_nav"
    assert result["restored_reason"] == "distribution_fetch_error"
    assert result["restored_return_pct"] == 10.0


def test_restored_drawdown_uses_adjusted_series():
    navs = [
        _nav("2025-01-02", 1.00, 1.00),
        _nav("2025-01-03", 0.80, 0.90),
        _nav("2025-01-06", 1.00, 1.00),
    ]
    events = FundDistributionEvents(
        dividends=[FundDividendEvent("2025-01-03", 0.10)]
    )
    result = compute_restored_metrics(navs, 0, 2, events)
    # adjusted middle value: 0.80 * (1 + 0.10/0.80) = 0.90
    assert result["restored_max_drawdown_pct"] == -10.0


def test_compute_adjusted_series_applies_events_after_start():
    navs = [
        _nav("2025-01-02", 1.00, 1.10),
        _nav("2025-01-03", 0.90, 1.10),
        _nav("2025-01-06", 0.99, 1.10),
    ]
    events = FundDistributionEvents(
        dividends=[FundDividendEvent("2025-01-03", 0.10)]
    )
    series = compute_adjusted_series(navs, 0, events)
    assert series == [1.0, 1.0, 1.1]
