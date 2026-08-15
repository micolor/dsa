# -*- coding: utf-8 -*-
"""
Baostock fundamental adapter for A-share fallback (fail-open).

Mirrors the bundle shape of `AkshareFundamentalAdapter.get_fundamental_bundle`
so it can be plugged into `data_provider.base.get_fundamental_context()` as a
fallback when the AkShare-only bundle returns no content. Reuses
`BaostockFetcher`'s connection lifecycle and code conversion so there is a
single source of truth for Baostock login/logout.

Data sources (free, no token, T+1):
- ``bs.query_profit_data``  — 盈利能力：ROE、毛利率、净利率、净利、营收
- ``bs.query_growth_data``  — 成长能力：净利润/归母净利润同比

This adapter intentionally treats every Baostock call as best-effort and never
raises to caller. Partial data is allowed.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _baostock_fetcher():
    # 延迟导入避免与 base.py 循环依赖（baostock_fetcher 内部引用 base）。
    from .baostock_fetcher import BaostockFetcher

    return BaostockFetcher()


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN guard
        return None
    return result


def _safe_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


class BaostockFundamentalAdapter:
    """Baostock fallback for A-share growth / earnings fundamentals."""

    def __init__(self, fetcher: Optional[Any] = None) -> None:
        self._fetcher = fetcher or _baostock_fetcher()

    def get_fundamental_bundle(self, stock_code: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "status": "not_supported",
            "growth": {},
            "earnings": {},
            "institution": {},
            "source_chain": [],
            "errors": [],
        }

        try:
            bs_code = self._fetcher._convert_stock_code(stock_code)
        except Exception as exc:
            result["errors"].append(f"convert:{type(exc).__name__}")
            return result

        growth_row = self._latest_profit_and_growth(bs_code, result)
        if growth_row is None:
            return result

        # 盈利能力（roe / 毛利率 / 净利率 / 净利 / 营收）
        profit = growth_row.get("profit")
        if profit:
            roe = _safe_float(profit.get("roeAvg"))
            gross_margin = _safe_float(profit.get("gpMargin"))
            net_margin = _safe_float(profit.get("npMargin"))
            net_profit = _safe_float(profit.get("netProfit"))
            revenue = _safe_float(profit.get("MBRevenue"))
            report_date = profit.get("statDate") or None

            result["growth"] = {
                "roe": roe,
                "gross_margin": gross_margin,
            }
            financial_report = {
                "report_date": report_date,
                "revenue": revenue,
                "net_profit_parent": net_profit,
                "roe": roe,
                "net_margin": net_margin,
            }
            if any(v is not None for v in financial_report.values()):
                result["earnings"]["financial_report"] = financial_report
            result["source_chain"].append("growth:baostock_profit")

        # 成长能力（净利同比 / 归母净利同比）
        growth = growth_row.get("growth")
        if growth:
            net_profit_yoy = _safe_float(growth.get("YOYNI"))
            net_profit_parent_yoy = _safe_float(growth.get("YOYPNI"))
            if net_profit_yoy is not None or net_profit_parent_yoy is not None:
                result["growth"].update(
                    {
                        "net_profit_yoy": net_profit_yoy,
                        "net_profit_parent_yoy": net_profit_parent_yoy,
                    }
                )
                result["source_chain"].append("growth:baostock_growth")

        has_content = bool(result["growth"] or result["earnings"])
        result["status"] = "partial" if has_content else "not_supported"
        return result

    def _latest_profit_and_growth(
        self,
        bs_code: str,
        result: Dict[str, Any],
    ) -> Optional[Dict[str, Optional[Dict[str, Any]]]]:
        """取 pubDate 早于今天的最近一份报告（从当年回退若干期）。

        Baostock 需显式传 year/quarter，且数据 T+1 更新。从当前季度向前
        回退最多 6 期，返回第一份已披露（pubDate <= today）的 profit+growth。
        """
        today = date.today()
        year, quarter = today.year, (today.month - 1) // 3 + 1

        try:
            with self._fetcher._baostock_session() as bs:
                for _ in range(6):
                    profit = self._query_latest(bs, "profit", bs_code, year, quarter, result)
                    growth = self._query_latest(bs, "growth", bs_code, year, quarter, result)
                    if profit:
                        pub_date = _safe_date(profit.get("pubDate"))
                        if pub_date is not None and pub_date <= today:
                            return {"profit": profit, "growth": growth}

                    # 回退上一季度
                    quarter -= 1
                    if quarter < 1:
                        quarter = 4
                        year -= 1
        except Exception as exc:
            result["errors"].append(f"baostock_session:{type(exc).__name__}")
            return None

        return None

    def _query_latest(
        self,
        bs: Any,
        kind: str,
        bs_code: str,
        year: int,
        quarter: int,
        result: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        try:
            if kind == "profit":
                rs = bs.query_profit_data(code=bs_code, year=year, quarter=quarter)
            else:
                rs = bs.query_growth_data(code=bs_code, year=year, quarter=quarter)

            if rs.error_code != "0":
                result["errors"].append(f"baostock_{kind}:{rs.error_msg}")
                return None

            if not rs.next():
                return None
            row = dict(zip(rs.fields, rs.get_row_data()))
            return row
        except Exception as exc:
            result["errors"].append(f"baostock_{kind}:{type(exc).__name__}")
            return None


def _safe_date(value: Any) -> Optional[date]:
    s = _safe_str(value)
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None
