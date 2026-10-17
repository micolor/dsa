# -*- coding: utf-8 -*-
"""Dividend-reinvested restoration for fund NAV review outcomes.

The fund review ledger stores unit-NAV outcomes. Funds that pay dividends look
like losses under that basis, because the unit NAV drops on the ex-dividend
day. This module restores the interval return by reinvesting cash dividends at
the first NAV on or after the ex-dividend date, and multiplies share counts for
split/conversion events.

Fallback order (recorded on every evaluated row):

1. ``dividend_reinvested`` — distribution events parsed and usable;
2. ``acc_nav`` — cumulative NAV available for the whole window;
3. ``unit_nav`` — the original v1 basis.

Only pure functions live here so the service layer can cache network results
once per fund and keep the arithmetic testable.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from data_provider.fund_fetcher import (
    FundDistributionEvents,
    NavRecord,
)

RESTORED_BASIS_DIVIDEND_REINVESTED = "dividend_reinvested"
RESTORED_BASIS_ACC = "acc_nav"
RESTORED_BASIS_UNIT = "unit_nav"


def _day(value: str) -> str:
    return (value or "")[:10]


def _acc_metrics(
    navs: Sequence[NavRecord], anchor_idx: int, target_idx: int
) -> Dict[str, Any]:
    window = navs[anchor_idx:target_idx + 1]
    values = [float(record.acc_nav or 0) for record in window]
    anchor_acc = values[0] if values else 0.0
    eval_acc = values[-1] if values else 0.0
    if not values or anchor_acc <= 0 or eval_acc <= 0 or any(v <= 0 for v in values):
        return {
            "anchor_acc_nav": anchor_acc if anchor_acc > 0 else None,
            "eval_acc_nav": eval_acc if eval_acc > 0 else None,
            "acc_nav_return_pct": None,
            "acc_max_drawdown_pct": None,
        }
    peak = anchor_acc
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1.0)
    return {
        "anchor_acc_nav": anchor_acc,
        "eval_acc_nav": eval_acc,
        "acc_nav_return_pct": round((eval_acc / anchor_acc - 1.0) * 100.0, 4),
        "acc_max_drawdown_pct": round(drawdown * 100.0, 4),
    }


def compute_adjusted_series(
    navs: Sequence[NavRecord],
    start_idx: int,
    events: FundDistributionEvents,
    end_idx: Optional[int] = None,
) -> Optional[List[float]]:
    """Return the adjusted value series for ``navs[start_idx:end_idx + 1]``.

    The factor is 1.0 at ``start_idx``. Events strictly after the start date and
    on or before the end date are applied at the first NAV on/after their date
    (a non-trading ex-dividend date is approximated by the next record). Invalid
    event values return ``None`` so the caller can fall back instead of guessing.
    """
    if not navs:
        return None
    last_idx = len(navs) - 1 if end_idx is None else min(end_idx, len(navs) - 1)
    if start_idx < 0 or start_idx > last_idx:
        return None
    window = navs[start_idx:last_idx + 1]
    by_date: Dict[str, float] = {}
    for record in window:
        day = _day(record.date)
        if day:
            by_date[day] = float(record.unit_nav or 0)
    dates = sorted(by_date)
    if not dates:
        return None
    start_day = dates[0]
    end_day = dates[-1]

    event_rows = [
        ("dividend", _day(event.date), float(event.cash_per_share))
        for event in events.dividends
    ] + [
        ("split", _day(event.date), float(event.ratio))
        for event in events.splits
    ]
    event_rows = sorted(
        (row for row in event_rows if start_day < row[1] <= end_day),
        key=lambda row: row[1],
    )

    values: List[float] = []
    factor = 1.0
    event_index = 0
    for day in dates:
        while event_index < len(event_rows) and event_rows[event_index][1] <= day:
            kind, _, amount = event_rows[event_index]
            event_index += 1
            if amount <= 0:
                return None
            if kind == "split":
                factor *= amount
                continue
            nav_at_event = by_date.get(day, 0.0)
            if nav_at_event <= 0:
                return None
            factor *= 1.0 + amount / nav_at_event
        values.append(by_date[day] * factor)
    return values


def _drawdown_pct(values: Sequence[float]) -> Optional[float]:
    if not values or values[0] <= 0:
        return None
    peak = values[0]
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1.0)
    return round(drawdown * 100.0, 4)


def _events_in_window(
    navs: Sequence[NavRecord],
    anchor_idx: int,
    target_idx: int,
    events: FundDistributionEvents,
) -> int:
    start_day = _day(navs[anchor_idx].date)
    end_day = _day(navs[target_idx].date)
    return sum(
        1
        for event in list(events.dividends) + list(events.splits)
        if start_day < _day(event.date) <= end_day
    )


def compute_restored_metrics(
    navs: Sequence[NavRecord],
    anchor_idx: int,
    target_idx: int,
    events: Optional[FundDistributionEvents],
    *,
    events_error: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute the best available restored metrics for one review row."""
    result: Dict[str, Any] = {
        **_acc_metrics(navs, anchor_idx, target_idx),
        "restored_return_pct": None,
        "restored_max_drawdown_pct": None,
        "restored_basis": None,
        "restored_reason": None,
        "restored_events": 0,
    }

    adjusted = (
        compute_adjusted_series(navs, anchor_idx, events, end_idx=target_idx)
        if events is not None
        else None
    )
    if adjusted and adjusted[0] > 0:
        result.update({
            "restored_return_pct": round(
                (adjusted[-1] / adjusted[0] - 1.0) * 100.0, 4
            ),
            "restored_max_drawdown_pct": _drawdown_pct(adjusted),
            "restored_basis": RESTORED_BASIS_DIVIDEND_REINVESTED,
            "restored_events": _events_in_window(
                navs, anchor_idx, target_idx, events
            ),
        })
        return result

    reason = (
        (events_error or "distribution_fetch_error")
        if events is None
        else "invalid_distribution_event"
    )
    if result["acc_nav_return_pct"] is not None:
        result.update({
            "restored_return_pct": result["acc_nav_return_pct"],
            "restored_max_drawdown_pct": result["acc_max_drawdown_pct"],
            "restored_basis": RESTORED_BASIS_ACC,
            "restored_reason": reason,
        })
        return result

    unit_values = [
        float(record.unit_nav or 0) for record in navs[anchor_idx:target_idx + 1]
    ]
    if unit_values and unit_values[0] > 0 and unit_values[-1] > 0:
        result.update({
            "restored_return_pct": round(
                (unit_values[-1] / unit_values[0] - 1.0) * 100.0, 4
            ),
            "restored_max_drawdown_pct": _drawdown_pct(unit_values),
            "restored_basis": RESTORED_BASIS_UNIT,
            "restored_reason": reason or "acc_nav_unavailable",
        })
    return result
