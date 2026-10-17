# -*- coding: utf-8 -*-
"""Benchmark return helpers for decision-signal forward outcomes."""

from __future__ import annotations

from datetime import date
import logging
import math
from typing import Any, Callable, Dict, Optional, Tuple

from src.repositories.stock_repo import StockRepository
from src.storage import DatabaseManager


logger = logging.getLogger(__name__)

# Tradable ETF proxies, not index points: the data providers' daily-bar paths
# are stock/ETF oriented and normalize index codes into stock codes.
#   cn: 510300 tracks CSI 300 (sh000300 would be normalized to 000300)
#   hk: hk02800 is the Tracker Fund of Hong Kong (HSI proxy)
#   us: SPY tracks S&P 500
BENCHMARK_BY_MARKET: Dict[str, str] = {
    "cn": "510300",
    "hk": "hk02800",
    "us": "SPY",
}

BENCHMARK_STATUS_AVAILABLE = "available"
BENCHMARK_STATUS_MISSING = "missing"
BENCHMARK_STATUS_UNSUPPORTED = "unsupported"
BENCHMARK_STATUS_ERROR = "error"

BENCHMARK_STATUSES = frozenset({
    BENCHMARK_STATUS_AVAILABLE,
    BENCHMARK_STATUS_MISSING,
    BENCHMARK_STATUS_UNSUPPORTED,
    BENCHMARK_STATUS_ERROR,
})
BENCHMARK_RETRYABLE_STATUSES = (
    BENCHMARK_STATUS_MISSING,
    BENCHMARK_STATUS_ERROR,
)

BenchmarkFetcher = Callable[[str, str, str], Tuple[Any, str]]


def benchmark_code_for_market(market: Optional[str]) -> Optional[str]:
    """Return the configured proxy code, or None when the market has no proxy."""
    return BENCHMARK_BY_MARKET.get(str(market or "").strip().lower())


def compute_benchmark_return(
    *,
    stock_repo: StockRepository,
    benchmark_code: str,
    anchor_date: date,
    window_end_date: date,
) -> Dict[str, Any]:
    """Compute the benchmark return over an already fixed stock window.

    Both endpoints must exist exactly: no previous-trading-day fallback. The
    window end is the stock's own last bar date so a suspended stock does not
    shift the benchmark window.
    """
    if window_end_date < anchor_date:
        return {
            "benchmark_status": BENCHMARK_STATUS_MISSING,
            "benchmark_return_pct": None,
        }

    anchor_bar = stock_repo.get_daily_on_date(code=benchmark_code, target_date=anchor_date)
    if anchor_bar is None or not _is_positive_finite(getattr(anchor_bar, "close", None)):
        return {
            "benchmark_status": BENCHMARK_STATUS_MISSING,
            "benchmark_return_pct": None,
        }

    end_bar = stock_repo.get_daily_on_date(code=benchmark_code, target_date=window_end_date)
    if end_bar is None or not _is_positive_finite(getattr(end_bar, "close", None)):
        return {
            "benchmark_status": BENCHMARK_STATUS_MISSING,
            "benchmark_return_pct": None,
        }

    start_price = float(anchor_bar.close)
    end_price = float(end_bar.close)
    return {
        "benchmark_status": BENCHMARK_STATUS_AVAILABLE,
        "benchmark_return_pct": (end_price - start_price) / start_price * 100,
    }


def ensure_benchmark_bars(
    *,
    db_manager: DatabaseManager,
    benchmark_code: str,
    anchor_date: date,
    window_end_date: date,
    fetcher: Optional[BenchmarkFetcher] = None,
) -> bool:
    """Fetch and persist benchmark daily bars for the anchor..window-end range.

    Fail-open: any failure is logged and returns False so the caller records an
    `error` benchmark status without disturbing the stock outcome.
    """
    if not benchmark_code:
        return False
    start_date = anchor_date.strftime("%Y-%m-%d")
    end_date = window_end_date.strftime("%Y-%m-%d")
    days = max((window_end_date - anchor_date).days + 7, 30)
    try:
        if fetcher is None:
            from data_provider.base import DataFetcherManager

            df, source = DataFetcherManager().get_daily_data(
                stock_code=benchmark_code,
                start_date=start_date,
                end_date=end_date,
                days=days,
            )
        else:
            df, source = fetcher(benchmark_code, start_date, end_date)
        if df is None or df.empty:
            return False
        db_manager.save_daily_data(df, code=benchmark_code, data_source=source)
        return True
    except Exception as exc:
        logger.warning("基准日线补全失败(%s): %s", benchmark_code, exc)
        return False


def _is_positive_finite(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0
