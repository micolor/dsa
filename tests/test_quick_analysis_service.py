# -*- coding: utf-8 -*-
"""Deterministic quick-mode summaries for scheduled tasks."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from src.services.quick_analysis_service import QuickAnalysisService


class _FakeFetcher:
    def get_realtime_quote(self, code):
        return SimpleNamespace(
            name="贵州茅台",
            price=18.12,
            change_pct=-2.11,
            volume_ratio=0.8,
        )

    def get_daily_data(self, code, days=30):
        closes = [20.0 - index * 0.05 for index in range(25)]
        return pd.DataFrame(
            {
                "date": [f"2026-10-{index + 1:02d}" for index in range(25)],
                "close": closes,
                "volume": [1000 + index for index in range(25)],
            }
        ), "fake"


class _FakeFundFetcher:
    def get_profile(self, code):
        from data_provider.fund_fetcher import FundProfile

        return FundProfile(
            code=code,
            name="测试基金",
            nav_history=[],
            return_1m=None,
            return_3m=None,
            return_6m=None,
            return_1y=0.12,
            max_drawdown=-0.18,
            annual_volatility=None,
            sharpe=None,
            holdings=[],
            asset_allocation=None,
        )


def test_quick_stock_summary_contains_price_and_indicators():
    service = QuickAnalysisService(fetcher=_FakeFetcher(), notifier=SimpleNamespace(send=lambda text: True))

    summary = service.analyze(["600519"])

    assert "贵州茅台(600519)" in summary
    assert "18.12" in summary
    assert "MA20" in summary
    assert "量比 0.80" in summary
    assert "不构成投资建议" in summary


def test_quick_summary_keeps_going_when_one_symbol_fails():
    class _BrokenFetcher(_FakeFetcher):
        def get_realtime_quote(self, code):
            if code == "000001":
                raise RuntimeError("quote unavailable")
            return super().get_realtime_quote(code)

        def get_daily_data(self, code, days=30):
            if code == "000001":
                raise RuntimeError("quote unavailable")
            return super().get_daily_data(code, days=days)

    service = QuickAnalysisService(fetcher=_BrokenFetcher())

    summary = service.analyze(["000001", "600519"])

    assert "000001" in summary
    assert "数据获取失败" in summary
    assert "600519" in summary


def test_quick_fund_summary_uses_nav_profile():
    service = QuickAnalysisService(
        fetcher=_FakeFetcher(),
        fund_fetcher=_FakeFundFetcher(),
    )

    summary = service.analyze(["fund:005827"])

    assert "测试基金" in summary
    assert "最新净值" in summary
