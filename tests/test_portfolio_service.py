# -*- coding: utf-8 -*-
"""Unit tests for portfolio replay service (P0 PR1 scope)."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import patch

import pandas as pd
from sqlalchemy.exc import OperationalError
from sqlalchemy import select

from src.config import Config
from src.repositories.portfolio_repo import PortfolioBusyError, PortfolioRepository
from src.services import portfolio_cache
from src.services.portfolio_service import (
    _AvgState,
    _QUOTE_REFRESH_INFLIGHT,
    PortfolioConflictError,
    PortfolioOversellError,
    PortfolioService,
)
from src.storage import DatabaseManager, PortfolioDailySnapshot, PortfolioPosition, PortfolioPositionLot, PortfolioTrade


class PortfolioServiceTestCase(unittest.TestCase):
    """Portfolio service replay tests for FIFO/AVG and corporate actions."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_path = Path(self.temp_dir.name) / ".env"
        self.db_path = Path(self.temp_dir.name) / "portfolio_test.db"
        self.env_path.write_text(
            "\n".join(
                [
                    "STOCK_LIST=600519",
                    "GEMINI_API_KEY=test",
                    "ADMIN_AUTH_ENABLED=false",
                    f"DATABASE_PATH={self.db_path}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        os.environ["ENV_FILE"] = str(self.env_path)
        os.environ["DATABASE_PATH"] = str(self.db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()

        self.db = DatabaseManager.get_instance()
        self.service = PortfolioService()
        # 后台刷新线程是 daemon，模块级 in-flight 标记可能跨测试残留，避免误跳过取数
        _QUOTE_REFRESH_INFLIGHT.clear()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    def _save_close(self, symbol: str, on_date: date, close: float) -> None:
        df = pd.DataFrame(
            [
                {
                    "date": on_date,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1.0,
                    "amount": close,
                    "pct_chg": 0.0,
                }
            ]
        )
        self.db.save_daily_data(df, code=symbol, data_source="unit-test")

    def _cache_quote(
        self,
        symbol: str,
        price: float,
        provider: str = "unit-test",
        quote_date: Optional[date] = None,
    ) -> None:
        """种子 position_quote_cache：给 _resolve_position_price 提供新鲜实时缓存。"""
        self.service.repo.upsert_cached_quote(
            symbol=symbol,
            price=price,
            provider=provider,
            quote_date=quote_date or date.today(),
        )

    def _create_account_with_position(
        self,
        *,
        market: str,
        currency: str,
        symbol: str,
        quantity: float = 10.0,
        price: float = 100.0,
        close: Optional[float] = None,
        close_date: Optional[date] = None,
    ) -> int:
        account = self.service.create_account(name=f"{market}-account", broker="Demo", market=market, base_currency=currency)
        aid = account["id"]
        self.service.record_cash_ledger(
            account_id=aid,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=100000,
            currency=currency,
        )
        self.service.record_trade(
            account_id=aid,
            symbol=symbol,
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=quantity,
            price=price,
            market=market,
            currency=currency,
        )
        if close is not None:
            self._save_close(self.service._normalize_symbol(symbol), close_date or date(2026, 1, 3), close)
        return aid

    def test_current_snapshot_uses_cached_quote_when_close_missing(self) -> None:
        today = date.today()
        account = self.service.create_account(name="Main", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]
        self.service.record_trade(
            account_id=aid,
            symbol="600519",
            trade_date=today,
            side="buy",
            quantity=10,
            price=100,
            market="cn",
            currency="CNY",
        )
        self._cache_quote("600519", 125.0, "unit-test", today)

        with patch.object(PortfolioService, "_fetch_realtime_position_price", return_value=(None, None)):
            snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=today, cost_method="fifo")

        pos = snapshot["accounts"][0]["positions"][0]
        self.assertAlmostEqual(pos["last_price"], 125.0, places=6)
        self.assertAlmostEqual(pos["market_value_base"], 1250.0, places=6)
        self.assertAlmostEqual(pos["unrealized_pnl_base"], 250.0, places=6)
        self.assertEqual(pos["price_source"], "realtime_cached")
        self.assertEqual(pos["price_provider"], "unit-test")
        self.assertFalse(pos["price_stale"])
        self.assertEqual(pos["price_date"], today.isoformat())
        self.assertTrue(pos["price_available"])

    def test_current_snapshot_prefers_cached_quote_over_stale_close(self) -> None:
        today = date.today()
        account = self.service.create_account(name="Main", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]
        self.service.record_trade(
            account_id=aid,
            symbol="600519",
            trade_date=today,
            side="buy",
            quantity=10,
            price=100,
            market="cn",
            currency="CNY",
        )
        self._save_close("600519", today - timedelta(days=1), 110.0)
        self._cache_quote("600519", 125.0, "unit-test", today)

        with patch.object(PortfolioService, "_fetch_realtime_position_price", return_value=(None, None)):
            snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=today, cost_method="fifo")

        pos = snapshot["accounts"][0]["positions"][0]
        self.assertAlmostEqual(pos["last_price"], 125.0, places=6)
        self.assertAlmostEqual(pos["market_value_base"], 1250.0, places=6)
        self.assertAlmostEqual(pos["unrealized_pnl_base"], 250.0, places=6)
        self.assertEqual(pos["price_source"], "realtime_cached")
        self.assertEqual(pos["price_provider"], "unit-test")
        self.assertEqual(pos["price_date"], today.isoformat())
        self.assertFalse(pos["price_stale"])
        self.assertTrue(pos["price_available"])

    def test_current_snapshot_prefers_cached_quote_over_same_day_close(self) -> None:
        today = date.today()
        account = self.service.create_account(name="Main", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]
        self.service.record_trade(
            account_id=aid,
            symbol="600519",
            trade_date=today,
            side="buy",
            quantity=10,
            price=100,
            market="cn",
            currency="CNY",
        )
        self._save_close("600519", today, 118.0)
        self._cache_quote("600519", 125.0, "unit-test", today)

        with patch.object(PortfolioService, "_fetch_realtime_position_price", return_value=(None, None)):
            snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=today, cost_method="fifo")

        pos = snapshot["accounts"][0]["positions"][0]
        self.assertAlmostEqual(pos["last_price"], 125.0, places=6)
        self.assertAlmostEqual(pos["market_value_base"], 1250.0, places=6)
        self.assertAlmostEqual(pos["unrealized_pnl_base"], 250.0, places=6)
        self.assertEqual(pos["price_source"], "realtime_cached")
        self.assertEqual(pos["price_provider"], "unit-test")
        self.assertEqual(pos["price_date"], today.isoformat())
        self.assertFalse(pos["price_stale"])
        self.assertTrue(pos["price_available"])

    def test_historical_snapshot_ignores_realtime_quote_cache(self) -> None:
        """历史日期的快照必须用当日收盘价，而不是实时缓存里的「当前价」。

        缓存每 symbol 只有一行、取的是最近一次抓取，没有日期维度；只要当天
        打开过持仓页（后台刷新会写缓存），回撤回填的过去日期就会拿今天的价格
        去估值，并连同 positions / lots / daily snapshot 一起落库。
        """
        today = date.today()
        as_of = today - timedelta(days=5)
        account = self.service.create_account(name="Main", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]
        self.service.record_trade(
            account_id=aid,
            symbol="600519",
            trade_date=as_of - timedelta(days=2),
            side="buy",
            quantity=10,
            price=100,
            market="cn",
            currency="CNY",
        )
        self._save_close("600519", as_of, 110.0)
        # 缓存里是今天的 125.0，与 as_of 当天的真实收盘价 110.0 相差很大
        self._cache_quote("600519", 125.0, "unit-test", today)

        with patch.object(PortfolioService, "_fetch_realtime_position_price", return_value=(None, None)):
            snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=as_of, cost_method="fifo")

        pos = snapshot["accounts"][0]["positions"][0]
        self.assertEqual(pos["price_source"], "history_close")
        self.assertEqual(pos["price_date"], as_of.isoformat())
        self.assertAlmostEqual(pos["last_price"], 110.0, places=6)
        self.assertAlmostEqual(pos["market_value_base"], 1100.0, places=6)

    def test_current_snapshot_falls_back_to_close_when_realtime_unavailable(self) -> None:
        today = date.today()
        account = self.service.create_account(name="Main", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]
        self.service.record_trade(
            account_id=aid,
            symbol="600519",
            trade_date=today,
            side="buy",
            quantity=10,
            price=100,
            market="cn",
            currency="CNY",
        )
        self._save_close("600519", today, 118.0)

        with patch.object(
            PortfolioService,
            "_fetch_realtime_position_price",
            return_value=(None, None),
        ):
            snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=today, cost_method="fifo")

        pos = snapshot["accounts"][0]["positions"][0]
        self.assertAlmostEqual(pos["last_price"], 118.0, places=6)
        self.assertAlmostEqual(pos["market_value_base"], 1180.0, places=6)
        self.assertAlmostEqual(pos["unrealized_pnl_base"], 180.0, places=6)
        self.assertEqual(pos["price_source"], "history_close")
        self.assertTrue(pos["price_available"])

    def test_current_snapshot_can_skip_realtime_quote_for_fast_load(self) -> None:
        today = date.today()
        account = self.service.create_account(name="Main", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]
        self.service.record_trade(
            account_id=aid,
            symbol="600519",
            trade_date=today,
            side="buy",
            quantity=10,
            price=100,
            market="cn",
            currency="CNY",
        )
        self._save_close("600519", today - timedelta(days=1), 118.0)

        with patch.object(
            PortfolioService,
            "_fetch_realtime_position_price",
            side_effect=AssertionError("fast portfolio snapshot should not fetch realtime quote"),
        ):
            snapshot = self.service.get_portfolio_snapshot(
                account_id=aid,
                as_of=today,
                cost_method="fifo",
                include_realtime=False,
            )

        pos = snapshot["accounts"][0]["positions"][0]
        self.assertAlmostEqual(pos["last_price"], 118.0, places=6)
        self.assertEqual(pos["price_source"], "history_close")
        self.assertEqual(pos["price_date"], (today - timedelta(days=1)).isoformat())
        self.assertTrue(pos["price_stale"])
        self.assertTrue(pos["price_available"])

    def test_current_snapshot_background_refresh_populates_cache_and_second_view_uses_it(self) -> None:
        """请求路径不阻塞实时行情：首次无缓存回到 missing，后台刷新写回缓存，二次视图命中新鲜缓存。"""
        today = date.today()
        account = self.service.create_account(name="Main", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]
        symbols = ["600519", "000001", "300750"]
        for symbol in symbols:
            self.service.record_trade(
                account_id=aid,
                symbol=symbol,
                trade_date=today,
                side="buy",
                quantity=10,
                price=100,
                market="cn",
                currency="CNY",
            )

        fetched: list[str] = []

        def fake_fetch(symbol: str) -> tuple[Optional[float], Optional[str]]:
            fetched.append(symbol)
            return (125.0, "unit-test")

        with patch.object(PortfolioService, "_fetch_realtime_position_price", side_effect=fake_fetch):
            # 首次请求路径不得内联取实时价（非阻塞）
            snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=today, cost_method="fifo")

        positions = snapshot["accounts"][0]["positions"]
        self.assertEqual(len(positions), 3)
        for p in positions:
            self.assertNotEqual(p["price_source"], "realtime_quote")

        # 等后台线程写回全部缓存
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if len(fetched) >= 3 and all(
                self.service.repo.get_latest_cached_quote(s) is not None for s in symbols
            ):
                break
            time.sleep(0.02)

        self.assertEqual(set(fetched), set(symbols))
        for s in symbols:
            cached = self.service.repo.get_latest_cached_quote(s)
            self.assertIsNotNone(cached)
            self.assertAlmostEqual(cached.price, 125.0, places=6)

        # 强制快照缓存过期，二次视图应命中新鲜持久化缓存
        portfolio_cache.clear()
        snapshot2 = self.service.get_portfolio_snapshot(account_id=aid, as_of=today, cost_method="fifo")
        for p in snapshot2["accounts"][0]["positions"]:
            self.assertEqual(p["price_source"], "realtime_cached")
            self.assertAlmostEqual(p["last_price"], 125.0, places=6)

    def test_current_snapshot_background_refresh_batches_all_symbols_without_blocking(self) -> None:
        """大持仓集合：后台一次性刷新全部标的，请求路径不阻塞（不内联实时取数）。"""
        today = date.today()
        account = self.service.create_account(name="Main", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]
        symbols = ["600519", "000001", "300750", "601318", "002594", "600036"]
        for symbol in symbols:
            self.service.record_trade(
                account_id=aid,
                symbol=symbol,
                trade_date=today,
                side="buy",
                quantity=10,
                price=100,
                market="cn",
                currency="CNY",
            )

        fetched: list[str] = []

        def fake_fetch(symbol: str) -> tuple[Optional[float], Optional[str]]:
            fetched.append(symbol)
            return (125.0, "unit-test")

        with patch.object(PortfolioService, "_fetch_realtime_position_price", side_effect=fake_fetch):
            snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=today, cost_method="fifo")

        positions = snapshot["accounts"][0]["positions"]
        self.assertEqual(len(positions), len(symbols))
        for p in positions:
            self.assertNotEqual(p["price_source"], "realtime_quote")

        deadline = time.time() + 3.0
        while time.time() < deadline:
            if len(fetched) >= len(symbols) and all(
                self.service.repo.get_latest_cached_quote(s) is not None for s in symbols
            ):
                break
            time.sleep(0.02)

        self.assertEqual(set(fetched), set(symbols))
        portfolio_cache.clear()
        snapshot2 = self.service.get_portfolio_snapshot(account_id=aid, as_of=today, cost_method="fifo")
        self.assertTrue(
            all(p["price_source"] == "realtime_cached" for p in snapshot2["accounts"][0]["positions"])
        )

    def test_historical_snapshot_marks_missing_price_without_cost_fallback(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]
        self.service.record_trade(
            account_id=aid,
            symbol="600519",
            trade_date=date(2026, 1, 1),
            side="buy",
            quantity=10,
            price=100,
            market="cn",
            currency="CNY",
        )

        with patch.object(
            PortfolioService,
            "_fetch_realtime_position_price",
            side_effect=AssertionError("historical snapshot should not fetch realtime quote"),
        ):
            snapshot = self.service.get_portfolio_snapshot(
                account_id=aid,
                as_of=date(2026, 1, 2),
                cost_method="fifo",
            )

        pos = snapshot["accounts"][0]["positions"][0]
        self.assertEqual(pos["last_price"], 0.0)
        self.assertEqual(pos["market_value_base"], 0.0)
        self.assertEqual(pos["unrealized_pnl_base"], 0.0)
        self.assertEqual(pos["price_source"], "missing")
        self.assertFalse(pos["price_available"])
        self.assertTrue(pos["price_stale"])
        self.assertEqual(snapshot["accounts"][0]["total_market_value"], 0.0)
        self.assertEqual(snapshot["accounts"][0]["unrealized_pnl"], 0.0)

    def test_snapshot_fifo_vs_avg_on_partial_sell(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]

        self.service.record_cash_ledger(
            account_id=aid,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=100000,
            currency="CNY",
        )
        self.service.record_trade(
            account_id=aid,
            symbol="600519",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=100,
            price=10,
            fee=10,
            tax=0,
            market="cn",
            currency="CNY",
        )
        self.service.record_trade(
            account_id=aid,
            symbol="600519",
            trade_date=date(2026, 1, 3),
            side="buy",
            quantity=100,
            price=20,
            fee=10,
            tax=0,
            market="cn",
            currency="CNY",
        )
        self.service.record_trade(
            account_id=aid,
            symbol="600519",
            trade_date=date(2026, 1, 4),
            side="sell",
            quantity=150,
            price=30,
            fee=10,
            tax=5,
            market="cn",
            currency="CNY",
        )
        self._save_close("600519", date(2026, 1, 5), 25)

        fifo = self.service.get_portfolio_snapshot(account_id=aid, as_of=date(2026, 1, 5), cost_method="fifo")
        avg = self.service.get_portfolio_snapshot(account_id=aid, as_of=date(2026, 1, 5), cost_method="avg")

        fifo_acc = fifo["accounts"][0]
        avg_acc = avg["accounts"][0]
        self.assertAlmostEqual(fifo_acc["total_equity"], avg_acc["total_equity"], places=6)

        self.assertAlmostEqual(fifo_acc["realized_pnl"], 2470.0, places=6)
        self.assertAlmostEqual(avg_acc["realized_pnl"], 2220.0, places=6)
        self.assertAlmostEqual(fifo_acc["unrealized_pnl"], 245.0, places=6)
        self.assertAlmostEqual(avg_acc["unrealized_pnl"], 495.0, places=6)

        self.assertEqual(len(fifo_acc["positions"]), 1)
        self.assertEqual(len(avg_acc["positions"]), 1)
        self.assertAlmostEqual(fifo_acc["positions"][0]["quantity"], 50.0, places=6)
        self.assertAlmostEqual(avg_acc["positions"][0]["quantity"], 50.0, places=6)

    def test_snapshot_position_price_metadata_uses_backend_values_for_cn_hk_us(self) -> None:
        for market, currency, symbol, close, expected_symbol in [
            ("cn", "CNY", "600519", 12.5, "600519"),
            ("hk", "HKD", "hk700", 420.0, "HK00700"),
            ("us", "USD", "aapl", 210.0, "AAPL"),
        ]:
            with self.subTest(market=market):
                aid = self._create_account_with_position(market=market, currency=currency, symbol=symbol, close=close)
                position = self.service.get_portfolio_snapshot(account_id=aid, as_of=date(2026, 1, 3), cost_method="fifo")["accounts"][0]["positions"][0]

                self.assertEqual(position["symbol"], expected_symbol)
                self.assertEqual(position["price_source"], "history_close")
                self.assertEqual(position["price_date"], "2026-01-03")
                self.assertFalse(position["price_stale"])
                self.assertTrue(position["price_available"])
                self.assertAlmostEqual(position["last_price"], close, places=6)
                self.assertAlmostEqual(position["market_value_base"], close * 10, places=6)
                self.assertAlmostEqual(position["unrealized_pnl_base"], close * 10 - 1000, places=6)
                self.assertAlmostEqual(position["unrealized_pnl_pct"], (close * 10 - 1000) / 1000 * 100, places=6)
                self.assertEqual(position["data_quality"], "ok")
                self.assertEqual(position["limitations"], [])

    def test_jp_kr_portfolio_snapshot_marks_partial_valuation_boundaries(self) -> None:
        for market, currency, symbol, close in [
            ("jp", "JPY", "7203.T", 3000.0),
            ("kr", "KRW", "005930.KS", 70000.0),
        ]:
            with self.subTest(market=market):
                aid = self._create_account_with_position(
                    market=market,
                    currency=currency,
                    symbol=symbol,
                    close=close,
                )

                snapshot = self.service.get_portfolio_snapshot(
                    account_id=aid,
                    as_of=date(2026, 1, 3),
                    cost_method="fifo",
                )
                account = snapshot["accounts"][0]
                position = account["positions"][0]

                self.assertEqual(account["market"], market)
                self.assertEqual(account["base_currency"], currency)
                self.assertEqual(account["data_quality"], "partial")
                self.assertEqual(
                    account["limitations"],
                    [
                        "realtime_quote_best_effort",
                        "fx_and_cost_basis_partial",
                        "sector_and_risk_metrics_limited",
                    ],
                )
                self.assertEqual(position["symbol"], symbol)
                self.assertEqual(position["data_quality"], "partial")
                self.assertIn("fx_and_cost_basis_partial", position["limitations"])

    def test_aggregate_snapshot_marks_partial_when_any_account_has_limitations(self) -> None:
        self._create_account_with_position(
            market="cn",
            currency="CNY",
            symbol="600519",
            close=120.0,
        )
        self._create_account_with_position(
            market="jp",
            currency="JPY",
            symbol="7203.T",
            close=3000.0,
        )

        snapshot = self.service.get_portfolio_snapshot(
            as_of=date(2026, 1, 3),
            cost_method="fifo",
        )

        self.assertEqual(snapshot["account_count"], 2)
        self.assertEqual(snapshot["data_quality"], "partial")
        self.assertEqual(
            snapshot["limitations"],
            [
                "realtime_quote_best_effort",
                "fx_and_cost_basis_partial",
                "sector_and_risk_metrics_limited",
            ],
        )

    def test_snapshot_marks_stale_close_and_missing_price(self) -> None:
        aid = self._create_account_with_position(
            market="cn",
            currency="CNY",
            symbol="600519",
            close=110,
            close_date=date(2026, 1, 2),
        )
        self.service.record_trade(
            account_id=aid,
            symbol="000001",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=5,
            price=20,
            market="cn",
            currency="CNY",
        )
        self._save_close("600519", date(2026, 1, 2), 110)

        snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=date(2026, 1, 3), cost_method="fifo")
        positions = {item["symbol"]: item for item in snapshot["accounts"][0]["positions"]}

        stale_close = positions["600519"]
        self.assertEqual(stale_close["price_source"], "history_close")
        self.assertEqual(stale_close["price_date"], "2026-01-02")
        self.assertTrue(stale_close["price_stale"])
        self.assertTrue(stale_close["price_available"])
        self.assertAlmostEqual(stale_close["last_price"], 110.0, places=6)
        self.assertAlmostEqual(stale_close["unrealized_pnl_pct"], 10.0, places=6)

        missing = positions["000001"]
        self.assertEqual(missing["price_source"], "missing")
        self.assertIsNone(missing["price_date"])
        self.assertTrue(missing["price_stale"])
        self.assertFalse(missing["price_available"])
        self.assertAlmostEqual(missing["last_price"], 0.0, places=6)
        self.assertAlmostEqual(missing["market_value_base"], 0.0, places=6)
        self.assertAlmostEqual(missing["unrealized_pnl_base"], 0.0, places=6)
        self.assertIsNone(missing["unrealized_pnl_pct"])

    def test_offexchange_fund_position_values_at_latest_nav(self) -> None:
        from data_provider.fund_fetcher import FundFetcher

        with patch.object(FundFetcher, "get_latest_nav", return_value=(1.5, date(2026, 1, 3))):
            account = self.service.create_account(name="Fund", broker="Demo", market="cn", base_currency="CNY")
            aid = account["id"]
            self.service.record_trade(
                account_id=aid,
                symbol="fund:006229",
                trade_date=date(2026, 1, 2),
                side="buy",
                quantity=500.0,
                price=1.2,
                market="cn",
                currency="CNY",
            )

            snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=date(2026, 1, 3), cost_method="fifo")
            position = snapshot["accounts"][0]["positions"][0]

            self.assertEqual(position["symbol"], "FUND:006229")
            self.assertEqual(position["price_source"], "fund_nav")
            self.assertEqual(position["price_provider"], "eastmoney")
            self.assertEqual(position["price_date"], "2026-01-03")
            self.assertFalse(position["price_stale"])
            self.assertTrue(position["price_available"])
            self.assertAlmostEqual(position["last_price"], 1.5, places=6)
            self.assertAlmostEqual(position["quantity"], 500.0, places=6)
            self.assertAlmostEqual(position["market_value_base"], 1.5 * 500.0, places=6)
            self.assertAlmostEqual(position["unrealized_pnl_base"], 1.5 * 500.0 - 1.2 * 500.0, places=6)
            self.assertIn("场外基金按最新单位净值估值，非实时", position["limitations"])

    def test_offexchange_fund_falls_back_to_missing_when_nav_unavailable(self) -> None:
        from data_provider.fund_fetcher import FundFetcher

        with patch.object(FundFetcher, "get_latest_nav", return_value=None):
            account = self.service.create_account(name="Fund", broker="Demo", market="cn", base_currency="CNY")
            aid = account["id"]
            self.service.record_trade(
                account_id=aid,
                symbol="fund:006229",
                trade_date=date(2026, 1, 2),
                side="buy",
                quantity=500.0,
                price=1.2,
                market="cn",
                currency="CNY",
            )

            snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=date(2026, 1, 3), cost_method="fifo")
            position = snapshot["accounts"][0]["positions"][0]

            self.assertEqual(position["price_source"], "missing")
            self.assertFalse(position["price_available"])
            self.assertAlmostEqual(position["last_price"], 0.0, places=6)
            self.assertIn("场外基金按最新单位净值估值，非实时", position["limitations"])

    def test_build_positions_handles_zero_cost_without_division(self) -> None:
        account = SimpleNamespace(base_currency="CNY")

        positions, _, _, _, _ = self.service._build_positions(
            account=account,
            as_of_date=date(2026, 1, 3),
            cost_method="avg",
            fifo_lots={},
            avg_state={("AAPL", "us", "USD"): _AvgState(quantity=10.0, total_cost=0.0)},
        )

        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["price_source"], "missing")
        self.assertIsNone(positions[0]["unrealized_pnl_pct"])
        self.assertAlmostEqual(positions[0]["last_price"], 0.0, places=6)

    def test_symbol_filter_matches_legacy_prefix_suffix_variants(self) -> None:
        account = self.service.create_account(name="Legacy", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]
        for symbol in ["600519", "SH600519", "600519.SH", "600519.SS"]:
            self.service.repo.add_trade(
                account_id=aid,
                trade_uid=None,
                symbol=symbol,
                market="cn",
                currency="CNY",
                trade_date=date(2026, 1, 2),
                side="buy",
                quantity=1,
                price=10,
                fee=0,
                tax=0,
            )

        rows = self.service.list_trade_events(account_id=aid, symbol="600519", page=1, page_size=20)["items"]
        self.assertEqual({row["symbol"] for row in rows}, {"600519", "SH600519", "600519.SH", "600519.SS"})

    def test_symbol_filter_matches_legacy_hk_variants(self) -> None:
        account = self.service.create_account(name="Legacy HK", broker="Demo", market="hk", base_currency="HKD")
        aid = account["id"]
        for symbol in ["HK00700", "HK700", "00700.HK", "700.HK"]:
            self.service.repo.add_trade(
                account_id=aid,
                trade_uid=None,
                symbol=symbol,
                market="hk",
                currency="HKD",
                trade_date=date(2026, 1, 2),
                side="buy",
                quantity=1,
                price=10,
                fee=0,
                tax=0,
            )

        rows = self.service.list_trade_events(account_id=aid, symbol="HK00700", page=1, page_size=20)["items"]
        self.assertEqual({row["symbol"] for row in rows}, {"HK00700", "HK700", "00700.HK", "700.HK"})

    def test_explicit_exchange_symbol_filter_does_not_match_other_exchanges(self) -> None:
        account = self.service.create_account(name="Mixed", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]
        for symbol in ["SH000001", "SZ000001", "000001.SH", "000001.SZ"]:
            self.service.repo.add_trade(
                account_id=aid,
                trade_uid=None,
                symbol=symbol,
                market="cn",
                currency="CNY",
                trade_date=date(2026, 1, 2),
                side="buy",
                quantity=1,
                price=10,
                fee=0,
                tax=0,
            )

        rows = self.service.list_trade_events(account_id=aid, symbol="SH000001", page=1, page_size=20)["items"]
        self.assertEqual({row["symbol"] for row in rows}, {"SH000001", "000001.SH"})

    def test_explicit_exchange_symbols_are_preserved_in_position_snapshot_and_validation(self) -> None:
        account = self.service.create_account(name="Explicit", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]
        self.service.record_cash_ledger(
            account_id=aid,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=10000,
            currency="CNY",
        )
        self.service.record_trade(
            account_id=aid,
            symbol="SH000001",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=1,
            price=10,
            currency="CNY",
            market="cn",
        )
        self.service.record_trade(
            account_id=aid,
            symbol="000001.SZ",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=1,
            price=10,
            currency="CNY",
            market="cn",
        )
        self.service.record_trade(
            account_id=aid,
            symbol="BJ920748",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=1,
            price=10,
            currency="CNY",
            market="cn",
        )

        sh_trades = self.service.list_trade_events(account_id=aid, symbol="SH000001", page=1, page_size=20)["items"]
        sz_trades = self.service.list_trade_events(account_id=aid, symbol="000001.SZ", page=1, page_size=20)["items"]
        bj_trades = self.service.list_trade_events(account_id=aid, symbol="BJ920748", page=1, page_size=20)["items"]
        self.assertEqual(sh_trades[0]["symbol"], "SH000001")
        self.assertEqual(sz_trades[0]["symbol"], "000001.SZ")
        self.assertEqual(bj_trades[0]["symbol"], "BJ920748")

        snapshot = self.service.get_portfolio_snapshot(
            account_id=aid,
            as_of=date(2026, 1, 4),
            cost_method="fifo",
        )
        symbols = {item["symbol"] for item in snapshot["accounts"][0]["positions"]}
        self.assertEqual(symbols, {"SH000001", "SZ000001", "BJ920748"})

        with self.assertRaises(PortfolioOversellError):
            self.service.record_trade(
                account_id=aid,
                symbol="SZ000001",
                trade_date=date(2026, 1, 5),
                side="sell",
                quantity=2,
                price=10,
                market="cn",
                currency="CNY",
            )

    def test_corporate_actions_dividend_and_split(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]
        self.service.record_cash_ledger(
            account_id=aid,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=10000,
            currency="CNY",
        )
        self.service.record_trade(
            account_id=aid,
            symbol="600519",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=100,
            price=10,
            fee=0,
            tax=0,
            market="cn",
            currency="CNY",
        )
        self.service.record_corporate_action(
            account_id=aid,
            symbol="600519",
            effective_date=date(2026, 1, 3),
            action_type="cash_dividend",
            market="cn",
            currency="CNY",
            cash_dividend_per_share=1.0,
        )
        self.service.record_corporate_action(
            account_id=aid,
            symbol="600519",
            effective_date=date(2026, 1, 4),
            action_type="split_adjustment",
            market="cn",
            currency="CNY",
            split_ratio=2.0,
        )
        self._save_close("600519", date(2026, 1, 5), 6.0)

        snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=date(2026, 1, 5), cost_method="fifo")
        acc = snapshot["accounts"][0]
        pos = acc["positions"][0]

        self.assertAlmostEqual(acc["total_cash"], 9100.0, places=6)
        self.assertAlmostEqual(acc["total_market_value"], 1200.0, places=6)
        self.assertAlmostEqual(acc["total_equity"], 10300.0, places=6)
        self.assertAlmostEqual(pos["quantity"], 200.0, places=6)
        self.assertAlmostEqual(pos["avg_cost"], 5.0, places=6)

    def test_normalize_symbol_preserves_cn_exchange_prefix_and_suffix(self) -> None:
        self.assertEqual(self.service._normalize_symbol("sh600519"), "SH600519")
        self.assertEqual(self.service._normalize_symbol("600519.SH"), "SH600519")
        self.assertEqual(self.service._normalize_symbol("SZ000001"), "SZ000001")
        self.assertEqual(self.service._normalize_symbol("000001.SZ"), "SZ000001")

    def test_explicit_exchange_position_valuation_uses_exchange_qualified_symbol(self) -> None:
        account = self.service.create_account(name="Explicit Valuation", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]
        self.service.record_cash_ledger(
            account_id=aid,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=20000,
            currency="CNY",
        )
        self.service.record_trade(
            account_id=aid,
            symbol="SH600519",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=1,
            price=10,
            currency="CNY",
            market="cn",
        )
        self.service.record_trade(
            account_id=aid,
            symbol="000001.SZ",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=2,
            price=8,
            currency="CNY",
            market="cn",
        )
        self._save_close(self.service._normalize_symbol("SH600519"), date(2026, 1, 3), 12.0)
        self._save_close(self.service._normalize_symbol("000001.SZ"), date(2026, 1, 3), 9.0)

        snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=date(2026, 1, 3), cost_method="fifo")
        positions = {item["symbol"]: item for item in snapshot["accounts"][0]["positions"]}
        self.assertEqual(set(positions), {"SH600519", "SZ000001"})
        self.assertEqual(positions["SH600519"]["price_source"], "history_close")
        self.assertAlmostEqual(positions["SH600519"]["last_price"], 12.0, places=6)
        self.assertEqual(positions["SZ000001"]["price_source"], "history_close")
        self.assertAlmostEqual(positions["SZ000001"]["last_price"], 9.0, places=6)

    def test_same_day_dividend_processed_before_trade(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]

        self.service.record_cash_ledger(
            account_id=aid,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=2000,
            currency="CNY",
        )
        self.service.record_corporate_action(
            account_id=aid,
            symbol="600519",
            effective_date=date(2026, 1, 2),
            action_type="cash_dividend",
            market="cn",
            currency="CNY",
            cash_dividend_per_share=1.0,
        )
        self.service.record_trade(
            account_id=aid,
            symbol="600519",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=100,
            price=10,
            market="cn",
            currency="CNY",
        )
        self._save_close("600519", date(2026, 1, 2), 10.0)

        snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=date(2026, 1, 2), cost_method="fifo")
        acc = snapshot["accounts"][0]

        self.assertAlmostEqual(acc["total_cash"], 1000.0, places=6)
        self.assertAlmostEqual(acc["total_market_value"], 1000.0, places=6)
        self.assertAlmostEqual(acc["total_equity"], 2000.0, places=6)

    def test_same_day_split_processed_before_trade(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]

        self.service.record_cash_ledger(
            account_id=aid,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=2000,
            currency="CNY",
        )
        self.service.record_trade(
            account_id=aid,
            symbol="600519",
            trade_date=date(2026, 1, 1),
            side="buy",
            quantity=100,
            price=10,
            market="cn",
            currency="CNY",
        )
        self.service.record_corporate_action(
            account_id=aid,
            symbol="600519",
            effective_date=date(2026, 1, 2),
            action_type="split_adjustment",
            market="cn",
            currency="CNY",
            split_ratio=2.0,
        )
        self.service.record_trade(
            account_id=aid,
            symbol="600519",
            trade_date=date(2026, 1, 2),
            side="sell",
            quantity=100,
            price=6,
            market="cn",
            currency="CNY",
        )
        self._save_close("600519", date(2026, 1, 2), 6.0)

        snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=date(2026, 1, 2), cost_method="fifo")
        acc = snapshot["accounts"][0]
        pos = acc["positions"][0]

        self.assertAlmostEqual(acc["realized_pnl"], 100.0, places=6)
        self.assertAlmostEqual(acc["total_cash"], 1600.0, places=6)
        self.assertAlmostEqual(pos["quantity"], 100.0, places=6)
        self.assertAlmostEqual(pos["avg_cost"], 5.0, places=6)

    def test_sell_oversell_rejected_before_write(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]

        self.service.record_trade(
            account_id=aid,
            symbol="600519",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=10,
            price=10,
            market="cn",
            currency="CNY",
        )

        with self.assertRaises(PortfolioOversellError):
            self.service.record_trade(
                account_id=aid,
                symbol="600519",
                trade_date=date(2026, 1, 3),
                side="sell",
                quantity=20,
                price=11,
                market="cn",
                currency="CNY",
            )

        trades = self.service.list_trade_events(account_id=aid, page=1, page_size=20)
        self.assertEqual(len(trades["items"]), 1)
        self.assertEqual(trades["items"][0]["side"], "buy")

    def test_duplicate_full_close_sell_keeps_conflict_semantics(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]

        self.service.record_trade(
            account_id=aid,
            symbol="600519",
            trade_date=date(2026, 1, 1),
            side="buy",
            quantity=10,
            price=10,
            market="cn",
            currency="CNY",
        )
        self.service.record_trade(
            account_id=aid,
            symbol="600519",
            trade_date=date(2026, 1, 2),
            side="sell",
            quantity=10,
            price=11,
            market="cn",
            currency="CNY",
            trade_uid="sell-full-close-1",
        )

        with self.assertRaises(PortfolioConflictError) as ctx:
            self.service.record_trade(
                account_id=aid,
                symbol="600519",
                trade_date=date(2026, 1, 2),
                side="sell",
                quantity=10,
                price=11,
                market="cn",
                currency="CNY",
                trade_uid="sell-full-close-1",
            )

        self.assertIn("Duplicate trade_uid", str(ctx.exception))

    def test_backdated_trade_write_invalidates_future_cache(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]

        self.service.record_cash_ledger(
            account_id=aid,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=10000,
            currency="CNY",
        )
        self.service.record_trade(
            account_id=aid,
            symbol="600519",
            trade_date=date(2026, 1, 3),
            side="buy",
            quantity=10,
            price=100,
            market="cn",
            currency="CNY",
        )
        self._save_close("600519", date(2026, 1, 3), 100.0)
        self.service.get_portfolio_snapshot(account_id=aid, as_of=date(2026, 1, 3), cost_method="fifo")

        with self.db.get_session() as session:
            snapshot_count = session.execute(
                select(PortfolioDailySnapshot).where(PortfolioDailySnapshot.account_id == aid)
            ).scalars().all()
            position_count = session.execute(
                select(PortfolioPosition).where(PortfolioPosition.account_id == aid)
            ).scalars().all()
            lot_count = session.execute(
                select(PortfolioPositionLot).where(PortfolioPositionLot.account_id == aid)
            ).scalars().all()
        self.assertEqual(len(snapshot_count), 1)
        self.assertEqual(len(position_count), 1)
        self.assertEqual(len(lot_count), 1)

        self.service.record_trade(
            account_id=aid,
            symbol="600519",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=5,
            price=80,
            market="cn",
            currency="CNY",
        )

        with self.db.get_session() as session:
            snapshot_rows = session.execute(
                select(PortfolioDailySnapshot).where(PortfolioDailySnapshot.account_id == aid)
            ).scalars().all()
            position_rows = session.execute(
                select(PortfolioPosition).where(PortfolioPosition.account_id == aid)
            ).scalars().all()
            lot_rows = session.execute(
                select(PortfolioPositionLot).where(PortfolioPositionLot.account_id == aid)
            ).scalars().all()
        self.assertEqual(len(snapshot_rows), 0)
        self.assertEqual(len(position_rows), 0)
        self.assertEqual(len(lot_rows), 0)

    def test_delete_trade_invalidates_cache_and_removes_source_event(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]

        self.service.record_cash_ledger(
            account_id=aid,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=10000,
            currency="CNY",
        )
        trade = self.service.record_trade(
            account_id=aid,
            symbol="600519",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=10,
            price=100,
            market="cn",
            currency="CNY",
        )
        self._save_close("600519", date(2026, 1, 2), 100.0)
        self.service.get_portfolio_snapshot(account_id=aid, as_of=date(2026, 1, 2), cost_method="fifo")

        self.assertTrue(self.service.delete_trade_event(trade["id"]))

        with self.db.get_session() as session:
            trade_rows = session.execute(
                select(PortfolioTrade).where(PortfolioTrade.account_id == aid)
            ).scalars().all()
            snapshot_rows = session.execute(
                select(PortfolioDailySnapshot).where(PortfolioDailySnapshot.account_id == aid)
            ).scalars().all()
            lot_rows = session.execute(
                select(PortfolioPositionLot).where(PortfolioPositionLot.account_id == aid)
            ).scalars().all()
        self.assertEqual(len(trade_rows), 0)
        self.assertEqual(len(snapshot_rows), 0)
        self.assertEqual(len(lot_rows), 0)

    def test_concurrent_sell_race_allows_only_one_write(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]
        self.service.record_trade(
            account_id=aid,
            symbol="600519",
            trade_date=date(2026, 1, 1),
            side="buy",
            quantity=10,
            price=10,
            market="cn",
            currency="CNY",
        )

        barrier = threading.Barrier(3)
        results: list[str] = []
        errors: list[Exception] = []

        def _worker(uid: str) -> None:
            svc = PortfolioService()
            barrier.wait()
            try:
                svc.record_trade(
                    account_id=aid,
                    symbol="600519",
                    trade_date=date(2026, 1, 2),
                    side="sell",
                    quantity=10,
                    price=11,
                    market="cn",
                    currency="CNY",
                    trade_uid=uid,
                )
                results.append(uid)
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [
            threading.Thread(target=_worker, args=(f"sell-race-{idx}",), daemon=True)
            for idx in range(2)
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], PortfolioOversellError)

        trades = self.service.list_trade_events(account_id=aid, page=1, page_size=20)
        sell_count = sum(1 for item in trades["items"] if item["side"] == "sell")
        self.assertEqual(sell_count, 1)

    def test_concurrent_duplicate_full_close_sell_keeps_conflict_semantics(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]
        self.service.record_trade(
            account_id=aid,
            symbol="600519",
            trade_date=date(2026, 1, 1),
            side="buy",
            quantity=10,
            price=10,
            market="cn",
            currency="CNY",
        )

        barrier = threading.Barrier(3)
        results: list[str] = []
        errors: list[Exception] = []

        def _worker() -> None:
            svc = PortfolioService()
            barrier.wait()
            try:
                svc.record_trade(
                    account_id=aid,
                    symbol="600519",
                    trade_date=date(2026, 1, 2),
                    side="sell",
                    quantity=10,
                    price=11,
                    market="cn",
                    currency="CNY",
                    trade_uid="dup-race-sell-1",
                )
                results.append("ok")
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=_worker, daemon=True) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], PortfolioConflictError)
        self.assertIn("Duplicate trade_uid", str(errors[0]))

    def test_event_symbol_filters_match_legacy_prefixed_symbols(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]

        self.service.repo.add_trade(
            account_id=aid,
            trade_uid="legacy-prefixed-trade",
            symbol="SH600519",
            market="cn",
            currency="CNY",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=10,
            price=100,
            fee=0,
            tax=0,
        )
        self.service.repo.add_corporate_action(
            account_id=aid,
            symbol="SH600519",
            market="cn",
            currency="CNY",
            effective_date=date(2026, 1, 3),
            action_type="cash_dividend",
            cash_dividend_per_share=1.0,
        )

        trades = self.service.list_trade_events(account_id=aid, symbol="600519", page=1, page_size=20)
        actions = self.service.list_corporate_action_events(account_id=aid, symbol="600519", page=1, page_size=20)

        self.assertEqual(trades["total"], 1)
        self.assertEqual(actions["total"], 1)
        self.assertEqual(trades["items"][0]["symbol"], "SH600519")
        self.assertEqual(actions["items"][0]["symbol"], "SH600519")

    def test_event_symbol_filters_match_legacy_suffix_symbols(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]

        self.service.repo.add_trade(
            account_id=aid,
            trade_uid="legacy-suffix-trade",
            symbol="600519.SH",
            market="cn",
            currency="CNY",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=10,
            price=100,
            fee=0,
            tax=0,
        )
        self.service.repo.add_corporate_action(
            account_id=aid,
            symbol="600519.SH",
            market="cn",
            currency="CNY",
            effective_date=date(2026, 1, 3),
            action_type="cash_dividend",
            cash_dividend_per_share=1.0,
        )

        trades = self.service.list_trade_events(account_id=aid, symbol="600519", page=1, page_size=20)
        actions = self.service.list_corporate_action_events(account_id=aid, symbol="600519", page=1, page_size=20)

        self.assertEqual(trades["total"], 1)
        self.assertEqual(actions["total"], 1)
        self.assertEqual(trades["items"][0]["symbol"], "600519.SH")
        self.assertEqual(actions["items"][0]["symbol"], "600519.SH")

    def test_event_symbol_filters_match_legacy_hk_variants(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="hk", base_currency="HKD")
        aid = account["id"]

        self.service.repo.add_trade(
            account_id=aid,
            trade_uid="legacy-hk-prefixed-trade",
            symbol="HK700",
            market="hk",
            currency="HKD",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=10,
            price=400,
            fee=0,
            tax=0,
        )
        self.service.repo.add_trade(
            account_id=aid,
            trade_uid="legacy-hk-suffix-trade",
            symbol="00700.HK",
            market="hk",
            currency="HKD",
            trade_date=date(2026, 1, 3),
            side="buy",
            quantity=5,
            price=410,
            fee=0,
            tax=0,
        )
        self.service.repo.add_trade(
            account_id=aid,
            trade_uid="legacy-hk-short-suffix-trade",
            symbol="700.HK",
            market="hk",
            currency="HKD",
            trade_date=date(2026, 1, 4),
            side="buy",
            quantity=3,
            price=415,
            fee=0,
            tax=0,
        )
        self.service.repo.add_corporate_action(
            account_id=aid,
            symbol="HK700",
            market="hk",
            currency="HKD",
            effective_date=date(2026, 1, 4),
            action_type="cash_dividend",
            cash_dividend_per_share=1.0,
        )
        self.service.repo.add_corporate_action(
            account_id=aid,
            symbol="00700.HK",
            market="hk",
            currency="HKD",
            effective_date=date(2026, 1, 5),
            action_type="cash_dividend",
            cash_dividend_per_share=1.5,
        )
        self.service.repo.add_corporate_action(
            account_id=aid,
            symbol="700.HK",
            market="hk",
            currency="HKD",
            effective_date=date(2026, 1, 6),
            action_type="cash_dividend",
            cash_dividend_per_share=2.0,
        )

        trades = self.service.list_trade_events(account_id=aid, symbol="HK00700", page=1, page_size=20)
        actions = self.service.list_corporate_action_events(account_id=aid, symbol="HK00700", page=1, page_size=20)

        self.assertEqual(trades["total"], 3)
        self.assertEqual(actions["total"], 3)
        self.assertEqual({item["symbol"] for item in trades["items"]}, {"HK700", "00700.HK", "700.HK"})
        self.assertEqual({item["symbol"] for item in actions["items"]}, {"HK700", "00700.HK", "700.HK"})

    def test_portfolio_write_session_maps_sqlite_locked_error(self) -> None:
        repo = PortfolioRepository(db_manager=self.db)
        session = self.db.get_session()
        stmt_exc = OperationalError(
            "BEGIN IMMEDIATE",
            None,
            sqlite3.OperationalError("database is locked"),
        )

        with patch.object(self.db, "get_session", return_value=session):
            with patch.object(
                session.connection(),
                "exec_driver_sql",
                side_effect=stmt_exc,
            ):
                with self.assertRaises(PortfolioBusyError):
                    with repo.portfolio_write_session():
                        pass


if __name__ == "__main__":
    unittest.main()
