# -*- coding: utf-8 -*-
"""Tests for the deterministic ATR-based risk/position engine."""

import unittest

import pandas as pd

from src.services.risk_position_engine import (
    compute_atr,
    compute_position_size,
    derive_stop_loss,
    derive_take_profit,
    position_size_to_cheng,
)


def _bars(prices: list[float]) -> list[dict]:
    """构造 list[dict] 形式的日线 OHLC。"""
    return [
        {
            "date": "2025-01-01",
            "open": p,
            "high": p * 1.02,
            "low": p * 0.98,
            "close": p,
            "volume": 1_000_000,
        }
        for p in prices
    ]


class AtrTest(unittest.TestCase):
    def test_compute_atr_returns_positive_for_volatile_series(self) -> None:
        prices = [10 + i * 0.2 for i in range(30)]
        atr = compute_atr(_bars(prices))
        self.assertIsNotNone(atr)
        self.assertGreater(atr, 0)

    def test_compute_atr_accepts_dataframe(self) -> None:
        prices = [10 + i * 0.2 for i in range(30)]
        df = pd.DataFrame(_bars(prices))
        atr = compute_atr(df)
        self.assertIsNotNone(atr)

    def test_compute_atr_returns_none_for_insufficient_data(self) -> None:
        atr = compute_atr([])
        self.assertIsNone(atr)
        atr2 = compute_atr(_bars([]))
        self.assertIsNone(atr2)


class StopLossTest(unittest.TestCase):
    def test_derive_stop_loss(self) -> None:
        stop = derive_stop_loss(100.0, 2.0, multiplier=2.0)
        self.assertAlmostEqual(stop, 100.0 - 2.0 * 2.0, places=2)

    def test_derive_stop_loss_returns_none_on_invalid_input(self) -> None:
        self.assertIsNone(derive_stop_loss(None, 2.0))
        self.assertIsNone(derive_stop_loss(100.0, None))
        self.assertIsNone(derive_stop_loss(100.0, 0.0))
        # 止损低于 0 时不返回
        self.assertIsNone(derive_stop_loss(1.0, 2.0))

    def test_derive_take_profit(self) -> None:
        tp = derive_take_profit(100.0, 2.0, stop_multiplier=2.0, risk_reward=1.5)
        self.assertAlmostEqual(tp, 100.0 + 2.0 * 2.0 * 1.5, places=2)


class PositionSizeTest(unittest.TestCase):
    def test_compute_position_size_bounded_ratio(self) -> None:
        ratio = compute_position_size(100.0, 2.0, equity=100_000.0)
        self.assertIsNotNone(ratio)
        self.assertGreater(ratio, 0.0)
        self.assertLessEqual(ratio, 0.30)

    def test_compute_position_size_returns_none_without_equity(self) -> None:
        self.assertIsNone(compute_position_size(100.0, 2.0, equity=None))
        self.assertIsNone(compute_position_size(100.0, 2.0, equity=0.0))

    def test_concentration_scale_compresses_position(self) -> None:
        base = compute_position_size(100.0, 2.0, equity=100_000.0, concentration_scale=1.0)
        compressed = compute_position_size(100.0, 2.0, equity=100_000.0, concentration_scale=0.5)
        self.assertIsNotNone(base)
        self.assertIsNotNone(compressed)
        self.assertLess(compressed, base)

    def test_position_size_to_cheng(self) -> None:
        self.assertEqual(position_size_to_cheng(0.3), "3成仓")
        self.assertEqual(position_size_to_cheng(0.15), "1.5成仓")
        self.assertIsNone(position_size_to_cheng(None))
        self.assertIsNone(position_size_to_cheng(0.0))


if __name__ == "__main__":
    unittest.main()
