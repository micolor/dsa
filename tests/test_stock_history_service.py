# -*- coding: utf-8 -*-
"""Tests for StockService.get_history_data DB fast-path + persist."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

from src.config import Config
from src.services.stock_service import StockService
from src.storage import DatabaseManager


def _daily_df(code: str, latest: date, count: int) -> pd.DataFrame:
    rows = []
    for offset in range(count):
        d = latest - timedelta(days=(count - 1 - offset))
        close = 100.0 + offset
        rows.append({
            "date": d,
            "open": close - 1,
            "high": close + 1,
            "low": close - 2,
            "close": close,
            "volume": 1000.0,
            "amount": 10000.0,
            "pct_chg": 1.0,
        })
    return pd.DataFrame(rows)


class StockHistoryServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_path = Path(self.temp_dir.name) / ".env"
        self.db_path = Path(self.temp_dir.name) / "history_test.db"
        self.env_path.write_text(
            "\n".join([
                "STOCK_LIST=600519",
                "GEMINI_API_KEY=test",
                "ADMIN_AUTH_ENABLED=false",
                f"DATABASE_PATH={self.db_path}",
            ])
            + "\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(self.env_path)
        os.environ["DATABASE_PATH"] = str(self.db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()
        self.service = StockService()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    def test_db_fast_path_returns_cached_rows_without_network(self) -> None:
        today = date.today()
        self.db.save_daily_data(_daily_df("600519", today, 60), "600519", "unit-test")

        with patch("data_provider.base.DataFetcherManager") as ctor:
            ctor.return_value.get_daily_data.side_effect = AssertionError("network must not be hit on cache hit")

            result = self.service.get_history_data("600519", period="daily", days=60)

        self.assertTrue(ctor.call_count == 0, "cache hit should not construct DataFetcherManager")
        self.assertGreaterEqual(len(result["data"]), 1)
        self.assertEqual(result["stock_code"], "600519")
        # date 升序
        dates = [r["date"] for r in result["data"]]
        self.assertEqual(dates, sorted(dates))

    def test_network_fetch_is_persisted_and_second_call_hits_cache(self) -> None:
        today = date.today()
        network_df = _daily_df("600519", today, 40)
        mock_manager = MagicMock()
        mock_manager.get_daily_data.return_value = (network_df, "test-source")
        mock_manager.get_stock_name.return_value = "贵州茅台"

        with patch("data_provider.base.DataFetcherManager", return_value=mock_manager):
            first = self.service.get_history_data("600519", period="daily", days=60)
        self.assertEqual(first["stock_name"], "贵州茅台")
        self.assertGreaterEqual(len(first["data"]), 1)
        self.assertEqual(mock_manager.get_daily_data.call_count, 1)

        # 落库成功后第二次请求走 DB 快路径，不再调网络
        with patch("data_provider.base.DataFetcherManager") as ctor2:
            ctor2.return_value.get_daily_data.side_effect = AssertionError("network must not be hit after persist")
            second = self.service.get_history_data("600519", period="daily", days=60)
        self.assertEqual(ctor2.call_count, 0)
        self.assertGreaterEqual(len(second["data"]), 1)

    def test_stale_cache_falls_back_to_network(self) -> None:
        # 最新日线距今超过 4 个日历日 -> 视为陈旧，应回退网络
        old = date.today() - timedelta(days=30)
        self.db.save_daily_data(_daily_df("600519", old, 60), "600519", "unit-test")

        mock_manager = MagicMock()
        mock_manager.get_daily_data.return_value = (_daily_df("600519", date.today(), 60), "test-source")
        mock_manager.get_stock_name.return_value = "贵州茅台"

        with patch("data_provider.base.DataFetcherManager", return_value=mock_manager):
            result = self.service.get_history_data("600519", period="daily", days=60)
        self.assertEqual(mock_manager.get_daily_data.call_count, 1)

    def test_hk_missing_returns_empty_without_crash(self) -> None:
        mock_manager = MagicMock()
        mock_manager.get_daily_data.return_value = (None, "test-source")
        mock_manager.get_stock_name.return_value = None

        with patch("data_provider.base.DataFetcherManager", return_value=mock_manager):
            result = self.service.get_history_data("HK00700", period="daily", days=60)
        self.assertEqual(result["data"], [])
        self.assertEqual(mock_manager.get_daily_data.call_count, 1)


if __name__ == "__main__":
    unittest.main()
