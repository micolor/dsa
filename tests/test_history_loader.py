# -*- coding: utf-8 -*-
"""Unit tests for src.services.history_loader (Issue #1066)."""
from __future__ import annotations

import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd


class HistoryLoaderTestCase(unittest.TestCase):
    """Tests for load_history_df and frozen target date ContextVar."""

    # ------------------------------------------------------------------
    # ContextVar lifecycle
    # ------------------------------------------------------------------
    def test_frozen_target_date_lifecycle(self):
        from src.services.history_loader import (
            get_frozen_target_date,
            reset_frozen_target_date,
            set_frozen_target_date,
        )

        self.assertIsNone(get_frozen_target_date())
        d = date(2026, 4, 18)
        token = set_frozen_target_date(d)
        self.assertEqual(get_frozen_target_date(), d)
        reset_frozen_target_date(token)
        self.assertIsNone(get_frozen_target_date())

    # ------------------------------------------------------------------
    # DB hit path
    # ------------------------------------------------------------------
    @patch("src.storage.get_db")
    def test_returns_db_data_when_sufficient(self, mock_get_db):
        from src.services.history_loader import load_history_df

        fake_bar = MagicMock()
        fake_bar.to_dict.return_value = {
            "date": "2026-04-18",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10.5,
            "volume": 100,
        }
        mock_db = MagicMock()
        mock_db.get_data_range.return_value = [fake_bar] * 40
        mock_get_db.return_value = mock_db

        df, source = load_history_df("600519", days=60, target_date=date(2026, 4, 18))

        self.assertIsNotNone(df)
        self.assertEqual(source, "db_cache")
        self.assertEqual(len(df), 40)
        mock_db.get_data_range.assert_called_once()

    # ------------------------------------------------------------------
    # Default end date on a non-session day
    # ------------------------------------------------------------------
    @patch("src.core.trading_calendar.get_effective_trading_date")
    @patch("src.core.trading_calendar.get_market_for_stock", return_value="cn")
    @patch("src.storage.get_db")
    def test_default_end_date_is_the_latest_completed_session(
        self, mock_get_db, _mock_market, mock_effective
    ):
        """无 target_date 时，DB 查询上界必须是最近一个已完成交易日，而不是今天。

        命中条件是 `latest_date >= end`，而 `get_data_range` 是闭区间，所以
        `end = date.today()` 实际要求「库里有一根日期正好等于今天的 K 线」。
        周末 / 节假日 / 当日日线尚未落库时它必然不成立，哪怕库里已有整段历史，
        也会跳过本地缓存去走网络（网络不可用或限流时价格走势图长期停在「刷新中」）。
        """
        from src.services.history_loader import load_history_df

        session = date.today() - timedelta(days=7)
        mock_effective.return_value = session

        class _Bar:
            def __init__(self, d):
                self.date = d
                self.open = self.high = self.low = self.close = 10.0
                self.volume = 100

            def to_dict(self):
                return {
                    "date": self.date,
                    "open": self.open,
                    "high": self.high,
                    "low": self.low,
                    "close": self.close,
                    "volume": self.volume,
                }

        mock_db = MagicMock()
        mock_db.get_data_range.return_value = [_Bar(session) for _ in range(40)]
        mock_get_db.return_value = mock_db

        with patch("src.services.history_loader._get_fetcher_manager") as mock_get_fm:
            df, source = load_history_df("600519", days=60)

        self.assertEqual(source, "db_cache")
        self.assertIsNotNone(df)
        self.assertEqual(mock_db.get_data_range.call_args[0][2], session)
        mock_get_fm.assert_not_called()

    # ------------------------------------------------------------------
    # DB miss → DFM fallback
    # ------------------------------------------------------------------
    @patch("src.services.history_loader._get_fetcher_manager")
    @patch("src.storage.get_db")
    def test_falls_back_to_dfm_when_db_empty(self, mock_get_db, mock_get_fm):
        from src.services.history_loader import load_history_df

        mock_db = MagicMock()
        mock_db.get_data_range.return_value = []
        mock_get_db.return_value = mock_db

        fake_df = pd.DataFrame({"close": [1, 2, 3]})
        mock_fm = MagicMock()
        mock_fm.get_daily_data.return_value = (fake_df, "eastmoney")
        mock_get_fm.return_value = mock_fm

        df, source = load_history_df("600519", days=60, target_date=date(2026, 4, 18))

        self.assertIsNotNone(df)
        self.assertEqual(source, "eastmoney")
        mock_fm.get_daily_data.assert_called_once_with("600519", days=60)

    # ------------------------------------------------------------------
    # ContextVar integration
    # ------------------------------------------------------------------
    @patch("src.storage.get_db")
    def test_uses_frozen_target_date_from_contextvar(self, mock_get_db):
        from src.services.history_loader import (
            load_history_df,
            reset_frozen_target_date,
            set_frozen_target_date,
        )

        frozen_date = date(2026, 4, 15)
        token = set_frozen_target_date(frozen_date)
        try:
            mock_db = MagicMock()
            fake_bar = MagicMock()
            fake_bar.to_dict.return_value = {"date": "2026-04-15", "close": 10}
            mock_db.get_data_range.return_value = [fake_bar] * 30
            mock_get_db.return_value = mock_db

            df, source = load_history_df("600519", days=30)

            self.assertEqual(source, "db_cache")
            call_args = mock_db.get_data_range.call_args
            _code, _start, end = call_args[0]
            self.assertEqual(end, frozen_date)
        finally:
            reset_frozen_target_date(token)

    # ------------------------------------------------------------------
    # normalize_stock_code fallback for prefixed codes
    # ------------------------------------------------------------------
    @patch("src.storage.get_db")
    def test_uses_normalize_fallback_for_prefixed_code(self, mock_get_db):
        from src.services.history_loader import load_history_df

        fake_bar = MagicMock()
        fake_bar.to_dict.return_value = {"date": "2026-04-18", "close": 10}

        mock_db = MagicMock()

        def side_effect(code, start, end):
            if code == "SH600519":
                return []
            return [fake_bar] * 30

        mock_db.get_data_range.side_effect = side_effect
        mock_get_db.return_value = mock_db

        df, source = load_history_df("SH600519", days=30, target_date=date(2026, 4, 18))

        self.assertEqual(source, "db_cache")
        self.assertEqual(mock_db.get_data_range.call_count, 2)

    # ------------------------------------------------------------------
    # Both paths fail gracefully
    # ------------------------------------------------------------------
    @patch("src.services.history_loader._get_fetcher_manager")
    @patch("src.storage.get_db")
    def test_graceful_when_both_fail(self, mock_get_db, mock_get_fm):
        from src.services.history_loader import load_history_df

        mock_db = MagicMock()
        mock_db.get_data_range.side_effect = Exception("DB down")
        mock_get_db.return_value = mock_db

        mock_fm = MagicMock()
        mock_fm.get_daily_data.side_effect = Exception("API down")
        mock_get_fm.return_value = mock_fm

        df, source = load_history_df("600519", days=60, target_date=date(2026, 4, 18))

        self.assertIsNone(df)
        self.assertEqual(source, "none")


if __name__ == "__main__":
    unittest.main()
