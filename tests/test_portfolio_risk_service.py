# -*- coding: utf-8 -*-
"""Unit tests for PortfolioRiskService stop-loss guard, backfill cap and symbol merge."""

from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.services.portfolio_risk_service import (
    DRAWDOWN_BACKFILL_BATCH_DAYS,
    PortfolioRiskService,
)


class PortfolioRiskStopLossTest(unittest.TestCase):
    def _thresholds(self) -> dict:
        return {"stop_loss_alert_pct": 10.0, "stop_loss_near_ratio": 0.8}

    def _snapshot(self, positions) -> dict:
        return {"accounts": [{"account_id": 1, "positions": positions}]}

    @staticmethod
    def _pos(symbol, avg_cost, last_price, price_available=True) -> dict:
        return {
            "symbol": symbol,
            "market": "cn",
            "avg_cost": avg_cost,
            "last_price": last_price,
            "price_available": price_available,
        }

    def test_missing_price_is_not_a_false_stop_loss_trigger(self) -> None:
        # 缺价（last_price=0 / price_available=False）不应按 (avg_cost-0)/avg_cost 误算成 100% 亏损而误报。
        result = PortfolioRiskService._build_stop_loss(
            self._snapshot([
                self._pos("600519", avg_cost=100.0, last_price=0.0, price_available=False),
                self._pos("600036", avg_cost=100.0, last_price=120.0),
            ]),
            self._thresholds(),
        )
        self.assertEqual(result["triggered_count"], 0)
        self.assertFalse(result["near_alert"])
        self.assertEqual(len(result["items"]), 0)

    def test_real_triggered_stop_loss_is_still_flagged(self) -> None:
        # 真实触发（现价显著低于成本）仍应被标记。
        result = PortfolioRiskService._build_stop_loss(
            self._snapshot([
                self._pos("600519", avg_cost=100.0, last_price=85.0),  # 15% 亏损 >= 10% 触发
            ]),
            self._thresholds(),
        )
        self.assertEqual(result["triggered_count"], 1)
        self.assertTrue(result["items"][0]["is_triggered"])

    def test_near_stop_loss_is_counted_as_near_but_not_triggered(self) -> None:
        # 9% 亏损 >= near_threshold(0.8*10%=8%) 计入 near，但 < 10% 不触发。
        result = PortfolioRiskService._build_stop_loss(
            self._snapshot([
                self._pos("600519", avg_cost=100.0, last_price=91.0),
            ]),
            self._thresholds(),
        )
        self.assertEqual(result["near_count"], 1)
        self.assertFalse(result["items"][0]["is_triggered"])

    def test_drawdown_backfill_caps_per_call_batch(self) -> None:
        # 首屏 /risk 不应一次性补齐整个 lookback 窗口，最近一批即可。
        repo = MagicMock()
        repo.list_accounts.return_value = [SimpleNamespace(id=1)]
        repo.get_first_activity_date.return_value = date(2020, 1, 1)
        repo.list_daily_snapshots_for_risk.return_value = []
        svc = PortfolioRiskService(repo=repo)
        svc.portfolio_service.get_portfolio_snapshot = MagicMock()

        svc._ensure_drawdown_snapshot_window(
            account_id=1,
            as_of_date=date(2026, 1, 2),
            cost_method="fifo",
            lookback_days=365,
            include_realtime=False,
        )
        self.assertLessEqual(svc.portfolio_service.get_portfolio_snapshot.call_count, DRAWDOWN_BACKFILL_BATCH_DAYS)

    def test_concentration_merges_symbol_format_variants(self) -> None:
        # 同一股票以 600519 / SH600519 两种格式出现时应合并为一条，避免集中度被拆分。
        svc = PortfolioRiskService(repo=MagicMock())
        svc.portfolio_service.convert_amount = MagicMock(return_value=(1000.0, False, None))
        snapshot = {
            "total_market_value": 2000.0,
            "accounts": [
                {
                    "account_id": 1,
                    "base_currency": "CNY",
                    "market": "cn",
                    "positions": [
                        {"symbol": "600519", "market": "cn", "market_value_base": 1000.0, "valuation_currency": "CNY"},
                        {"symbol": "SH600519", "market": "cn", "market_value_base": 1000.0, "valuation_currency": "CNY"},
                    ],
                },
            ],
        }
        result = svc._build_concentration(snapshot, 35.0, as_of_date=date(2026, 1, 2))
        self.assertEqual(len(result["top_positions"]), 1)
        self.assertAlmostEqual(result["top_positions"][0]["weight_pct"], 100.0, places=4)
